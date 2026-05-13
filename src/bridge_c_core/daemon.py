"""通用守护循环 + 本地落盘。

设计成"协议无关":只要 ``client`` 提供 ``inbox_pull_relaxed`` 和
``inbox_ack_relaxed`` 两个方法即可被它驱动 —— ``BaseClient`` 默认满足该接口。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Protocol

import httpx

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


def notify_webhook(
    url: str,
    record: dict[str, Any],
    *,
    secret: str = "",
    timeout: float = 5.0,
    record_id: str = "",
) -> bool:
    """把单条记录 POST 给本机 webhook(典型场景:通知本机 Agent 立即处理)。

    设计原则:
    - **永不抛**:任何异常都被吞掉并记日志(任务文件已经落盘,push 失败不应
      影响主循环;消费者随时可以用 list/poll 兜底)。
    - **HMAC 签名兼容 GitHub 风格**:secret 非空时,在 ``X-Hub-Signature-256``
      头里写 ``sha256=<hex>``,这样 hermes-agent 等支持 GitHub 协议的 webhook
      adapter 可以直接校验。如果 secret 是 ``INSECURE_NO_AUTH``,不签名,留给
      接收方按其约定跳过校验(仅供本机 loopback 调试)。
    - **携带 X-Request-ID = record_id**:接收方据此实现幂等(重复 push 同一条
      不会触发两次 agent run)。

    返回 True 当且仅当对端响应 2xx。其它情况返回 False 但不抛。
    """
    if not url:
        return False

    try:
        body = json.dumps(record, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as e:
        logger.warning("notify_webhook 序列化失败,跳过: %s", e)
        return False

    headers: dict[str, str] = {"Content-Type": "application/json"}
    rid = (record_id or "").strip() or str(
        record.get("record_id") or record.get("id") or ""
    ).strip()
    if rid:
        headers["X-Request-ID"] = rid
        headers["X-GitHub-Delivery"] = rid

    if secret and secret != "INSECURE_NO_AUTH":
        sig = "sha256=" + hmac.new(
            secret.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()
        headers["X-Hub-Signature-256"] = sig

    try:
        r = httpx.post(url, content=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        logger.warning("notify_webhook POST 失败(已忽略): %s", e)
        return False

    if r.status_code >= 400:
        logger.warning(
            "notify_webhook 对端返回 %s,body=%s",
            r.status_code,
            (r.text or "")[:300],
        )
        return False

    logger.debug("notify_webhook 已通知 rid=%s status=%s", rid, r.status_code)
    return True


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

    notify_url = (settings.notify_webhook_url or "").strip()
    if notify_url:
        logger.info(
            "bridge-c-core daemon starting base=%s pool=%s interval=%ss "
            "notify_webhook=%s",
            settings.base_url,
            pool.resolve(),
            settings.poll_interval_sec,
            notify_url,
        )
    else:
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
                    if notify_url:
                        notify_webhook(
                            notify_url,
                            item,
                            secret=settings.notify_webhook_secret,
                            timeout=settings.notify_webhook_timeout_sec,
                            record_id=_extract_record_id(item),
                        )
                rid = _extract_record_id(item)
                if rid and auto_ack:
                    ack_r = client.inbox_ack_relaxed(rid)
                    logger.debug("ack %s -> %s", rid, ack_r.get("success", ack_r))
        except Exception:
            logger.exception("轮询失败,%s 秒后重试", settings.poll_interval_sec)

        time.sleep(settings.poll_interval_sec)
