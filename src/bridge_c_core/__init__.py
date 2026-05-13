"""bridge_c_core: C 端守护进程通用内核。"""

from bridge_c_core.client import BaseClient
from bridge_c_core.daemon import (
    PollableInbox,
    notify_webhook,
    run_daemon,
    write_local_item,
)
from bridge_c_core.messages_log import append_inbound, append_outbound
from bridge_c_core.settings import Settings

__all__ = [
    "BaseClient",
    "PollableInbox",
    "Settings",
    "append_inbound",
    "append_outbound",
    "notify_webhook",
    "run_daemon",
    "write_local_item",
    "__version__",
]

__version__ = "0.1.5"
