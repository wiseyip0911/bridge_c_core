"""覆盖 daemon.notify_webhook 钩子的启用/禁用/签名/失败不抛。

- 直接调 notify_webhook(): 验证 url 为空 / 签名 / 状态码 / 异常吞下。
- 集成 run_daemon(): 在 Settings.notify_webhook_url 非空时,落盘后会触发
  一次 POST,且 POST 失败不会让主循环崩溃。
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from bridge_c_core import Settings, notify_webhook, run_daemon


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _PostRecorder:
    """替代 httpx.post,记录调用 + 可控返回。"""

    def __init__(self, status_code: int = 200, raise_exc: Exception | None = None):
        self.status_code = status_code
        self.raise_exc = raise_exc
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url, *, content=None, headers=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "content": content,
                "headers": dict(headers or {}),
                "timeout": timeout,
            }
        )
        if self.raise_exc is not None:
            raise self.raise_exc
        return _FakeResp(self.status_code)


class _StopLoop(Exception):
    pass


def _settings_with_notify(
    tmp_path: Path, *, url: str = "", secret: str = "", timeout: float = 5.0
) -> Settings:
    return Settings(
        base_url="https://x.example",
        api_key="k",
        instance_id="",
        poll_interval_sec=5.0,
        pull_limit=10,
        local_pool_dir=str(tmp_path),
        request_timeout_sec=60.0,
        notify_webhook_url=url,
        notify_webhook_secret=secret,
        notify_webhook_timeout_sec=timeout,
    )


class _FakeClient:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items
        self.acks: list[str] = []

    def inbox_pull_relaxed(self, limit: int = 10) -> dict[str, Any]:
        return {"success": True, "items": self._items}

    def inbox_ack_relaxed(self, record_id: str) -> dict[str, Any]:
        self.acks.append(record_id)
        return {"success": True}


# ---------------------------------------------------------------------------
# notify_webhook(): 单元测试
# ---------------------------------------------------------------------------

def test_notify_webhook_empty_url_returns_false() -> None:
    assert notify_webhook("", {"record_id": "x"}) is False


def test_notify_webhook_2xx_returns_true(monkeypatch) -> None:
    rec = _PostRecorder(status_code=200)
    monkeypatch.setattr("bridge_c_core.daemon.httpx.post", rec)

    ok = notify_webhook(
        "http://127.0.0.1:8644/webhooks/x",
        {"record_id": "abc", "payload": "p"},
    )
    assert ok is True
    assert len(rec.calls) == 1
    call = rec.calls[0]
    assert call["url"] == "http://127.0.0.1:8644/webhooks/x"
    assert call["headers"]["X-Request-ID"] == "abc"
    assert call["headers"]["X-GitHub-Delivery"] == "abc"
    assert "X-Hub-Signature-256" not in call["headers"]


def test_notify_webhook_signs_when_secret_present(monkeypatch) -> None:
    rec = _PostRecorder(status_code=200)
    monkeypatch.setattr("bridge_c_core.daemon.httpx.post", rec)

    body = {"record_id": "r1", "payload": {"k": "v"}}
    ok = notify_webhook(
        "http://127.0.0.1:8644/webhooks/x",
        body,
        secret="topsecret",
    )
    assert ok is True
    sent = rec.calls[0]
    expected_sig = "sha256=" + hmac.new(
        b"topsecret",
        json.dumps(body, ensure_ascii=False).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert sent["headers"]["X-Hub-Signature-256"] == expected_sig


def test_notify_webhook_skips_signing_on_insecure_marker(monkeypatch) -> None:
    rec = _PostRecorder(status_code=200)
    monkeypatch.setattr("bridge_c_core.daemon.httpx.post", rec)

    notify_webhook(
        "http://127.0.0.1:8644/webhooks/x",
        {"record_id": "r"},
        secret="INSECURE_NO_AUTH",
    )
    assert "X-Hub-Signature-256" not in rec.calls[0]["headers"]


def test_notify_webhook_swallows_http_error(monkeypatch) -> None:
    rec = _PostRecorder(raise_exc=httpx.ConnectError("boom"))
    monkeypatch.setattr("bridge_c_core.daemon.httpx.post", rec)

    ok = notify_webhook(
        "http://127.0.0.1:8644/webhooks/x", {"record_id": "r"}
    )
    assert ok is False


def test_notify_webhook_returns_false_on_4xx(monkeypatch) -> None:
    rec = _PostRecorder(status_code=500)
    monkeypatch.setattr("bridge_c_core.daemon.httpx.post", rec)

    ok = notify_webhook(
        "http://127.0.0.1:8644/webhooks/x", {"record_id": "r"}
    )
    assert ok is False


# ---------------------------------------------------------------------------
# run_daemon 集成:钩子启用 / 禁用 / 失败不阻塞
# ---------------------------------------------------------------------------

def test_run_daemon_no_notify_url_does_not_post(monkeypatch, tmp_path: Path) -> None:
    rec = _PostRecorder()
    monkeypatch.setattr("bridge_c_core.daemon.httpx.post", rec)
    monkeypatch.setattr(
        "bridge_c_core.daemon.time.sleep",
        lambda *_: (_ for _ in ()).throw(_StopLoop()),
    )

    items = [{"record_id": "x-1", "payload": "p"}]
    with pytest.raises(_StopLoop):
        run_daemon(_FakeClient(items), _settings_with_notify(tmp_path, url=""))

    assert rec.calls == []
    assert (tmp_path / "x-1.json").exists()


def test_run_daemon_posts_each_new_item(monkeypatch, tmp_path: Path) -> None:
    rec = _PostRecorder(status_code=200)
    monkeypatch.setattr("bridge_c_core.daemon.httpx.post", rec)
    monkeypatch.setattr(
        "bridge_c_core.daemon.time.sleep",
        lambda *_: (_ for _ in ()).throw(_StopLoop()),
    )

    items = [
        {"record_id": "x-1", "payload": "a"},
        {"record_id": "x-2", "payload": "b"},
    ]
    with pytest.raises(_StopLoop):
        run_daemon(
            _FakeClient(items),
            _settings_with_notify(
                tmp_path,
                url="http://127.0.0.1:8644/webhooks/bridge-task",
                secret="INSECURE_NO_AUTH",
            ),
        )

    assert len(rec.calls) == 2
    sent_rids = sorted(c["headers"]["X-Request-ID"] for c in rec.calls)
    assert sent_rids == ["x-1", "x-2"]


def test_run_daemon_continues_when_notify_fails(monkeypatch, tmp_path: Path) -> None:
    """通知失败应仅记日志,不能中断主循环 / 不能阻止 ack。"""
    rec = _PostRecorder(raise_exc=httpx.ConnectError("nobody home"))
    monkeypatch.setattr("bridge_c_core.daemon.httpx.post", rec)
    monkeypatch.setattr(
        "bridge_c_core.daemon.time.sleep",
        lambda *_: (_ for _ in ()).throw(_StopLoop()),
    )

    items = [{"record_id": "x-9", "payload": "still-ok"}]
    fake = _FakeClient(items)
    fake._items = items  # 兼容子类

    with pytest.raises(_StopLoop):
        run_daemon(
            fake,
            _settings_with_notify(
                tmp_path,
                url="http://127.0.0.1:8644/webhooks/dead",
            ),
        )

    assert (tmp_path / "x-9.json").exists()
    assert len(rec.calls) == 1


def test_run_daemon_skips_post_for_duplicate_items(monkeypatch, tmp_path: Path) -> None:
    """同一 record_id 第二次出现时,write_local_item 返回 None,不应再 POST。"""
    rec = _PostRecorder(status_code=200)
    monkeypatch.setattr("bridge_c_core.daemon.httpx.post", rec)
    monkeypatch.setattr(
        "bridge_c_core.daemon.time.sleep",
        lambda *_: (_ for _ in ()).throw(_StopLoop()),
    )

    items = [
        {"record_id": "dup", "payload": "a"},
        {"record_id": "dup", "payload": "a"},
    ]
    with pytest.raises(_StopLoop):
        run_daemon(
            _FakeClient(items),
            _settings_with_notify(
                tmp_path,
                url="http://127.0.0.1:8644/webhooks/bridge-task",
            ),
        )

    assert len(rec.calls) == 1, "去重后只该 POST 一次"
