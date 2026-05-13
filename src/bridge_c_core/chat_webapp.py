"""通用本机消息看板(FastAPI + 单页 HTML)。

为什么放在 core?
================

`bridge-c-core` 已经把 ``messages.jsonl`` 这条**通用**总账落到位:守护进程
落 inbound、``BaseClient`` 落 outbound。一旦每家公司的 ``*_bridge_c`` 都基于
core 实现客户端,他们也都自动获得了这条总账;那么"按 peer 看时间线 + 直接
发消息"这件事本质上**也是通用的**,没有任何 aidun 专属逻辑可言。把它沉到
core 之后,新公司接入 C 端时,只要给一个客户端工厂指针即可零成本拥有本机
看板。

设计要点
========

- **客户端工厂注入**: core 不知道也不应该知道具体哪家的 ``BaseClient`` 子类。
  通过 ``BRIDGE_C_CLIENT_FACTORY=<module>:<ClassName>`` 环境变量(或 CLI
  ``--client-factory``)告诉 webapp 该用谁。例:
  ``BRIDGE_C_CLIENT_FACTORY=aidun_bridge_c:KqPoolClient``。
- **路径解析**: 总账位置优先级 = 显式 env ``BRIDGE_C_MESSAGE_LOG_PATH``
  > 工厂类的 ``ENV_MESSAGE_LOG_PATH`` 指向的 env > 退化到 ``cwd/data/messages.jsonl``。
  各家适配器可以继续用自己的 env 名(例如 ``KQ_POOL_MESSAGE_LOG_PATH``)而无需
  改 core,因为类已经声明过 ``ENV_MESSAGE_LOG_PATH``。
- **仅本机**: 默认 ``127.0.0.1`` + 无鉴权;不要暴露到公网。
- **不强加 schema**: ``/api/send`` 的 body 只是简单转换成 ``submit_to``,
  ``payload_json.channel`` 默认 ``chat``,与既有约定一致。

启动::

    bridge-c-chat-web --client-factory aidun_bridge_c:KqPoolClient
    # 或 export BRIDGE_C_CLIENT_FACTORY=aidun_bridge_c:KqPoolClient && bridge-c-chat-web

适配器 thin wrapper 模式
=========================

每家公司只需写一个 4 行的入口::

    # aidun_bridge_c/chat_webapp.py
    import os
    from bridge_c_core.chat_webapp import main as core_main

    def main(argv=None):
        os.environ.setdefault("BRIDGE_C_CLIENT_FACTORY", "aidun_bridge_c:KqPoolClient")
        return core_main(argv)

然后在 pyproject 里挂个 ``aidun-chat-web = "aidun_bridge_c.chat_webapp:main"``
即可让最终用户继续敲 ``aidun-chat-web``(品牌不变,逻辑全在 core)。
"""
from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# .env / 路径
# ---------------------------------------------------------------------------

ENV_FACTORY = "BRIDGE_C_CLIENT_FACTORY"
ENV_MESSAGE_LOG_PATH = "BRIDGE_C_MESSAGE_LOG_PATH"


def _candidate_dirs() -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for base in [Path.cwd(), Path(__file__).resolve().parent]:
        for parent in [base, *base.parents]:
            if parent in seen:
                continue
            seen.add(parent)
            out.append(parent)
    return out


def _autoload_dotenv() -> None:
    """从 cwd / 本文件父目录一路向上找 .env 加载到 os.environ。

    缺包 / 缺文件都安静跳过。一旦找到第一份 .env 就停。
    """
    try:
        from dotenv import load_dotenv  # type: ignore

        for parent in _candidate_dirs():
            cand = parent / ".env"
            if cand.exists():
                load_dotenv(cand, override=False)
                return
        return
    except Exception:
        pass

    for parent in _candidate_dirs():
        cand = parent / ".env"
        if not cand.exists():
            continue
        try:
            for raw in cand.read_text("utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass
        return


# ---------------------------------------------------------------------------
# Client 工厂解析
# ---------------------------------------------------------------------------

def _load_client_factory():
    """解析 ``BRIDGE_C_CLIENT_FACTORY=<module>:<Class>`` 并返回类对象。"""
    spec = os.environ.get(ENV_FACTORY, "").strip()
    if not spec:
        raise RuntimeError(
            "未配置客户端工厂:请设置环境变量 "
            f"{ENV_FACTORY}=<module>:<ClassName>(例如 "
            "aidun_bridge_c:KqPoolClient),或通过 --client-factory 传入。"
        )
    if ":" not in spec:
        raise RuntimeError(
            f"{ENV_FACTORY}={spec!r} 格式错误,应为 'module.path:ClassName'。"
        )
    mod_name, _, cls_name = spec.partition(":")
    try:
        module = importlib.import_module(mod_name.strip())
    except ImportError as e:
        raise RuntimeError(f"无法导入模块 {mod_name!r}: {e}") from e
    try:
        cls = getattr(module, cls_name.strip())
    except AttributeError as e:
        raise RuntimeError(
            f"模块 {mod_name!r} 里没有 {cls_name!r} 这个名字。"
        ) from e
    return cls


def _load_client():
    """实例化客户端;失败抛 RuntimeError(由 FastAPI 转 502)。"""
    factory = _load_client_factory()
    try:
        return factory()
    except ValueError as e:
        raise RuntimeError(
            f"{factory.__name__} 构造失败(常见原因:API_KEY 等环境变量未设): {e}"
        ) from e


def _self_instance_id() -> str:
    """优先用工厂类声明的 ``ENV_INSTANCE_ID``;读不到再用裸 env 兜底。"""
    try:
        factory = _load_client_factory()
        env_name = getattr(factory, "ENV_INSTANCE_ID", "") or ""
        if env_name:
            v = os.environ.get(env_name, "").strip()
            if v:
                return v
    except Exception:
        pass
    return os.environ.get("BRIDGE_C_INSTANCE_ID", "").strip()


# ---------------------------------------------------------------------------
# 消息总账读取
# ---------------------------------------------------------------------------

def resolve_message_log_path() -> Path:
    """返回 ``messages.jsonl`` 的绝对路径。

    优先级:
      1. ``BRIDGE_C_MESSAGE_LOG_PATH`` env(通用变量,跨适配器统一)
      2. 工厂类的 ``ENV_MESSAGE_LOG_PATH`` 指向的 env(例如 aidun 的
         ``KQ_POOL_MESSAGE_LOG_PATH``)
      3. ``<cwd>/data/messages.jsonl``
    """
    _autoload_dotenv()
    v = os.environ.get(ENV_MESSAGE_LOG_PATH, "").strip()
    if v:
        p = Path(v)
        return p if p.is_absolute() else (Path.cwd() / p).resolve()
    try:
        factory = _load_client_factory()
        env_name = getattr(factory, "ENV_MESSAGE_LOG_PATH", "") or ""
        if env_name:
            v2 = os.environ.get(env_name, "").strip()
            if v2:
                p = Path(v2)
                return p if p.is_absolute() else (Path.cwd() / p).resolve()
    except Exception:
        pass
    return (Path.cwd() / "data" / "messages.jsonl").resolve()


def read_message_log_rows(log_path: Path | None = None) -> list[dict[str, Any]]:
    """逐行解析 ``messages.jsonl``,跳过坏行,失败返回 ``[]``。"""
    p = log_path or resolve_message_log_path()
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        text = p.read_text("utf-8")
    except OSError as e:
        logger.warning("读消息总账失败: %s", e)
        return []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

def _static_dir() -> Path:
    return Path(__file__).resolve().parent / "web_static"


def _serialize_row(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": r.get("ts") or "",
        "direction": r.get("direction") or "",
        "peer_instance_id": r.get("peer_instance_id") or "",
        "text": r.get("text") or "",
        "channel": r.get("channel") or "",
        "record_type": r.get("record_type") or "",
        "correlation_id": r.get("correlation_id") or "",
        "record_id": r.get("record_id") or "",
    }


def _build_snapshot(peer: str | None) -> dict[str, Any]:
    rows = read_message_log_rows()
    items: list[dict[str, Any]] = []
    try:
        client = _load_client()
        try:
            resp = client.directory_relaxed()
        finally:
            client.close()
        if isinstance(resp, dict) and resp.get("success", True):
            raw = resp.get("items")
            if isinstance(raw, list):
                items = [it for it in raw if isinstance(it, dict)]
    except Exception as e:
        logger.warning("directory_relaxed 失败(看板仍可用 log): %s", e)

    dir_ids = {str(it.get("instance_id") or "").strip() for it in items}
    dir_ids.discard("")

    last_by: dict[str, dict[str, Any]] = {}
    for r in rows:
        pid = str(r.get("peer_instance_id") or "").strip()
        if not pid:
            continue
        ts = str(r.get("ts") or "")
        cur = last_by.get(pid)
        if cur is None or ts > str(cur.get("ts") or ""):
            last_by[pid] = r

    all_ids = sorted(dir_ids | set(last_by.keys()))
    summaries: list[dict[str, Any]] = []
    for pid in all_ids:
        row = last_by.get(pid)
        summaries.append(
            {
                "peer": pid,
                "last_ts": row.get("ts", "") if row else "",
                "last_preview": (str(row.get("text") or ""))[:120] if row else "",
                "last_direction": str(row.get("direction") or "") if row else "",
                "last_record_type": str(row.get("record_type") or "")
                if row else "",
            }
        )
    summaries.sort(key=lambda s: s["last_ts"] or "", reverse=True)

    thread: list[dict[str, Any]] = []
    if peer:
        p = peer.strip()
        for r in rows:
            if str(r.get("peer_instance_id") or "") == p:
                thread.append(_serialize_row(r))
        thread.sort(key=lambda m: m["ts"])

    return {
        "log_path": str(resolve_message_log_path()),
        "self_instance_id": _self_instance_id(),
        "directory": items,
        "peer_summaries": summaries,
        "thread": thread,
        "peer": peer or "",
    }


class SendBody(BaseModel):
    to: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    channel: str = Field(default="chat", min_length=1)


app = FastAPI(title="bridge-c-chat-web", version="1.0")


@app.get("/")
def index() -> FileResponse:
    html = _static_dir() / "index.html"
    if not html.exists():
        raise HTTPException(
            status_code=500,
            detail=f"缺少静态页: {html}",
        )
    return FileResponse(html, media_type="text/html; charset=utf-8")


@app.get("/api/snapshot")
def api_snapshot(peer: str = Query(default="")) -> JSONResponse:
    return JSONResponse(_build_snapshot(peer or None))


@app.post("/api/send")
def api_send(body: SendBody) -> JSONResponse:
    cid = f"web-{uuid.uuid4().hex[:10]}"
    payload = {
        "channel": body.channel.strip(),
        "input_text": body.text,
    }
    me = _self_instance_id()
    if me:
        payload.setdefault("source", me)
    req = {
        "to_instance_id": body.to.strip(),
        "correlation_id": cid,
        "record_type": "task",
        "payload_json": payload,
    }
    try:
        client = _load_client()
        try:
            resp = client.submit_to(req)
        finally:
            client.close()
    except Exception as e:
        logger.exception("submit_to 失败")
        raise HTTPException(status_code=502, detail=str(e)) from e

    rid = str(resp.get("record_id") or "")
    return JSONResponse(
        {
            "success": True,
            "record_id": rid,
            "correlation_id": cid,
            "response": resp,
        }
    )


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bridge-c-chat-web",
        description=(
            "本机消息看板(双栏 + 1.5s 轮询)。需通过 "
            f"{ENV_FACTORY}=<module>:<Class> 或 --client-factory 指定客户端工厂。"
        ),
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听地址,默认仅本机 loopback",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8645,
        help="监听端口,默认 8645",
    )
    parser.add_argument(
        "--client-factory",
        default="",
        help=(
            "客户端工厂指针,格式 module.path:ClassName。"
            "也可走环境变量 " + ENV_FACTORY + "。"
        ),
    )
    args = parser.parse_args(argv)

    _autoload_dotenv()
    if args.client_factory:
        os.environ[ENV_FACTORY] = args.client_factory.strip()

    if not os.environ.get(ENV_FACTORY, "").strip():
        sys.stderr.write(
            f"[ERR] 缺少客户端工厂。请用 --client-factory module:Cls 或设置 "
            f"{ENV_FACTORY}=module:Cls。\n"
        )
        return 2

    # 先 dry-run 验证一次,把"导入失败 / 类不存在"的错误提前到启动期。
    try:
        _load_client_factory()
    except Exception as e:
        sys.stderr.write(f"[ERR] 客户端工厂解析失败: {e}\n")
        return 2

    try:
        import uvicorn
    except ImportError as e:
        sys.stderr.write(
            "[ERR] 缺少 uvicorn/fastapi。bridge-c-core 已声明这两项为必依赖,"
            "请重新 pip install。\n"
            f"详情: {e}\n"
        )
        return 1

    url = f"http://{args.host}:{args.port}/"
    print(f"bridge-c-chat-web 已启动: {url}")
    print(f"  client_factory = {os.environ[ENV_FACTORY]}")
    print(f"  message_log    = {resolve_message_log_path()}")
    print("  Ctrl+C 停止服务。")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
