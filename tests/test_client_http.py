"""用 httpx.MockTransport 验证 BaseClient 真的按协议发请求。

不依赖任何真实后端,可以在 CI / 离线机器上跑。
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from bridge_c_core import BaseClient


class DummyClient(BaseClient):
    URL_PREFIX = "/dummy/v1"
    INSTANCE_HEADER = "X-Dummy-Instance-Id"


def _build_client(handler) -> DummyClient:
    transport = httpx.MockTransport(handler)
    c = DummyClient(base_url="https://api.example.com", api_key="k-123", instance_id="inst-A")
    c.close()
    c._client = httpx.Client(transport=transport, timeout=5.0)
    return c


def test_inbox_pull_url_and_headers() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["method"] = req.method
        captured["headers"] = dict(req.headers)
        return httpx.Response(200, json={"success": True, "items": []})

    c = _build_client(handler)
    out = c.inbox_pull(limit=7)
    assert out == {"success": True, "items": []}

    assert captured["method"] == "GET"
    assert captured["url"] == "https://api.example.com/dummy/v1/inbox/pull?limit=7"
    assert captured["headers"]["authorization"] == "Bearer k-123"
    assert captured["headers"]["accept"] == "application/json"
    assert captured["headers"]["x-dummy-instance-id"] == "inst-A"


def test_inbox_ack_sends_empty_body() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["body"] = req.content.decode("utf-8") if req.content else ""
        return httpx.Response(200, json={"success": True})

    c = _build_client(handler)
    out = c.inbox_ack("rec-42")
    assert out == {"success": True}

    assert captured["url"].endswith("/dummy/v1/inbox/rec-42/ack")
    assert json.loads(captured["body"]) == {}


def test_relaxed_returns_dict_on_404_without_raising() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not deployed"})

    c = _build_client(handler)
    out = c.inbox_pull_relaxed(limit=10)
    assert out["success"] is False
    assert out["http_status"] == 404
    assert out["detail"] == "not deployed"


def test_strict_raises_on_500() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "boom"})

    c = _build_client(handler)
    with pytest.raises(httpx.HTTPStatusError):
        c.inbox_pull(limit=10)


def test_directory_strict() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/dummy/v1/directory"
        return httpx.Response(200, json={"success": True, "instances": [{"instance_id": "a"}]})

    c = _build_client(handler)
    out = c.directory()
    assert out["instances"][0]["instance_id"] == "a"


def test_submit_and_submit_to_post_bodies() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(
            {
                "path": req.url.path,
                "body": json.loads(req.content.decode("utf-8")),
            }
        )
        return httpx.Response(200, json={"success": True})

    c = _build_client(handler)
    c.submit({"input_text": "hi"})
    c.submit_to({"to_instance_id": "B", "input_text": "yo"})

    assert captured[0] == {"path": "/dummy/v1/submit", "body": {"input_text": "hi"}}
    assert captured[1] == {
        "path": "/dummy/v1/submit_to",
        "body": {"to_instance_id": "B", "input_text": "yo"},
    }


def test_no_instance_header_when_not_set() -> None:
    captured: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(req.headers)
        return httpx.Response(200, json={"success": True, "items": []})

    transport = httpx.MockTransport(handler)
    c = DummyClient(base_url="https://x.example", api_key="k", instance_id=None)
    c.close()
    c._client = httpx.Client(transport=transport, timeout=5.0)
    c.inbox_pull(limit=1)

    assert "x-dummy-instance-id" not in captured["headers"]
