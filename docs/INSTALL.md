# C 端安装与部署(中性模板)

> 本文写给**在某企业客户机上第一次跑起 C 端守护进程**的运维/工程师。
> 各企业仓库会基于这份模板生成自己的 `docs/INSTALL.md`,把占位符 `acme` / `ACME` / `Acme` 换成实际企业代号、`https://api.acme.example.com` 换成实际生产地址。
> 装完之后怎么投递、怎么消费,见 [USAGE.md](USAGE.md)。

---

## 1. 准备

- **Python ≥ 3.10**(`python3 --version` 或 Windows 上 `py -3 --version`)
- 客户机能访问对端服务器(`curl -I <BASE_URL>` 应有响应)
- 客户机能访问 `github.com`(`pip install` 时会拉取 `bridge-c-core`)
- **一个 `api_key`**(由对方管理员在管理页生成给你这台机器;只会出现一次,丢了重发)

---

## 2. 安装

```bash
git clone <企业仓库地址>
cd acme_bridge_c
git checkout <稳定 tag>
python -m pip install .
```

强烈建议用虚拟环境:

```bash
# Linux / Mac
python3 -m venv .venv && source .venv/bin/activate
python -m pip install .

# Windows
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install .
```

---

## 3. 配置(一行就够)

```bash
cp .env.example .env
# Windows PowerShell: copy .env.example .env
```

打开 `.env`,只需要填这一行:

```
ACME_API_KEY=<管理页给你的 apikey>
```

其他变量都有默认值,**不要动**,除非你知道自己在干什么。

> 进程启动时会自动加载 `.env`(由 `bridge-c-core` 在 `cli.py` 里做)。
> 如果同时设了系统环境变量,**系统环境变量优先**(`.env` 不覆盖已设值)。

### 完整环境变量清单

| 变量                       | 必填 | 默认值                              | 何时改 |
|--------------------------|----|---------------------------------|---|
| `ACME_API_KEY`             | 是  | -                               | 必填 |
| `ACME_BASE_URL`            | 否  | 仓库内置(`DEFAULT_BASE_URL`) | 对端域名/协议临时变化时覆盖 |
| `ACME_INSTANCE_ID`         | 否  | -                               | 服务端要求与凭证分开展示实例时设置 |
| `ACME_POLL_INTERVAL_SEC`   | 否  | `5`                             | 想拉得更快或更慢 |
| `ACME_PULL_LIMIT`          | 否  | `10`                            | 单次拉取上限(1..100) |
| `ACME_LOCAL_POOL_DIR`      | 否  | `data/pending`                  | 想把任务文件放到别处 |
| `ACME_HTTP_TIMEOUT_SEC`    | 否  | `60`                            | 网络慢/对端慢时调大 |

---

## 4. 自检(必做)

```bash
python -m acme_bridge_c --once
```

期望输出:

```
... GET <BASE_URL>/acme/v1/directory "HTTP/1.1 200 OK"
{
  "success": true,
  "items": [ ... ],
  "count": ...
}
```

**自检不过就不要往下走。** 排错见本文件 §7。

---

## 5. 启动守护(日常运行)

```bash
python -m acme_bridge_c
```

会一直跑,直到 Ctrl+C。看到周期性的 `HTTP/1.1 200 OK` 就是健康。

---

## 6. 后台运行(systemd / Windows 计划任务)

### Linux:systemd

```ini
# /etc/systemd/system/acme-bridge-c.service
[Unit]
Description=Acme Bridge C
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/acme_bridge_c
ExecStart=/opt/acme_bridge_c/.venv/bin/python -m acme_bridge_c --no-interactive
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

`WorkingDirectory` 下要有 `.env`,守护启动后会自动读。也可以直接在 unit 里写 `Environment=ACME_API_KEY=...`(权限更紧)。

启用:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now acme-bridge-c
sudo journalctl -u acme-bridge-c -f
```

> `--no-interactive` 是给无 TTY 环境的硬开关:没读到 `api_key` 直接 `exit 2`,而不是停在那儿等输入。

### Windows:任务计划程序

- 程序:`C:\path\to\.venv\Scripts\python.exe`
- 参数:`-m acme_bridge_c --no-interactive`
- 起始位置:`C:\path\to\acme_bridge_c`(`.env` 要在这里)
- 触发器:"启动时"
- 用户账户:勾选"不管用户是否登录都要运行"

---

## 7. 装不上时的排错

| 现象 | 多半原因 | 怎么办 |
|---|---|---|
| `pip install` 报 `Could not find a version` / `Repository not found` | 客户机访问不了 github.com | 网管放行,或挂代理 |
| `--once` 卡住几十秒后超时 | 客户机访问不了对端 | `curl -I <BASE_URL>` 自查 |
| 启动时**回显输入密钥** | 没建 `.env`,也没 `export ACME_API_KEY` | 回到 §3 建 `.env` |
| 启动直接 `请设置环境变量 ACME_API_KEY` 退出 | 同上,且加了 `--no-interactive` 或在 systemd 里 | 同上;systemd 下 `.env` 要放在 `WorkingDirectory` |
| HTTP 401 | api_key 错 / 被禁用 / 复制时多了空格 | 找对端管理员确认;`cat .env` 检查那一行尾部是否多了空格 |
| HTTP 404 在所有路径上 | 对端 nginx 还没把 `/<企业前缀>/` 反代到 C 端服务端口 | 找对端运维确认;或临时 `ACME_BASE_URL=http://host:port` 直连绕过反代 |
| TLS 报错 / 证书时间错 | 客户机系统时间错乱 | Linux `timedatectl status` / Windows `w32tm /query /status` |

---

## 8. 升级

```bash
cd acme_bridge_c
git pull
git checkout <新 tag>
python -m pip install -U .
sudo systemctl restart acme-bridge-c   # 如果用 systemd
```

venv 里跑同样命令即可。

> `bridge-c-core` 小版本升级是向后兼容的(SemVer)。企业仓 bump 到内核大版本(`bridge-c-core 1.x → 2.x`)时,务必看 release notes。

---

## 9. 卸载

```bash
sudo systemctl stop acme-bridge-c
sudo systemctl disable acme-bridge-c
rm -rf /opt/acme_bridge_c
```

> ⚠ `data/pending/` 里如果还有 `.json`,**说明本机 Agent 还没消费完**。直接删等于丢任务。删之前先确认。
