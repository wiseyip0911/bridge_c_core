# C 端 ↔ 服务端 协议规范 v1

任何接入 `bridge-c-core` 的企业服务端**必须**遵循此规范。规范是版本化的:不向前兼容的改动需要 bump 到 v2。

> 本文用 `{prefix}` 表示企业自定义的 URL 前缀,例如 `/c/v1`(誉佳)、`/kq-pool/v1`(Aidun)、`/acme/v1`(示例)。
> 同理用 `{Prefix}` 表示请求头里对应的驼峰式前缀,例如 `C`、`Kq-Pool`、`Acme`。

---

## 1. 鉴权

所有请求必须带:

```
Authorization: Bearer <api_key>
```

`api_key` 由服务端管理后台为每个 C 端实例生成,仅出现一次。

可选请求头:

```
X-{Prefix}-Instance-Id: <instance_id>
```

用于当一个 `api_key` 与一个具体实例代号是分开管理的场景。**核心约定:服务端的鉴权决策只看 `Authorization`,实例头仅作为日志/调试线索。**

服务端必须额外支持:

```
Accept: application/json
```

---

## 2. 端点清单

| 方法   | 路径                              | 用途                                          |
|--------|-----------------------------------|---------------------------------------------|
| `GET`  | `{prefix}/inbox/pull?limit=<n>`   | C 端拉取本实例的待处理条目                       |
| `POST` | `{prefix}/inbox/{record_id}/ack`  | C 端确认条目已处理(取决于服务端语义,见 §5)       |
| `GET`  | `{prefix}/directory`              | 列出所有已启用实例,用于连通性自检 + 寻址 `submit_to` |
| `POST` | `{prefix}/submit`                 | 向**自己**的收件箱投递一条记录                   |
| `POST` | `{prefix}/submit_to`              | 向**任意已知实例**的收件箱投递一条记录            |

服务端**必须**实现 `inbox/pull` + `directory` + `submit`;`inbox/ack` 和 `submit_to` 是可选但强烈推荐。

---

## 3. 请求/响应详情

### 3.1 `GET {prefix}/inbox/pull`

| 字段              | 类型 | 必填 | 默认 | 说明                                    |
|------------------|------|------|------|---------------------------------------|
| `limit`(query)   | int  | 否   | 10   | 单次最多拉取条数,服务端可裁剪到上限         |

**成功响应** `200 OK`:

```json
{
  "success": true,
  "items": [
    {
      "record_id": "uuid-or-other-stable-id",
      "instance_id": "yeweizhi",
      "correlation_id": "调用方提交时给的id(可选)",
      "record_type": "task-or-message-or-...",
      "payload_json": { "...": "调用方原样投递的业务数据" },
      "created_at": "2026-05-13T11:35:18Z"
    }
  ],
  "auto_ack": false,
  "count": 1
}
```

字段约定:

- `items`:**当前批次的条目数组**。可以为空。条目顺序无要求(C 端按 `record_id` 去重)。
- `record_id`:**必须存在且全局唯一**。C 端用它做文件名与去重 key。
- `auto_ack`(可选):若为 `true`,C 端在写盘成功后会主动调 `inbox/ack`;若缺失或 `false`,C 端不会调 ack(服务端可能采用"pull 即消费"语义,见 §5)。
- 兼容写法:若服务端把 `items` 嵌在 `data.items` 里(`{"success": true, "data": {"items": [...]}}`),C 端也能识别。**新接入服务端请直接放在顶层 `items`。**

### 3.2 `POST {prefix}/inbox/{record_id}/ack`

请求体:`{}`(空 JSON 对象,服务端可忽略)。

**成功响应** `200 OK`:

```json
{ "success": true }
```

幂等性:同一 `record_id` 多次 ack 应返回 `success: true`,不报错。

### 3.3 `GET {prefix}/directory`

**成功响应** `200 OK`:

```json
{
  "success": true,
  "items": [
    {
      "instance_id": "yeweizhi",
      "remark": "可选备注",
      "created_at": "2026-05-12T23:10:49Z"
    }
  ],
  "count": 1
}
```

**禁止**返回 `api_key`。

### 3.4 `POST {prefix}/submit`

请求体:

```json
{
  "correlation_id": "调用方自定义,可空",
  "input_text": "用户输入文本(可选,与 payload_json 二选一或并存)",
  "payload_json": { "任意业务键": "值" },
  "record_type": "task"
}
```

**成功响应** `200 OK`:

```json
{
  "success": true,
  "record_id": "服务端分配的全局唯一id",
  "correlation_id": "回传调用方给的id",
  "instance_id": "本次投递到的实例代号",
  "message": "accepted"
}
```

### 3.5 `POST {prefix}/submit_to`

与 `submit` 字段相同,**多一个必填字段**:

```json
{
  "to_instance_id": "接收方实例代号",
  "...": "其余同 submit"
}
```

Bearer 仍是**发送方**自己的 `api_key`,服务端凭 `to_instance_id` 把记录写入**对方**收件箱。发送方无需知道接收方的 `api_key`。

---

## 4. 错误响应

| 场景                | HTTP 状态 | body                                          |
|---------------------|----------|------------------------------------------------|
| 鉴权失败             | `401`   | `{"success": false, "detail": "invalid api_key"}` |
| 路径不存在/未部署     | `404`   | `{"success": false, "detail": "..."}` 或纯文本     |
| 业务校验失败          | `400`   | `{"success": false, "detail": "..."}`             |
| 服务端内部错误        | `5xx`   | 任意,C 端会兜底为 `{"success": false, "http_status": 5xx}` |

C 端在 *strict* 模式下会抛 `httpx.HTTPStatusError`;在 *relaxed* 模式下(守护进程内部使用)永远返回字典,不抛异常。

---

## 5. 消费语义:两种合规做法

服务端可以选其中一种,**必须在文档里写明**,因为这决定了 C 端守护进程是否依赖 `auto_ack` 字段:

### A. Pull 即消费(简单但弱一致)

服务端在 `inbox/pull` 返回条目的同时,立刻把它从待处理队列移除。**响应不带 `auto_ack` 字段**(或显式 `false`),C 端不会再发 ack。

- 优点:实现简单,无需维护"派发中"状态。
- 风险:C 端写盘失败 / 进程崩溃 → 记录永久丢失。Aidun 目前采用此语义。

### B. 显式 ack(强一致)

服务端在 `inbox/pull` 返回时,**把条目标记为"派发中"**(对其他 pull 不可见),并在响应里附 `"auto_ack": true`。C 端写盘成功后会主动调 `inbox/ack`,服务端收到 ack 才把条目从队列里彻底移除。

- 服务端**必须**实现"派发中 + 超时回收":一段时间(建议 ≥ 5 倍 C 端轮询间隔)内未收到 ack,把条目重置为"待处理"以便下次 pull 重新派发。
- 优点:不丢消息。
- 缺点:服务端实现复杂度上升,需要维护可见性 + 超时计时。

> v1 协议**两种语义都允许**,由服务端选。未来 v2 会推荐 B 作为默认。

---

## 6. 版本兼容性约定

- **添加字段**(向后兼容):服务端可以在任意响应里加字段,C 端会忽略未知字段。bump core minor 不强制。
- **修改字段语义 / 删除字段**:不兼容改动,服务端必须 bump 到 v2 路径前缀(例如 `{prefix}/v2/...`),并与新版 `bridge-c-core` 配套。

---

## 7. 一致性测试(给服务端实现者)

一份 `pytest` 风格的端到端用例待在另外的 `bridge-c-protocol-tests` 仓里(roadmap)。在它发布之前,请用下面这串 curl 自检:

```bash
KEY=<api_key>
URL=https://your-domain

# 1. 鉴权 + directory
curl -s $URL/your/v1/directory -H "Authorization: Bearer $KEY"

# 2. 自投自收
curl -s -X POST $URL/your/v1/submit \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"correlation_id":"t-1","input_text":"hi","payload_json":{}}'

# 3. 拉取
curl -s "$URL/your/v1/inbox/pull?limit=10" \
  -H "Authorization: Bearer $KEY"

# 4. ack(若是显式 ack 语义)
curl -s -X POST $URL/your/v1/inbox/<record_id>/ack \
  -H "Authorization: Bearer $KEY"
```
