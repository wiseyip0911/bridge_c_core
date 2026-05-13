"""持久化收发消息总账(JSON Lines)。

为什么独立成一个模块?
=====================

C 端守护进程把收到的任务原子落盘到 ``data/pending/<rid>.json``,但应用层
往往会在处理完后**删掉**那个文件。同理,我们通过 ``submit_to`` 发出去的
消息在协议层落到对方服务端,本机也**完全不留底**。这导致想做一个"已发
+ 已收"的对话窗口时,根本拿不到历史记录。

本模块给出极简的解决方案:**在守护落盘和客户端 submit 成功这两个时点上,
顺手往一个 append-only 的 JSON Lines 文件里追加一行**。文件位置由
``Settings.message_log_path`` 决定(``""`` = 关闭,默认开启,落到
``data/messages.jsonl``)。

设计取舍
========

- **纯文本 JSON Lines**:一行一条,易追加、易 tail、易 ``rg``、易跨语言读。
  不引入 SQLite 之类依赖。
- **多进程并发友好**:守护进程写 inbound、各种 CLI / 应用脚本写 outbound,
  使用文件系统层 ``O_APPEND`` 语义,在 Linux/macOS 下对单次 write 是原子的,
  Windows 上对几 KB 内的文本写入实际表现也无明显交错。我们写的每行都用
  ``json.dumps`` 一次性算好再 ``f.write``,然后立刻 ``flush``。
- **永不抛**:append 失败只记 WARNING,绝不打断主流程(消息已通过协议层成功
  发出 / 落盘)。

行格式
======

每行是一个 JSON 对象,字段:

- ``ts`` (ISO8601 UTC, ``2026-05-13T17:48:44Z``):事件发生时间
- ``direction`` (``"inbound"`` / ``"outbound"``)
- ``peer_instance_id``:对端实例代号
  - inbound: 从 ``payload_json._from_instance_id`` 或 ``instance_id`` 推断
  - outbound: 来自 ``body.to_instance_id``(``submit_to``);
    ``submit`` 时填 ``"<self>"``
- ``self_instance_id``:本端实例代号(若已配置 ``INSTANCE_ID``,否则空串)
- ``record_id``:消息 id(若收到的或返回的 record 里有,否则空)
- ``correlation_id``:关联 id(在 inbound/outbound 双向追踪同一对话的 turn)
- ``channel``:``payload_json.channel`` 若有
- ``record_type``:``task`` / ``result`` / 自定义
- ``text``:``payload_json.input_text`` / ``payload_json.result_text``
  (优先 ``input_text``,outbound 上也会包含)
- ``raw``:完整原始 payload(``payload_json``);为方便排查保留

故意**不**记录 ``Authorization`` / API_KEY 等敏感数据。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------

def append_inbound(
    path: str | Path | None,
    record: dict[str, Any],
    *,
    self_instance_id: str = "",
) -> bool:
    """记录一条 **inbound**(我从池子里拉到的)消息。

    返回 True 当且仅当成功 append。其它情况返回 False 但不抛。
    """
    if not path:
        return False

    payload = record.get("payload_json") if isinstance(record, dict) else None
    if not isinstance(payload, dict):
        payload = {}

    peer = (
        str(payload.get("_from_instance_id") or "").strip()
        or str(record.get("from_instance_id") or "").strip()
        or str(record.get("sender_instance_id") or "").strip()
    )

    row = _row(
        direction="inbound",
        peer_instance_id=peer,
        self_instance_id=self_instance_id,
        record_id=str(record.get("record_id") or record.get("id") or ""),
        correlation_id=str(record.get("correlation_id") or ""),
        channel=str(payload.get("channel") or ""),
        record_type=str(record.get("record_type") or ""),
        text=str(
            payload.get("input_text") or payload.get("result_text") or ""
        ),
        raw=payload,
        created_at=str(record.get("created_at") or ""),
    )
    return _append(path, row)


def append_outbound(
    path: str | Path | None,
    body: dict[str, Any],
    response: dict[str, Any] | None,
    *,
    kind: str = "submit_to",
    self_instance_id: str = "",
) -> bool:
    """记录一条 **outbound**(我刚刚 submit / submit_to 出去的)消息。

    - ``body``  : 我发的 HTTP body(含 ``to_instance_id``、``correlation_id``、
                  ``payload_json``、``record_type`` 等)。
    - ``response``: 服务端响应(若解析失败可为 None);成功时通常含 ``record_id``。
    - ``kind``  : ``"submit_to"`` 或 ``"submit"``,用于 ``peer`` 兜底逻辑。

    返回 True 当且仅当成功 append。
    """
    if not path:
        return False

    body = body if isinstance(body, dict) else {}
    response = response if isinstance(response, dict) else {}

    payload = body.get("payload_json")
    if not isinstance(payload, dict):
        payload = {}

    if kind == "submit":
        peer = self_instance_id or "<self>"
    else:
        peer = (
            str(body.get("to_instance_id") or "").strip()
            or str(body.get("recipient_instance_id") or "").strip()
        )

    row = _row(
        direction="outbound",
        peer_instance_id=peer,
        self_instance_id=self_instance_id,
        record_id=str(response.get("record_id") or ""),
        correlation_id=str(
            body.get("correlation_id") or response.get("correlation_id") or ""
        ),
        channel=str(payload.get("channel") or body.get("channel") or ""),
        record_type=str(body.get("record_type") or ""),
        text=str(
            payload.get("input_text")
            or payload.get("result_text")
            or body.get("input_text")
            or ""
        ),
        raw=payload,
        created_at="",  # 由本地 ts 表征
    )
    row["kind"] = kind
    return _append(path, row)


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(
    *,
    direction: str,
    peer_instance_id: str,
    self_instance_id: str,
    record_id: str,
    correlation_id: str,
    channel: str,
    record_type: str,
    text: str,
    raw: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    return {
        "ts": _now_iso(),
        "direction": direction,
        "peer_instance_id": peer_instance_id,
        "self_instance_id": self_instance_id,
        "record_id": record_id,
        "correlation_id": correlation_id,
        "channel": channel,
        "record_type": record_type,
        "text": text,
        "raw": raw,
        "created_at": created_at,
    }


def _append(path: str | Path, row: dict[str, Any]) -> bool:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False)
        # \n 在外面拼接,避免 json.dumps 出现意外的换行
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        return True
    except OSError as e:
        logger.warning("append message log 失败,跳过(已忽略): %s", e)
        return False
    except (TypeError, ValueError) as e:
        logger.warning("append message log 序列化失败,跳过(已忽略): %s", e)
        return False
