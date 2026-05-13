# C 端使用规则(参考稿)

> 本文放在 `bridge-c-core` 仓库里,作为**所有企业 C 端仓**的应用侧参考。
> 各企业专用仓可直接复制为 `docs/USAGE.md`,再全仓把 `acme` / `Acme` / `ACME`、`AcmeClient` 等占位替换成该企业实际类名与环境前缀。
> 怎么把守护装上,见 [INSTALL.md](INSTALL.md)。

C 端守护进程做的事只有两件:

1. **拉(收件)**:周期性从对端拉本实例的待办任务,以 JSON 文件形式落到 `data/pending/`。本机应用读这个目录就行。
2. **不替你处理任务**:C 端从不解释任务内容,也从不替任何应用删文件。**消费完要不要删文件,是应用自己的事。**

---

## 1. 取任务:扫 `data/pending/`

### 1.1 任务文件结构

每条任务 = `data/pending/<record_id>.json`,内容固定结构:

```json
{
  "record_id": "721dddd0-ef10-4d3b-8e67-2908de3b4b7d",
  "instance_id": "yeweizhi",
  "correlation_id": "调用方提交时给的 id",
  "record_type": "task",
  "payload_json": {
    "input_text": "用户原始输入",
    "其他业务字段": "..."
  },
  "created_at": "2026-05-13T11:35:18Z"
}
```

字段含义:

| 字段 | 含义 | 应用关心吗 |
|---|---|---|
| `record_id` | 全局唯一 id | 关心(去重 / 日志关联) |
| `instance_id` | 本实例代号 | 一般不关心 |
| `correlation_id` | 调用方给的关联 id | **关心**(回投结果时原样带回) |
| `record_type` | 业务类型,由调用方约定 | 关心 |
| `payload_json` | 真正的业务载荷 | **关心** |
| `created_at` | 任务进池子的时间 | 仅供参考 |

### 1.2 最小消费循环

```python
from pathlib import Path
import json, time

POOL = Path("data/pending")

while True:
    for fp in sorted(POOL.glob("*.json")):
        record = json.loads(fp.read_text("utf-8"))
        try:
            handle(record)              # 你自己的业务处理
        except Exception:
            continue                     # 失败不删,下次重试
        else:
            fp.unlink()                  # 成功才删
    time.sleep(1)
```

### 1.3 守护进程的承诺

- **同一 `record_id` 绝不写两次**(基于 record_id 去重)。
- **绝不写半截 JSON**(临时文件 + 原子 rename)。应用看到的要么是完整文件,要么文件根本没出现。
- **守护或应用崩溃不丢消息**:文件落盘后就归应用管,不会因为守护重启而消失。

### 1.4 多 worker 并发消费同一目录

最简单的互斥姿势:把文件 rename 成"占用态"再处理。

```python
target = fp.with_suffix(f".processing.{os.getpid()}")
try:
    fp.rename(target)
except FileNotFoundError:
    continue                            # 被另一个 worker 抢了
record = json.loads(target.read_text("utf-8"))
# ... handle ...
target.unlink()
```

---

## 2. 投任务:Python 库用法

C 端不止能"收",也能"发"。在本机 Python 代码里:

```python
from acme_bridge_c import AcmeClient
```

`AcmeClient()` 不传参数时,会自动从 `.env` / 环境变量里读 `ACME_API_KEY` 和默认 `base_url`。

### 2.1 列实例(发任务前先确认对方代号)

```python
with AcmeClient() as c:
    print(c.directory())
# {'success': True, 'items': [{'instance_id': 'yeweizhi', ...}, ...]}
```

### 2.2 给自己的收件箱投一条(常用于自测)

```python
with AcmeClient() as c:
    r = c.submit({
        "correlation_id": "any-id",
        "input_text": "hi",
        "payload_json": {"foo": "bar"},
        "record_type": "task",
    })
# {'success': True, 'record_id': '...', 'correlation_id': 'any-id', ...}
```

下一轮拉取就会在 `data/pending/<record_id>.json` 看到它。

### 2.3 给指定接收方投一条(跨实例)

```python
with AcmeClient() as c:
    c.submit_to({
        "to_instance_id": "对方实例代号",     # 从 directory() 拿
        "correlation_id": "xyz",
        "input_text": "...",
        "payload_json": {"...": "..."},
        "record_type": "task",
    })
```

> ⚠ `Bearer` 始终是**发送方自己的** api_key,服务端凭 `to_instance_id` 路由。
> 你不需要、也不应该知道对方的 api_key。

### 2.4 处理完把结果回投给调用方(典型双向 agent 模式)

```python
def handle(record):
    result = do_my_business(record["payload_json"])
    with AcmeClient() as c:
        c.submit_to({
            "to_instance_id": record["payload_json"].get("reply_to") or "<调用方实例代号>",
            "correlation_id": record["correlation_id"],   # 原样带回
            "record_type": "result",
            "payload_json": {"ok": True, "result": result},
        })
```

> `correlation_id` 必须**原样带回**,调用方就靠它把请求和响应配对。

---

## 3. 用起来后的排错

| 现象 | 多半原因 | 怎么办 |
|---|---|---|
| `data/pending/` 一直没文件 | 服务端 inbox 是空的 | 正常,等真有人投。可以自己 `c.submit({...})` 投一条自测 |
| `directory()` 里没有自己的实例 | api_key 关联了别的实例 / 实例没注册 | 找对端管理员 |
| `submit_to` 返回 404 / "instance not found" | `to_instance_id` 拼错 | 先 `directory()` 拿准确的代号 |
| `data/pending/` 堆积越来越多 | 应用没在跑 / 应用处理跟不上 | 检查应用进程;考虑加 worker(见 §1.4) |
| `submit` 返回 200 但下一轮拉不到 | 投到了"别人"的实例 | `submit`(投自己) vs `submit_to`(投别人),别混 |
| 守护日志反复 `轮询失败` | 对端临时抽风 / 网络抖 | 守护自动重试,几分钟内属正常;持续异常找对端 |

### 3.1 调高日志级别看请求细节

```bash
python -c "import logging; logging.basicConfig(level=logging.DEBUG); \
           from acme_bridge_c.__main__ import main; main()"
```

### 3.2 看池子堆积

```bash
ls -la data/pending/        # 看积压
ls data/pending/ | wc -l    # 数量
```

---

## 4. 协议字段完整定义

完整 HTTP 协议、字段语义、错误码见 [PROTOCOL.md](PROTOCOL.md)。
在 C 端这层你只需要记住上面 §1 / §2 的几个 Python 用法就够了。
