# CI 门禁：本机演练（没有 remote，GitHub 上跑不了，就在本地把每一步跑给你看）

`.github/workflows/tests.yml` 编排了三个 job（测试、打 wheel、构建镜像）。
这台机器没有 remote、也没法 push，所以**同样的命令在本地跑一遍**，用
`tools/ci_local.ps1` 当入口，结果如下（2026-11-17）：

| 步骤 | 命令 | 结果 |
| --- | --- | --- |
| 单元测试 | `python -m unittest discover -s tests -t .` | **247 个 / 88.6 秒 / OK(skipped=4)** |
| 统计冒烟（空库自建） | `python run.py stats`（`SIGNALWATCH_HOME` 指临时目录） | ok |
| 迁移台账（只读） | `python run.py migrate --check` | ok（v1..v10 无缺） |
| 接口触达率 | `python tools/route_coverage.py` | **64 / 64 = 100%** |
| 打 wheel | `python -m build --wheel --no-isolation` | `signalwatch-0.2.0-py3-none-any.whl` |
| 装 wheel + 验静态文件 | `pip install --no-index --no-deps <whl>` + `tools/check_wheel.py` | 装进 site-packages，`web/index.html` 在包内 |

结论：**CI 本机演练：全部通过**。

## 为什么要 `--no-isolation`

`python -m build` 默认隔离环境，会临时从 PyPI 拉 setuptools/wheel；
这台机器 pypi.org 超时，所以会卡死。两个解法二选一：

- 本地：`--no-isolation`（用已装的 setuptools/wheel）；
- CI（GitHub 能访问 pypi）：不用加，保持隔离。

## pip 在这台机器怎么才能用

`pypi.org` 直连 25 秒超时；清华镜像能通，但**不带 `--timeout` 会卡在版本解析**。
能用的姿势：

```bash
python -m pip install --no-cache-dir --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  --timeout 10 "defusedxml==0.7.1" "build==1.2.2" "wheel==0.45.1"
```

三个键：固定版本号、`--timeout 10`、`--index-url` 指向清华。
