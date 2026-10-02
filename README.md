# 邮件图片请求通知工具

Python 客户端 TUI + Debian 自托管服务端。客户端创建独立的追踪图片 URL，显示 HTML 源码供你粘贴到邮件编辑器的**源码模式**。图片被请求时，服务端记录事件并向创建时填写的邮箱发送一封通知。

通知证明的是**服务器收到图片请求**，不证明真人已读。Apple 邮件隐私保护可能提前下载图片；Gmail 等邮箱通过图片代理访问，来源 IP 可能是代理地址。缓存也可能使后续打开不再访问源站。服务端设置禁止缓存响应头，但无法强制邮箱遵守。

相关原始说明：[Apple 邮件隐私保护](https://www.apple.com/legal/privacy/data/en/mail-privacy-protection/)、[Google 图片代理](https://knowledge.workspace.google.com/admin/gmail/advanced/set-up-an-image-url-proxy-allowlist)。

## Windows 客户端

成品位于 `dist/receipt-client-windows-x64.zip`。解压到可写目录后：

1. 编辑 exe 同目录的 `client.toml`，填写你的 API 地址：

   ```toml
   [client]
   api_base_url = "https://receipt.example.com"
   timeout_seconds = 15
   ```

2. 双击 `receipt-client.exe`；输入通知邮箱和可选备注，点击“创建追踪链接”。
3. 点击“复制 HTML”（或 Ctrl+Y），粘贴到邮件编辑器源码模式。保存源码并发送邮件。
4. 从历史中选择记录，可刷新通知队列状态、重新投递失败通知、停用链接，或点击两次删除。

窗口较小时可以滚动；Tab 切换控件，Enter 激活按钮，Ctrl+Q 退出。首次运行且没有配置时，会生成指向 `http://127.0.0.1:8000` 的模板；编辑配置并重启客户端后生效。

`history.json` 保存 HTML、邮箱、备注、创建时间、原 API 地址以及**管理凭据**。不要公开它；备份后才能在另一台电脑管理旧链接。切换 API 配置不会改变旧记录对应的服务器。丢失本地凭据不会使图片失效，但会失去该链接的管理权限。创建接口无需鉴权；管理凭据仅用于查询、停用、重试和删除。

链接一经创建，客户端可关闭；后续通知完全由服务端处理。一个链接对应一个通知邮箱，不会识别多收件人邮件中具体是谁请求了图片。

### 从源码运行或重新打包

需要 Windows x64 Python 3.12：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-client.lock -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
Copy-Item client.example.toml client.toml
.\.venv\Scripts\python.exe -m receipt.client
```

打包：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-client.ps1
```

生成一个有终端窗口的单文件 exe，以及包含配置模板和本说明的 zip。`--config C:\path\client.toml` 可指定配置位置，历史文件保存在该配置同目录。`--smoke-test` 检查 exe 的运行时依赖，不联网。

## Docker Compose 部署（推荐）

需要已安装 Docker Engine 和 Compose 插件的 Debian 服务器、指向该服务器的域名，以及有效的 HTTPS 证书。Docker 安装方法见 [Docker Debian 官方文档](https://docs.docker.com/engine/install/debian/)。容器镜像使用 Python 3.12，依赖版本锁定在 `requirements-server.lock`。

### 1. 准备配置和证书

在项目目录执行：

```sh
cp server.example.toml server.toml
mkdir -p certs
```

编辑 `server.toml`：

- `public_base_url`：填写实际公网 HTTPS 地址，例如 `https://receipt.your-domain.com`，不要带路径、查询参数或端口外的额外信息。
- `database`：容器部署保留 `/data/receipt.sqlite3`。
- `trusted_proxies`：默认仅信任 Compose 内 Nginx 的固定 IP `172.30.97.2/32`。
- `[smtp]`：后续填写发件服务器、端口、用户名、密码及发件邮箱。587 常用 `starttls`，465 常用 `ssl`；以你的邮箱提供商要求为准。用户名可为空，用于无需登录的本地中继。

`host` 和 `from_address` 必须同时填写或同时为空；为空时服务端照常记录请求，但通知进入队列，72 小时后标为失败。填好配置并重启后，尚未超时的通知会自动投递；已标为失败的通知在客户端手动重新投递。

可以使用 `RECEIPT_SMTP_PASSWORD` 环境变量覆盖文件里的密码。Compose 默认不传此变量，需要时在 app 的 `environment` 中自行加入。配置文件、历史文件和证书私钥已被 Git 与 Docker 构建上下文忽略。

app 容器使用 UID/GID 10001。服务端配置文件必须允许该用户读取；在 Debian 上可执行 `sudo chown root:10001 server.toml` 和 `sudo chmod 640 server.toml`。

将证书放在：

```text
certs/fullchain.pem
certs/privkey.pem
```

证书应匹配公网域名。可使用现有证书，或在启动 Nginx 容器前用 Certbot standalone 签发（需要 80 端口空闲）：

```sh
sudo certbot certonly --standalone -d receipt.your-domain.com
sudo cp /etc/letsencrypt/live/receipt.your-domain.com/fullchain.pem certs/fullchain.pem
sudo cp /etc/letsencrypt/live/receipt.your-domain.com/privkey.pem certs/privkey.pem
sudo chmod 600 certs/privkey.pem
```

安装 Certbot 及自动续期按 [Certbot 官方说明](https://certbot.eff.org/instructions) 配置。本项目不自动签发或续期证书；每次续期后复制新证书并执行 `docker compose exec nginx nginx -s reload`。standalone 续期需要释放 80 端口，可以使用 DNS 验证避免停机。

编辑 `deploy/nginx.conf`，将两处 `receipt.example.com` 换成实际域名。证书路径与 Compose 挂载路径对应，通常无需更改。

### 2. 启动

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 app
curl https://receipt.your-domain.com/health
```

公网放行 TCP 80/443。app 的 8000 端口不映射到宿主机；请求通过 Nginx 转发。`/health` 返回 `smtp_configured`，提示 SMTP 是否已配置，它不测试邮箱凭据是否有效。

Compose 的 `172.30.97.0/24` 网络若与现有网络冲突，需同时调整 Compose 中的子网和两个服务 IP，以及 `server.toml` 中信任的 Nginx IP。若服务器已有 Nginx，可以只运行 app，并将它映射到宿主机回环地址，再按照实际来源 IP配置 `trusted_proxies`。

修改服务端 TOML 后执行：

```sh
docker compose restart app
```

修改域名或 Nginx 配置后执行：

```sh
docker compose exec nginx nginx -t
docker compose exec nginx nginx -s reload
```

Nginx 会覆盖公网传入的 `X-Forwarded-For`，应用只信任明确配置的代理地址。不要启用 Uvicorn 的 `proxy_headers`，不要把 `0.0.0.0/0` 设为可信代理。通知包含的是**应用实际收到的请求头**：Nginx 转发时重写的字段以转发后的值为准，TLS 握手、底层原始报文和被代理丢弃的内容不在记录范围内。

### 3. 数据持久化与备份

SQLite 位于 `receipt-data` 命名数据卷中；普通 `docker compose down` 不删除数据。**不要使用 `docker compose down -v`，否则会删除数据卷。** 单个实例运行一个 Uvicorn 进程和一个通知 worker，不横向扩容；全局 SMTP 发送节奏由这个 worker 控制。

在线备份可调用 SQLite 的备份 API，避免单独复制仍在写入的数据库文件：

```sh
docker compose exec app python -c 'import sqlite3; s=sqlite3.connect("/data/receipt.sqlite3"); d=sqlite3.connect("/data/backup.sqlite3"); s.backup(d); d.close(); s.close()'
docker compose cp app:/data/backup.sqlite3 ./receipt-backup.sqlite3
```

同时妥善备份 `server.toml` 及各客户端的 `history.json`。恢复时先停止 app，再将备份数据库恢复到数据卷，删除旧 WAL/SHM 文件并保持 UID 10001 可写。

## 直接部署（systemd）

源码放到 `/opt/mail-read-receipt`，创建专用用户 `receipt`，使用 Python 3.11+（锁定依赖验证环境为 3.12）创建虚拟环境：

```sh
cd /opt/mail-read-receipt
python3 -m venv .venv
.venv/bin/pip install -r requirements-server.lock
.venv/bin/pip install --no-deps .
sudo useradd --system --home /opt/mail-read-receipt --shell /usr/sbin/nologin receipt
sudo install -d -o receipt -g receipt /var/lib/mail-read-receipt
sudo install -d /etc/mail-read-receipt
sudo install -m 640 -o root -g receipt server.example.toml /etc/mail-read-receipt/server.toml
```

编辑该配置，将 `database` 改为 `/var/lib/mail-read-receipt/receipt.sqlite3`，`trusted_proxies` 改为 `["127.0.0.1/32", "::1/128"]`，填写公网域名和 SMTP。

```sh
sudo cp deploy/receipt.service /etc/systemd/system/receipt.service
sudo systemctl daemon-reload
sudo systemctl enable --now receipt
sudo journalctl -u receipt -n 100
```

宿主机 Nginx 配置参考 `deploy/nginx.conf`，将上游 `http://app:8000` 改为 `http://127.0.0.1:8000`，证书路径改为宿主机的实际路径。直接部署的 app 默认监听 8000，使用防火墙阻止公网直接访问此端口。

## 通知、重试和清理语义

- 每个有效 `GET` 或 `HEAD` 都独立创建一个持久化事件；`HEAD` 无图片响应正文，但仍发通知。其他方法返回 405。
- 图片采用同一份有效透明 PNG 字节，由每条链接的唯一随机文件名动态返回，不需要保存成百万份磁盘图片。
- 通知包括事件编号、链接编号、备注、带时区的请求时间、方法、HTTP 版本、路径及原始查询参数、全部请求头（保留重复字段）、连接 IP/端口及可信代理推导的来源 IP。
- 默认每个来源 IP 每小时允许创建 20 个链接；超过返回 429。仅限制创建，不合并或丢弃有效图片请求通知。
- SMTP 默认至少间隔 2 秒发送一封；失败后从 30 秒开始指数退避，最长间隔 1 小时，事件创建 72 小时后标为 `failed`。配置未填同样适用该期限。每次手动重新投递开始新的 72 小时周期。
- 图片响应要先成功写入数据库；写入失败不会假装已记录。邮件由后台 worker 投递，不占用图片响应的 SMTP 等待时间。
- 通知状态：`pending` 等待、`sending` 正在发送、`sent` SMTP 已接收、`failed` 超过重试期限、`cancelled` 链接停用后取消。
- SMTP 接收不等于最终进入收件箱。SMTP 已接收但进程在提交数据库状态前崩溃时，租约恢复后可能重发，所以投递语义为**至少一次**。通知里的事件编号可帮助识别同一事件的重发。
- 停用阻止新请求并取消未完成通知；已经交给 SMTP 或正在发出的邮件可能无法撤回。删除会永久移除该链接及其全部请求/队列记录。
- 每分钟清理超过 90 天的 `sent`/`cancelled` 请求记录。等待中和失败的记录保留，方便排查和重新投递；链接及备注不自动过期。

## API

地址均相对于配置的 API origin；创建不需要鉴权。管理操作用请求头 `X-Management-Token`，不要把凭据放到 URL。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/links` | JSON：`notification_email`、可选 `note`；返回 `id`、`pixel_url`、`html`、`management_token` |
| GET/HEAD | `/pixel/{id}.png` | 返回透明图片并记录请求 |
| GET | `/api/links/{id}` | 查询启用状态、全部状态计数及最近 100 个事件的投递摘要 |
| POST | `/api/links/{id}/stop` | 停用，并取消未完成通知 |
| POST | `/api/links/{id}/retry` | 重新排队该链接所有失败通知；停用后不允许重试 |
| DELETE | `/api/links/{id}` | 永久删除链接和关联事件 |
| GET | `/health` | 进程状态和 SMTP 是否配置 |

无效邮箱或超过 500 字的备注返回 422。邮箱支持 ASCII 本地部分和可转换为 ASCII 的域名。管理凭据错误或链接不存在统一返回 404。API 文档位于 `/docs`。

公网创建 API 可被任何人调用，指定的通知邮箱不进行所有权验证。这是所选无鉴权方案的行为；限速阈值可以按需要调整。

## 本地开发与验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-server.lock -r requirements-client.lock -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pytest -q
```

本地服务端：复制 `server.example.toml` 为 `server.toml`，将 `public_base_url` 改为 `http://127.0.0.1:8000`、`database` 改为 `data/receipt.sqlite3`，`trusted_proxies` 改为 `[]`，然后运行：

```powershell
.\.venv\Scripts\python.exe -m receipt.server
```

测试使用临时 SQLite 和本机 SMTP 接收器，不发送真实外部邮件。覆盖透明 PNG 解码、每次 GET/HEAD 记录、管理凭据、并发创建限速、代理来源判定、失败重试、过期和保留策略、两封独立 SMTP 通知，以及 TUI 历史和管理操作。

已在 Windows、WSL Ubuntu 和 Debian Docker 容器中验证。Docker 完整链路还验证了 Nginx HTTPS、3 次请求对应 3 封 SMTP 通知，以及容器重启后的记录保留。详细环境、复现命令和验证边界见 [VALIDATION.md](VALIDATION.md)。

本项目使用 [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/) 管理通知 worker，用 [Textual](https://textual.textualize.io/) 构建 TUI。依赖升级后应重新测试，再运行 `scripts/lock-dependencies.py` 更新锁文件。
