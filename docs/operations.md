# 配置、运行和故障处理

本手册面向**服务端运维**：如何改配置、看懂通知状态、排查故障、备份与更新。

若你只是用 Windows 客户端发邮件，请看 **[客户端使用说明](client-guide.md)**，不必读本文。

**默认场景：** 已有 1Panel / OpenResty，采用 app-only 部署。若你用了其他方式启动，请保持命令一致：

| 部署方式 | 额外参数 |
| --- | --- |
| 默认 app-only | 不加额外参数 |
| bridge 附加方案 | 每条命令加两份 `-f`：`-f compose.yaml -f compose.proxy-network.yaml` |
| 独立 Nginx 方案 | 每条命令加 `--profile standalone` |

---

## 一、配置文件分别归谁管

| 文件 | 谁使用 | 何时生效 |
| --- | --- | --- |
| `server.toml` | app 容器（挂载到 `/config/server.toml`） | 保存后 `docker compose restart app` |
| `.env` | Docker Compose（宿主机端口、共享网络名） | 保存后 `docker compose up -d app` |
| `client.toml` | Windows 客户端 | 保存后完全退出并重新启动客户端 |
| `history.json` | 客户端本地历史 | 程序自动维护；迁移前请备份 |

**TOML 书写规则：** 字符串用双引号，数字不加引号。未知参数、非法 URL、错误时区或无效 SMTP 配置会导致启动失败——以 app 日志为准。应用**不会**热加载配置，改完必须重启。

---

## 二、服务端参数

### `[service]` 段

| 参数 | 示例 / 默认 | 含义 |
| --- | --- | --- |
| `public_base_url` | `https://receipt.example.com` | 生成图片地址时使用的公网 origin。不要含路径、账号密码或查询参数 |
| `database` | `/data/receipt.sqlite3` | 容器内数据库路径。通常保持默认 |
| `timezone` | `Asia/Shanghai` | 请求时间与邮件正文使用的时区 |
| `trusted_proxies` | `[]` | 初始不信任任何转发头。通过真实请求核对代理对端后，再填写准确 IP |
| `create_limit` | `20` | 每个来源 IP 在一个窗口内最多创建多少条链接 |
| `create_window_seconds` | `3600` | 创建限速窗口（秒） |
| `retention_days` | `90` | 已发送 / 已取消事件的保留天数 |
| `retry_hours` | `72` | 从事件创建起，允许重试的期限 |
| `worker_interval` | `1.0` | 后台队列检查间隔（秒） |

### `[smtp]` 段

| 参数 | 示例 / 默认 | 含义 |
| --- | --- | --- |
| `host` | `smtp.example.com` | 发件服务器。空字符串表示暂不启用发信 |
| `port` | `587` | 按服务商填写；SSL 常见 465 |
| `security` | `starttls` | `starttls`、`ssl`，或可信本地中继用的 `plain` |
| `username` | `sender@example.com` | 登录用户名；无需登录的中继可留空 |
| `password` | 服务商密码 / 授权码 | 不是所有邮箱都允许普通登录密码用于 SMTP |
| `from_address` | `sender@example.com` | 账号允许使用的发件地址。不填显示姓名 |
| `timeout` | `15.0` | SMTP 操作超时（秒） |
| `min_interval` | `2.0` | 两次发信之间的最小间隔（秒） |

**注意：**

- `host` 与 `from_address` **要么都填，要么都空**。
- 邮箱支持 ASCII 本地部分，以及可转成 ASCII 的域名。
- 可用环境变量 `RECEIPT_SMTP_PASSWORD` 覆盖密码；Compose 默认未传入，需要在 app 的 `environment` 中显式配置。
- 改配置前先备份。不要在公开日志或工单里粘贴密码、管理凭据和完整请求头。

---

## 三、通知状态怎么看

| 状态 | 含义 | 你该做什么 |
| --- | --- | --- |
| `pending` | 等待发送，或失败后等待下次尝试 | 核对 SMTP 是否已配置、队列速度、错误日志 |
| `sending` | 正在投递 | 通常很快结束；异常退出后租约到期会自动恢复 |
| `sent` | SMTP 服务器已接受 | 查收件箱、垃圾箱和服务商投递日志 |
| `failed` | 超过允许的重试期限 | 修好配置后，在客户端点「重新投递失败通知」 |
| `cancelled` | 链接已停用，未完成通知被取消 | 不会自动重发 |

### 发送节奏与重试

- 未配置 SMTP 时：访问仍会记录，通知进入队列等待；72 小时后变为 `failed`。
- 补填 SMTP 并重启后：只自动发送仍在期限内的 `pending`；已 `failed` 的必须手动重投。
- 发送失败后从 30 秒起指数退避，最长间隔 1 小时。
- 手动重投会重置该批失败事件的尝试次数和 72 小时期限。
- 默认每 2 秒最多开始投递一封；积压**不会**合并成一封。

### 投递语义（重要）

SMTP 已接受、但进程在写回数据库前崩溃时，恢复后**可能重发**。因此语义是**至少一次**：

- 事件编号相同 → 可能是同一事件的重试，不一定是两次图片访问。
- `sent` 只表示 SMTP 已接受，**不保证**进入最终收件箱。

---

## 四、图片访问与停用的准确含义

- 每次有效的 `GET` 或 `HEAD` 各产生一个事件。`HEAD` 没有图片正文，但仍会发通知。其他方法返回 405。
- 浏览器刷新不一定新发请求；邮箱代理可能预加载或缓存。**最终计数以源站收到的请求为准。**
- 通知包含：完整请求头、查询参数、方法、HTTP 版本、时间、连接 IP/端口、推导来源 IP。不包含 TLS 握手或代理丢弃的原始字段。
- **停用**：新访问返回 404，并取消未完成通知。正在发送或已交给 SMTP 的邮件可能无法撤回。
- **删除**：图片失效；服务端链接与事件永久删除；客户端对应本地历史一并删除。
- 链接**不自动过期**。每分钟清理超过保留期限的 `sent`/`cancelled` 事件；`pending`/`failed` 保留供处理。链接和备注不自动删除。

---

## 五、常见故障速查

| 现象 | 检查与处理 |
| --- | --- |
| 绑定 80/443 报 `address already in use` | 改用新版默认 app-only；移除**本工具**的 nginx，不要停 1Panel 的 OpenResty |
| 18000 被占用 | 在 `.env` 改 `RECEIPT_PORT`，并同步修改 OpenResty 代理目标 |
| app 日志 `PermissionError` | 确认 `server.toml` 可被 UID/GID 10001 读取；数据库目录可写；不要随意改数据卷路径 |
| Docker 提示 `Pool overlaps` | 更新 Compose，去掉固定子网和容器 IP，让 Docker 自动分配；不要删除其他应用的网络 |
| 本机正常、公网 502 | 查 OpenResty 代理、ESA 回源协议 / Host / SNI、源站自签名证书与强制校验是否兼容 |
| 返回 HTML 或验证码 | ESA / WAF 挑战、登录页、防盗链或 OpenResty 静态规则拦截了 API / 图片 |
| 链接 URL 是 `example.com` 或 `localhost` | 修正 `public_base_url` 并重启 app，再创建**新**链接。旧邮件里的 URL 不会自动变 |
| GET 图片成功但没新事件 | 确认拿到的是源站响应；ESA 与 OpenResty 的缓存都需关闭 |
| 创建返回 429 | 达到创建限速。等窗口结束后重试，或按实际需求调整阈值 |
| 事件 `pending`，日志 `SMTPAuthenticationError` | 查用户名、授权码、SMTP 服务是否启用、发件地址权限 |
| SMTP 超时 / 连接拒绝 | 查端口、服务器出站网络、服务商连接要求。部分云平台限制 25 端口 |
| 显示 `failed`，修好 SMTP 后仍不发送 | 在客户端点「重新投递失败通知」，再刷新状态 |
| 显示 `sent`，却没有收件 | 查垃圾箱 / 隔离区 / 服务商投递日志。通常不是图片接口问题 |
| IP 全是代理，或所有人共用一个创建额度 | 核对两层信任：OpenResty 恢复 ESA 地址 → app 信任实际代理对端 |
| 旧链接返回 404 | 是否已停用 / 删除？是否连到新的空数据卷？是否访问了错误服务器？ |

### 看日志

```sh
docker compose ps
docker compose logs --tail=100 app
```

---

## 六、更新与备份

### 更新

更新源码后重建 app。保留原项目名、配置和数据卷：

```sh
docker compose up -d --build app
```

SQLite 在 `receipt-data` 命名卷里。普通的 `down` **不**删卷；**`down -v` 会删卷**。本工具按单实例部署；同时跑多个 app 会打乱全局发信节奏。

### 在线备份

使用 SQLite backup API，不要直接复制正在写入的库文件：

```sh
docker compose exec -T app python -c 'import sqlite3; s=sqlite3.connect("/data/receipt.sqlite3"); d=sqlite3.connect("/data/backup.sqlite3"); s.backup(d); d.close(); s.close()'
docker compose cp app:/data/backup.sqlite3 ./receipt-backup.sqlite3
```

另外请备份：

1. `server.toml`
2. `.env`（若使用）
3. 客户端的 `history.json`（含管理凭据；**数据库备份不能替代它**）

### 恢复

1. 先停止 app。
2. 确认使用的是**当前项目**的 `receipt-data` 卷。
3. 将备份恢复为 `receipt.sqlite3`。
4. 移除与旧库配套的 WAL / SHM 文件。
5. 确保 UID/GID 10001 可写，再启动。

恢复到另一个项目名时，须明确指定原卷或迁移数据。看到空历史不一定是数据被删，也可能只是连上了一个新卷。

---

## 七、不用 Docker 的 systemd 部署

源码放在 `/opt/mail-read-receipt`。创建 Python 3.11+ 虚拟环境（依赖锁定验证环境为 3.12），安装：

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

编辑系统配置 `/etc/mail-read-receipt/server.toml`：

| 项 | 改成 |
| --- | --- |
| `database` | `/var/lib/mail-read-receipt/receipt.sqlite3` |
| `trusted_proxies` | `["127.0.0.1/32", "::1/128"]` |
| `public_base_url` | 你的公网域名 |
| `[smtp]` | 按第二节填写 |

启动：

```sh
sudo cp deploy/receipt.service /etc/systemd/system/receipt.service
sudo systemctl daemon-reload
sudo systemctl enable --now receipt
sudo journalctl -u receipt -n 100
```

源码服务监听 **8000**。OpenResty 的代理目标改为 `http://127.0.0.1:8000`，并用防火墙阻止公网直接访问 8000。本路线的 TLS 证书仍放在现有 OpenResty，不由本应用处理。
