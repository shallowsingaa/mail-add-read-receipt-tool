# 给邮件添加图片访问通知

这个工具帮你了解：**收件人大致是否打开过你发的邮件。**

做法很简单：你在邮件里插入一张**肉眼看不见的透明小图**。对方打开邮件时，图片会从你的服务器加载；服务器每收到一次有效请求，就给你指定的邮箱发一封通知，里面有时间、来源 IP、请求头等信息。

**请先明白它的边界：** 通知证明的是“服务器收到过图片请求”，**不等于**真人已读。Apple 邮件隐私保护、邮箱代理、预加载和缓存都可能提前或重复下载图片。参考资料：[Apple 邮件隐私保护](https://www.apple.com/legal/privacy/data/en/mail-privacy-protection/)、[Google 图片代理](https://knowledge.workspace.google.com/admin/gmail/advanced/set-up-an-image-url-proxy-allowlist)。

工具由两部分组成：

| 部分 | 作用 | 谁使用 |
| --- | --- | --- |
| **Windows 客户端** | 创建追踪链接、生成 HTML、管理历史 | 发邮件的人 |
| **自托管服务端** | 收图片请求、记事件、发通知邮件 | 有服务器的人（可只部署一次给多人用） |

---

## 你应该从哪篇文档看起

| 你的情况 | 阅读入口 |
| --- | --- |
| **我只想发邮件，用客户端** | **[客户端使用说明（保姆级）](docs/client-guide.md)** ← 普通用户从这里开始 |
| 服务器已有 1Panel、OpenResty、阿里云 ESA | **[1Panel＋ESA 部署指南](docs/deploy-1panel-esa.md)**（本项目默认方案） |
| 旧版部署后 80 端口被占用 | 上述指南的「从旧版迁移」一节 |
| 服务器没有现成反向代理，需要本项目提供 HTTPS | [独立 Nginx 部署](docs/deploy-standalone.md) |
| 要填服务器配置、排查通知、备份或更新 | [配置与运维手册](docs/operations.md) |
| 想看测试范围或复现验证 | [验证记录](VALIDATION.md) |

> **给部署者的一句话：** 默认 `docker compose up -d --build` **只启动应用**，不占用 80/443，也不要求在项目里放证书。应用监听本机 `127.0.0.1:18000`，公网 HTTPS 交给你的 OpenResty。项目里的 `certs/` 仅用于显式启用的独立 Nginx 方案。

---

## 客户端快速上手（详见完整说明）

完整步骤、邮件粘贴方法、常见问题请看 **[客户端使用说明](docs/client-guide.md)**。这里只给最短路径：

1. 解压 `receipt-client-windows-x64.zip` 到可写目录（不要在压缩包里直接运行）。
2. 用记事本打开同目录的 `client.toml`，填入已完成反向代理的**公网域名**：

   ```toml
   [client]
   api_base_url = "https://receipt.example.com"
   timeout_seconds = 15
   ```

   不要写 `/api`、`/pixel`，也不要写 `localhost`。

3. 双击 `receipt-client.exe`，填写**通知邮箱**和可选备注，点「创建追踪链接」。
4. 点「复制 HTML」（或 `Ctrl+Y`），粘贴到邮件编辑器的**源码模式**，再切回正常模式写正文后发送。粘贴到普通正文框会显示代码乱码。
5. 之后可在本地历史里刷新状态、重投失败通知、停用或删除链接。删除需连点两次。

补充说明：

- 客户端退出后通知照常发送（发信在服务器上）。
- 同一封邮件发给多人并共用一条链接时，**无法区分**是谁访问；需要区分请分别建链接、分别发。
- `history.json` 含管理凭据，请备份。丢失不影响图片继续有效，但客户端将无法再管理旧链接。
- 快捷键：`Ctrl+Y` 复制 HTML，`Ctrl+Q` 退出；`Tab` 切换焦点。

---

## API 与行为约定

创建接口无需密钥；每个来源 IP 默认每小时最多创建 20 条链接。图片地址长期有效，直至手动停用或删除。管理请求通过请求头 `X-Management-Token` 携带创建时返回的独立凭据。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/links` | 输入 `notification_email`、可选 `note`；返回链接、HTML 和管理凭据 |
| GET / HEAD | `/pixel/{id}.png` | 返回透明图片；每次有效请求分别记录并通知 |
| GET | `/api/links/{id}` | 查询状态、通知计数和最近 100 条投递摘要 |
| POST | `/api/links/{id}/stop` | 停用图片并取消未完成通知 |
| POST | `/api/links/{id}/retry` | 重新排队失败通知；停用后不可重试 |
| DELETE | `/api/links/{id}` | 删除链接和全部关联事件 |
| GET | `/health` | 检查进程和 SMTP 是否已配置（不验证密码） |

交互式 API 文档在 `/docs`。凭据错误或记录不存在返回 404，创建限速返回 429，参数无效返回 422。

图片为有效的 1×1 透明 PNG，按链接唯一文件名动态返回，不落盘重复图片。响应先写入 SQLite 再返回；SMTP 在后台队列投递。服务端设置禁止缓存，但无法强制邮箱代理每次打开都回源。

---

## 从源码运行与打包

Windows 客户端建议使用 Python 3.12 x64：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-client.lock -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
Copy-Item client.example.toml client.toml
.\.venv\Scripts\python.exe -m receipt.client
```

重新打包客户端：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build-client.ps1
```

输出为带终端窗口的单文件 exe，以及配置模板、文档和许可证。`--config C:\path\client.toml` 可指定配置（历史与配置同目录）；`--smoke-test` 仅检查运行时，不联网。

### 开发与测试

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-server.lock -r requirements-client.lock -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m pytest -q
```

本地运行服务端时，复制 `server.example.toml` 为 `server.toml`，把 `public_base_url` 改为 `http://127.0.0.1:8000`，`database` 改为 `data/receipt.sqlite3`，`trusted_proxies` 改为 `[]`，然后 `python -m receipt.server`。源码运行默认监听 8000；Compose 对应宿主机端口为 18000。

测试使用临时 SQLite 和本机 SMTP 接收器，不向真实邮箱发信。项目使用 [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/) 管理 worker，[Textual](https://textual.textualize.io/) 构建 TUI。升级依赖后先测试，再用 `scripts/lock-dependencies.py` 更新锁文件。
