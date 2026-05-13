"""C 端 HTTP 客户端基类。

每家公司项目只需子类化并设置 ``URL_PREFIX`` 与 ``INSTANCE_HEADER``,
其余 5 个 endpoint(inbox_pull / inbox_ack / directory / submit / submit_to)
自动可用,且每个都提供 *strict*(出错抛异常,适合库调用方)与
*relaxed*(出错返回 ``{"success": False, ...}``,适合守护进程轮询)两种风格。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, ClassVar

import httpx

from bridge_c_core.messages_log import append_outbound

logger = logging.getLogger(__name__)


class BaseClient:
    URL_PREFIX: ClassVar[str] = ""
    INSTANCE_HEADER: ClassVar[str] = "X-Bridge-Instance-Id"

    #: 企业专用仓建议在子类里写死 ``DEFAULT_BASE_URL``,这样末端部署机器只需配 API_KEY。
    #: 优先级:构造入参 > 环境变量 > 类默认值。
    DEFAULT_BASE_URL: ClassVar[str] = ""

    ENV_BASE_URL: ClassVar[str] = ""
    ENV_API_KEY: ClassVar[str] = ""
    ENV_INSTANCE_ID: ClassVar[str] = ""

    #: 子类可以覆盖这个 env 名,用来读取消息总账的落地路径。空字符串 / 未声明
    #: 代表"不读取 env";构造参数 ``message_log_path`` 仍然有效。
    ENV_MESSAGE_LOG_PATH: ClassVar[str] = ""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        instance_id: str | None = None,
        *,
        timeout: float = 60.0,
        message_log_path: str | None = None,
    ) -> None:
        if not self.URL_PREFIX:
            raise TypeError(f"{type(self).__name__} 必须声明 URL_PREFIX")

        self.base_url = (
            base_url
            or (os.environ.get(self.ENV_BASE_URL) if self.ENV_BASE_URL else None)
            or self.DEFAULT_BASE_URL
            or ""
        ).rstrip("/")
        self.api_key = (
            api_key
            or (os.environ.get(self.ENV_API_KEY) if self.ENV_API_KEY else None)
            or ""
        ).strip()
        self.instance_id = (
            instance_id
            or (os.environ.get(self.ENV_INSTANCE_ID) if self.ENV_INSTANCE_ID else None)
            or ""
        ).strip()

        if not self.base_url:
            raise ValueError(
                f"base_url is required (or set env {self.ENV_BASE_URL or '<未声明>'})"
            )
        if not self.api_key:
            raise ValueError(
                f"api_key is required (or set env {self.ENV_API_KEY or '<未声明>'})"
            )

        # 消息总账路径优先级:构造参数 > env > 空(关闭)。
        if message_log_path is None:
            env_v = (
                os.environ.get(self.ENV_MESSAGE_LOG_PATH)
                if self.ENV_MESSAGE_LOG_PATH
                else None
            )
            self.message_log_path = (env_v or "").strip()
        else:
            self.message_log_path = (message_log_path or "").strip()

        self._timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BaseClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        if self.instance_id:
            h[self.INSTANCE_HEADER] = self.instance_id
        return h

    def _full_url(self, path: str) -> str:
        return f"{self.base_url}{self.URL_PREFIX}{path}"

    def _request_strict(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        r = self._client.request(
            method,
            self._full_url(path),
            headers=self._headers(),
            json=json_body,
            params=params,
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("unexpected non-JSON object response")
        return data

    def _request_relaxed(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            r = self._client.request(
                method,
                self._full_url(path),
                headers=self._headers(),
                json=json_body,
                params=params,
            )
        except httpx.HTTPError as e:
            logger.warning("HTTP 请求异常 method=%s path=%s err=%s", method, path, e)
            return {"success": False, "error": str(e)}

        try:
            data: Any = r.json()
        except json.JSONDecodeError:
            data = {"raw": (r.text or "")[:2000]}

        if r.status_code >= 400:
            logger.warning("对端响应 http=%s body=%s", r.status_code, data)
            return {
                "success": False,
                "http_status": r.status_code,
                **(data if isinstance(data, dict) else {}),
            }
        if isinstance(data, dict):
            return data
        return {"success": True, "data": data}

    def inbox_pull(self, limit: int = 10) -> dict[str, Any]:
        return self._request_strict("GET", "/inbox/pull", params={"limit": limit})

    def inbox_pull_relaxed(self, limit: int = 10) -> dict[str, Any]:
        return self._request_relaxed("GET", "/inbox/pull", params={"limit": limit})

    def inbox_ack(self, record_id: str) -> dict[str, Any]:
        rid = (record_id or "").strip()
        if not rid:
            raise ValueError("record_id required")
        return self._request_strict(
            "POST", f"/inbox/{rid}/ack", json_body={}
        )

    def inbox_ack_relaxed(self, record_id: str) -> dict[str, Any]:
        rid = (record_id or "").strip()
        if not rid:
            return {"success": False, "detail": "record_id required"}
        return self._request_relaxed(
            "POST", f"/inbox/{rid}/ack", json_body={}
        )

    def directory(self) -> dict[str, Any]:
        return self._request_strict("GET", "/directory")

    def directory_relaxed(self) -> dict[str, Any]:
        return self._request_relaxed("GET", "/directory")

    # 这四个 submit 包装都在成功后调一次 append_outbound;失败/relaxed 失败
    # 不写日志,避免污染时间线。

    def submit(self, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._request_strict("POST", "/submit", json_body=body)
        self._log_outbound(body, resp, kind="submit")
        return resp

    def submit_relaxed(self, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._request_relaxed("POST", "/submit", json_body=body)
        if resp.get("success", True):
            self._log_outbound(body, resp, kind="submit")
        return resp

    def submit_to(self, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._request_strict("POST", "/submit_to", json_body=body)
        self._log_outbound(body, resp, kind="submit_to")
        return resp

    def submit_to_relaxed(self, body: dict[str, Any]) -> dict[str, Any]:
        resp = self._request_relaxed("POST", "/submit_to", json_body=body)
        if resp.get("success", True):
            self._log_outbound(body, resp, kind="submit_to")
        return resp

    def _log_outbound(
        self,
        body: dict[str, Any],
        response: dict[str, Any],
        *,
        kind: str,
    ) -> None:
        """append 一条 outbound 行;失败被 append_outbound 内部吞掉。"""
        if not self.message_log_path:
            return
        append_outbound(
            self.message_log_path,
            body,
            response,
            kind=kind,
            self_instance_id=self.instance_id,
        )
