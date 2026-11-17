"""生成 app.ico（不依赖 Pillow：直接写 PNG，再包进 ICO）。

图案：深色圆角底 + 三道同心的"雷达"弧 + 中间一个绿点 = 盯着 + 有信号。
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

BG = (16, 20, 24, 255)
RING = (75, 147, 255, 255)
DOT = (63, 185, 80, 255)
OUT = Path(__file__).resolve().parents[1] / "assets" / "app.ico"


def _png(w: int, h: int, pixels: bytearray) -> bytes:
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)  # filter: none
        raw += pixels[y * stride : (y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def draw(size: int) -> bytes:
    px = bytearray(size * size * 4)
    cx = cy = (size - 1) / 2.0
    radius = size * 0.32
    corner = size * 0.22

    def put(x: int, y: int, color) -> None:
        i = (y * size + x) * 4
        px[i : i + 4] = bytes(color)

    for y in range(size):
        for x in range(size):
            # 圆角方块：到内缩矩形的距离判断
            dx = max(abs(x - cx) - (size / 2 - corner), 0)
            dy = max(abs(y - cy) - (size / 2 - corner), 0)
            if (dx * dx + dy * dy) > corner * corner * 0.55:
                continue
            put(x, y, BG)
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            for frac in (0.50, 0.75, 1.00):
                r = radius * frac
                if abs(d - r) <= max(1.0, size * 0.028):
                    put(x, y, RING)
    # 中间的点
    for y in range(size):
        for x in range(size):
            if (x - cx) ** 2 + (y - cy) ** 2 <= (size * 0.10) ** 2:
                put(x, y, DOT)
    return _png(size, size, px)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    blobs = [draw(s) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, blob in zip(sizes, blobs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    OUT.write_bytes(header + entries + b"".join(blobs))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(sizes)} sizes)")


if __name__ == "__main__":
    main()

