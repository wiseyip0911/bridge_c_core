# bridge-c-core

C 端守护进程通用内核。封装一切**协议无关**的逻辑,各公司项目(`aidun_bridge_c` / `yujia_bridge_c` / 未来的 `xxx_bridge_c`)只需写很薄的适配层。

## 它做什么

每个公司的 C 端守护进程要做的事情都一样：

1. 周期性调用对端 `/{前缀}/v1/inbox/pull` 拉取条目
2. 把条目原子地写入本地 `data/pending/*.json` 供本机 Agent 消费
3. 同一条记录只写一次(基于 `record_id`,缺失时用 sha256 兜底)
4. 如果服务端 `auto_ack: true`,自动回 `/{前缀}/v1/inbox/{id}/ack`
5. 错误一律 warn 后继续轮询,绝不退出

`bridge-c-core` 实现了以上全部,**协议差异只剩三处**:

| 公司差异点        | 配置位置                                |
|-----------------|----------------------------------------|
| URL 前缀         | `BaseClient.URL_PREFIX`(子类覆写)       |
| 实例请求头        | `BaseClient.INSTANCE_HEADER`(子类覆写) |
| 环境变量前缀       | `make_cli(env_prefix=...)` 入参         |

## 接入新公司的最小模板

```python
# xxx_bridge_c/client.py
from bridge_c_core import BaseClient

class XxxClient(BaseClient):
    URL_PREFIX = "/xxx/v1"
    INSTANCE_HEADER = "X-Xxx-Instance-Id"
```

```python
# xxx_bridge_c/__main__.py
from bridge_c_core.cli import make_cli
from xxx_bridge_c.client import XxxClient

main = make_cli(client_cls=XxxClient, env_prefix="XXX_", prog_name="XxxBridgeC")

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
```

整个适配项目大约 30~50 行代码。

## 服务端协议要求(v1)

所有公司服务端必须实现以下 endpoint(`{prefix}` 由各公司自取,例如 `/c/v1`、`/kq-pool/v1`):

| 方法 + 路径                        | 用途                                     |
|----------------------------------|------------------------------------------|
| `GET  {prefix}/inbox/pull?limit=` | 拉取待处理条目,响应 `{"success": true, "items": [...], "auto_ack": bool?}` |
| `POST {prefix}/inbox/{id}/ack`    | 标记条目已处理                              |
| `GET  {prefix}/directory`         | 返回当前所有已启用实例 id 列表(连通性检查)     |
| `POST {prefix}/submit`            | 向**自己**收件箱投递                        |
| `POST {prefix}/submit_to`         | 向**指定 instance_id** 收件箱投递           |

鉴权:`Authorization: Bearer <api_key>`。实例 header:`X-{Prefix}-Instance-Id`(可选)。

## 环境变量

各公司项目通过 `env_prefix` 共享同一组变量名。以 `KQ_POOL_` 为例:

| 变量                          | 必填 | 默认值          | 说明                              |
|-----------------------------|----|-----------------|---------------------------------|
| `{PREFIX}BASE_URL`           | 是  | -               | 对端根 URL,无尾斜杠                |
| `{PREFIX}API_KEY`            | 是* | -               | 管理页生成的 api_key,TTY 下可交互输入 |
| `{PREFIX}INSTANCE_ID`        | 否  | -               | 实例代号(与凭证一致时建议设置)        |
| `{PREFIX}POLL_INTERVAL_SEC`  | 否  | `5`             | 轮询间隔秒                          |
| `{PREFIX}PULL_LIMIT`         | 否  | `10`            | 每次拉取上限(裁剪到 1..100)         |
| `{PREFIX}LOCAL_POOL_DIR`     | 否  | `data/pending`  | 本地落盘目录                         |
| `{PREFIX}HTTP_TIMEOUT_SEC`   | 否  | `60`            | HTTP 超时秒                         |

## 开发安装

```powershell
cd D:\BridgeCCore
py -3 -m pip install -e .[dev]
py -3 -m pytest -q
```
