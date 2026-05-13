# 架构与设计取舍

写给维护 `bridge-c-core` 自身的人,以及想理解"为什么这么设计"的接入方工程师。

---

## 1. 一句话总览

> **把一个本应是消息队列的事情,用"HTTP 长轮询 + 本机文件系统"实现** —— 是因为客户机大多没有稳定公网 IP,既装不了 MQ 又开不出端口,只能由客户机主动出去拉。

---

## 2. 三层职责划分

| 层 | 它做什么 | 它不做什么 |
|---|---|---|
| **`bridge-c-core`**(本仓) | HTTP 客户端 / 守护轮询 / 本地落盘 / Settings / CLI 框架 / 协议规范 | 任何企业特定的 URL / Header / env 名 |
| **企业适配仓**(`acme_bridge_c`) | 只声明本企业的 5~6 个常量 | 任何业务逻辑 |
| **本机 Agent**(Hermes 等) | 读 `data/pending/*.json` 处理任务,自管删除/重命名 | 不与对端服务器直接通信 |

**不变量**:Agent 永远不导入 `bridge-c-core`。它**只跟文件系统说话**。这样 Agent 实现可以是 Python / Node / Go / shell 脚本,跟 C 端守护进程完全解耦。

---

## 3. 关键设计取舍

### 3.1 为什么用"文件系统当消息队列"

可选项:Unix socket、HTTP 本机端口、IPC、SQLite、Redis…

文件系统赢在:

- **跨语言、零依赖**:Agent 用啥写都行,只要会读文件
- **天然持久化**:进程崩了任务还在
- **天然支持多 Agent**:用文件改名做互斥,或者把 `data/pending/` 拆成多个子目录
- **可观察**:`ls data/pending` 就能看到积压

代价:不能精确 push,Agent 也是轮询(但本地轮询的延迟可以做到 < 100ms,可以接受)。

### 3.2 为什么 `record_id` 不在没值时用 `hash()` 兜底

Python 内置 `hash()` 在不同进程里**不一致**(`PYTHONHASHSEED` 默认随机)。如果守护进程重启,上一次跑时算出的 fallback id 跟这次不一样,**同一条记录会被写两次**,破坏去重不变量。

改用 `hashlib.sha256(canonical_json)[:16]`:跨进程稳定,跨机器稳定,跨 Python 版本稳定。

### 3.3 为什么写文件要"临时文件 + os.replace"

`Path.write_text()` 不是原子的。在写一半时进程被 `kill -9`,Agent 看到的就是半截 JSON,parse 必崩。

`os.replace(tmp, target)` 在 POSIX 和 Windows 上都是原子重命名(同一文件系统内)。Agent 看到的要么是完整 JSON,要么文件根本没出现。

### 3.4 为什么 client 同时提供 strict 和 relaxed 两套

- **strict**(`inbox_pull` / `inbox_ack` / `directory` / `submit` / `submit_to`):出错抛 `httpx.HTTPStatusError`。适合**库用法**,调用方需要明确处理失败。
- **relaxed**(`xxx_relaxed`):永远返回字典 `{"success": False, "http_status": ..., ...}`。适合**守护进程内部**,出错也得继续轮询,绝不能因为对端一次 500 让 daemon 退出。

两套并存的原因是它们各自的调用方期望不同。**不要让 strict 模式在守护进程里被误用 —— 一次 HTTP 异常就让 daemon 死掉是事故。**

### 3.5 为什么 `DEFAULT_BASE_URL` 写在企业适配仓里,而不是 env 必填

末端运维只想关心一件事:**api_key**。base_url 在企业生产环境里几乎不变,把它写在企业仓的代码里:

- 末端只需配 1 个 env 变量
- 切换 base_url 时,企业仓发新版本 + 客户 `pip install -U`,而不是给每台机器改 env
- env 优先级更高,作为"应急覆盖"通道仍然存在

### 3.6 为什么 `httpx.Client` 是长命的

旧代码每次 HTTP 调用都新建一个 `httpx.Client()`,连接池/TLS session 每次重建。守护进程每 5 秒一轮,每天 17,000 次,这种浪费没必要。

`BaseClient.__init__` 创建一个 `httpx.Client(timeout=...)` 并持有,`close()` 时显式释放。也实现了 `__enter__/__exit__`,方便 `with` 语法用。

### 3.7 为什么 `setup_logging` 检查已有 handler

如果 `bridge-c-core` 被嵌入到别的应用里(比如某个 dashboard 进程),那边可能已经配过 root logger。`logging.basicConfig` 的实现是"root logger 没 handler 才生效",但为了显式表达意图,我们写成:

```python
if logging.getLogger().handlers:
    return
logging.basicConfig(...)
```

不污染宿主。

---

## 4. 模块依赖关系

```
cli.py                          ← make_cli 工厂
  ↓
daemon.py(run_daemon, write_local_item, PollableInbox Protocol)
  ↓                                   ↑
client.py(BaseClient)            ← settings.py(Settings, from_env)
```

- `settings.py` 不依赖任何项目内模块。
- `client.py` 不依赖 `daemon.py`。
- `daemon.py` 不依赖 `client.py` 具体实现,只依赖 `PollableInbox` 这个 Protocol。
- `cli.py` 是粘合层,把以上拼起来。

**任何企业适配仓只需要 import `bridge_c_core` 这一个名字**(`BaseClient`、`make_cli`、`Settings`、`run_daemon` 都从这里导出)。

---

## 5. 已知 limitation 与未来方向

### L1. "Pull 即消费"语义可能丢消息

服务端如果是 Pull-on-consume(目前 Aidun 是),C 端写盘失败 → 记录丢失。
**缓解**:协议 v1 同时允许"显式 ack + 派发中超时回收"语义,见 [PROTOCOL.md §5](PROTOCOL.md)。
**未来**:v2 协议默认推荐显式 ack,并提供一个轻量服务端参考实现样板。

### L2. 多 daemon 实例并发写同一目录会冲突

如果同一台机器跑了两个 `python -m xxx_bridge_c`(配置一样),两个 daemon 都会拉同一批记录,都会尝试写同一个文件名。`os.replace` 是原子的所以不会损坏文件,但会浪费请求和 CPU,而且无意义。
**缓解**:同一机器只跑一个实例;或为每个 daemon 配不同的 `LOCAL_POOL_DIR`。
**未来**:可以加个文件锁(`fcntl` / Windows MutexEx)防止并发启动。当前不做。

### L3. 没有 push / SSE 机制,延迟下限 = 轮询间隔

服务端有新消息时,平均要等 `POLL_INTERVAL_SEC / 2` 秒才被拉到。最小 1 秒,默认 5 秒。
**未来**:加一个可选的 SSE 长连接通道,服务端有消息就推个 wake 信号,C 端立刻拉一次。**协议 v1 不要求服务端实现**,作为 enhancement。

### L4. 客户端没做请求重试/指数退避

`relaxed` 路径会吞掉 HTTP 错误并继续下一轮,但**不会**对失败请求做立刻重试。对短抖动不友好。
**未来**:加 `tenacity` 或手写一个指数退避包装,封装到 `BaseClient` 里。当前可以由企业适配仓显式包一层。

### L5. 没有对端服务端的协议一致性测试套件

接入新企业时,**只能靠 curl 抽查**对方服务端是否真符合 v1 协议。
**未来**:发布 `bridge-c-protocol-tests`,提供一份 pytest 套件,对方部署完后跑一遍即可验收。

---

## 6. 内核变更与版本号约定

`bridge-c-core` 遵循 SemVer:

| 变更类型 | 版本号 bump | 例子 |
|---|---|---|
| Bug fix,API 不变 | patch (`0.1.0` → `0.1.1`) | sha256 算法没变,但修了一个 race condition |
| 新增可选参数 / 新增方法 / 新增 endpoint | minor (`0.1.x` → `0.2.0`) | `BaseClient` 新增 `DEFAULT_BASE_URL` 类变量(2026-05) |
| 删除/重命名公开方法 / 改字段含义 / 协议 v1 → v2 | major (`0.x` → `1.0`) | 协议大版本升级,与 v2 配套 |

企业适配仓在 `pyproject.toml` 里建议把依赖锁到**当前 major 范围**:`bridge-c-core>=0.1,<0.2`。这样自动吃 bug fix,但不会被动升大版。
