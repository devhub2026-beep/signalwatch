"""配置：路径、抓取节奏、限速。

所有可调的东西都在这里，不做隐式默认值散落在各处。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

def _root() -> Path:
    """数据根目录。

    源码运行时是仓库根；PyInstaller 打过包之后 `__file__` 落在临时解包目录里，
    跟着它走会在 `%TEMP%` 里新建一个库——所以冻结时把数据放在 exe 旁边。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


ROOT = _root()


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser() if raw else default


def _home() -> Path:
    return _env_path("SIGNALWATCH_HOME", ROOT)


@dataclass(frozen=True)
class Settings:
    home: Path = field(default_factory=_home)
    # 不传 db_path 时跟着 home 走：既尊重 SIGNALWATCH_HOME，也保证「换个 home 就是另一个库」——
    # 以前这里写死成 _home()，结果测试传了临时 home 还是写进真库，把线上配置覆盖了。
    db_path: Path | None = None
    # 每个源两次请求之间的最小间隔（秒）。1.0 = 每秒最多一次。
    min_interval: float = 1.0
    # 单个源连续失败到这个次数，状态标成 degraded，页面顶部会红条。
    fail_threshold: int = 3
    # 抓取失败后的退避基数（秒），第 n 次失败等 base * 2**(n-1)，封顶 max_backoff。
    backoff_base: float = 30.0
    max_backoff: float = 900.0
    http_timeout: float = 25.0
    user_agent: str = "signalwatch/0.1 (+local watch tool; polite crawler)"
    # 每个源每轮最多取多少条（防止第一次就把几万条全拉进本地库）。
    page_size: int = 30

    def __post_init__(self) -> None:
        if self.db_path is None:
            object.__setattr__(
                self, "db_path",
                _env_path("SIGNALWATCH_DB", self.home / "data" / "signalwatch.db"))

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


SETTINGS = Settings()
