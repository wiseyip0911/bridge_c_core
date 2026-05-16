from bridge_c_core.record_protocol import (
    BRIDGE_PHASE,
    SUPPRESS_AUTO_REPLY,
    build_result_payload,
    is_agent_work_item,
)


def test_result_record_not_work_item() -> None:
    rec = {
        "record_type": "result",
        "payload_json": {"result_text": "ok", "status": "ok"},
    }
    assert is_agent_work_item(rec) is False


def test_task_with_input_is_work_item() -> None:
    rec = {
        "record_type": "task",
        "payload_json": {"input_text": "hello", "_from_instance_id": "a"},
    }
    assert is_agent_work_item(rec) is True


def test_suppress_flag_blocks_work_item() -> None:
    rec = {
        "record_type": "task",
        "payload_json": {
            "input_text": "x",
            SUPPRESS_AUTO_REPLY: True,
        },
    }
    assert is_agent_work_item(rec) is False


def test_build_result_payload_stamps_flags() -> None:
    out = build_result_payload(
        {"result_text": "hi"},
        correlation_id="cid-1",
        answered_by="hermes",
    )
    assert out["result_text"] == "hi"
    assert out[BRIDGE_PHASE] == "result"
    assert out[SUPPRESS_AUTO_REPLY] is True
    assert out["_bridge_reply_to_cid"] == "cid-1"
