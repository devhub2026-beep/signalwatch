"""单点登录（SSO）抽象层。

企业里往往已有账号体系（OIDC / 企业微信 / 飞书 / SAML），SignalWatch
通过 ``IdentityProvider`` 协议接入：授权 → 换令牌 → 取用户画像，再把外部
身份映射成本地用户。当前提供 OIDC 配置结构和 dev 内存实现（测试用），
接真实 IdP 时只需实现三个方法。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class OIDCConfig:
    """OIDC 提供方配置（对应标准 discovery 字段）。"""

    issuer: str
    client_id: str
    client_secret: str = ""
    authorize_endpoint: str = ""
    token_endpoint: str = ""
    userinfo_endpoint: str = ""
    scopes: list[str] = field(default_factory=lambda: ["openid", "email", "profile"])
    redirect_uri: str = ""


@dataclass
class SSOUser:
    """外部身份解析出的用户画像。"""

    subject: str          # 提供方的稳定用户标识
    username: str
    email: str = ""
    display_name: str = ""


class IdentityProvider(ABC):
    """SSO 提供方协议：authorize → exchange → fetch_user 三步。"""

    @abstractmethod
    def authorize_url(self, state: str) -> str:
        """生成授权页 URL（带 state 防 CSRF）。"""

    @abstractmethod
    def exchange(self, code: str) -> str:
        """用授权码换访问令牌，返回 token。"""

    @abstractmethod
    def fetch_user(self, token: str) -> SSOUser:
        """用令牌取用户画像。"""


class DevIdentityProvider(IdentityProvider):
    """开发/测试用内存实现：任意 code 映射到一个固定用户。"""

    def __init__(self, user: SSOUser | None = None) -> None:
        self.user = user or SSOUser(subject="dev-1", username="dev",
                                    email="dev@local")

    def authorize_url(self, state: str) -> str:
        return f"https://idp.dev/authorize?state={state}"

    def exchange(self, code: str) -> str:
        return f"tok-{code}"

    def fetch_user(self, token: str) -> SSOUser:
        return self.user
