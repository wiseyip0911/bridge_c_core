# 新企业接入指南

> 本文写给"**首次为一家新企业接入这套 C 端系统**"的工程师。
> 接入完成的产物是:**一个新的企业专用 git 仓库**(例如 `foo_bridge_c`),末端机器克隆它就能用。
>
> **不需要、也不存在单独的「接入模板仓」。** 企业 C 端 = `bridge-c-core`(pip 依赖) + 本指南 + 下面任选一条落地路径。安装与使用说明以本仓库 [INSTALL.md](INSTALL.md)、[USAGE.md](USAGE.md) 为准;企业仓可复制这两份后做全仓字符串替换。

---

## 接入前先确认两件事

### 1. 服务端是否符合协议

服务端必须实现 [PROTOCOL.md](PROTOCOL.md) 里定义的 5 个 endpoint(`inbox/pull` / `inbox/{id}/ack` / `directory` / `submit` / `submit_to`),并使用 Bearer 鉴权。

最低限度需要的:**`inbox/pull` + `directory` + `submit`**。
强烈推荐:**`inbox/ack` + `submit_to`**。

服务端验收清单(让对方对照 `PROTOCOL.md` §7 的 curl 检查表跑一遍即可)。

### 2. 拿到协议差异三件套

| 项 | 例子 | 用途 |
|---|---|---|
| 企业代号(小写下划线) | `foo` | 包名、文件路径、CLI prog |
| 企业代号(大写) | `FOO` | env 变量前缀 |
| 企业代号(首字母大写) | `Foo` | log/标题、客户端类名 |
| URL 路径前缀 | `/foo/v1` | 服务端的 API 路径 |
| 实例请求头名 | `X-Foo-Instance-Id` | 与 URL 前缀同一套命名 |
| 默认 base URL | `https://api.foo.example.com` | 企业 C 端的生产地址(可公开) |

---

## 接入流程(推荐路径:以活样板为起点)

当前**已上线、文档齐全**的活样板是 [`aidun_bridge_c`](https://github.com/wiseyip0911/aidun_bridge_c)。新企业最省事的做法是:**fork 或克隆后改名、全仓替换**,无需从零造轮子。

### Step 1. 复制活样板

```bash
git clone https://github.com/wiseyip0911/aidun_bridge_c.git foo_bridge_c
cd foo_bridge_c
rm -rf .git
git init
```

(若用 GitHub fork,保留 fork 关系亦可;关键是得到一份可改名的完整树。)

### Step 2. 全仓 find·replace(Aidun 样板 → 新企业)

按下表**大小写敏感**替换。建议顺序:**先**换 URL / header / env 前缀,**再**换包名与目录,减少误伤。

| 当前(Aidun 样板里常见) | 替换成(新企业) |
|---|---|
| `aidun_bridge_c` | `<企业代号>_bridge_c`,例如 `foo_bridge_c` |
| `KQ_POOL` | `<企业 env 前缀大写>`,例如 `FOO` |
| `KqPool` | `<类名前缀>`,例如 `Foo`(如 `KqPoolClient` → `FooClient`) |
| `/kq-pool/v1` | `/<你的>/v1`,例如 `/foo/v1` |
| `X-Kq-Pool-Instance-Id` | `X-<你的>-Instance-Id`,例如 `X-Foo-Instance-Id` |
| 默认 base URL(文档与 `DEFAULT_BASE_URL` 等处写死的 `http://c.aidunkouqiang.com` 等) | 新企业生产 URL |
| 文案里的 `Aidun` / `aidun`(若你希望企业仓 README 不再出现 Aidun 品牌) | 按需改成新企业名;也可保留部分说明性句子后手工润色 |

### Step 3. 重命名包目录

```bash
mv src/aidun_bridge_c src/foo_bridge_c
```

(若 Step 2 已把路径里的 `aidun_bridge_c` 全部换成 `foo_bridge_c`,此步可能已由 IDE 完成,以实际目录名为准。)

### Step 4. 涉及文件清单(改完应自检)

至少检查这些路径(与 `aidun_bridge_c` 结构一致):

- `pyproject.toml`(包名、`dependencies` 里的 `bridge-c-core` 版本范围、console script 若有)
- `README.md`、`docs/INSTALL.md`、`docs/USAGE.md`、`.env.example`
- `src/<pkg>/client.py`(`URL_PREFIX` / `INSTANCE_HEADER` / `DEFAULT_BASE_URL` / `ENV_*`)
- `src/<pkg>/__main__.py`(`make_cli` 的 `env_prefix` / `prog_name`)
- `src/<pkg>/__init__.py`(导出类名)
- `tests/` 下测试文件中的常量与 import

所有改动在样板阶段通常都是**字符串替换 + 目录改名**,无需写新协议逻辑。

### Step 5. 装上跑测试

```bash
python -m pip install -e .[dev]
python -m pytest -q
```

### Step 6. 连真实后端做 `--once`

```bash
cp .env.example .env
# 编辑 .env 填入 <PREFIX>_API_KEY
python -m foo_bridge_c --once
```

应看到 `directory` JSON 且 HTTP 200。**不过不要直接上守护进程。**

### Step 7. 推到企业自己的 GitHub

```bash
git add .
git commit -m "feat: initial onboarding for <Company>"
git remote add origin git@github.com:<org>/<repo>.git
git push -u origin main
```

发布到 PyPI / 私有源(可选)。

---

## 备选路径:从零建最小仓

若不想复制 Aidun 树,可新建空仓库,只放本仓库 [README.md](../README.md)「三分钟示例」里的:

- `pyproject.toml`:声明依赖 `bridge-c-core`(建议版本范围如 `>=0.1,<0.2`,按你们策略 pin tag 亦可)
- `src/<pkg>/client.py`:继承 `BaseClient`,填 `URL_PREFIX` / `INSTANCE_HEADER` / `DEFAULT_BASE_URL` / `ENV_*`
- `src/<pkg>/__main__.py`:`make_cli(...)`
- `src/<pkg>/__init__.py`:导出 Client
- `.env.example`、可选 `tests/` 烟测

末端运维文档:可直接复制本仓库的 [INSTALL.md](INSTALL.md)、[USAGE.md](USAGE.md) 到企业仓 `docs/`,再把其中的 `acme` / `ACME` 占位替换成该企业实际值(与 README 三分钟示例一致)。

---

## 接入完成后:发给客户的文档结构

企业仓里建议有两份**互不重叠**的文档:

| 给谁 | 文档 |
|---|---|
| 装并跑起守护的人(运维) | `docs/INSTALL.md` |
| 把守护接入自家应用的人(开发者) | `docs/USAGE.md` |

可从本仓库 `docs/INSTALL.md`、`docs/USAGE.md` 复制后做占位符替换。

末端用户最短路径:

```bash
git clone <你刚发布的企业仓地址>
cd <repo>
git checkout <stable tag>
pip install .
cp .env.example .env
python -m <pkg>_bridge_c --once
python -m <pkg>_bridge_c
```

---

## 注意事项

1. **`api_key` 一定要由对端管理后台生成**,**不要**把 key 写进仓库代码;`.env.example` 里只保留变量名、不要填真 key。
2. **`DEFAULT_BASE_URL` 可以放仓库里**,这只是一个 URL,不是机密。
3. **`URL_PREFIX` / `INSTANCE_HEADER` 一旦发布就别再改**:已部署的客户机会因路径不对而失联。协议要变应 bump 到 v2 路径,与 `bridge-c-core` 大版本协同。
4. 内核版本约束建议在企业仓 `pyproject.toml` 里写**精确小版本范围**,例如:
   ```toml
   dependencies = ["bridge-c-core>=0.1,<0.2"]
   ```
   小版本自动跟 bugfix;大版本升级走显式评审。

---

## 已知 limitation(写文档时务必跟对方讲清楚)

服务端选 §5-A "Pull 即消费"语义时,**C 端写盘失败会丢消息**(对方已经把这条标记消费完毕)。如果业务对可靠性敏感,要求对方实现 §5-B "显式 ack + 派发中超时回收"。

详见 [PROTOCOL.md](PROTOCOL.md) §5。
