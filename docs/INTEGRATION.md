# 新企业接入指南

> 本文写给"**首次为一家新企业接入这套 C 端系统**"的工程师。
> 接入完成的产物是:**一个新的企业专用 git 仓库**(例如 `acme_bridge_c`),末端机器克隆它就能用。

---

## 接入前先确认两件事

### 1. 服务端是否符合协议

服务端必须实现 [PROTOCOL.md](PROTOCOL.md) 里定义的 5 个 endpoint(`inbox/pull` / `inbox/{id}/ack` / `directory` / `submit` / `submit_to`),并使用 Bearer 鉴权。

最低限度需要的:**`inbox/pull` + `directory` + `submit`**。
强烈推荐:**`inbox/ack` + `submit_to`**。

服务端验收清单(让对方对照 `PROTOCOL.md` §7 的 curl 检查表跑一遍即可)。

### 2. 拿到三个"身份号码"

| 项 | 例子 | 用途 |
|---|---|---|
| 企业代号(小写下划线) | `acme` | 包名、文件路径、CLI prog |
| 企业代号(大写) | `ACME` | env 变量前缀 |
| 企业代号(首字母大写) | `Acme` | log/标题描述 |
| URL 路径前缀 | `/acme/v1` | 服务端的 API 路径 |
| 实例请求头名 | `X-Acme-Instance-Id` | 与 URL 前缀同一套命名 |
| 默认 base URL | `https://api.acme.example.com` | 企业 C 端的生产地址 |

---

## 接入流程(总耗时 ~20 分钟)

### Step 1. 从模板仓克隆

```bash
git clone https://github.com/wiseyip0911/bridge-c-template.git acme_bridge_c
cd acme_bridge_c
rm -rf .git
git init
```

模板仓里以"Acme"为示例,所有需要替换的地方都已经写成 `acme` / `ACME` / `Acme` 三个形态,跟着模板里的 [SETUP.md](https://github.com/wiseyip0911/bridge-c-template/blob/main/SETUP.md) 走 find·replace 即可。

### Step 2. 找替换 6 处字符串

按下表替换(注意大小写敏感):

| 模板里的字符串              | 替换成              | 出现位置                                      |
|---------------------------|--------------------|---------------------------------------------|
| `acme`                    | `<你的企业代号小写>` | 包名/路径/prog_name                          |
| `ACME`                    | `<企业代号大写>`     | env 变量前缀                                  |
| `Acme`                    | `<企业代号首大写>`   | log description、客户端类名                    |
| `/acme/v1`                | `/你的/v1`           | `URL_PREFIX` 常量                            |
| `X-Acme-Instance-Id`      | `X-你的-Instance-Id` | `INSTANCE_HEADER` 常量                       |
| `https://api.acme.example.com` | 企业实际生产 URL    | `DEFAULT_BASE_URL` 常量                      |

具体哪些文件需要改,模板里 `SETUP.md` 有完整清单。所有改动都是字符串替换,无需写新逻辑。

### Step 3. 重命名包目录

```bash
mv src/acme_bridge_c src/<你的企业代号小写>_bridge_c
```

### Step 4. 装上跑测试

```bash
python -m pip install -e .[dev]
python -m pytest -q
```

模板里有针对差异点(URL_PREFIX、INSTANCE_HEADER、env 读取)的 3 个烟测,改完应该全部通过。

### Step 5. 连真实后端做 `--once`

```bash
export <PREFIX>_API_KEY=测试用api_key
python -m <你的包名>_bridge_c --once
```

应该看到一份 `directory` JSON。能通就接入完毕。

### Step 6. 推到企业自己的 GitHub

```bash
git add .
git commit -m "feat: initial onboarding for <Company>"
git remote add origin git@github.com:<org>/<repo>.git
git push -u origin main
```

发布到 PyPI / 私有源(可选,只对外公开包名 + 版本号,**不要把 api_key/默认 url 当机密**,base_url 公开无害):

```bash
python -m pip install build twine
python -m build
twine upload dist/*
```

---

## 接入完成后:发给客户的文档结构

企业仓里有两份**互不重叠**的文档,在接入流程完成后会随仓库一起发布:

| 给谁 | 文档 |
|---|---|
| 装并跑起守护的人(运维) | `docs/INSTALL.md` |
| 把守护接入自家应用的人(开发者) | `docs/USAGE.md` |

末端用户的最短路径:

```bash
git clone <你刚发布的企业仓地址>
cd <repo>
git checkout <stable tag>
pip install .
cp .env.example .env             # 填入 <PREFIX>_API_KEY
python -m <pkg>_bridge_c --once  # 自检
python -m <pkg>_bridge_c          # 启动守护
```

具体顺序、自检期望输出、systemd / 任务计划写法,都在企业仓的 `docs/INSTALL.md` 里。

---

## 注意事项

1. **`api_key` 一定要由对端管理后台生成**,**不要**把 key 写进仓库代码 / `.env.example`(模板里的 `.env.example` 是空 key,只有变量名)。
2. **`DEFAULT_BASE_URL` 可以放仓库里**,这只是一个 URL,不是机密。
3. **`URL_PREFIX` / `INSTANCE_HEADER` 一旦发布就别再改**:已部署的客户机会因路径不对而失联。如果协议要变,bump 到 v2 路径(`/your/v2/...`),与 `bridge-c-core` 的大版本一起升级。
4. 内核版本约束建议写**精确小版本范围**(已在模板的 `pyproject.toml` 里):
   ```toml
   dependencies = ["bridge-c-core>=0.1,<0.2"]
   ```
   这样 core 出小版本升级(bug fix / 新 endpoint)企业仓自动受益,但 core 出 1.0 大版本时不会被动升级。

---

## 已知 limitation(写文档时务必跟对方讲清楚)

服务端选 §5-A "Pull 即消费"语义时,**C 端写盘失败会丢消息**(对方已经把这条标记消费完毕)。如果业务对可靠性敏感,要求对方实现 §5-B "显式 ack + 派发中超时回收"。

详见 [PROTOCOL.md](PROTOCOL.md) §5。
