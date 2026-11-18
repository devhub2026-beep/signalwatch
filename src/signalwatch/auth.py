"""用户、密码、令牌、角色。

为什么自己写而不是上 JWT 库：这台机器装不上包，而且这东西只有三件事要做——
哈希口令、签一个带过期时间的令牌、按角色挡请求。标准库全能做到，代码还能读。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time

try:
    from psycopg2 import IntegrityError as _pg_integrity
except ImportError:  # SQLite 模式没有 psycopg2，用占位
    _pg_integrity = sqlite3.IntegrityError

# 唯一约束冲突：兼容 sqlite3 与 psycopg2 两种后端
_INTEGRITY_ERRORS = (sqlite3.IntegrityError, _pg_integrity)

PBKDF2_ROUNDS = 120_000
TOKEN_TTL = 7 * 24 * 3600
ROLES = ("admin", "analyst", "auditor", "viewer")
ROLE_RANK = {"viewer": 10, "auditor": 20, "analyst": 50, "admin": 100}


def roles(conn) -> dict[str, dict]:
    """角色表（rank + 权限点）。角色是数据，不是代码里的 if。"""
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT name, rank, perms, builtin FROM roles ORDER BY rank DESC"):
        out[r["name"]] = {
            "rank": int(r["rank"]),
            "perms": {p for p in str(r["perms"]).split(",") if p},
            "builtin": bool(r["builtin"]),
        }
    return out or {name: {"rank": ROLE_RANK[name], "perms": set(), "builtin": True} for name in ROLES}


def perms_of(conn, role: str) -> set[str]:
    table = roles(conn)
    row = table.get(role)
    if row is None:
        return set()
    # 高 rank 的角色自动继承所有低 rank 的权限点
    inherited: set[str] = set(row["perms"])
    for other in table.values():
        if other["rank"] < row["rank"]:
            inherited |= other["perms"]
    return inherited


def can(conn, user: dict, perm: str) -> bool:
    return perm in perms_of(conn, user["role"])


class AuthError(Exception):
    """认证/授权失败。status 是给 HTTP 层用的。"""

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


# ---- 口令 -------------------------------------------------------------------
def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if not password or len(password) < 6:
        raise ValueError("口令至少 6 位")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), PBKDF2_ROUNDS
    )
    return salt, digest.hex()


def verify_password(password: str, salt: str, expected: str) -> bool:
    if not password or not salt or not expected:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), PBKDF2_ROUNDS
    )
    return hmac.compare_digest(digest.hex(), expected)


# ---- 令牌 -------------------------------------------------------------------
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _secret(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM app_meta WHERE key = 'auth_secret'").fetchone()
    if row:
        return str(row[0])
    value = secrets.token_hex(32)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_meta(key, value) VALUES ('auth_secret', ?)", (value,)
        )
    return value


def make_token(conn, username: str, *, ttl: int = TOKEN_TTL, now: int | None = None) -> str:
    now = int(time.time()) if now is None else int(now)
    row = get_user(conn, username)
    tv = int(row["token_version"]) if row is not None else 0
    payload = {"u": username, "exp": now + int(ttl), "tv": tv, "n": secrets.token_hex(4)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_secret(conn).encode("ascii"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def parse_token(conn, token: str, *, now: int | None = None) -> dict | None:
    """返回 payload；签名不对、格式不对、过期，一律 None。"""
    if not token or "." not in token:
        return None
    body, _, sig = token.rpartition(".")
    expect = hmac.new(_secret(conn).encode("ascii"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        payload = json.loads(_unb64(body))
    except Exception:
        return None
    if not isinstance(payload, dict) or "u" not in payload or "exp" not in payload:
        return None
    current = int(time.time()) if now is None else int(now)
    if int(payload["exp"]) < current:
        return None
    return payload


# ---- 用户 -------------------------------------------------------------------
def _row_to_user(row: sqlite3.Row) -> dict:
    keys = row.keys()
    return {
        "id": int(row["id"]),
        "username": row["username"],
        "role": row["role"],
        "enabled": bool(row["enabled"]),
        "created_at": int(row["created_at"]),
        "last_login_at": int(row["last_login_at"] or 0),
        "email": (row["email"] if "email" in keys else "") or "",
        "display_name": (row["display_name"] if "display_name" in keys else "") or "",
        "must_change_password": bool(row["must_change_password"]) if "must_change_password" in keys else False,
    }


def count_users(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


def list_users(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    return [_row_to_user(r) for r in rows]


def get_user(conn, username: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def create_user(conn, username: str, password: str, role: str = "viewer", email: str = "",
                *, must_change_password: bool = False) -> int:
    username = (username or "").strip()
    if not username:
        raise ValueError("用户名不能为空")
    if role not in roles(conn):
        raise ValueError(f"角色只能是 {'/'.join(roles(conn))}")
    salt, digest = hash_password(password)
    with conn:
        try:
            cur = conn.execute(
                "INSERT INTO users(username, pwd_salt, pwd_hash, role, enabled, created_at, email,"
                " must_change_password) VALUES (?,?,?,?,1,?,?,?)",
                (username, salt, digest, role, int(time.time() * 1000), (email or "").strip(),
                 1 if must_change_password else 0),
            )
        except _INTEGRITY_ERRORS:
            raise ValueError(f"用户名已存在：{username}") from None
    return int(cur.lastrowid)


def set_email(conn, username: str, email: str) -> None:
    value = (email or "").strip()
    if value and "@" not in value:
        raise ValueError("邮箱格式不对")
    with conn:
        cur = conn.execute("UPDATE users SET email = ? WHERE username = ?", (value, username))
    if cur.rowcount == 0:
        raise ValueError(f"没有这个用户：{username}")


def set_password(conn, username: str, password: str) -> None:
    salt, digest = hash_password(password)
    with conn:
        cur = conn.execute(
            "UPDATE users SET pwd_salt = ?, pwd_hash = ?, token_version = token_version + 1,"
            " must_change_password = 0 WHERE username = ?",
            (salt, digest, username),
        )
    if cur.rowcount == 0:
        raise ValueError(f"没有这个用户：{username}")


def set_enabled(conn, username: str, enabled: bool) -> None:
    with conn:
        cur = conn.execute("UPDATE users SET enabled = ? WHERE username = ?", (1 if enabled else 0, username))
    if cur.rowcount == 0:
        raise ValueError(f"没有这个用户：{username}")


def set_role(conn, username: str, role: str) -> None:
    if role not in roles(conn):
        raise ValueError(f"角色只能是 {'/'.join(roles(conn))}")
    with conn:
        cur = conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
    if cur.rowcount == 0:
        raise ValueError(f"没有这个用户：{username}")


def delete_user(conn, username: str) -> None:
    row = get_user(conn, username)
    if row is None:
        raise ValueError(f"没有这个用户：{username}")
    if row["role"] == "admin":
        admins = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND enabled = 1").fetchone()[0]
        if int(admins) <= 1:
            raise ValueError("最后一个管理员不能删")
    with conn:
        conn.execute("DELETE FROM users WHERE username = ?", (username,))


def login(conn, username: str, password: str) -> dict:
    row = get_user(conn, username)
    if row is None or not verify_password(password, row["pwd_salt"], row["pwd_hash"]):
        raise AuthError("用户名或口令不对")
    if not int(row["enabled"]):
        raise AuthError("这个账号被停用了", status=403)
    with conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (int(time.time() * 1000), row["id"]))
    return {"token": make_token(conn, username), "user": _row_to_user(row)}


def user_from_token(conn, token: str) -> dict:
    payload = parse_token(conn, token)
    if payload is None:
        raise AuthError("登录过期或令牌无效")
    row = get_user(conn, str(payload["u"]))
    if row is None:
        raise AuthError("账号不存在")
    if not int(row["enabled"]):
        raise AuthError("这个账号被停用了", status=403)
    if int(payload.get("tv", 0)) != int(row["token_version"]):
        raise AuthError("口令改过了，重新登录")
    return _row_to_user(row)


def need(user: dict, *roles: str) -> None:
    if not roles:
        return
    if user["role"] in roles:
        return
    # 用 rank 做继承：admin 天然满足 analyst 的要求
    if ROLE_RANK.get(user["role"], 0) >= min(ROLE_RANK.get(r, 0) for r in roles):
        return
    raise AuthError(f"这个操作要 {'/'.join(roles)} 权限", status=403)


def seed_admin(conn, username: str = "admin", password: str = "admin123") -> bool:
    """第一次启动时建一个管理员，口令走环境变量 SIGNALWATCH_ADMIN_PASSWORD 可覆盖。"""
    if count_users(conn) > 0:
        return False
    password = os.environ.get("SIGNALWATCH_ADMIN_PASSWORD") or password
    create_user(conn, username, password, role="admin",
                must_change_password=(password == "admin123"))
    return True


def enforce_default_password_policy(conn) -> None:
    # 老库也要兜底：admin 仍是默认口令时，下次登录必须先改密。
    row = get_user(conn, "admin")
    if row is None:
        return
    if verify_password("admin123", row["pwd_salt"], row["pwd_hash"]):
        with conn:
            conn.execute("UPDATE users SET must_change_password = 1 WHERE id = ?", (row["id"],))

# --- 自助注册与找回口令 ------------------------------------------------------
#
# 为什么要做：手机上敲一个 16 位随机口令，十次有八次敲错，敲错了还要等限速。
# 让人自己注册、自己收验证码改口令，比"发一个口令给他"可靠。

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
RESET_TTL = 600          # 验证码 10 分钟有效
RESET_MAX_TRIES = 5      # 一条验证码最多试 5 次


def _now_ms() -> int:
    return int(time.time() * 1000)


def register(conn, username: str, password: str, email: str = "", role: str = "viewer") -> dict:
    """自助注册。默认给 viewer，管理员事后可以提权，避免注册即拿到删数据的权限。"""
    username = (username or "").strip()
    if not USERNAME_RE.match(username):
        raise AuthError("用户名要 3–32 位，只能用字母、数字、下划线、点、短横线")
    if len(password or "") < 6:
        raise AuthError("口令至少 6 位")
    email = (email or "").strip()
    if email and "@" not in email:
        raise AuthError("邮箱格式不对")
    if get_user(conn, username) is not None:
        raise AuthError("用户名已被占用", status=409)
    try:
        create_user(conn, username, password, role=role, email=email)
    except ValueError as exc:
        raise AuthError(str(exc), status=409) from None
    return login(conn, username, password)


def find_user(conn, who: str):
    """who 可以是用户名，也可以是邮箱。邮箱必须唯一命中。"""
    who = (who or "").strip()
    if not who:
        return None
    row = get_user(conn, who)
    if row is not None:
        return row
    if "@" in who:
        rows = conn.execute("SELECT * FROM users WHERE lower(email) = ?", (who.lower(),)).fetchall()
        if len(rows) == 1:
            return rows[0]
    return None


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def make_reset(conn, who: str):
    """生成 6 位验证码。用户不存在或没填邮箱时返回 None（调用方不要泄露差在哪）。"""
    row = find_user(conn, who)
    if row is None or not int(row["enabled"]):
        return None
    code = f"{secrets.randbelow(10 ** 6):06d}"
    now = _now_ms()
    with conn:
        conn.execute("UPDATE password_resets SET used_at = ? WHERE username = ? AND used_at IS NULL",
                     (now, row["username"]))
        conn.execute(
            "INSERT INTO password_resets(username, code_hash, expires_at, tries, created_at)"
            " VALUES (?,?,?,0,?)",
            (row["username"], _hash_code(code), now + RESET_TTL * 1000, now),
        )
    return str(row["username"]), code


def reset_password(conn, who: str, code: str, new_password: str) -> str:
    """校验验证码并改口令。改完把 token_version 加一，旧令牌立刻作废。"""
    if len(new_password or "") < 6:
        raise AuthError("新口令至少 6 位")
    row = find_user(conn, who)
    if row is None:
        raise AuthError("验证码不对或已过期")
    now = _now_ms()
    rec = conn.execute(
        "SELECT * FROM password_resets WHERE username = ? AND used_at IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (row["username"],),
    ).fetchone()
    if rec is None or int(rec["expires_at"]) < now:
        raise AuthError("验证码不对或已过期")
    if int(rec["tries"]) >= RESET_MAX_TRIES:
        raise AuthError("这条验证码试错太多次了，重新获取一条", status=429)
    if not hmac.compare_digest(_hash_code((code or "").strip()), rec["code_hash"]):
        with conn:
            conn.execute("UPDATE password_resets SET tries = tries + 1 WHERE id = ?", (rec["id"],))
        raise AuthError("验证码不对或已过期")
    salt, digest = hash_password(new_password)
    with conn:
        conn.execute("UPDATE users SET pwd_salt = ?, pwd_hash = ?, token_version = token_version + 1,"
                     " must_change_password = 0 WHERE username = ?", (salt, digest, row["username"]))
        conn.execute("UPDATE password_resets SET used_at = ? WHERE id = ?", (now, rec["id"]))
    return str(row["username"])
