"""HTTP 层：本地 Web 应用 + JSON API。

默认只绑 127.0.0.1——这是"本地不暴露"的意思：
不监听 0.0.0.0、不建隧道。公开时间线可读，正文与写操作按角色鉴权。
要给自己手机在同一 Wi-Fi 下看，显式传 --host 0.0.0.0，并配置 HTTPS。
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from pydantic import BaseModel, Field
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from . import __version__, admin, audit, auth, board, cache, dedupe, enrich, ingest, maintenance, notify, report, taxonomy, xlsx
from .config import SETTINGS, Settings
from .db import db_stats, init_db
from .scheduler import Crawler
from . import wakeup

WEB_DIR = Path(__file__).resolve().parent / "web"
TOKEN_COOKIE_TTL = 7 * 24 * 3600


def _ts(ms: int | None) -> str:
    """毫秒时间戳 -> 本地 'YYYY-MM-DD HH:MM'，给导出用。"""
    if not ms:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ms) / 1000))


class LoginIn(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class UserIn(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=6)
    role: str = "viewer"
    email: str = ""


class UserPatch(BaseModel):
    password: str | None = None
    role: str | None = None
    enabled: bool | None = None
    email: str | None = None


class SourcePatch(BaseModel):
    interval_seconds: int | None = Field(default=None, ge=60, le=86400)
    enabled: bool | None = None


class ItemEdit(BaseModel):
    title: str | None = None
    summary: str | None = None
    category: str | None = None


class ItemsStateIn(BaseModel):
    ids: list[int] = Field(max_length=500)
    state: str


class ItemsDeleteIn(BaseModel):
    ids: list[int] = Field(max_length=500)


class CategoryIn(BaseModel):
    name: str


class CategoryPatch(BaseModel):
    enabled: bool | None = None
    name: str | None = None


class JobIn(BaseModel):
    kind: str = "all"
    target: str = ""
    priority: int = 0


class InternalFinish(BaseModel):
    ok: bool = True
    result: str = ""
    error: str = ""


class InternalCrawl(BaseModel):
    source: str = "all"


class RuleIn(BaseModel):
    keyword: str
    category: str = ""
    source: str = ""


class RulePatch(BaseModel):
    enabled: bool


class SettingsIn(BaseModel):
    values: dict[str, str]


class NotifyIn(BaseModel):
    host: str = ""
    port: str = ""
    user: str = ""
    password: str | None = None
    to: str = ""
    enabled: bool | None = None
    send_test: bool = False


class PruneIn(BaseModel):
    days: int = Field(default=90, ge=1, le=3650)


class PasswordIn(BaseModel):
    old: str = ""
    new: str = Field(min_length=6)


# 测试期间被触达过的路由记录在这里（process 级）。tools/route_coverage.py 用它算
# 「测试到底打到了多少条接口」——比行覆盖率便宜，也更贴近「接口有没有人验过」这个问题。
HIT_ROUTES: set[str] = set()


def _fill_summaries(app: FastAPI) -> int:
    """没手写 summary 的路由，用函数名转成人话，保证 openapi.json 里不留空标题。"""
    filled = 0
    for route in app.routes:
        if not hasattr(route, "methods"):
            continue
        if getattr(route, "summary", None):
            continue
        name = getattr(route, "name", "") or ""
        words = [w for w in name.replace("-", "_").split("_") if w]
        if not words:
            continue
        route.summary = " ".join(w if w.isupper() else w.capitalize() for w in words)[:80]
        filled += 1
    return filled


def _scheduler_on() -> bool:
    """SIGNALWATCH_SCHEDULER=0 时 API 只当 API：抓取交给独立 worker 进程。"""
    return os.environ.get("SIGNALWATCH_SCHEDULER", "1").strip() not in ("0", "false", "no")


def create_app(settings: Settings | None = None, *, autostart: bool = True) -> FastAPI:
    settings = settings or SETTINGS
    crawler = Crawler(settings)

    app = FastAPI(title="signalwatch", version=__version__)
    app.state.crawler = crawler
    app.state.settings = settings
    app.state.crawl_lock = threading.Lock()
    app.state.cache = cache.make_cache(maxsize=512)
    app.state.metrics = {
        "started": time.time(),
        "total": 0,
        "by_status": {},
        "by_path": {},
        "lat": deque(maxlen=5000),
    }
    app.state.login_fails = {}
    SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        "Content-Security-Policy": (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
            "connect-src 'self'; frame-ancestors 'none'"
        ),
    }

    @app.middleware("http")
    async def _observe(request: Request, call_next):
        """给每个请求发一个 request id、记耗时、补安全响应头。"""
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        m = app.state.metrics
        path = request.url.path
        counted = not path.startswith("/api/events")
        m["total"] += 1
        if counted:
            m["by_path"][path] = m["by_path"].get(path, 0) + 1
        start = time.perf_counter()
        response = await call_next(request)
        cost = (time.perf_counter() - start) * 1000.0
        code = str(response.status_code)
        m["by_status"][code] = m["by_status"].get(code, 0) + 1
        if counted:
            m["lat"].append(cost)
        # 业务路由的模板在 scope["route"].path；/docs、/redoc 这类 Starlette 内置路由
        # 不带 route，用原始路径兜底（它们本身就是固定路径，不会把 id 带进来）。
        route = request.scope.get("route")
        HIT_ROUTES.add(f"{request.method} {getattr(route, 'path', None) or path}")
        request.state.request_id = rid
        response.headers["X-Request-ID"] = rid
        response.headers["X-Response-Time"] = f"{cost:.1f}ms"
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        return response

    @app.get("/api/metrics")
    def api_metrics() -> dict:
        """进程内指标：请求数、耗时分布、状态码、最热的路径。"""
        m = app.state.metrics
        lat = sorted(m["lat"])
        def pct(q: float) -> float:
            if not lat:
                return 0.0
            idx = min(len(lat) - 1, int(len(lat) * q / 100))
            return round(lat[idx], 1)
        uptime = max(time.time() - m["started"], 1e-9)
        top = sorted(m["by_path"].items(), key=lambda kv: -kv[1])[:8]
        return {
            "uptime_seconds": round(uptime, 1),
            "requests": m["total"],
            "rps": round(m["total"] / uptime, 2),
            "by_status": dict(m["by_status"]),
            "cache": app.state.cache.stats(),
            "latency_ms": {
                "p50": pct(50), "p95": pct(95), "p99": pct(99),
                "max": round(lat[-1], 1) if lat else 0.0, "samples": len(lat),
            },
            "top_paths": [{"path": p, "count": c} for p, c in top],
        }

    @app.exception_handler(HTTPException)
    async def _on_http_error(request: Request, exc: HTTPException):
        """把 FastAPI 的 detail 统一成 {"error": 人话}，同时保留 detail 兼容老前端。"""
        return JSONResponse(status_code=exc.status_code, content={
            "error": exc.detail,
            "detail": exc.detail,
            "path": request.url.path,
        })

    @app.exception_handler(Exception)
    async def _on_error(request: Request, exc: Exception):
        """兜底：任何没接住的异常都返回结构化 JSON，不把 500 堆栈甩给页面。"""
        if isinstance(exc, HTTPException):
            return await _on_http_error(request, exc)
        audit.log(crawler.conn, "system", "error.unhandled", request.url.path,
                  f"{type(exc).__name__}: {exc}")
        return JSONResponse(status_code=500, content={
            "error": "服务内部出错了，已经记进审计日志",
            "detail": "服务内部出错了，已经记进审计日志",
            "path": request.url.path,
        })

    @app.on_event("startup")
    def _startup() -> None:
        init_db(crawler.conn)
        if auth.seed_admin(crawler.conn):
            audit.log(crawler.conn, "system", "user.seed", "admin", "首次启动，建了默认管理员")
        auth.enforce_default_password_policy(crawler.conn)
        if autostart and _scheduler_on():
            crawler.start()
        if autostart:
            _start_mail_loop()
            app.state.autobackup = maintenance.AutoBackup(settings)
            app.state.autobackup.start()

    def _start_mail_loop() -> None:
        """服务端也要发信：关掉桌面窗口，订阅摘要不能跟着停。"""
        stop = threading.Event()
        app.state.mail_stop = stop

        def loop() -> None:
            while not stop.wait(60):
                try:
                    since = notify.last_sent(crawler.conn)
                    rows = ingest.query_items(crawler.conn, limit=30, since_ms=since)
                    if not rows:
                        continue
                    payload = [{"source": r["source"], "title": r["title"], "url": r["url"],
                                "when": "", "sec_name": r["sec_name"], "summary": r["summary"]}
                               for r in rows]
                    sent = notify.pump(crawler.conn, payload)
                    if sent:
                        audit.log(crawler.conn, "system", "notify.sent", "mail", "、".join(sent))
                except Exception as exc:
                    audit.log(crawler.conn, "system", "notify.fail", "mail",
                              f"{type(exc).__name__}: {exc}")

        threading.Thread(target=loop, name="mail-loop", daemon=True).start()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        stop = getattr(app.state, "mail_stop", None)
        if stop is not None:
            stop.set()
        auto = getattr(app.state, "autobackup", None)
        if auto is not None:
            auto.stop()
        crawler.stop()
        crawler.conn.close()

    # ---- 内部接口（给 worker 用：让 worker 不碰库）----------------------------
    internal_token = os.environ.get("SIGNALWATCH_INTERNAL_TOKEN", "").strip()

    def _internal(request: Request) -> None:
        given = request.headers.get("x-internal-token", "")
        if not hmac.compare_digest(given, internal_token):
            raise HTTPException(status_code=403, detail="内部接口需要 X-Internal-Token")

    if internal_token:
        @app.get("/api/internal/sources")
        def internal_sources(request: Request) -> dict:
            _internal(request)
            snap = crawler.snapshot()
            return {"sources": [s.get("name") for s in snap.get("sources", []) if s.get("name")]}

        @app.get("/api/internal/jobs/claim")
        def internal_claim(request: Request) -> dict:
            _internal(request)
            return {"job": admin.claim_job(crawler.conn)}

        @app.post("/api/internal/jobs/{job_id}/finish")
        def internal_finish(job_id: int, body: "InternalFinish", request: Request) -> dict:
            _internal(request)
            try:
                admin.finish_job(crawler.conn, job_id, ok=body.ok,
                                 result=body.result, error=body.error)
            except admin.JobStateError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            return {"ok": True}

        @app.post("/api/internal/crawl")
        def internal_crawl(body: "InternalCrawl", request: Request) -> dict:
            _internal(request)
            names = ([s.get("name") for s in crawler.snapshot().get("sources", [])]
                     if body.source == "all" else [body.source])
            out = {"ok": True, "fetched": 0, "inserted": 0, "parts": []}
            for name in [n for n in names if n]:
                try:
                    res = crawler.run_source(name, force=True)
                except KeyError as exc:
                    raise HTTPException(status_code=404, detail=str(exc)) from None
                if res is None:
                    continue
                out["fetched"] += int(res.fetched or 0)
                out["inserted"] += int(res.inserted or 0)
                out["ok"] = out["ok"] and bool(res.ok)
                out["parts"].append(f"{name}:{'ok' if res.ok else 'fail'}")
            if out["inserted"]:
                dedupe.mark_duplicates(crawler.conn, limit=200)
                enrich.enrich(crawler.conn, limit=2, only=("cninfo",))
            taxonomy.backfill(crawler.conn)
            return out

    # ---- 页面 ----------------------------------------------------------------
    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html", media_type="text/html; charset=utf-8")

    @app.get("/index.css")
    def index_css() -> FileResponse:
        return FileResponse(WEB_DIR / "index.css", media_type="text/css; charset=utf-8")

    @app.get("/index.js")
    def index_js() -> FileResponse:
        return FileResponse(WEB_DIR / "index.js", media_type="application/javascript; charset=utf-8")

    @app.get("/admin.css")
    def admin_css() -> FileResponse:
        return FileResponse(WEB_DIR / "admin.css", media_type="text/css; charset=utf-8")

    @app.get("/admin.js")
    def admin_js() -> FileResponse:
        return FileResponse(WEB_DIR / "admin.js", media_type="application/javascript; charset=utf-8")

    @app.get("/manifest.webmanifest")
    def manifest() -> FileResponse:
        return FileResponse(WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js")
    def service_worker() -> FileResponse:
        return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript")

    @app.get("/icon-192.png")
    def icon192() -> FileResponse:
        return FileResponse(WEB_DIR / "icon-192.png", media_type="image/png")

    @app.get("/icon-512.png")
    def icon512() -> FileResponse:
        return FileResponse(WEB_DIR / "icon-512.png", media_type="image/png")

    @app.get("/apple-touch-icon.png")
    def apple_icon() -> FileResponse:
        return FileResponse(WEB_DIR / "apple-touch-icon.png", media_type="image/png")

    # ---- API -----------------------------------------------------------------
    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "version": __version__, "ts": int(time.time() * 1000)}

    @app.get("/api/items/{item_id}/text")
    def item_text(item_id: int, request: Request) -> dict:
        _need(request, "viewer")
        row = crawler.conn.execute(
            "SELECT title, body_text, body_bytes, url FROM items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="没有这条")
        text = row["body_text"] or ""
        return {
            "id": item_id,
            "title": row["title"],
            "chars": len(text),
            "bytes": row["body_bytes"] or 0,
            "url": row["url"],
            "text": text[:8000],
        }

    @app.get("/api/items")
    def items(
        request: Request,
        limit: int = Query(50, ge=1, le=500),
        source: str | None = None,
        kind: str | None = None,
        q: str | None = None,
        sec_code: str | None = None,
        since: int | None = None,
    ) -> dict:
        cache_key = (f"items:{limit}:{source}:{kind}:{q}:{sec_code}:{since}:"
                     f"{_uid(request)}")
        hit = app.state.cache.get(cache_key)
        if hit is not cache.MISS:
            return hit
        rows = ingest.query_items(
            crawler.conn, limit=limit, source=source, kind=kind, q=q,
            sec_code=sec_code, since_ms=since,
        )
        uid = _uid(request)
        marks = {}
        if rows:
            ids = [int(r["id"]) for r in rows]
            marks = {
                int(m["item_id"]): m
                for m in crawler.conn.execute(
                    "SELECT item_id, read_at, starred_at FROM marks"
                    " WHERE user_id = ? AND item_id IN (" + ",".join("?" * len(ids)) + ")",
                    (uid, *ids),
                )
            }
        for row in rows:
            text = row.pop("body_text", "") or ""
            row["body_chars"] = len(text)
            mark = marks.get(int(row["id"]))
            row["read"] = bool(mark and mark["read_at"])
            row["starred"] = bool(mark and mark["starred_at"])
        out = {"count": len(rows), "items": rows, "user_id": uid}
        app.state.cache.set(cache_key, out)
        return out

    @app.post("/api/items/{item_id}/read")
    def item_read(item_id: int, request: Request, read: bool = True) -> dict:
        _need(request, "viewer")
        if crawler.conn.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="没有这条")
        board.mark_read(crawler.conn, item_id, read, user_id=_uid(request))
        return {"ok": True, "read": bool(read)}

    @app.post("/api/items/{item_id}/star")
    def item_star(item_id: int, request: Request) -> dict:
        _need(request, "viewer")
        if crawler.conn.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="没有这条")
        starred = board.toggle_star(crawler.conn, item_id, user_id=_uid(request))
        return {"ok": True, "starred": starred}

    def _cached(key: str, build):
        """同一秒内的重复读直接吃缓存（TTL 见 `app.state.cache`）。"""
        return app.state.cache.get_or_build(key, build)

    @app.get("/api/stats")
    def stats() -> dict:
        return _cached("stats", lambda: db_stats(crawler.conn))

    @app.get("/api/sources")
    def sources() -> dict:
        return _cached("sources", crawler.snapshot)

    @app.post("/api/crawl")
    def crawl(request: Request, source: str | None = None) -> JSONResponse:
        """手动触发一轮。放到后台线程里跑，HTTP 立刻返回，避免前端等几十秒。"""
        _need(request, "analyst", "admin")
        if not app.state.crawl_lock.acquire(blocking=False):
            return JSONResponse({"started": False, "reason": "上一轮还没跑完"}, status_code=409)
        try:
            def _run() -> None:
                try:
                    crawler.run_source(source, force=True)
                except KeyError as exc:
                    pass
                except Exception:
                    pass
                finally:
                    app.state.crawl_lock.release()

            if source and source not in crawler.sources:
                app.state.crawl_lock.release()
                raise HTTPException(status_code=404, detail=f"没有这个源：{source}")
            threading.Thread(target=_run, name="manual-crawl", daemon=True).start()
            return JSONResponse({"started": True, "source": source or "all-due"})
        except HTTPException:
            raise
        except Exception:
            app.state.crawl_lock.release()
            raise

    # ---- 企业化：用户 / 审计 / 报表 -----------------------------------------

    def _token(request: Request) -> str:
        header = request.headers.get("authorization") or ""
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return request.cookies.get("sw_token", "")

    def _check_csrf(request: Request) -> None:
        """Cookie 认证的写请求必须带双提交 CSRF 头；Bearer 客户端不受浏览器 CSRF 影响。"""
        if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
            return
        authorization = request.headers.get("authorization") or ""
        if authorization.lower().startswith("bearer "):
            return
        expected = request.cookies.get("sw_csrf", "")
        supplied = request.headers.get("x-csrf-token", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            raise HTTPException(status_code=403, detail="缺少或错误的 CSRF 令牌")

    def _user(request: Request) -> dict:
        try:
            user = auth.user_from_token(crawler.conn, _token(request))
        except auth.AuthError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc))
        allowed_during_password_change = ("/api/auth/me", "/api/auth/password", "/api/auth/logout")
        if user.get("must_change_password") and request.url.path not in allowed_during_password_change:
            raise HTTPException(status_code=403, detail="首次登录必须先修改默认口令")
        _check_csrf(request)
        return user

    def _uid(request: Request) -> int:
        """登录了就用 user_id，没登录按 0（匿名）。标记按人存，互不影响。"""
        try:
            return int(auth.user_from_token(crawler.conn, _token(request))["id"])
        except Exception:
            return 0

    def _need(request: Request, *roles: str) -> dict:
        user = _user(request)
        try:
            auth.need(user, *roles)
        except auth.AuthError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc))
        return user

    @app.get("/admin")
    def admin_page() -> FileResponse:
        # Vue 管理端（构建产物在 web/admin-vue/），旧手写版保留在 web/admin.html 作回滚备份
        return FileResponse(WEB_DIR / "admin-vue" / "index.html", media_type="text/html; charset=utf-8")

    @app.get("/assets/{path:path}")
    def vue_assets(path: str) -> FileResponse:
        """服务 Vue 管理端构建出的 /assets/* 静态资源，带路径遍历防护。"""
        root = (WEB_DIR / "admin-vue" / "assets").resolve()
        target = (root / path).resolve()
        if not str(target).startswith(str(root)):
            raise HTTPException(status_code=404)
        return FileResponse(target)

    MAX_LOGIN_FAILS = 5
    LOGIN_WINDOW = 300.0

    def _login_fails(ip: str) -> deque:
        bucket = app.state.login_fails.get(ip)
        if bucket is None:
            bucket = deque(maxlen=64)
            app.state.login_fails[ip] = bucket
        now = time.time()
        while bucket and now - bucket[0] > LOGIN_WINDOW:
            bucket.popleft()
        return bucket

    @app.post("/api/auth/login")
    def auth_login(body: LoginIn, request: Request) -> JSONResponse:
        ip = request.client.host if request.client else "unknown"
        if len(_login_fails(ip)) >= MAX_LOGIN_FAILS:
            raise HTTPException(status_code=429, detail="失败次数太多，请 5 分钟后再试")
        try:
            result = auth.login(crawler.conn, body.username, body.password)
        except auth.AuthError as exc:
            _login_fails(ip).append(time.time())
            audit.log(crawler.conn, body.username, "auth.login_failed", "", str(exc))
            raise HTTPException(status_code=exc.status, detail=str(exc))
        _login_fails(ip).clear()
        audit.log(crawler.conn, body.username, "auth.login", "", "登录成功")
        csrf = secrets.token_urlsafe(32)
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp = JSONResponse({"token": result["token"], "user": result["user"], "csrf_token": csrf})
        resp.set_cookie("sw_token", result["token"], httponly=True, samesite="lax",
                        secure=secure, max_age=TOKEN_COOKIE_TTL, path="/")
        resp.set_cookie("sw_csrf", csrf, httponly=False, samesite="lax",
                        secure=secure, max_age=TOKEN_COOKIE_TTL, path="/")
        return resp

    @app.post("/api/auth/logout")
    def auth_logout(request: Request) -> dict:
        _check_csrf(request)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie("sw_token", path="/")
        resp.delete_cookie("sw_csrf", path="/")
        return resp

    @app.get("/api/auth/me")
    def auth_me(request: Request) -> dict:
        return {"user": _user(request)}

    @app.post("/api/auth/password")
    def auth_password(body: PasswordIn, request: Request) -> dict:
        user = _user(request)
        try:
            auth.login(crawler.conn, user["username"], body.old)
        except auth.AuthError:
            raise HTTPException(status_code=400, detail="原口令不对")
        auth.set_password(crawler.conn, user["username"], body.new)
        audit.log(crawler.conn, user["username"], "auth.password", user["username"], "改口令")
        return {"ok": True, "relogin": True}

    @app.get("/api/users")
    def users_list(request: Request) -> dict:
        _need(request, "admin")
        return {"users": auth.list_users(crawler.conn)}

    @app.post("/api/users")
    def users_create(body: UserIn, request: Request) -> JSONResponse:
        actor = _need(request, "admin")
        try:
            uid = auth.create_user(crawler.conn, body.username, body.password, body.role, body.email)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        audit.log(crawler.conn, actor["username"], "user.create", body.username,
                  f"角色 {body.role}" + (f"，邮箱 {body.email}" if body.email else ""))
        return JSONResponse({"id": uid, "username": body.username, "role": body.role}, status_code=201)

    @app.patch("/api/users/{username}")
    def users_patch(username: str, body: UserPatch, request: Request) -> dict:
        actor = _need(request, "admin")
        changed: list[str] = []
        try:
            if body.password:
                auth.set_password(crawler.conn, username, body.password)
                changed.append("改了口令")
            if body.role:
                auth.set_role(crawler.conn, username, body.role)
                changed.append(f"角色->{body.role}")
            if body.enabled is not None:
                auth.set_enabled(crawler.conn, username, body.enabled)
                changed.append("停用" if not body.enabled else "启用")
            if body.email is not None:
                auth.set_email(crawler.conn, username, body.email)
                changed.append(f"邮箱->{body.email or '清空'}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        audit.log(crawler.conn, actor["username"], "user.update", username, "；".join(changed) or "无改动")
        return {"ok": True, "changed": changed}

    @app.delete("/api/users/{username}")
    def users_delete(username: str, request: Request) -> dict:
        actor = _need(request, "admin")
        try:
            auth.delete_user(crawler.conn, username)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        audit.log(crawler.conn, actor["username"], "user.delete", username, "")
        return {"ok": True}

    @app.get("/api/audit")
    def audit_list(request: Request, limit: int = Query(200, ge=1, le=1000),
                   username: str | None = None, action: str | None = None) -> dict:
        _need(request, "admin")
        return {"count": audit.count(crawler.conn),
                "rows": audit.query(crawler.conn, limit=limit, username=username, action=action)}

    @app.get("/api/report")
    def report_overview(request: Request, days: int = Query(7, ge=1, le=90)) -> dict:
        _need(request)
        return _cached(f"report:{days}", lambda: report.overview(crawler.conn, days=days))

    @app.patch("/api/sources/{name}")
    def sources_patch(name: str, body: SourcePatch, request: Request) -> dict:
        actor = _need(request, "analyst", "admin")
        crawler.apply_patch(name, interval_seconds=body.interval_seconds, enabled=body.enabled)
        audit.log(crawler.conn, actor["username"], "source.update", name,
                  f"间隔={body.interval_seconds} 启用={body.enabled}")
        return {"ok": True, **crawler.snapshot()}

    # ---- 企业化：条目 / 分类 / 任务 / 设置 -----------------------------------


    @app.get("/api/admin/items")
    def admin_items(request: Request, q: str = "", source: str = "", state: str = "",
                    category: str = "", page: int = Query(1, ge=1),
                    size: int = Query(50, ge=1, le=200),
                    before_pub: int = Query(0, ge=0), before_id: int = Query(0, ge=0)) -> dict:
        """列表。深页用 `before_pub` + `before_id`（键集游标），比 page=3000 快 4 个数量级。"""
        _need(request)
        cursor = (before_pub, before_id) if before_pub and before_id else None
        rows, total = admin.item_page(crawler.conn, q=q, source=source, state=state,
                                      category=category, offset=(page - 1) * size, limit=size,
                                      before=cursor)
        body = {"items": rows, "total": total, "page": page, "size": size,
                "pages": max(1, (total + size - 1) // size)}
        if rows:
            body["next_cursor"] = {"before_pub": rows[-1]["published_at"],
                                   "before_id": rows[-1]["id"]}
        return body

    @app.patch("/api/admin/items/{item_id}")
    def admin_item_edit(item_id: int, body: ItemEdit, request: Request) -> dict:
        actor = _need(request, "analyst", "admin")
        try:
            row = admin.update_item(crawler.conn, item_id, title=body.title,
                                    summary=body.summary, category=body.category)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        audit.log(crawler.conn, actor["username"], "item.edit", str(item_id), row.get("title", "")[:40])
        return {"ok": True, "item": row}

    @app.post("/api/admin/items/state")
    def admin_items_state(body: ItemsStateIn, request: Request) -> dict:
        actor = _need(request, "analyst", "admin")
        try:
            n = admin.set_items_state(crawler.conn, body.ids, body.state)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        audit.log(crawler.conn, actor["username"], f"item.{body.state}", "", f"{n} 条")
        return {"ok": True, "changed": n}

    @app.post("/api/admin/items/delete")
    def admin_items_delete(body: ItemsDeleteIn, request: Request) -> dict:
        actor = _need(request, "admin")
        n = admin.delete_items(crawler.conn, body.ids)
        audit.log(crawler.conn, actor["username"], "item.delete", "", f"{n} 条")
        return {"ok": True, "deleted": n}

    @app.get("/api/admin/items.csv")
    def admin_items_csv(request: Request, q: str = "", source: str = "") -> Response:
        _need(request)
        text = admin.export_items_csv(crawler.conn, q=q, source=source)
        return Response(content=text, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="items.csv"'})

    @app.post("/api/admin/items/{item_id}/pin")
    def admin_item_pin(item_id: int, request: Request) -> dict:
        actor = _need(request, "analyst", "admin")
        pinned = admin.toggle_pin(crawler.conn, item_id)
        audit.log(crawler.conn, actor["username"], "item.pin", str(item_id),
                  "置顶" if pinned else "取消置顶")
        return {"ok": True, "pinned": pinned}

    @app.get("/api/admin/items/{item_id}/history")
    def admin_item_history(item_id: int, request: Request) -> dict:
        _need(request)
        try:
            return {"history": admin.item_history(crawler.conn, item_id),
                    "diff": admin.item_diff(crawler.conn, item_id)}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.get("/api/admin/timeline")
    def admin_timeline(request: Request, code: str = "", limit: int = Query(50, ge=1, le=200)) -> dict:
        _need(request)
        return {"code": code, "items": admin.company_timeline(crawler.conn, code, limit)}

    @app.get("/api/admin/roles")
    def admin_roles(request: Request) -> dict:
        _need(request)
        table = auth.roles(crawler.conn)
        return {"roles": [{"name": name, "rank": v["rank"], "builtin": v["builtin"],
                           "perms": sorted(auth.perms_of(crawler.conn, name))}
                          for name, v in sorted(table.items(), key=lambda kv: -kv[1]["rank"])]}

    @app.get("/api/admin/rules")
    def list_rules(request: Request) -> dict:
        user = _user(request)
        rows = board.list_rules(crawler.conn,
                                None if user["role"] == "admin" else int(user["id"]))
        return {"rules": rows}

    @app.post("/api/admin/rules")
    def create_rule(payload: RuleIn, request: Request) -> dict:
        user = _need(request, "analyst")
        try:
            ok = board.add_rule(crawler.conn, payload.keyword, payload.category, payload.source,
                                int(user["id"]))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        audit.log(crawler.conn, user["username"], "rule.add", payload.keyword)
        return {"ok": ok, "duplicated": not ok}

    @app.patch("/api/admin/rules/{rule_id}")
    def patch_rule(rule_id: int, payload: RulePatch, request: Request) -> dict:
        user = _need(request, "analyst")
        board.set_rule_enabled(crawler.conn, rule_id, payload.enabled)
        audit.log(crawler.conn, user["username"],
                  "rule.enable" if payload.enabled else "rule.disable", str(rule_id))
        return {"ok": True}

    @app.delete("/api/admin/rules/{rule_id}")
    def remove_rule(rule_id: int, request: Request) -> dict:
        user = _need(request, "analyst")
        board.delete_rule(crawler.conn, rule_id)
        audit.log(crawler.conn, user["username"], "rule.delete", str(rule_id))
        return {"ok": True}

    @app.get("/api/admin/categories")
    def admin_categories(request: Request) -> dict:
        _need(request)
        return {"categories": admin.list_categories(crawler.conn)}

    @app.post("/api/admin/categories")
    def admin_categories_add(body: CategoryIn, request: Request) -> JSONResponse:
        actor = _need(request, "admin")
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="分类名不能为空")
        try:
            cid = admin.add_category(crawler.conn, name)
        except Exception:
            raise HTTPException(status_code=400, detail=f"分类「{name}」已经有了")
        audit.log(crawler.conn, actor["username"], "category.add", name, "")
        return JSONResponse({"id": cid, "name": name}, status_code=201)

    @app.patch("/api/admin/categories/{cid}")
    def admin_categories_patch(cid: int, body: CategoryPatch, request: Request) -> dict:
        actor = _need(request, "admin")
        if body.enabled is not None:
            admin.set_category_enabled(crawler.conn, cid, body.enabled)
        if body.name:
            admin.rename_category(crawler.conn, cid, body.name)
        audit.log(crawler.conn, actor["username"], "category.update", str(cid), body.model_dump_json())
        return {"ok": True, "categories": admin.list_categories(crawler.conn)}

    @app.get("/api/admin/jobs")
    def admin_jobs(request: Request) -> dict:
        _need(request)
        return {"jobs": admin.job_list(crawler.conn), "counts": admin.job_counts(crawler.conn)}

    @app.post("/api/admin/jobs")
    def admin_jobs_add(body: JobIn, request: Request) -> JSONResponse:
        actor = _need(request, "analyst", "admin")
        jid = admin.enqueue_job(crawler.conn, body.kind, body.target,
                                by=actor["username"], priority=body.priority)
        audit.log(crawler.conn, actor["username"], "job.enqueue", f"#{jid}", f"{body.kind} {body.target}")
        wakeup.notify(jid)          # 配了 Redis 就立刻叫醒 worker，没配就靠 worker 轮询
        return JSONResponse({"id": jid}, status_code=201)

    @app.post("/api/admin/jobs/{job_id}/{act}")
    def admin_jobs_act(job_id: int, act: str, request: Request) -> dict:
        actor = _need(request, "analyst", "admin")
        actions = {"retry": admin.retry_job, "cancel": admin.cancel_job,
                   "pause": admin.pause_job, "resume": admin.resume_job}
        fn = actions.get(act)
        if fn is None:
            raise HTTPException(status_code=400, detail=f"不认识的动作：{act}")
        try:
            fn(crawler.conn, job_id)
        except admin.JobStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        audit.log(crawler.conn, actor["username"], f"job.{act}", f"#{job_id}", "")
        return {"ok": True, "counts": admin.job_counts(crawler.conn)}

    @app.post("/api/admin/enrich")
    def admin_enrich(request: Request, limit: int = Query(3, ge=1, le=20),
                     source: str = Query("cninfo")) -> dict:
        actor = _need(request, "analyst", "admin")
        result = enrich.enrich(crawler.conn, limit=limit, only=(source,))
        audit.log(crawler.conn, actor["username"], "items.enrich", source,
                  f"试 {result['tried']} 得 {result['ok']}")
        # enrich 自己也返回一个 "ok"（成功抠到几条），别让它把响应里的 ok 覆盖成数字
        body = dict(result)
        body["succeeded"] = body.pop("ok", 0)
        body["ok"] = True
        return body

    @app.get("/api/events/stats")
    def events_stats(request: Request):
        """SSE：每 3 秒推一次计数，页面不用再定时轮询。未读数按当前登录的人算。"""
        uid = _uid(request)

        def gen():
            conn = crawler.conn
            last = None
            while True:
                try:
                    total = crawler.conn.execute("SELECT COUNT(*) FROM items WHERE dup_of = 0").fetchone()[0]
                    midnight = int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)) * 1000)
                    today = crawler.conn.execute(
                        "SELECT COUNT(*) FROM items WHERE dup_of = 0 AND first_seen_at >= ?",
                        (midnight,)).fetchone()[0]
                    unread = crawler.conn.execute(
                        "SELECT COUNT(*) FROM items i LEFT JOIN marks m"
                        " ON m.item_id = i.id AND m.user_id = ?"
                        " WHERE i.dup_of = 0 AND m.read_at IS NULL", (uid,)).fetchone()[0]
                    bad = crawler.conn.execute(
                        "SELECT COUNT(*) FROM sources WHERE status <> 'ok'").fetchone()[0]
                    payload = {
                        "items": int(total),
                        "today": int(today),
                        "unread": int(unread),
                        "degraded": int(bad),
                        "ts": int(time.time() * 1000),
                    }
                except Exception:
                    break
                if payload != last:
                    last = payload
                    yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                else:
                    yield ": keep-alive\n\n"
                time.sleep(3)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/admin/items.xlsx")
    def admin_items_xlsx(request: Request, q: str = "", state: str = "", category: str = "") -> Response:
        _need(request)
        rows, total = admin.item_page(crawler.conn, q=q, state=state, category=category,
                                     offset=0, limit=5000)
        data = [["时间", "来源", "分类", "状态", "标题", "公司", "链接"]]
        for r in rows:
            data.append([_ts(r["published_at"]), r["source"], r.get("category") or "",
                         "下架" if r["state"] == "offline" else "在架", r["title"],
                         r.get("sec_name") or "", r.get("url") or ""])
        blob = xlsx.build([("条目", data, [20, 10, 10, 8, 60, 16, 50])])
        return Response(content=blob,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": "attachment; filename=items.xlsx"})

    @app.get("/api/report.xlsx")
    def report_xlsx(request: Request, days: int = 7) -> Response:
        _need(request)
        rep = admin.report(crawler.conn, days)
        by_source = [["来源", "条目", "修订"]]
        for r in rep["by_source"]:
            by_source.append([r["source"], r["n"], r.get("revised", 0)])
        by_day = [["日期", "新增", "修订"]]
        for r in rep["by_day"]:
            by_day.append([r["day"], r["n"], r.get("revised", 0)])
        top = [["代码", "公司", "条目"]]
        for r in rep["top_company"]:
            top.append([r.get("sec_code") or "", r.get("sec_name") or "", r["n"]])
        blob = xlsx.build([("按来源", by_source, [16, 10, 10]),
                           ("按天", by_day, [14, 10, 10]),
                           ("公司Top", top, [14, 26, 10])])
        return Response(content=blob,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename=report-{days}d.xlsx"})

    @app.get("/api/admin/audit.csv")
    def admin_audit_csv(request: Request) -> Response:
        actor = _need(request, "auditor", "admin")
        audit.log(crawler.conn, actor["username"], "audit.export", "", "")
        return Response(
            content=audit.export_csv(crawler.conn),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=audit.csv"},
        )

    @app.post("/api/admin/audit/prune")
    def admin_audit_prune(body: PruneIn, request: Request) -> dict:
        actor = _need(request, "admin")
        removed = audit.prune(crawler.conn, body.days)
        audit.log(crawler.conn, actor["username"], "audit.prune", f"{body.days} 天前", f"删除 {removed} 行")
        return {"ok": True, "removed": removed, "left": audit.count(crawler.conn)}

    @app.get("/api/admin/notify")
    def admin_notify(request: Request) -> dict:
        _need(request, "admin")
        cfg = notify.config(crawler.conn)
        return {**cfg, "ready": notify.is_ready(crawler.conn), "last": notify.last_result(crawler.conn)}

    @app.post("/api/admin/notify")
    def admin_notify_save(body: NotifyIn, request: Request) -> dict:
        actor = _need(request, "admin")
        notify.save(crawler.conn, host=body.host, port=body.port, user=body.user,
                    password=body.password, to=body.to, enabled=body.enabled)
        audit.log(crawler.conn, actor["username"], "notify.config", body.user, f"收件 {body.to}")
        out: dict = {"ok": True}
        if body.send_test:
            try:
                sent = notify.test_mail(crawler.conn)
                audit.log(crawler.conn, actor["username"], "notify.test", sent, "成功")
                out["sent_to"] = sent
            except Exception as exc:
                audit.log(crawler.conn, actor["username"], "notify.test", "", f"失败：{exc}")
                out["error"] = f"{type(exc).__name__}: {exc}"
        out["config"] = notify.config(crawler.conn)
        out["last"] = notify.last_result(crawler.conn)
        return out

    @app.get("/api/admin/settings")
    def admin_settings(request: Request) -> dict:
        _need(request)
        keys = {
            "notify_toast": "1", "notify_sound": "1", "notify_rules_only": "0",
            "notify.alert": "1", "enrich.auto": "1",
        }
        return {"values": {k: admin.get_setting(crawler.conn, k, d) for k, d in keys.items()}}

    @app.patch("/api/admin/settings")
    def admin_settings_patch(body: SettingsIn, request: Request) -> dict:
        actor = _need(request, "admin")
        for key, value in body.values.items():
            admin.set_setting(crawler.conn, key, str(value))
        audit.log(crawler.conn, actor["username"], "settings.update", "",
                  ", ".join(f"{k}={v}" for k, v in body.values.items()))
        return {"ok": True, "values": body.values}

    _fill_summaries(app)
    return app


def serve(
    host: str = "127.0.0.1",
    port: int = 8766,
    settings: Settings | None = None,
    certfile: str | None = None,
    keyfile: str | None = None,
) -> None:
    """起服务。给了 certfile/keyfile 就走 HTTPS——手机装 PWA 必须要 https。"""
    import uvicorn

    kw: dict = {"host": host, "port": port, "log_level": "info"}
    if certfile and keyfile:
        kw["ssl_certfile"] = certfile
        kw["ssl_keyfile"] = keyfile
    uvicorn.run(create_app(settings), **kw)



