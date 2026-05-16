"""用伪 client 跑一次 run_daemon 的内层循环,验证落盘 + ack 行为。

run_daemon 是 while True 死循环,这里用 monkeypatch 把 time.sleep 改成抛异常,
让循环跑完一轮后退出,再断言副作用。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bridge_c_core import Settings, run_daemon


class FakeClient:
    def __init__(self, pull_response: dict[str, Any]) -> None:
        self._resp = pull_response
        self.pull_calls = 0
        self.ack_calls: list[str] = []

    def inbox_pull_relaxed(self, limit: int = 10) -> dict[str, Any]:
        self.pull_calls += 1
        return self._resp

    def inbox_ack_relaxed(self, record_id: str) -> dict[str, Any]:
        self.ack_calls.append(record_id)
        return {"success": True}


class _StopLoop(Exception):
    pass


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        base_url="https://x.example",
        api_key="k",
        instance_id="",
        poll_interval_sec=5.0,
        pull_limit=10,
        local_pool_dir=str(tmp_path),
        request_timeout_sec=60.0,
        message_log_path="",
    )


def test_writes_items_to_pool(monkeypatch, tmp_path: Path) -> None:
    items = [
        {"record_id": "r-1", "payload": {"x": 1}},
        {"record_id": "r-2", "payload": {"x": 2}},
    ]
    client = FakeClient({"success": True, "items": items})

    monkeypatch.setattr("bridge_c_core.daemon.time.sleep", lambda *_: (_ for _ in ()).throw(_StopLoop()))

    with pytest.raises(_StopLoop):
        run_daemon(client, _settings(tmp_path))

    assert client.pull_calls == 1
    assert (tmp_path / "r-1.json").exists()
    assert (tmp_path / "r-2.json").exists()
    assert json.loads((tmp_path / "r-1.json").read_text("utf-8")) == items[0]
    assert client.ack_calls == [], "auto_ack 未声明时不应主动 ack"


def test_auto_ack_triggers_ack(monkeypatch, tmp_path: Path) -> None:
    items = [{"record_id": "r-9", "payload": "ok"}]
    client = FakeClient({"success": True, "auto_ack": True, "items": items})

    monkeypatch.setattr("bridge_c_core.daemon.time.sleep", lambda *_: (_ for _ in ()).throw(_StopLoop()))
    with pytest.raises(_StopLoop):
        run_daemon(client, _settings(tmp_path))

    assert client.ack_calls == ["r-9"]


def test_handles_nested_data_items(monkeypatch, tmp_path: Path) -> None:
    items = [{"record_id": "r-x", "payload": "deep"}]
    client = FakeClient({"success": True, "data": {"items": items}})

    monkeypatch.setattr("bridge_c_core.daemon.time.sleep", lambda *_: (_ for _ in ()).throw(_StopLoop()))
    with pytest.raises(_StopLoop):
        run_daemon(client, _settings(tmp_path))

    assert (tmp_path / "r-x.json").exists()


def test_skips_result_items(monkeypatch, tmp_path: Path) -> None:
    items = [
        {
            "record_id": "r-task",
            "record_type": "task",
            "payload_json": {"input_text": "hi"},
        },
        {
            "record_id": "r-result",
            "record_type": "result",
            "payload_json": {"result_text": "done", "status": "ok"},
        },
    ]
    client = FakeClient({"success": True, "auto_ack": True, "items": items})

    monkeypatch.setattr("bridge_c_core.daemon.time.sleep", lambda *_: (_ for _ in ()).throw(_StopLoop()))
    with pytest.raises(_StopLoop):
        run_daemon(client, _settings(tmp_path))

    assert (tmp_path / "r-task.json").exists()
    assert not (tmp_path / "r-result.json").exists()
    assert client.ack_calls == ["r-task", "r-result"]


def test_skips_non_dict_items(monkeypatch, tmp_path: Path) -> None:
    items = [{"record_id": "good"}, "not-a-dict", 42]
    client = FakeClient({"success": True, "items": items})

    monkeypatch.setattr("bridge_c_core.daemon.time.sleep", lambda *_: (_ for _ in ()).throw(_StopLoop()))
    with pytest.raises(_StopLoop):
        run_daemon(client, _settings(tmp_path))

    written = sorted(p.name for p in tmp_path.iterdir())
    assert written == ["good.json"]
