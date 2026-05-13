# C 端部署与运维手册

> 本手册写给**在某企业客户机上跑 C 端守护进程**的运维/工程师。
> 假设这家企业已经接入完毕,你拿到的是一份具体的企业仓库,例如 `aidun_bridge_c` / `yujia_bridge_c` / 未来某个 `acme_bridge_c`。
> 下文示例用 `acme_bridge_c` + env 前缀 `ACME_`。请把它换成你拿到的实际仓库名和前缀。

---

## 0. 准备

- **Python ≥ 3.10**(`python3 --version` 或 Windows 上 `py -3 --version`)
- 客户机能访问对端服务器(`curl -I <BASE_URL>` 应有响应)
- 一个由对方管理员发给你的 `api_key`(只会出现一次,丢了重新生成一个)

---

## 1. 安装

```bash
git clone <企业仓库地址>
cd acme_bridge_c
python -m pip install .
```

> 这会同时拉取 `bridge-c-core` 内核。建议在自己的虚拟环境里装,不污染系统 Python:
> `python -m venv .venv && source .venv/bin/activate`(Linux/Mac)
> `py -3 -m venv .venv && .venv\Scripts\activate`(Windows)
>
> 不强制用 venv,但多机部署若各机器 Python 环境不一致,venv 能避免依赖冲突。

---

## 2. 配置(最简情况只需 1 个)

```bash
export ACME_API_KEY=你的apikey
```

这就够了。`ACME_BASE_URL` 已经内置在仓库里(企业默认地址)。

### 完整环境变量清单

| 变量                          | 必填 | 默认值          | 何时需要改                            |
|-----------------------------|----|-----------------|------------------------------------|
| `ACME_API_KEY`                | 是  | -               | 必填                                |
| `ACME_BASE_URL`               | 否  | 仓库内置        | 对端临时切换域名/协议时覆盖              |
| `ACME_INSTANCE_ID`            | 否  | -               | 服务端要求与凭证分开展示实例时填写        |
| `ACME_POLL_INTERVAL_SEC`      | 否  | `5`             | 想拉得更快或更慢                       |
| `ACME_PULL_LIMIT`             | 否  | `10`            | 单次拉取条数上限(1..100)              |
| `ACME_LOCAL_POOL_DIR`         | 否  | `data/pending`  | 想把任务文件放到别处                    |
| `ACME_HTTP_TIMEOUT_SEC`       | 否  | `60`            | 网络慢/对端慢时调大                     |

---

## 3. 三种运行方式

### 3.1 连通性自检(部署第一步必做)

```bash
python -m acme_bridge_c --once
```

期望输出:一份 JSON,列出当前服务端所有已启用实例。例如:

```json
{
  "success": true,
  "items": [{"instance_id": "yeweizhi", "remark": "", "created_at": "2026-05-12T23:10:49"}],
  "count": 1
}
```

如果这里就报错或挂起,**先解决这一步再说**,不要直接上 daemon。常见错误见 §6。

### 3.2 守护轮询(日常运行)

```bash
python -m acme_bridge_c
```

会一直跑,直到 Ctrl+C。日志走 stderr,长这样:

```
2026-05-13 11:33:17,579 INFO bridge_c_core.daemon bridge-c-core daemon starting base=http://c.acme.example pool=D:\acme_bridge_c\data\pending interval=5.0s
2026-05-13 11:33:17,627 INFO httpx HTTP Request: GET http://c.acme.example/acme/v1/inbox/pull?limit=10 "HTTP/1.1 200 OK"
2026-05-13 11:33:17,631 INFO bridge_c_core.daemon 已写入本地池 4825814a-1976-47d1-975d-560bd2a9b456.json
```

### 3.3 无 TTY 部署(systemd / supervisor / Windows 计划任务)

```bash
python -m acme_bridge_c --no-interactive
```

`--no-interactive` 防止在没有终端的环境下尝试 `getpass` 卡住。**必须提前把 `ACME_API_KEY` 设到环境里**,否则进程会 `exit 2`。

systemd 单元示例:

```ini
# /etc/systemd/system/acme-bridge-c.service
[Unit]
Description=Acme Bridge C
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/acme_bridge_c
Environment=ACME_API_KEY=...
ExecStart=/opt/acme_bridge_c/.venv/bin/python -m acme_bridge_c --no-interactive
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## 4. 任务文件长什么样

每条任务作为一个 JSON 文件落到 `data/pending/<record_id>.json`,例如:

```json
{
  "record_id": "721dddd0-ef10-4d3b-8e67-2908de3b4b7d",
  "instance_id": "yeweizhi",
  "correlation_id": "bridge-c-test-002",
  "record_type": "test",
  "payload_json": {
    "tag": "verify-landing",
    "input_text": "second smoke test"
  },
  "created_at": "2026-05-13T11:35:18Z"
}
```

### 本机 Agent 怎么消费

最朴素的方式:**轮询目录**

```python
from pathlib import Path
import json, time

POOL = Path("data/pending")

while True:
    for fp in sorted(POOL.glob("*.json")):
        record = json.loads(fp.read_text("utf-8"))
        try:
            handle(record)
        except Exception:
            continue  # 暂不删,下次重试
        else:
            fp.unlink()  # 处理完删文件
    time.sleep(1)
```

要点:

- **同一个 record_id 不会被写两次**(`bridge-c-core` 用 sha256/原子写保证去重和不写半截)。
- Agent 自己负责"处理成功就删文件"。如果 Agent 也崩了,下次启动还能看到这个文件。
- 多个 Agent 同时消费同一目录时,需要自己实现互斥(简单做法:重命名 `*.json` → `*.processing.<pid>` 再处理)。

---

## 5. 投递消息(C 端作为发送方)

C 端不只能"收",也能"发"。在本机的 Python 代码里:

```python
from acme_bridge_c import AcmeClient

with AcmeClient() as c:               # 自动从 env 读 API_KEY + 默认 base_url
    r = c.submit({
        "correlation_id": "any-id",
        "input_text": "hi",
        "payload_json": {"foo": "bar"},
        "record_type": "task",
    })
    print(r)
```

或发给指定接收方:

```python
c.submit_to({
    "to_instance_id": "其他实例代号",
    "correlation_id": "xyz",
    "input_text": "...",
    "payload_json": {...},
})
```

---

## 6. 排错

| 现象 | 多半原因 | 怎么办 |
|---|---|---|
| `--once` 卡住,无响应 | 对端 URL 不通 / 防火墙 | `curl -I <BASE_URL>` 看看;改 `ACME_BASE_URL` 临时覆盖 |
| `请设置环境变量 ACME_API_KEY` 然后退出 | 没设 API_KEY,且无 TTY | 设环境变量,或在 TTY 下交互输入 |
| HTTP 401 | api_key 错 / 被禁用 | 管理员后台确认状态,必要时重新生成 |
| HTTP 404 + 日志说"可能尚未部署 /inbox/pull" | 服务端路径前缀不对 | 跟服务端确认,临时 `ACME_BASE_URL` 切到正确域名 |
| `data/pending/` 一直没有文件 | 服务端 inbox 是空的 / 实例代号没注册 | `--once` 看 `directory` 是否包含你预期的实例;让对方往你的 inbox 投一条测试消息 |
| 日志里反复"轮询失败" | 对端临时抽风 / 证书问题 | daemon 会自动重试,等几分钟;持续异常去查对端日志 |
| 客户机时间错乱导致 TLS 失败 | 系统时间不准 | `systemctl status systemd-timesyncd` / `w32tm /query /status` 校时 |

### 进一步排查

把日志级别调到 DEBUG:

```bash
# Linux/Mac
PYTHONLOGGING_LEVEL=DEBUG python -m acme_bridge_c
```

不过当前实现的 `setup_logging` 没有走环境变量,要 DEBUG 信息最简单的办法是:

```python
import logging; logging.basicConfig(level=logging.DEBUG)
# 然后再 import 跑 daemon
```

---

## 7. 升级

```bash
cd acme_bridge_c
git pull
python -m pip install -U .
```

`bridge-c-core` 的小版本升级是向后兼容的(SemVer)。如果企业仓 bump 了内核大版本(`bridge-c-core 1.x → 2.x`),需要走 release notes,可能涉及协议更换。
