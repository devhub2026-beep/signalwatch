"""把新条目推到人眼前：邮件（SMTP）为主，webhook 留了同一个接口。

凭据只存在本机数据库的 settings 表里，不写进代码、不打印日志。
没配置时所有函数都抛 NotConfigured，界面据此把按钮置灰。
"""
from __future__ import annotations

import smtplib
import sqlite3
import time
from email.message import EmailMessage
from email.utils import formatdate

from .db import guard

SETTING_KEYS = {
    "host": "notify.smtp_host",
    "port": "notify.smtp_port",
    "user": "notify.smtp_user",
    "password": "notify.smtp_password",
    "to": "notify.mail_to",
    "enabled": "notify.mail_enabled",
}

DEFAULTS = {"host": "smtp.qq.com", "port": "465"}


class NotConfigured(RuntimeError):
    """还没填推送凭据。"""


def _get(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    with guard(conn):
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return str(row["value"])


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    with guard(conn), conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def config(conn: sqlite3.Connection) -> dict:
    """读出当前配置；口令不回给界面，只回「有没有」。"""
    return {
        "host": _get(conn, SETTING_KEYS["host"], DEFAULTS["host"]),
        "port": _get(conn, SETTING_KEYS["port"], DEFAULTS["port"]),
        "user": _get(conn, SETTING_KEYS["user"]),
        "to": _get(conn, SETTING_KEYS["to"]),
        "has_password": bool(_get(conn, SETTING_KEYS["password"])),
        "enabled": _get(conn, SETTING_KEYS["enabled"], "0") == "1",
    }


def is_ready(conn: sqlite3.Connection) -> bool:
    cfg = config(conn)
    return bool(cfg["user"] and cfg["has_password"] and cfg["to"])


def save(conn: sqlite3.Connection, *, host: str = "", port: str = "", user: str = "",
         password: str | None = None, to: str = "", enabled: bool | None = None) -> dict:
    """password 传 None 表示「不改」，传空串表示「清掉」。"""
    if host:
        _set(conn, SETTING_KEYS["host"], host.strip())
    if port:
        _set(conn, SETTING_KEYS["port"], str(port).strip())
    if user:
        _set(conn, SETTING_KEYS["user"], user.strip())
    if to:
        _set(conn, SETTING_KEYS["to"], to.strip())
    if password is not None:
        _set(conn, SETTING_KEYS["password"], password.strip())
    if enabled is not None:
        _set(conn, SETTING_KEYS["enabled"], "1" if enabled else "0")
    return config(conn)


def _message(conn: sqlite3.Connection, subject: str, body: str, to: str | None) -> EmailMessage:
    cfg = config(conn)
    sender = cfg["user"]
    receiver = to or cfg["to"]
    if not (sender and receiver):
        raise NotConfigured("还没填发件邮箱或收件邮箱")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = receiver
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(body)
    return msg


def send(conn: sqlite3.Connection, subject: str, body: str, to: str | None = None,
         timeout: float = 20.0) -> str:
    """同步发一封。失败会把原因原样抛出，调用方决定怎么显示。"""
    cfg = config(conn)
    if not is_ready(conn):
        raise NotConfigured("推送没配好：需要发件邮箱、授权码、收件邮箱")
    msg = _message(conn, subject, body, to)
    host, port = cfg["host"], int(cfg["port"] or 465)
    try:
        if port == 465:
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
            try:
                server.starttls()
            except smtplib.SMTPException:
                pass
        try:
            server.login(cfg["user"], _get(conn, SETTING_KEYS["password"]))
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass
    except Exception as exc:
        _mark_result(conn, ok=False, error=f"{type(exc).__name__}: {exc}")
        raise
    _mark_result(conn, ok=True)
    return msg["To"]


def test_mail(conn: sqlite3.Connection, timeout: float = 20.0) -> str:
    return send(conn, "SignalWatch 测试邮件",
                "收到这封就说明推送配好了。\n\n这条消息由 SignalWatch 本机发送。",
                timeout=timeout)


def compose_digest(rows: list[dict], *, limit: int = 20) -> tuple[str, str]:
    """把一批新条目拼成一封信的标题和正文。"""
    count = len(rows)
    subject = f"SignalWatch：{count} 条新信息"
    lines = [f"共 {count} 条，下面是最新的 {min(count, limit)} 条：", ""]
    for r in rows[:limit]:
        when = r.get("when") or ""
        lines.append(f"· [{r.get('source', '')}] {r.get('title', '')}")
        meta = " ".join(x for x in (when, r.get("sec_name") or "") if x)
        if meta:
            lines.append(f"  {meta}")
        if r.get("url"):
            lines.append(f"  {r['url']}")
        lines.append("")
    if count > limit:
        lines.append(f"…… 还有 {count - limit} 条，打开 SignalWatch 看完整时间线。")
    return subject, "\n".join(lines)


# ---- 自动投递 ---------------------------------------------------------------
# 这一段被桌面端和网页服务端**共用**：谁在跑谁发，不会因为关了窗口就断掉。
LAST_SENT_KEY = "notify.last_sent_at"
MIN_GAP_MS = 10 * 60 * 1000


def last_sent(conn: sqlite3.Connection) -> int:
    raw = _get(conn, LAST_SENT_KEY, "0")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _mark_result(conn: sqlite3.Connection, *, ok: bool, error: str = "") -> None:
    _set(conn, "notify.last_at", str(int(time.time() * 1000)))
    _set(conn, "notify.last_ok", "1" if ok else "0")
    _set(conn, "notify.last_error", "" if ok else error[:300])


def last_result(conn: sqlite3.Connection) -> dict:
    raw = _get(conn, "notify.last_at", "0")
    try:
        at = int(raw)
    except (TypeError, ValueError):
        at = 0
    return {
        "at": at,
        "ok": _get(conn, "notify.last_ok", "") == "1",
        "error": _get(conn, "notify.last_error", ""),
    }


def _touch(conn: sqlite3.Connection, at: int) -> None:
    _set(conn, LAST_SENT_KEY, str(int(at)))


def _targets(conn: sqlite3.Connection) -> list[tuple[str, set[str]]]:
    """收件人：全局收件箱 + 每个填了邮箱的用户（带他自己的订阅词，空 = 全收）。"""
    cfg = config(conn)
    out: list[tuple[str, set[str]]] = []
    if cfg["to"]:
        out.append((cfg["to"], set()))
    for row in conn.execute("SELECT username, email FROM users WHERE enabled = 1 AND email != ''"):
        words = {
            r["keyword"].lower()
            for r in conn.execute(
                "SELECT keyword FROM rules WHERE enabled = 1 AND user_id IN (0, (SELECT id FROM users WHERE username = ?))",
                (row["username"],),
            )
        }
        out.append((row["email"], words))
    return out


def _filter(rows: list[dict], words: set[str]) -> list[dict]:
    if not words:
        return rows
    keep = []
    for r in rows:
        text = f"{r.get('title', '')} {r.get('sec_name') or ''} {r.get('summary', '')}".lower()
        if any(w in text for w in words):
            keep.append(r)
    return keep


def pump(conn: sqlite3.Connection, rows: list[dict], *, now: int | None = None,
         force: bool = False) -> list[str]:
    """有新条目就按人发摘要。返回实际发给了谁（空 = 这次没发）。

    规则：开关关着不发；没配好不发；十分钟内最多一批；每个收件人只看自己订阅词命中的条目。
    """
    if not rows:
        return []
    cfg = config(conn)
    if not cfg["enabled"] or not is_ready(conn):
        return []
    at = int(now if now is not None else time.time() * 1000)
    if not force and at - last_sent(conn) < MIN_GAP_MS:
        return []
    sent: list[str] = []
    for address, words in _targets(conn):
        picked = _filter(rows, words)
        if not picked:
            continue
        try:
            subject, body = compose_digest(picked)
            send(conn, subject, body, to=address)
            sent.append(address)
        except Exception:
            # 一个人发不出去不该拖累别人；错误交给上层记审计
            raise
    if sent:
        _touch(conn, at)
    return sent
