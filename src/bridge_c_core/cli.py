"""命令行入口工厂。

下游公司只需:

    from bridge_c_core.cli import make_cli
    from xxx_bridge_c.client import XxxClient

    main = make_cli(client_cls=XxxClient, env_prefix="XXX_", prog_name="XxxBridgeC")

支持两种运行方式:

- 默认: 守护轮询 ``{prefix}/inbox/pull``,落盘到 ``data/pending/``
- ``--once``: 仅请求一次 ``{prefix}/directory`` 并打印 JSON,做连通性自检
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Callable

from bridge_c_core.client import BaseClient
from bridge_c_core.daemon import run_daemon
from bridge_c_core.settings import Settings


def setup_logging(level: int = logging.INFO) -> None:
    """不覆盖宿主应用已有的 logging 配置。"""
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _autoload_dotenv() -> None:
    """启动时自动加载 ``.env``,优先级低于已存在的环境变量。

    查找顺序:当前工作目录 → 入口脚本同级目录。``python-dotenv`` 缺失或
    文件不存在时静默跳过,确保把 C 端作为库嵌入到宿主应用时不会出意外。
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    candidates = [Path.cwd() / ".env"]
    main_mod = sys.modules.get("__main__")
    main_file = getattr(main_mod, "__file__", None)
    if main_file:
        candidates.append(Path(main_file).resolve().parent / ".env")
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            load_dotenv(path, override=False)


def make_cli(
    *,
    client_cls: type[BaseClient],
    env_prefix: str,
    prog_name: str = "bridge-c",
) -> Callable[[], int]:
    """生成一个可用作 ``main()`` 的入口函数。"""

    def main() -> int:
        url_prefix = client_cls.URL_PREFIX
        parser = argparse.ArgumentParser(
            prog=prog_name,
            description=(
                f"{prog_name}: 轮询 {url_prefix}/inbox/pull,"
                f"写入本地 data/pending/"
            ),
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help=(
                f"不启动守护进程,仅请求一次 GET {url_prefix}/directory "
                f"并打印 JSON(连通性检查)"
            ),
        )
        parser.add_argument(
            "--no-interactive",
            action="store_true",
            help=f"无 TTY 时不要交互输入密钥(未设 {env_prefix}API_KEY 则退出)",
        )
        args = parser.parse_args()

        setup_logging()
        _autoload_dotenv()

        try:
            settings = Settings.from_env(
                env_prefix=env_prefix,
                interactive=not args.no_interactive,
                default_base_url=client_cls.DEFAULT_BASE_URL,
            )
        except SystemExit:
            raise
        except Exception as e:
            print(e, file=sys.stderr)
            return 2

        if args.once:
            try:
                with client_cls(
                    base_url=settings.base_url,
                    api_key=settings.api_key,
                    instance_id=settings.instance_id or None,
                    timeout=settings.request_timeout_sec,
                ) as c:
                    out = c.directory()
            except Exception as e:
                print(e, file=sys.stderr)
                return 1
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

        with client_cls(
            base_url=settings.base_url,
            api_key=settings.api_key,
            instance_id=settings.instance_id or None,
            timeout=settings.request_timeout_sec,
        ) as c:
            run_daemon(c, settings)
        return 0

    return main
