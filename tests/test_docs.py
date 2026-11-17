"""文档门禁：代码里每条路由都必须在 README 的「接口」一节出现。

写文档最容易的失败是「加了接口忘了写」——README 上看着挺全，
调用方按文档去调，404。这条测试把它变成红灯。
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from signalwatch.api import create_app  # noqa: E402
from signalwatch.config import Settings  # noqa: E402


def readme_text() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


class DocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.app = create_app(Settings(home=Path(cls.tmp.name)))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _routes(self) -> list[tuple[str, str]]:
        out = []
        for r in self.app.routes:
            path = getattr(r, "path", None)
            methods = getattr(r, "methods", None) or set()
            if not path or not path.startswith("/"):
                continue
            for m in sorted(m for m in methods if m not in ("HEAD", "OPTIONS")):
                out.append((m, path))
        return out

    @staticmethod
    def _norm(path: str) -> str:
        """路径参数名字不重要：代码里是 {item_id}，README 里写 {id} 也算对上。"""
        return re.sub(r"\{[^}]+\}", "{}", path)

    def test_every_route_is_documented(self) -> None:
        text = self._norm(readme_text())
        missing = [f"{m} {p}" for m, p in self._routes() if self._norm(p) not in text]
        self.assertEqual(missing, [], "README 缺这些路由：" + ", ".join(missing))

    def test_route_count_matches_claim(self) -> None:
        # README 写着「共 51 条路由」，代码变了这句就得跟着改
        n = len(self._routes())
        self.assertIn(f"共 **{n} 条路由**", readme_text(), f"实际 {n} 条路由，README 的数字要对上")

    def test_no_dead_docs_section(self) -> None:
        # 接口一节里提到的路径，代码里要有（只查 /api 开头的，页面路径单说）
        text = readme_text()
        api = text[text.index("## 接口"):text.index("## 加一个源")]
        known = {self._norm(p) for _, p in self._routes()}
        missing = []
        for token in api.split("`"):
            if not token.startswith("/api"):
                continue
            base = token.split("?")[0]
            if base.endswith("/") or "*" in base:
                continue
            if self._norm(base) not in known and "{" not in token:
                missing.append(token)
        self.assertEqual(missing, [], "README 提到但代码里没有：" + ", ".join(missing))


if __name__ == "__main__":
    unittest.main()
