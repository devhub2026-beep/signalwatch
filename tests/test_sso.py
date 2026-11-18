"""SSO 抽象层：OIDC 配置默认值 + dev 提供方的三步流程。"""
from __future__ import annotations

import unittest

from signalwatch.sso import OIDCConfig, SSOUser, DevIdentityProvider


class OIDCConfigTests(unittest.TestCase):
    def test_default_scopes(self) -> None:
        cfg = OIDCConfig(issuer="https://idp.example", client_id="c1")
        self.assertEqual(cfg.scopes, ["openid", "email", "profile"])

    def test_custom_fields(self) -> None:
        cfg = OIDCConfig(
            issuer="https://idp.example",
            client_id="c1",
            client_secret="s1",
            authorize_endpoint="https://idp.example/auth",
            token_endpoint="https://idp.example/token",
            userinfo_endpoint="https://idp.example/userinfo",
            redirect_uri="https://sw.local/cb",
        )
        self.assertEqual(cfg.token_endpoint, "https://idp.example/token")


class DevIdentityProviderTests(unittest.TestCase):
    def test_authorize_url_carries_state(self) -> None:
        idp = DevIdentityProvider()
        self.assertIn("state=abc", idp.authorize_url("abc"))

    def test_exchange_returns_token(self) -> None:
        idp = DevIdentityProvider()
        self.assertEqual(idp.exchange("code123"), "tok-code123")

    def test_fetch_user(self) -> None:
        idp = DevIdentityProvider(SSOUser(subject="s1", username="alice",
                                          email="a@x.com"))
        u = idp.fetch_user("tok")
        self.assertEqual(u.username, "alice")
        self.assertEqual(u.subject, "s1")


if __name__ == "__main__":
    unittest.main()
