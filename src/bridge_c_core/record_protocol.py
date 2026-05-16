"""kq-pool 消息类型约定:区分 task(需 Agent 处理) 与 result(投递回执).

防止双方把对方的 result 再次当作 task 回复,形成 ping-pong。
"""
from __future__ import annotations

from typing import Any

RECORD_TYPE_TASK = "task"
RECORD_TYPE_RESULT = "result"

# payload_json 内强制标志(回投 result 时必须带上)
BRIDGE_PHASE = "_bridge_phase"
SUPPRESS_AUTO_REPLY = "_bridge_suppress_auto_reply"
REPLY_TO_CID = "_bridge_reply_to_cid"


def record_type_of(record: dict[str, Any]) -> str:
    raw = record.get("record_type")
    if raw is None or str(raw).strip() == "":
        return RECORD_TYPE_TASK
    return str(raw).strip().lower()


def payload_of(record: dict[str, Any]) -> dict[str, Any]:
    pj = record.get("payload_json")
    return dict(pj) if isinstance(pj, dict) else {}


def is_agent_work_item(record: dict[str, Any]) -> bool:
    """是否应进入 pending 并触发 Hermes 处理。"""
    rt = record_type_of(record)
    if rt == RECORD_TYPE_RESULT:
        return False

    pj = payload_of(record)
    if pj.get(SUPPRESS_AUTO_REPLY) is True:
        return False
    if str(pj.get(BRIDGE_PHASE) or "").strip().lower() == RECORD_TYPE_RESULT:
        return False
    # 仅有 result_text、无 input_text → 对端回执,非新任务
    if pj.get("result_text") and not str(pj.get("input_text") or "").strip():
        return False
    return True


def build_result_payload(
    base: dict[str, Any],
    *,
    correlation_id: str = "",
    answered_by: str = "hermes",
) -> dict[str, Any]:
    """构造带回执标志的 result payload,避免对端再次自动 reply。"""
    out = dict(base)
    out[BRIDGE_PHASE] = RECORD_TYPE_RESULT
    out[SUPPRESS_AUTO_REPLY] = True
    if correlation_id:
        out[REPLY_TO_CID] = correlation_id
    out.setdefault("status", "ok")
    if answered_by:
        out.setdefault("answered_by", answered_by)
    return out
