"""生成一套「本地 CA + 服务器证书」，让手机能把 SignalWatch 当受信任站点。

为什么需要 CA 而不是继续用自签证书：
    自签证书在 Chrome 里属于「证书错误」页面，而 Chrome 在证书错误的源上
    **拒绝注册 Service Worker**；没有 Service Worker，就永远不会出现「安装」入口。
    把一张自己签的 CA 装进手机，等于告诉手机「这张 CA 签的东西我信」，
    之后 https 正常、SW 能注册、PWA 才能装。

用法：
    python tools/make_ca.py            # 生成 certs/ca.crt + certs/cert.pem + certs/key.pem
    python tools/make_ca.py --days 365 # 有效期

依赖：本机没有 openssl.exe，借 `python:3.11-slim` 镜像里的 openssl 跑。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CERTS = ROOT / "certs"
IMAGE = "python:3.11-slim"

# 虚拟网卡（Docker / WSL / 虚拟化）不写进 SAN，手机根本不会用这些地址访问
SKIP_PREFIX = ("127.", "169.254.", "172.16.", "172.17.", "172.18.", "172.19.",
               "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
               "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")


def lan_ips() -> list[str]:
    out = subprocess.run(["ipconfig"], capture_output=True, text=True, encoding="gbk", errors="ignore")
    ips: list[str] = []
    for m in re.finditer(r"IPv4[^:]*:\s*([0-9.]+)", out.stdout or ""):
        ip = m.group(1)
        if ip.startswith(SKIP_PREFIX):
            continue
        if ip not in ips:
            ips.append(ip)
    return ips


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成本地 CA 与服务器证书")
    ap.add_argument("--days", type=int, default=825, help="服务器证书有效期（iOS 上限 825 天）")
    ap.add_argument("--ca-days", type=int, default=3650)
    args = ap.parse_args(argv)

    if shutil.which("docker") is None:
        print("[x] 找不到 docker：这台机器上 openssl 只有 docker 镜像里有", file=sys.stderr)
        return 1

    ips = lan_ips()
    if not ips:
        print("[x] 没读到任何局域网 IPv4（ipconfig 输出里没有非虚拟网卡地址）", file=sys.stderr)
        return 1
    sans = ["DNS:localhost", "DNS:signalwatch.local", "IP:127.0.0.1"] + [f"IP:{ip}" for ip in ips]
    san = ",".join(sans)
    print(f"[i] SAN: {san}")

    CERTS.mkdir(parents=True, exist_ok=True)
    conf = CERTS / "openssl-ca.cnf"
    conf.write_text(
        f"""[req]
distinguished_name = dn
x509_extensions = v3_ca
prompt = no

[dn]
CN = SignalWatch Local CA
O = SignalWatch

[v3_ca]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash

[v3_srv]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = {san}
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
""",
        encoding="utf-8",
        newline="\n",
    )

    # 一个脚本干完：CA → 服务器 key/csr → 用 CA 签 → 自检
    script = f"""
set -e
cd /certs
openssl genrsa -out ca.key 4096 2>/dev/null
openssl req -x509 -new -nodes -key ca.key -sha256 -days {args.ca_days} -out ca.crt -config openssl-ca.cnf 2>/dev/null
openssl genrsa -out key.pem 2048 2>/dev/null
openssl req -new -key key.pem -out server.csr -subj "/CN=SignalWatch" -config openssl-ca.cnf 2>/dev/null
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \\
  -out cert.pem -days {args.days} -sha256 -extfile openssl-ca.cnf -extensions v3_srv 2>/dev/null
rm -f server.csr ca.srl
echo "--- 链校验 ---"
openssl verify -CAfile ca.crt cert.pem
echo "--- SAN ---"
openssl x509 -in cert.pem -noout -text | grep -A1 "Subject Alternative Name"
"""
    cmd = ["docker", "run", "--rm", "-v", f"{CERTS}:/certs", IMAGE, "sh", "-c", script]
    print("[i] 借 docker 里的 openssl 生成证书…")
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = res.returncode == 0 and (CERTS / "cert.pem").exists()
    print((res.stdout or res.stderr or "").strip())
    if not ok:
        print("[x] 生成失败", file=sys.stderr)
        return 1

    (CERTS / "ca.key").unlink(missing_ok=True)   # CA 私钥不进仓库也不留盘
    print(f"\n[ok] certs/ca.crt（装到手机上）  certs/cert.pem  certs/key.pem（服务端用）")
    print("     有效期：服务器证书 %d 天，CA %d 天" % (args.days, args.ca_days))
    print("     下一步：docker compose --profile tls up -d --force-recreate proxy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
