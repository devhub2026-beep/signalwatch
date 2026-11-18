"""任务唤醒：让 worker 从「每 2 秒轮询一次」变成「有人排任务就立刻醒」。

jobs 表的真相仍然在 SQLite 里（优先级、状态机、崩溃恢复都靠它），
Redis 这里只当门铃：`enqueue_job` 之后 LPUSH 一个 id，worker 用 BRPOP 等。
门铃丢了不影响正确性（worker 最多退回轮询），门铃响了能省掉平均 1 秒的延迟。
"""

from __future__ import annotations

import os

KEY = "sw:jobs"
FALLBACK_POLL = 2.0


def enabled() -> bool:
    """SIGNALWATCH_WAKEUP=redis 且确实连得上才用；否则调用方退回 sleep 轮询。"""
    if os.environ.get("SIGNALWATCH_WAKEUP", "").strip().lower() != "redis":
        return False
    from .redis_client import Redis

    client = Redis.from_env()
    if client is None:
        return False
    ok = client.ping()
    client.close()
    return ok


def notify(job_id: int) -> None:
    """排完任务敲一下门铃；失败不抛（任务已经在库里，worker 轮询也能拿到）。"""
    if not enabled():
        return
    from .redis_client import Redis

    client = Redis.from_env()
    if client is None:
        return
    try:
        client.lpush(KEY, str(job_id))
        client.expire(KEY, 3600)
    except Exception:                                            # noqa: BLE001
        pass
    finally:
        client.close()


def wait(timeout: float = FALLBACK_POLL) -> int | None:
    """等门铃，最多 timeout 秒。返回被唤醒的 job id，超时返回 None。"""
    from .redis_client import Redis

    client = Redis.from_env()
    if client is None:
        return None
    try:
        got = client.brpop(KEY, timeout)
        if not got:
            return None
        try:
            return int(got[1])
        except (TypeError, ValueError):
            return -1
    except Exception:                                            # noqa: BLE001
        return None
    finally:
        client.close()
