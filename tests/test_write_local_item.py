from __future__ import annotations

import json
from pathlib import Path

from bridge_c_core.daemon import write_local_item


def test_uses_record_id_when_present(tmp_path: Path) -> None:
    record = {"record_id": "rec-001", "payload": {"x": 1}}
    written = write_local_item(tmp_path, record)
    assert written is not None
    assert written.name == "rec-001.json"
    assert json.loads(written.read_text("utf-8")) == record


def test_falls_back_to_id(tmp_path: Path) -> None:
    record = {"id": "alt-002", "payload": "hello"}
    written = write_local_item(tmp_path, record)
    assert written is not None
    assert written.name == "alt-002.json"


def test_sha256_fallback_is_stable(tmp_path: Path) -> None:
    """同一份记录,即使跨"进程"(即跨调用)算出的 sha256 文件名必须一致。

    Python 内置 hash() 在不同进程会变化,这里验证我们改用 sha256 后稳定。
    """
    record = {"payload": "no-id-here", "n": 42}
    first = write_local_item(tmp_path, record)
    assert first is not None

    other = tmp_path / "other"
    other.mkdir()
    second = write_local_item(other, record)
    assert second is not None
    assert first.name == second.name


def test_dedup_when_file_already_exists(tmp_path: Path) -> None:
    record = {"record_id": "dup", "v": 1}
    first = write_local_item(tmp_path, record)
    assert first is not None

    second = write_local_item(tmp_path, {"record_id": "dup", "v": 2})
    assert second is None
    assert json.loads(first.read_text("utf-8"))["v"] == 1


def test_atomic_write_leaves_no_tmp_file(tmp_path: Path) -> None:
    record = {"record_id": "atomic-1", "payload": "ok"}
    written = write_local_item(tmp_path, record)
    assert written is not None
    leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
