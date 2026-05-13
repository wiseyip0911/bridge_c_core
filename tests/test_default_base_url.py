"""验证"企业专用仓内置默认 base_url + 末端只配 API_KEY"这条路径。"""
from __future__ import annotations

import httpx
import pytest

from bridge_c_core import BaseClient, Settings


class EnterpriseClient(BaseClient):
    """模拟某企业的专用仓里写死的 client 子类。"""

    URL_PREFIX = "/ent/v1"
    INSTANCE_HEADER = "X-Ent-Instance-Id"
    DEFAULT_BASE_URL = "https://enterprise.example.com"

    ENV_BASE_URL = "ENT_BASE_URL"
    ENV_API_KEY = "ENT_API_KEY"
    ENV_INSTANCE_ID = "ENT_INSTANCE_ID"


def test_client_uses_default_when_no_env_and_no_param(monkeypatch) -> None:
    monkeypatch.delenv("ENT_BASE_URL", raising=False)
    c = EnterpriseClient(api_key="k-1")
    try:
        assert c.base_url == "https://enterprise.example.com"
    finally:
        c.close()


def test_env_overrides_default(monkeypatch) -> None:
    monkeypatch.setenv("ENT_BASE_URL", "https://staging.example.com")
    c = EnterpriseClient(api_key="k-1")
    try:
        assert c.base_url == "https://staging.example.com"
    finally:
        c.close()


def test_param_overrides_env_and_default(monkeypatch) -> None:
    monkeypatch.setenv("ENT_BASE_URL", "https://staging.example.com")
    c = EnterpriseClient(
        base_url="https://hotfix.example.com",
        api_key="k-1",
    )
    try:
        assert c.base_url == "https://hotfix.example.com"
    finally:
        c.close()


def test_settings_from_env_uses_default_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("ENT_BASE_URL", raising=False)
    monkeypatch.setenv("ENT_API_KEY", "k-2")

    s = Settings.from_env(
        env_prefix="ENT_",
        interactive=False,
        default_base_url="https://enterprise.example.com",
    )
    assert s.base_url == "https://enterprise.example.com"
    assert s.api_key == "k-2"


def test_settings_from_env_env_takes_precedence_over_default(monkeypatch) -> None:
    monkeypatch.setenv("ENT_BASE_URL", "https://override.example.com")
    monkeypatch.setenv("ENT_API_KEY", "k-2")

    s = Settings.from_env(
        env_prefix="ENT_",
        interactive=False,
        default_base_url="https://enterprise.example.com",
    )
    assert s.base_url == "https://override.example.com"


def test_settings_from_env_exits_when_no_env_and_no_default(monkeypatch) -> None:
    monkeypatch.delenv("ENT_BASE_URL", raising=False)
    monkeypatch.setenv("ENT_API_KEY", "k-2")

    with pytest.raises(SystemExit):
        Settings.from_env(
            env_prefix="ENT_",
            interactive=False,
        )


def test_end_to_end_only_api_key_configured(monkeypatch) -> None:
    """末端机器最简流程:仅 ENT_API_KEY 一项 env,客户端依然可以发出正确 HTTP 请求。"""
    monkeypatch.delenv("ENT_BASE_URL", raising=False)
    monkeypatch.delenv("ENT_INSTANCE_ID", raising=False)
    monkeypatch.setenv("ENT_API_KEY", "user-key-9")

    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"success": True, "items": []})

    c = EnterpriseClient()
    c.close()
    c._client = httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)
    c.inbox_pull(limit=1)

    assert captured["url"] == "https://enterprise.example.com/ent/v1/inbox/pull?limit=1"
    assert captured["auth"] == "Bearer user-key-9"
