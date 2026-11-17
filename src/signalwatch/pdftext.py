"""从 PDF 里抠文字（只用标准库）。

巨潮的公告 PDF 是「子集字体 + CID 编码」：正文里是 `<0014>Tj` 这种字形号，
必须靠文件里的 ToUnicode CMap 才能还原成汉字；而且**每个字体一张表**，
同一个码在不同字体里意思不同，所以必须按 `/FT8 Tf` 切换当前字体再查表。

三步：
1. 拆出所有 `N 0 obj ... endobj`，记下对象号；
2. 找每个页面的 `/Resources /Font << /FT8 12 0 R ... >>`，顺着拿到该字体的 ToUnicode 流并建码表；
3. 顺序扫内容流：`/FTx Tf` 切表，`Tj/TJ/'/"` 里的字符串按当前表翻译。
"""

from __future__ import annotations

import re
import zlib

OBJ_RE = re.compile(rb"(\d+)\s+0\s+obj(.*?)endobj", re.S)
STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.S)
BFCHAR_RE = re.compile(rb"beginbfchar(.*?)endbfchar", re.S)
BFRANGE_RE = re.compile(rb"beginbfrange(.*?)endbfrange", re.S)
HEX_PAIR_RE = re.compile(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>")
RANGE_RE = re.compile(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>")
FONT_ALIAS_RE = re.compile(rb"/Font\s*<<(.*?)>>", re.S)
ALIAS_REF_RE = re.compile(rb"/([A-Za-z0-9#+.\-]+)\s+(\d+)\s+0\s+R")
TOUNICODE_RE = re.compile(rb"/ToUnicode\s+(\d+)\s+0\s+R")
FONT_SWITCH_RE = re.compile(rb"/([A-Za-z0-9#+.\-]+)\s+[0-9.]+\s+Tf")
SHOW_RE = re.compile(
    rb"(\[(?:[^\[\]\\]|\\.)*\]\s*TJ"
    rb"|\((?:[^()\\]|\\.)*\)\s*(?:Tj|'|\")"
    rb"|<[0-9A-Fa-f\s]+>\s*(?:Tj|'|\"))",
    re.S,
)
ESC = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f",
       b"(": b"(", b")": b")", b"\\": b"\\"}


def _inflate(raw: bytes) -> bytes:
    try:
        return zlib.decompress(raw)
    except Exception:
        return b""


def _stream_of(body: bytes) -> bytes:
    m = STREAM_RE.search(body)
    if not m:
        return b""
    raw = m.group(1)
    data = _inflate(raw)
    if data:
        return data
    # 有些 PDF 的 CMap / 内容流是明文存的，解压失败时原样返回
    if b"beginbfchar" in raw or b"beginbfrange" in raw or b"BT" in raw:
        return raw
    return b""


def _hex_units(h: bytes) -> list[int]:
    h = re.sub(rb"\s", b"", h)
    if len(h) % 4 == 0:
        return [int(h[i:i + 4], 16) for i in range(0, len(h), 4)]
    if len(h) % 2 == 0:
        return [int(h[i:i + 2], 16) for i in range(0, len(h), 2)]
    return []


def _utf16(raw: bytes) -> str:
    try:
        return raw.decode("utf-16-be", "ignore")
    except Exception:
        return ""


def _cmap(stream: bytes) -> dict[int, str]:
    table: dict[int, str] = {}
    for block in BFCHAR_RE.findall(stream):
        for src, dst in HEX_PAIR_RE.findall(block):
            text = _utf16(bytes.fromhex(dst.decode()))
            if text:
                for code in _hex_units(src):
                    table[code] = text[0]
    for block in BFRANGE_RE.findall(stream):
        for lo, hi, dst in RANGE_RE.findall(block):
            lo_u, hi_u = _hex_units(lo), _hex_units(hi)
            base = _utf16(bytes.fromhex(dst.decode()))
            if not lo_u or not hi_u or not base:
                continue
            start, end = lo_u[0], hi_u[0]
            if end < start or end - start > 65535:
                continue
            for i, code in enumerate(range(start, min(end, start + len(base) - 1) + 1)):
                table[code] = base[i]
    return table


def _decode_literal(raw: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(raw):
        ch = raw[i:i + 1]
        if ch != b"\\":
            out += ch
            i += 1
            continue
        nxt = raw[i + 1:i + 2]
        if nxt in ESC:
            out += ESC[nxt]
            i += 2
        elif nxt.isdigit():
            digits = b""
            j = i + 1
            while j < len(raw) and len(digits) < 3 and raw[j:j + 1].isdigit():
                digits += raw[j:j + 1]
                j += 1
            out.append(int(digits, 8) & 0xFF)
            i = j
        else:
            i += 2
    return bytes(out)


def _piece(text: bytes, table: dict[int, str]) -> str:
    """把一段 (..) 或 <..> 显示串按当前字体的表翻译。"""
    out = []
    if text.startswith(b"<"):
        for code in _hex_units(text[1:-1]):
            out.append(table.get(code, ""))
        return "".join(out)
    raw = _decode_literal(text[1:-1])
    if any(code in table for code in range(256)):
        for i in range(0, len(raw) - 1, 2):
            out.append(table.get((raw[i] << 8) | raw[i + 1], ""))
        return "".join(out)
    return raw.decode("latin-1", "ignore")


def extract(pdf: bytes, limit: int = 20000) -> str:
    objs = {int(m.group(1)): m.group(2) for m in OBJ_RE.finditer(pdf)}
    if not objs:
        return ""

    # 资源里的别名 -> 字体对象号（多页共用同一批字体，合并即可）
    alias_to_obj: dict[bytes, int] = {}
    for body in objs.values():
        for block in FONT_ALIAS_RE.findall(body):
            for name, num in ALIAS_REF_RE.findall(block):
                alias_to_obj.setdefault(name, int(num))

    tables: dict[bytes, dict[int, str]] = {}
    for name, obj_num in alias_to_obj.items():
        body = objs.get(obj_num)
        if not body:
            continue
        m = TOUNICODE_RE.search(body)
        if not m:
            continue
        cmap_body = objs.get(int(m.group(1)))
        if not cmap_body:
            continue
        table = _cmap(_stream_of(cmap_body))
        if table:
            tables[name] = table

    if not tables:
        # 退路：全文件所有 CMap 合并，至少能出一部分字
        merged: dict[int, str] = {}
        for body in objs.values():
            for st in [body] + ([_stream_of(body)] if b"stream" in body else []):
                for t in (st,):
                    if b"beginbfchar" in t or b"beginbfrange" in t:
                        merged.update(_cmap(t))
        if not merged:
            return ""
        tables = {b"__all__": merged}

    out: list[str] = []
    current = next(iter(tables.values()))
    for body in objs.values():
        content = _stream_of(body) if b"stream" in body else b""
        if b"BT" not in content:
            continue
        pos = 0
        for token in re.finditer(rb"/([A-Za-z0-9#+.\-]+)\s+[0-9.]+\s+Tf", content):
            if token.start() < pos:
                continue
            out.append(_scan(content[pos:token.start()], current))
            current = tables.get(token.group(1), current)
            pos = token.end()
        out.append(_scan(content[pos:], current))
        if sum(len(x) for x in out) >= limit:
            break

    text = "".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text[:limit].strip()


def _scan(chunk: bytes, table: dict[int, str]) -> str:
    if b"Tj" not in chunk and b"TJ" not in chunk and b"'" not in chunk and b'"' not in chunk:
        return ""
    out = []
    for m in SHOW_RE.finditer(chunk):
        seg = m.group(0)
        for piece in re.finditer(rb"<([0-9A-Fa-f\s]+)>|\((?:[^()\\]|\\.)*\)", seg, re.S):
            token = piece.group(0)
            out.append(_piece(token if token.startswith(b"<") else token, table))
    return "".join(out)
