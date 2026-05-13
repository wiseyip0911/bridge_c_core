"""覆盖 messages_log.append_inbound / append_outbound 以及它们在 daemon /
BaseClient 中的集成行为。

要点:
- 空路径 = 关闭(返回 False,不抛、不写文件)。
- inbound: 从 record + payload_json 抽 peer / channel / text / correlation_id。
- outbound: 从 body 抽 to_instance_id / correlation_id;从 response 抽 record_id;
  ``submit`` 走 self 兜底。
- daemon: 启用时,每个新落盘 item 都会 append 一行;禁用时不写。
- BaseClient: 成功调用 submit_to 会 append 一行;失败的 relaxed 调用不写。
- 多次写入是 append 而不是覆盖,行格式严格 JSON Lines。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bridge_c_core import (
    BaseClient,
    Settings,
    append_inbound,
    append_outbound,
    run_daemon,
)


def _read_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for raw in path.read_text("utf-8").splitlines():
        raw = raw.strip()
        if raw:
            out.append(json.loads(raw))
    return out


# ---------------------------------------------------------------------------
# append_inbound / append_outbound 直接调用
# ---------------------------------------------------------------------------

def test_append_inbound_empty_path_is_noop(tmp_path: Path) -> None:
    ok = append_inbound("", {"record_id": "x"})
    assert ok is False
    assert not any(tmp_path.iterdir())


def test_append_inbound_extracts_fields(tmp_path: Path) -> None:
    log = tmp_path / "messages.jsonl"
    rec = {
        "record_id": "r-1",
        "instance_id": "me",
        "correlation_id": "cid-1",
        "record_type": "task",
        "created_at": "2026-05-13T11:35:18Z",
        "payload_json": {
            "channel": "chat",
            "input_text": "hi",
            "_from_instance_id": "peer-a",
        },
    }
    assert append_inbound(log, rec, self_instance_id="me") is True
    rows = _read_lines(log)
    assert len(rows) == 1
    row = rows[0]
    assert row["direction"] == "inbound"
    assert row["peer_instance_id"] == "peer-a"
    assert row["self_instance_id"] == "me"
    assert row["record_id"] == "r-1"
    assert row["correlation_id"] == "cid-1"
    assert row["channel"] == "chat"
    assert row["text"] == "hi"
    assert row["raw"]["input_text"] == "hi"
    assert row["created_at"] == "2026-05-13T11:35:18Z"


def test_append_outbound_submit_to(tmp_path: Path) -> None:
    log = tmp_path / "messages.jsonl"
    body = {
        "to_instance_id": "peer-b",
        "correlation_id": "cid-2",
        "record_type": "task",
        "payload_json": {"channel": "chat", "input_text": "hello"},
    }
    resp = {"success": True, "record_id": "srv-rid-1"}
    assert append_outbound(log, body, resp, self_instance_id="me") is True
    row = _read_lines(log)[0]
    assert row["direction"] == "outbound"
    assert row["peer_instance_id"] == "peer-b"
    assert row["self_instance_id"] == "me"
    assert row["record_id"] == "srv-rid-1"
    assert row["correlation_id"] == "cid-2"
    assert row["channel"] == "chat"
    assert row["text"] == "hello"
    assert row["kind"] == "submit_to"


def test_append_outbound_submit_falls_back_to_self_as_peer(tmp_path: Path) -> None:
    log = tmp_path / "messages.jsonl"
    body = {
        "correlation_id": "cid-3",
        "record_type": "task",
        "payload_json": {"input_text": "self note"},
    }
    resp = {"success": True, "record_id": "srv-rid-2"}
    assert (
        append_outbound(log, body, resp, kind="submit", self_instance_id="me")
        is True
    )
    row = _read_lines(log)[0]
    assert row["kind"] == "submit"
    assert row["peer_instance_id"] == "me"


def test_append_is_append_not_overwrite(tmp_path: Path) -> None:
    log = tmp_path / "messages.jsonl"
    append_inbound(log, {"record_id": "a", "payload_json": {"input_text": "1"}})
    append_inbound(log, {"record_id": "b", "payload_json": {"input_text": "2"}})
    rows = _read_lines(log)
    assert [r["record_id"] for r in rows] == ["a", "b"]


# ---------------------------------------------------------------------------
# daemon 集成
# ---------------------------------------------------------------------------

class _FakeClient:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items
        self.acks: list[str] = []

    def inbox_pull_relaxed(self, limit: int = 10) -> dict[str, Any]:
        return {"success": True, "items": self._items}

    def inbox_ack_relaxed(self, record_id: str) -> dict[str, Any]:
        self.acks.append(record_id)
        return {"success": True}


class _StopLoop(Exception):
    pass


def _settings(tmp_path: Path, *, log_name: str | None = "messages.jsonl") -> Settings:
    return Settings(
        base_url="https://x.example",
        api_key="k",
        instance_id="me",
        poll_interval_sec=5.0,
        pull_limit=10,
        local_pool_dir=str(tmp_path / "pending"),
        request_timeout_sec=60.0,
        message_log_path=str(tmp_path / log_name) if log_name else "",
    )


def test_daemon_appends_inbound_when_enabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "bridge_c_core.daemon.time.sleep",
        lambda *_: (_ for _ in ()).throw(_StopLoop()),
    )
    items = [
        {
            "record_id": "r-1",
            "correlation_id": "cid-A",
            "record_type": "task",
            "payload_json": {
                "channel": "chat",
                "input_text": "hello from mobile",
                "_from_instance_id": "yeweizhi_mobile",
            },
        }
    ]
    with pytest.raises(_StopLoop):
        run_daemon(_FakeClient(items), _settings(tmp_path))

    log = tmp_path / "messages.jsonl"
    rows = _read_lines(log)
    assert len(rows) == 1
    assert rows[0]["direction"] == "inbound"
    assert rows[0]["peer_instance_id"] == "yeweizhi_mobile"
    assert rows[0]["text"] == "hello from mobile"


def test_daemon_skips_log_when_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "bridge_c_core.daemon.time.sleep",
        lambda *_: (_ for _ in ()).throw(_StopLoop()),
    )
    items = [{"record_id": "r-1", "payload_json": {"input_text": "x"}}]
    with pytest.raises(_StopLoop):
        run_daemon(_FakeClient(items), _settings(tmp_path, log_name=None))

    assert not (tmp_path / "messages.jsonl").exists()


# ---------------------------------------------------------------------------
# BaseClient 集成
# ---------------------------------------------------------------------------

class _ToyClient(BaseClient):
    URL_PREFIX = "/toy/v1"
    INSTANCE_HEADER = "X-Toy-Instance-Id"
    DEFAULT_BASE_URL = "https://toy.example"


def _patch_strict(c: BaseClient, response: dict[str, Any]) -> None:
    c._request_strict = lambda *_, **__: response  # type: ignore[assignment]


def _patch_relaxed(c: BaseClient, response: dict[str, Any]) -> None:
    c._request_relaxed = lambda *_, **__: response  # type: ignore[assignment]


def test_client_submit_to_appends_outbound(tmp_path: Path) -> None:
    log = tmp_path / "messages.jsonl"
    c = _ToyClient(
        api_key="k-1", instance_id="me", message_log_path=str(log)
    )
    try:
        _patch_strict(c, {"success": True, "record_id": "srv-1"})
        c.submit_to(
            {
                "to_instance_id": "peer-x",
                "correlation_id": "cid-X",
                "record_type": "task",
                "payload_json": {"channel": "chat", "input_text": "yo"},
            }
        )
    finally:
        c.close()

    rows = _read_lines(log)
    assert len(rows) == 1
    assert rows[0]["direction"] == "outbound"
    assert rows[0]["peer_instance_id"] == "peer-x"
    assert rows[0]["record_id"] == "srv-1"
    assert rows[0]["text"] == "yo"
    assert rows[0]["self_instance_id"] == "me"


def test_client_disabled_log_writes_nothing(tmp_path: Path) -> None:
    log = tmp_path / "messages.jsonl"
    c = _ToyClient(api_key="k", instance_id="me", message_log_path="")
    try:
        _patch_strict(c, {"success": True, "record_id": "srv-1"})
        c.submit_to(
            {"to_instance_id": "p", "payload_json": {"input_text": "v"}}
        )
    finally:
        c.close()
    assert not log.exists()


def test_client_relaxed_failure_does_not_log(tmp_path: Path) -> None:
    log = tmp_path / "messages.jsonl"
    c = _ToyClient(api_key="k", instance_id="me", message_log_path=str(log))
    try:
        _patch_relaxed(c, {"success": False, "error": "boom"})
        c.submit_to_relaxed(
            {"to_instance_id": "p", "payload_json": {"input_text": "v"}}
        )
    finally:
        c.close()
    assert _read_lines(log) == []


def test_client_relaxed_success_logs(tmp_path: Path) -> None:
    log = tmp_path / "messages.jsonl"
    c = _ToyClient(api_key="k", instance_id="me", message_log_path=str(log))
    try:
        _patch_relaxed(c, {"success": True, "record_id": "srv-9"})
        c.submit_to_relaxed(
            {
                "to_instance_id": "peer-r",
                "correlation_id": "cid-R",
                "payload_json": {"channel": "chat", "input_text": "via relaxed"},
            }
        )
    finally:
        c.close()
    rows = _read_lines(log)
    assert len(rows) == 1
    assert rows[0]["record_id"] == "srv-9"
    assert rows[0]["peer_instance_id"] == "peer-r"
