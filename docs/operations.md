# 配置、运行和故障处理

本手册默认使用已有 1Panel/OpenResty 的 app-only 部署。bridge 附加方案的命令要加两份 `-f` 参数；独立 Nginx 方案的命令要加 `--profile standalone`，保持与启动时一致。

## 配置文件是怎么读取的

| 文件 | 使用者 | 什么时候生效 |
| --- | --- | --- |
| `server.toml` | app 容器，挂到 `/config/server.toml` | 保存后 `docker compose restart app` |
| `.env` | Docker Compose，用于宿主机端口或共享网络名 | 保存后 `docker compose up -d app` |
| `client.toml` | Windows exe | 保存后退出、重新启动客户端 |
| `history.json` | Windows 客户端的本地历史 | 客户端自动维护，迁移前备份 |

TOML 字符串要带双引号，数字不带引号。未知参数、非法 URL、错误时区或无效 SMTP 配置会导致启动失败，具体看 app 日志。应用不会动态重读配置。

## 服务端参数参考

以下属于 `[service]`：

| 参数 | 示例/默认 | 含义 |
| --- | --- | --- |
| `public_base_url` | `https://receipt.example.com` | 生成图片地址使用的公网 origin；不含路径、凭据、查询参数 |
| `database` | `/data/receipt.sqlite3` | 容器数据卷内数据库路径，通常不要改 |
| `timezone` | `Asia/Shanghai` | 请求时间和邮件正文使用的时区 |
| `trusted_proxies` | `["172.30.97.1/32"]` | app 直接信任的代理对端；不同部署按实际请求核对 |
| `create_limit` | `20` | 每个来源 IP 在一个窗口内最多创建多少链接 |
| `create_window_seconds` | `3600` | 创建限速窗口，单位秒 |
| `retention_days` | `90` | 清理已发送/已取消事件的保留天数 |
| `retry_hours` | `72` | 从事件创建时起允许重试的期限 |
| `worker_interval` | `1.0` | 后台队列检查间隔，单位秒 |

以下属于 `[smtp]`：

| 参数 | 示例/默认 | 含义 |
| --- | --- | --- |
| `host` | `smtp.example.com` | 发件服务器；空字符串表示暂不启用发信 |
| `port` | `587` | 按服务商填写，SSL 常见 465 |
| `security` | `starttls` | `starttls`、`ssl`，或可信本地中继使用的 `plain` |
| `username` | `sender@example.com` | 登录用户名；无需登录的中继可留空 |
| `password` | 服务商密码/授权码 | 不是所有邮箱都允许普通登录密码用于 SMTP |
| `from_address` | `sender@example.com` | 账号允许使用的发件地址，不填显示姓名 |
| `timeout` | `15.0` | SMTP 操作超时，单位秒 |
| `min_interval` | `2.0` | 两次发信之间的最小间隔，单位秒 |

`host` 和 `from_address` 同时填写或同时为空。邮箱支持 ASCII 本地部分和可转换为 ASCII 的域名。可用环境变量 `RECEIPT_SMTP_PASSWORD` 覆盖密码；Compose 默认没有传入它，需要你在 app 的 environment 中显式配置。更换配置前保留备份，不要在公开日志或工单粘贴密码、管理凭据和完整请求头。

## 通知状态怎么看

| 状态 | 含义 | 下一步 |
| --- | --- | --- |
| `pending` | 等待发送，或失败后等待下次尝试 | 核对 SMTP 已配置、队列速度及错误日志 |
| `sending` | 正在投递 | 通常很快结束；异常退出后租约到期会恢复 |
| `sent` | SMTP 服务器已接受 | 查看邮箱、垃圾箱和服务商投递日志 |
| `failed` | 超过允许的重试期限 | 修复配置后在客户端重新投递 |
| `cancelled` | 链接停用，取消未完成通知 | 不会自动重投 |

没有配置 SMTP 时，访问仍会记录，通知在队列等待；72 小时到期后变为 failed。补填 SMTP 并重启，只自动发送仍在期限内的 pending；已失败的要点击重新投递。

发送失败后从 30 秒开始指数退避，最长间隔 1 小时。手动重投会重置该批失败事件的尝试次数和 72 小时期。通知默认每 2 秒最多开始投递一封，积压不会合并成一封。

SMTP 已接受，但进程在写回数据库前崩溃时，恢复后可能重发。因此投递语义是至少一次；事件编号相同表示可能是同一事件的重试，不一定是两次图片访问。sent 不等于保证进入最终收件箱。

## 图片访问与停用的准确含义

- 每次有效 GET 或 HEAD 各产生一个事件，HEAD 没有图片正文但仍会发通知。其他方法返回 405。
- 浏览器刷新不一定新发请求；邮箱代理可预加载或缓存。最终计数以源站收到请求为准。
- 通知包含应用收到的完整请求头、查询参数、方法、HTTP 版本、时间、连接 IP/端口和推导来源 IP。不包含 TLS 握手或代理丢弃的原始字段。
- 停用使新访问返回 404，并取消未完成通知；正在发送或已经交给 SMTP 的邮件可能无法撤回。
- 删除使图片失效，永久删除服务端链接及事件，同时删除客户端对应本地历史。
- 链接不自动过期。每分钟清理超过保留期限的 sent/cancelled 事件；pending/failed 保留供处理，不自动删除链接和备注。

## 常见故障

| 现象 | 检查与处理 |
| --- | --- |
| 绑定 80/443 报 address already in use | 使用新版默认 app-only；移除旧版本工具 nginx，不停 1Panel OpenResty |
| 18000 被占用 | `.env` 改 RECEIPT_PORT，并同步修改 OpenResty 代理目标 |
| app 日志 PermissionError | server.toml 可由 UID/GID 10001 读取；数据库目录应可写；不要随意改数据卷路径 |
| Docker 提示子网重叠 | 改 compose.yaml 的子网与 app 固定 IP；独立 Nginx 同步改其 IP，再核对 trusted_proxies |
| 本机正常、公网 502 | 查 OpenResty 代理、ESA 回源协议/Host/SNI、源站自签名证书与强制校验是否兼容 |
| 返回 HTML 或验证码 | ESA/WAF 挑战、登录页、防盗链或 OpenResty 静态规则拦截了 API/图片 |
| 链接 URL 是 example.com/localhost | 修正 public_base_url、重启 app，再创建新链接；旧邮件里的 URL 不会自动改变 |
| GET 图片成功但没新事件 | 核对拿到的是否源站响应，ESA 和 OpenResty 缓存均需关闭 |
| 创建返回 429 | 达到创建限速；窗口后重试或按实际需求调整阈值 |
| 事件 pending、日志 SMTPAuthenticationError | 查用户名、授权码、SMTP 服务是否启用、发件地址权限 |
| SMTP 超时/连接拒绝 | 查端口、服务器出站网络、服务商连接要求；部分云平台限制 25 端口 |
| 显示 failed，修好 SMTP 后仍不发送 | 点击客户端重新投递，再刷新状态 |
| 显示 sent，却没有收件 | 垃圾箱/隔离区/服务商投递日志；不是图片接口问题 |
| IP 全是代理或所有人共用一个创建额度 | 核对两层信任：OpenResty 恢复 ESA 地址、app 信任实际代理对端 |
| 旧链接返回 404 | 是否已停用/删除，是否连接到新的空数据卷，是否访问了错误服务器 |

日志命令：

```sh
docker compose ps
docker compose logs --tail=100 app
```

## 更新和备份

更新源码后重建 app，保留原项目名、配置和数据卷：

```sh
docker compose up -d --build app
```

SQLite 在 `receipt-data` 命名卷里。普通 down 不删除卷，**down -v 会删除卷**。同时运行多个 app 实例会改变全局发信节奏，本工具按单实例部署。

在线备份使用 SQLite backup API，不单独复制正在写入的数据库文件：

```sh
docker compose exec -T app python -c 'import sqlite3; s=sqlite3.connect("/data/receipt.sqlite3"); d=sqlite3.connect("/data/backup.sqlite3"); s.backup(d); d.close(); s.close()'
docker compose cp app:/data/backup.sqlite3 ./receipt-backup.sqlite3
```

另外备份 `server.toml`、`.env`（若使用），以及客户端 `history.json`。数据库备份不能代替客户端管理凭据。

恢复前停止 app。确认是当前项目的 receipt-data 卷，将备份恢复为 `receipt.sqlite3`，移除与旧库配套的 WAL/SHM 文件，确保 UID/GID 10001 可写，再启动。恢复到另一个项目名时，须明确指定原卷或迁移数据；看到空历史不一定是数据删除，也可能连接了一个新卷。

## 不用 Docker 的 systemd 部署

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

编辑系统配置：database 改为 `/var/lib/mail-read-receipt/receipt.sqlite3`，trusted_proxies 改为 `["127.0.0.1/32", "::1/128"]`，填写公网域名及 SMTP。

```sh
sudo cp deploy/receipt.service /etc/systemd/system/receipt.service
sudo systemctl daemon-reload
sudo systemctl enable --now receipt
sudo journalctl -u receipt -n 100
```

源码服务监听 8000，OpenResty 目标改为 `http://127.0.0.1:8000`；使用防火墙阻止公网直接访问 8000。本路线的证书仍留在现有 OpenResty。
