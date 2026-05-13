# bridge-c-core

**C 端守护进程通用内核。** 为多企业(`aidun_bridge_c` / `yujia_bridge_c` / 未来的 `xxx_bridge_c`)提供同一套底层实现,各企业的 C 端项目只写 **30~50 行**协议差异声明即可上线。

---

## 它解决什么问题

大多数客户机器没有稳定公网 IP,Agent(比如 Hermes)之间无法直接相连。每家企业的服务端开了一个"实例池"中转,C 端守护进程在客户机本地长轮询这个池子,拿到任务后写入本地目录给 Agent 消费。

各企业服务端的接口长得几乎一样,只是路径前缀、请求头、env 变量名不同。**`bridge-c-core` 把"几乎一样"的部分一次性写好**,各企业仓库只声明"不一样"的三件事即可。

```
            ┌──────────────────────────────────────┐
            │           bridge_c_core              │
            │  (1 份, 上 PyPI 或私有源)             │
            │                                       │
            │  - 轮询循环                            │
            │  - sha256 + 原子写本地文件             │
            │  - Settings + 边界裁剪                  │
            │  - HTTP 基类(strict / relaxed 配对)    │
            │  - CLI 模板 (make_cli)                  │
            │  - PROTOCOL 规范(对端服务器需遵循)      │
            └──────────┬──────────────┬─────────────┘
                       │              │
              ┌────────▼──────┐ ┌─────▼────────────┐ ┌──── 新企业 ────┐
              │ yujia_bridge_c│ │ aidun_bridge_c   │ │ xxx_bridge_c    │
              │   /c/v1       │ │ /kq-pool/v1      │ │ /xxx/v1         │
              │   C_BRIDGE_*  │ │ KQ_POOL_*        │ │ XXX_*           │
              │   ~30 行代码  │ │ ~30 行代码       │ │ ~30 行代码       │
              └───────────────┘ └──────────────────┘ └─────────────────┘
```

---

## 文档导航

文档刻意拆成两条互不重叠的线,**装的事在 INSTALL,用的事在 USAGE**,避免 agent / 新人把"启动守护"和"投递消息"搞混:

| 看你是谁 | 看哪份 |
|---|---|
| 第一次在某企业客户机上**装并跑起** C 端的运维 | [docs/INSTALL.md](docs/INSTALL.md) |
| 守护已经在跑,要把它**接入自家应用**的开发者(hermes / 业务后端等) | [docs/USAGE.md](docs/USAGE.md) |
| 把某家**新企业**接入这套系统(写新仓) | [docs/INTEGRATION.md](docs/INTEGRATION.md) + [`bridge-c-template`](https://github.com/wiseyip0911/bridge-c-template) |
| 给企业**服务端**实现/调整接口 | [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| 维护 `bridge-c-core` 本身,想理解设计取舍 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |

> 上面 INSTALL / USAGE 是**中性版**,以 `acme` / `ACME` 占位。
> 企业仓库(例如 `aidun_bridge_c`、`yujia_bridge_c`)发布前,会照模板把占位符替换成自家代号,末端用户拿到的是已替换好的具体版本。

---

## 三分钟示例(新企业接入)

如果未来要接入一家新企业(假设代号 `acme`,服务端地址 `https://api.acme.example.com`,协议路径 `/acme/v1`):

```python
# acme_bridge_c/client.py  ——  全部内容
from bridge_c_core import BaseClient

class AcmeClient(BaseClient):
    URL_PREFIX = "/acme/v1"
    INSTANCE_HEADER = "X-Acme-Instance-Id"
    DEFAULT_BASE_URL = "https://api.acme.example.com"

    ENV_BASE_URL = "ACME_BASE_URL"
    ENV_API_KEY = "ACME_API_KEY"
    ENV_INSTANCE_ID = "ACME_INSTANCE_ID"
```

```python
# acme_bridge_c/__main__.py  ——  全部内容
from bridge_c_core.cli import make_cli
from acme_bridge_c.client import AcmeClient

main = make_cli(client_cls=AcmeClient, env_prefix="ACME_", prog_name="AcmeBridgeC")

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
```

末端机器只需:

```bash
git clone <acme_bridge_c 仓库地址>
cd acme_bridge_c
pip install .
export ACME_API_KEY=你的apikey
python -m acme_bridge_c
```

详细的接入步骤(含模板克隆、发布)见 [docs/INTEGRATION.md](docs/INTEGRATION.md)。

---

## 开发

```powershell
cd D:\BridgeCCore
py -3 -m pip install -e .[dev]
py -3 -m pytest -q
```

26 个测试覆盖:`write_local_item`(sha256 稳定性 / 去重 / 原子写)、`BaseClient`(URL 拼接 / Headers / strict-relaxed)、`run_daemon`(轮询 / auto_ack / 嵌套 items)、`Settings.from_env`(env / 默认值 / 优先级)。

---

## 当前状态

| 项 | 状态 |
|---|---|
| 协议 v1 | 稳定,详见 [PROTOCOL.md](docs/PROTOCOL.md) |
| 内核 API | 0.1.x,SemVer:接口不变 bump patch,接口扩展 bump minor,破坏性改动 bump major |
| 接入实例 | `aidun_bridge_c` ✅ 已上线 / `yujia_bridge_c` 待迁移 |
| 已知 limitation | 服务端是"pull 即消费"语义,无显式 ack 重投机制,见 [ARCHITECTURE.md](docs/ARCHITECTURE.md) |
