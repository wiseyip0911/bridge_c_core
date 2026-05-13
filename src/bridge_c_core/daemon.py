"""通用守护循环 + 本地落盘。

设计成"协议无关":只要 ``client`` 提供 ``inbox_pull_relaxed`` 和
``inbox_ack_relaxed`` 两个方法即可被它驱动 —— ``BaseClient`` 默认满足该接口。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Protocol

from bridge_c_core.settings import Settings

logger = logging.getLogger(__name__)


class PollableInbox(Protocol):
    """守护进程只依赖这两个方法,方便单测 mock。"""

    def inbox_pull_relaxed(self, limit: int = ...) -> dict[str, Any]: ...
    def inbox_ack_relaxed(self, record_id: str) -> dict[str, Any]: ...


def _extract_record_id(record: dict[str, Any]) -> str:
    return str(record.get("record_id") or record.get("id") or "").strip()


def _stable_fallback_id(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def write_local_item(pool_dir: Path, record: dict[str, Any]) -> Path | None:
    """把单条记录原子写到 pool_dir/<rid>.json。

    - 优先使用 record_id / id 作为文件名
    - 缺失时用 sha256(canonical_json)[:16] 兜底(**重启后稳定**)
    - 写入采用 临时文件 + os.replace 保证原子性
    - 同名已存在视为已落盘,直接跳过(去重)
    """
    rid = _extract_record_id(record) or _stable_fallback_id(record)
    path = pool_dir / f"{rid}.json"
    if path.exists():
        return None

    pool_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    return path


def _iter_items(resp: dict[str, Any]) -> list[dict[str, Any]]:
    items = resp.get("items")
    if isinstance(items, list) and items:
        return [it for it in items if isinstance(it, dict)]
    nested = resp.get("data")
    if isinstance(nested, dict):
        inner = nested.get("items")
        if isinstance(inner, list):
            return [it for it in inner if isinstance(it, dict)]
    return []


def run_daemon(client: PollableInbox, settings: Settings) -> None:
    pool = Path(settings.local_pool_dir)
    pool.mkdir(parents=True, exist_ok=True)

    logger.info(
        "bridge-c-core daemon starting base=%s pool=%s interval=%ss",
        settings.base_url,
        pool.resolve(),
        settings.poll_interval_sec,
    )

    while True:
        try:
            resp = client.inbox_pull_relaxed(limit=settings.pull_limit)

            if not resp.get("success", True) and resp.get("http_status") == 404:
                logger.error(
                    "对端返回 404:可能尚未部署 /inbox/pull,或 base_url 路径不对。"
                )

            auto_ack = resp.get("auto_ack") is True
            for item in _iter_items(resp):
                written = write_local_item(pool, item)
                if written:
                    logger.info("已写入本地池 %s", written.name)
                rid = _extract_record_id(item)
                if rid and auto_ack:
                    ack_r = client.inbox_ack_relaxed(rid)
                    logger.debug("ack %s -> %s", rid, ack_r.get("success", ack_r))
        except Exception:
            logger.exception("轮询失败,%s 秒后重试", settings.poll_interval_sec)

        time.sleep(settings.poll_interval_sec)
