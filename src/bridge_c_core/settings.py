"""Settings 数据类与 from_env 工厂。

每个公司项目共用一套字段语义,只换环境变量前缀。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from getpass import getpass


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name, "").strip()
    return v if v else default


@dataclass
class Settings:
    base_url: str
    api_key: str
    instance_id: str
    poll_interval_sec: float
    pull_limit: int
    local_pool_dir: str
    request_timeout_sec: float
    # 落盘成功后,可选地把整条记录 POST 给本机某个 HTTP 端点(常用于通知本机
    # Agent/Worker 立即处理新任务,典型场景是 hermes gateway 的 webhook URL)。
    # url 为空 = 关闭通知(默认,完全向后兼容)。
    notify_webhook_url: str = ""
    notify_webhook_secret: str = ""
    notify_webhook_timeout_sec: float = 5.0

    @classmethod
    def from_env(
        cls,
        *,
        env_prefix: str,
        interactive: bool = True,
        env_overrides: dict[str, str] | None = None,
        default_base_url: str = "",
    ) -> "Settings":
        """按约定的 env_prefix 读取配置。

        例如 env_prefix="KQ_POOL_" 会读取 KQ_POOL_BASE_URL / KQ_POOL_API_KEY ...

        - ``default_base_url`` 用于"企业专用仓内置默认 URL"场景:
          env 没设置时退化到该默认值,末端机器只需配 API_KEY。
        - ``env_overrides`` 用于个别公司想用不规则变量名时覆盖,例如
          ``{"instance_id": "C_INSTANCE_ID"}`` —— 仅在迁移老部署时使用,
          新接入的公司应该全部使用 ``{PREFIX}+后缀`` 的标准命名。
        """
        names = {
            "base_url": f"{env_prefix}BASE_URL",
            "api_key": f"{env_prefix}API_KEY",
            "instance_id": f"{env_prefix}INSTANCE_ID",
            "poll_interval_sec": f"{env_prefix}POLL_INTERVAL_SEC",
            "pull_limit": f"{env_prefix}PULL_LIMIT",
            "local_pool_dir": f"{env_prefix}LOCAL_POOL_DIR",
            "request_timeout_sec": f"{env_prefix}HTTP_TIMEOUT_SEC",
            "notify_webhook_url": f"{env_prefix}NOTIFY_WEBHOOK_URL",
            "notify_webhook_secret": f"{env_prefix}NOTIFY_WEBHOOK_SECRET",
            "notify_webhook_timeout_sec": (
                f"{env_prefix}NOTIFY_WEBHOOK_TIMEOUT_SEC"
            ),
        }
        if env_overrides:
            names.update(env_overrides)

        base = _env(names["base_url"], default_base_url).rstrip("/")
        if not base:
            print(
                f"请设置环境变量 {names['base_url']}(对端根 URL,无尾斜杠)",
                file=sys.stderr,
            )
            sys.exit(2)

        key = _env(names["api_key"], "")
        if not key and interactive and sys.stdin.isatty():
            key = getpass(f"{names['api_key']}(输入不回显): ").strip()
        if not key:
            print(
                f"未配置密钥:请设置环境变量 {names['api_key']},或在终端交互输入。",
                file=sys.stderr,
            )
            sys.exit(2)

        instance = _env(names["instance_id"], "")
        try:
            interval = float(_env(names["poll_interval_sec"], "5") or "5")
        except ValueError:
            interval = 5.0
        try:
            limit = int(_env(names["pull_limit"], "10") or "10")
        except ValueError:
            limit = 10
        pool_dir = _env(names["local_pool_dir"], "data/pending")
        try:
            timeout = float(_env(names["request_timeout_sec"], "60") or "60")
        except ValueError:
            timeout = 60.0

        notify_url = _env(names["notify_webhook_url"], "")
        notify_secret = _env(names["notify_webhook_secret"], "")
        try:
            notify_timeout = float(
                _env(names["notify_webhook_timeout_sec"], "5") or "5"
            )
        except ValueError:
            notify_timeout = 5.0

        return cls(
            base_url=base,
            api_key=key,
            instance_id=instance,
            poll_interval_sec=max(1.0, interval),
            pull_limit=max(1, min(limit, 100)),
            local_pool_dir=pool_dir,
            request_timeout_sec=max(5.0, timeout),
            notify_webhook_url=notify_url,
            notify_webhook_secret=notify_secret,
            notify_webhook_timeout_sec=max(1.0, notify_timeout),
        )
