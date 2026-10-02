# 给邮件添加图片访问通知

这个工具由 Windows 客户端和自托管服务端组成。客户端生成一段包含透明图片的 HTML，你把它粘贴到邮件编辑器的源码模式；服务端每次收到该图片的有效 GET 或 HEAD 请求，就记录一次事件，并向你填写的邮箱发送一封通知。

通知里有请求时间、方法、URL、请求头、来源 IP 和备注，供你判断是否为代理、预加载或自动扫描。它证明的是“服务器收到图片请求”。Apple 邮件隐私保护可能提前下载图片，邮箱代理和缓存也可能改变访问次数，不能据此确认真人是否已读。[Apple 官方说明](https://www.apple.com/legal/privacy/data/en/mail-privacy-protection/)、[Google 图片代理说明](https://knowledge.workspace.google.com/admin/gmail/advanced/set-up-an-image-url-proxy-allowlist)

## 你应该从哪里开始

| 你的情况 | 阅读入口 |
| --- | --- |
| 服务器已有 1Panel、OpenResty、阿里云 ESA | **[1Panel＋ESA 部署指南](docs/deploy-1panel-esa.md)**，这是本项目默认方案 |
| 已用旧版部署，遇到 80 端口被占用 | 上述指南的“从旧版迁移”一节 |
| 服务器没有现成反向代理，需要本项目提供 HTTPS | [独立 Nginx 部署](docs/deploy-standalone.md) |
| 要填写配置、排查邮件未收到、备份或更新 | [配置与运维手册](docs/operations.md) |
| 要检查测试范围或复现本地验证 | [验证记录](VALIDATION.md) |

**默认 `docker compose up -d --build` 只启动 app，不占用 80/443，不需要在项目里放证书。** app 通过本机 `127.0.0.1:18000` 提供 HTTP，由你的 OpenResty 处理公网 HTTPS。项目内的 `certs/` 仅用于显式启用的独立 Nginx 方案。

## Windows 客户端怎么用

1. 解压 `dist/receipt-client-windows-x64.zip` 到一个可写目录，不要直接在压缩包内运行。
2. 用文本编辑器打开 exe 同目录的 `client.toml`，填写已完成反向代理的公网域名：

   ```toml
   [client]
   api_base_url = "https://receipt.example.com"
   timeout_seconds = 15
   ```

   替换为你自己的地址，不填 `/api`、`/pixel` 或 Docker 内部地址。

3. 双击 `receipt-client.exe`，输入通知邮箱和可选备注，点击“创建追踪链接”。
4. 点击“复制 HTML”或按 Ctrl+Y，把源码粘贴进邮件编辑器的**源码模式**。保存后发送邮件；粘贴到普通正文输入框会显示代码。
5. 从本地历史选择记录，可以刷新通知状态、重新投递失败通知、停用链接或删除记录。删除需要连续点击两次。

Tab 切换控件，Enter 激活按钮，Ctrl+Q 退出。窗口较小时可滚动。首次运行没有配置时，会生成指向本机的模板；修改配置后重启客户端生效。

客户端退出后通知照常发送，因为发信工作在服务器上。同一封邮件的多个收件人共用一个链接时，工具无法区分具体是谁访问；需要区分时，分别创建链接并分别发送邮件。

`history.json` 保存邮箱、备注、HTML、原 API 地址和该链接的管理凭据。创建不需要登录；管理操作需要这份凭据。请保留和备份历史文件。丢失历史不会让图片自动失效，但你将无法通过客户端管理那些旧链接。切换 API 配置不会改变旧记录对应的服务器。

## 从源码运行与打包

Windows 客户端建议使用 Python 3.12 x64：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-client.lock -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
Copy-Item client.example.toml client.toml
.\.venv\Scripts\python.exe -m receipt.client
```

重新打包：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-client.ps1
```

输出是带终端窗口的单文件 exe，以及配置模板、文档和许可证。`--config C:\path\client.toml` 可指定配置；历史存放在该配置同目录。`--smoke-test` 检查 exe 运行时，不联网。

## API 和行为约定

创建 API 对外开放，无需 API 密钥；每个来源 IP 默认每小时最多创建 20 条链接。图片 URL 长期有效，直至手动停用或删除。管理请求在 `X-Management-Token` 请求头携带创建时返回的独立随机凭据。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/links` | 输入 `notification_email`、可选 `note`；返回链接、HTML 和管理凭据 |
| GET / HEAD | `/pixel/{id}.png` | 返回透明图片，每次有效请求分别记录、通知 |
| GET | `/api/links/{id}` | 查询状态、通知计数和最近 100 条投递摘要 |
| POST | `/api/links/{id}/stop` | 停用图片并取消未完成通知 |
| POST | `/api/links/{id}/retry` | 重新排队失败通知；停用后不允许重试 |
| DELETE | `/api/links/{id}` | 删除链接和全部关联事件 |
| GET | `/health` | 检查进程和 SMTP 是否已配置，不验证 SMTP 密码 |

交互式 API 文档在 `/docs`。管理凭据错误或记录不存在返回 404，创建限速返回 429，邮箱或备注无效返回 422。

图片使用有效的 1×1 透明 PNG，通过每条链接的唯一文件名动态返回，不重复保存相同图片文件。图片响应先写入 SQLite，再返回；SMTP 在后台队列投递。服务器设置禁止缓存，但无法强制邮箱代理每次打开邮件都回源。

## 开发验证

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-server.lock -r requirements-client.lock -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pytest -q
```

本地运行服务端时，复制 `server.example.toml` 为 `server.toml`，把 `public_base_url` 改为 `http://127.0.0.1:8000`，`database` 改为 `data/receipt.sqlite3`，`trusted_proxies` 改为 `[]`，然后运行 `python -m receipt.server`。源码运行默认监听 8000；Compose 对应的宿主机端口为 18000。

测试使用临时 SQLite 和本机 SMTP 接收器，不向真实邮箱发信。项目使用 [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/) 管理 worker、[Textual](https://textual.textualize.io/) 构建 TUI。升级依赖后先测试，再用 `scripts/lock-dependencies.py` 更新锁文件。
