"""接口触达率：跑一遍测试套件，看 64 条路由里有多少条真被请求过。

为什么不用 coverage.py：这台机器 pip 装 coverage 十分钟没动静（清华源也慢），
而「哪条接口一次都没被请求过」这个问题的答案不依赖行覆盖率——把每次请求的
路由模板记下来，和 app.routes 对一遍就够了。它便宜、可复跑，也正好回答
「这个接口有没有人验过」而不是「这行代码有没有被执行」。

用法：
    python tools/route_coverage.py            # 跑全套测试再算
    python tools/route_coverage.py --no-tests # 只统计已有记录（调试用）
"""

from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from signalwatch import api  # noqa: E402


def run_tests() -> tuple[int, int]:
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    return result.testsRun, len(result.failures) + len(result.errors)


def declared_routes() -> set[str]:
    app = api.create_app(autostart=False)
    routes: set[str] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if not methods or not path:
            continue
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            routes.add(f"{method} {path}")
    return routes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-tests", action="store_true", help="不跑测试，只看当前记录")
    args = parser.parse_args(argv)

    ran = failed = 0
    if not args.no_tests:
        ran, failed = run_tests()

    declared = declared_routes()
    hit = api.HIT_ROUTES & declared
    miss = sorted(declared - hit)
    rate = len(hit) / len(declared) * 100 if declared else 0.0

    print(f"测试：{ran} 个，失败 {failed} 个")
    print(f"接口触达：{len(hit)}/{len(declared)} = {rate:.1f}%")
    if miss:
        print("没被请求过的接口：")
        for item in miss:
            print(f"  - {item}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
