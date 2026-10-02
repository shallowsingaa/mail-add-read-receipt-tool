# 在已有 1Panel、OpenResty 和阿里云 ESA 的服务器上部署

这份指南适用于：公网 Debian 服务器由 1Panel 管理，OpenResty 已监听 80/443，域名接入阿里云 ESA，并已关闭缓存。

示例域名是 `receipt.example.com`，目录是 `/opt/mail-add-read-receipt-tool`。替换成实际值。**旧版升级要留在原项目目录中**，不要为了照抄示例另建目录，否则 Compose 项目名和数据卷可能改变。

## 1. 证书放在哪里，谁负责 HTTPS

```text
邮件客户端 / Windows 工具
    │ HTTPS，使用 ESA 边缘证书
    ▼
阿里云 ESA（该域名不缓存）
    │ 按 ESA 回源设置访问源站
    ▼
1Panel 的 OpenResty（沿用现有端口、域名和源站证书）
    │ 本机 HTTP：127.0.0.1:18000
    ▼
app 容器（内部端口 8000）
    ├─ SQLite 保存链接、请求、通知队列
    └─ SMTP 发送图片请求通知
```

app 无需处理公网 TLS，无需读取私钥。你**不用把 1Panel 的证书复制到本项目目录**。旧方案另起 Nginx 来处理 HTTPS，容器需要通过挂载读到证书，才要求准备 `certs/`；即使使用独立 Nginx，证书也可放在其他位置，只需调整挂载路径。

### 现有通配符自签名证书是否能继续用？

区分两段连接：

- **客户端 → ESA**：看到的是 ESA 边缘证书，应受浏览器、邮箱图片代理和 Python 信任，且匹配访问域名。
- **ESA → OpenResty**：这是回源连接，使用你在 1Panel 配置的源站证书。

通配符决定域名覆盖范围，不决定证书是否受信任。源站自签名证书可留在 OpenResty；能否用于 HTTPS 回源，取决于 ESA 的源站证书校验设置。ESA 官方说明，默认不校验 HTTPS 回源证书；启用强制校验后会检查有效期、域名和信任链，失败时可能返回 502。[ESA 回源证书说明](https://www.alibabacloud.com/help/en/edge-security-acceleration/esa/user-guide/back-to-source-protocols-and-ports)

若当前域名访问正常，先保留已有设置。不需要让 app 再配置 HTTPS，也不要把源站自签名证书误当成公网客户端应信任的证书。边缘证书独立于源站证书。[ESA 边缘证书说明](https://www.alibabacloud.com/help/en/edge-security-acceleration/esa/user-guide/configure-edge-certificates/)

## 2. 先检查 OpenResty 网络模式

在服务器 SSH 中执行：

```sh
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
docker inspect --format '{{.HostConfig.NetworkMode}}' 你的OpenResty容器名
```

第二条中的容器名取第一条结果里镜像含 `openresty` 的那行。

- 输出 **`host`**：按下面常规步骤，代理目标用 `http://127.0.0.1:18000`。
- 输出 bridge 网络名：先看本文最后的 bridge 分支。
- OpenResty 直接安装在宿主机：按 host 方案。

已核实的 [1Panel 官方 OpenResty 模板](https://github.com/1Panel-dev/appstore/blob/dev/apps/openresty/1.31.1.1-2-4-noble/docker-compose.yml) 使用 host 网络，仍以你的 inspect 结果为准。

## 3. 从旧版迁移，解决 80 端口冲突

首次部署跳到第 4 节。

报错 `failed to bind host port ...:80 ... address already in use` 的原因：旧版本工具 Nginx 也要绑定 80，而 OpenResty 已占用。需要移除的是**本工具的 nginx 服务**，不是 1Panel 的 OpenResty。

在原项目目录检查后，仅停止并删除本工具 nginx：

```sh
docker compose ls
docker compose ps -a
docker compose stop nginx
docker compose rm -f nginx
```

若此前启动失败、没有生成该容器，可能提示没有可操作的容器，可继续。更新源码和 Compose；保留 `server.toml`，不要用示例覆盖已填写的 SMTP 密码。

原配置 `trusted_proxies = ["172.30.97.2/32"]` 对应旧 Nginx。host 方案先改为 `["172.30.97.1/32"]`，之后按第 8 节核对实际来源。保留 `database = "/data/receipt.sqlite3"`。

新版默认只启动 app。已有 1Panel 时**不要启用 `standalone` profile**。不要运行 `down -v`，它会删除数据卷；不要改变 Compose 项目名、目录名称或卷名称。原来显式使用了 `-p 名称` 的，之后每条 Compose 命令都继续用同一个名称。

## 4. 准备配置并启动应用

首次部署，将服务端包解压到项目目录，进入目录：

```sh
cd /opt/mail-add-read-receipt-tool
ls Dockerfile compose.yaml server.example.toml
test -f server.toml || cp server.example.toml server.toml
```

编辑 `server.toml`，至少核对下面三项。这是节选，保留示例的其他参数和 `[smtp]` 部分：

```toml
[service]
public_base_url = "https://receipt.example.com"
database = "/data/receipt.sqlite3"
trusted_proxies = ["172.30.97.1/32"]
```

`public_base_url` 是实际公网域名，不是源站 IP、127.0.0.1 或 `/api` 路径。它决定生成的图片 URL。SMTP 可后续填写；`host` 和 `from_address` 同时为空时，仍能创建链接和记录访问，通知先排队。

容器用户 UID/GID 是 10001，要能读到配置：

```sh
sudo chown root:10001 server.toml
sudo chmod 640 server.toml
docker compose config --quiet
docker compose up -d --build --remove-orphans app
docker compose ps
docker compose logs --tail=80 app
curl -fsS http://127.0.0.1:18000/health
```

`config --quiet` 成功时无输出；app 最终应显示 healthy。最后一条预期为 `{"status":"ok","smtp_configured":false}`，已填 SMTP 时是 `true`。`true` 不验证邮箱密码或最终收件。

这条路线不需要创建 `certs/`、修改 `deploy/nginx.conf` 或开放公网 18000。

如果 18000 也被占用，在项目 `.env` 中写 `RECEIPT_PORT=18001`，重新 `docker compose up -d app`，并把下面的代理目标改为 `http://127.0.0.1:18001`；容器内部仍为 8000。

## 5. 在 1Panel 配置站点反向代理

建议用追踪专用域名，例如 `receipt.example.com`。不要把另一个业务网站的整个根路径替换掉。

1. 在 1Panel 网站中创建或打开该域名的站点。
2. 选择反向代理网站，或在已有站点添加路径 `/` 的代理。
3. 代理目标填 **`http://127.0.0.1:18000`**。这里 HTTP 是本机连接，与公网 HTTPS 不矛盾。
4. 关闭该代理缓存。ESA 已不缓存，OpenResty 这一层也应不缓存。
5. HTTPS 沿用 1Panel 已配置的源站证书和端口；ESA 回源 Host/SNI 应匹配该站点。
6. 保存并重载 OpenResty，不需要停止整个 OpenResty 服务。

按钮名称随面板版本变化。原则是把站点请求原样转发给 app，保留 `/api/*` 和 `/pixel/*` 路径。

如果要核对站点配置，参考 `deploy/openresty-location.conf`：

```nginx
location / {
    proxy_pass http://127.0.0.1:18000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Connection "";
    proxy_cache off;
    proxy_buffering off;
    proxy_read_timeout 60s;
    client_max_body_size 8k;
}
```

放在**该站点现有的 `server { ... }` 内**。已有 `location /` 时编辑或替换它，不添加同名 location。面板可能通过 include 生成代理配置，应修改实际生效的那段。保留 1Panel 管理的 `listen`、域名、证书和其他站点设置。

## 6. 核对 ESA 回源与缓存

你已对该域名完全不缓存，可以保留。确认规则覆盖 `/pixel/*` 和 `/api/*`，没有更高优先级缓存规则、边缘函数或页面托管截走请求。

图片 URL 必须直接返回 `image/png`。邮箱图片加载程序和 TUI 无法完成交互式 JS、验证码或滑块挑战；若 ESA 对这些路径启用挑战，需要使用允许直接回源的路径规则。避免把正常图片请求改写成防盗链错误页、登录页或 HTML 页面。

从外部检查公网入口，使用 GET，不对 `/health` 使用 `curl -I`：

```sh
curl -fsS https://receipt.example.com/health
```

不要加 `-k`，此处应正常通过 ESA 边缘证书验证。如果本机检查正常而这一步失败，先查 ESA/OpenResty，不必先改 Python 代码。

## 7. 填写 SMTP，完成一次人工验收

格式示例，按邮箱服务商实际信息填写：

```toml
[smtp]
host = "smtp.example.com"
port = 587
security = "starttls"
username = "sender@example.com"
password = "邮箱服务商提供的密码或授权码"
from_address = "sender@example.com"
timeout = 15.0
min_interval = 2.0
```

465 通常使用 `security = "ssl"`，以服务商要求为准。发件地址应是该账号允许使用的地址。保存后：

```sh
docker compose restart app
docker compose logs --tail=80 app
```

然后：

1. 客户端 API 地址填公网域名，重启客户端。
2. 创建链接，通知邮箱填你能收信的地址，备注写“部署测试”。
3. 将图片 URL 原样复制到浏览器打开。透明小图肉眼不可见是正常现象。
4. 客户端刷新状态，检查事件计数增加、通知从 pending 变为 sent；检查邮箱和垃圾箱。
5. 用下面两条 curl 测试同一链接。每条都会产生一次访问和一封通知：

```sh
curl -fsS -o /dev/null 'https://receipt.example.com/pixel/替换成实际编号.png'
curl -fsS -o /dev/null 'https://receipt.example.com/pixel/替换成实际编号.png'
```

检查是否新增两条事件。判断 ESA 是否真的回源，应以服务端计数为准；不要只根据浏览器看起来刷新过判断。最后将 HTML 插入真实邮件并发送，验证邮件编辑器确实保留外链图片。

## 8. 真实 IP 要分两层配置

先把功能跑通，再恢复真实 IP。不恢复也能通知，只是来源可能显示为 ESA 节点。

**OpenResty → ESA 的信任：** ESA 的托管转换可添加 `ali-real-client-ip`，表示连接到 ESA 的客户端地址。需要时启用该功能，再在 1Panel 真实 IP 设置中选择该请求头，并仅信任实际 ESA 回源网段。[ESA 托管转换说明](https://help.aliyun.com/zh/edge-security-acceleration/esa/user-guide/managed-conversion)

对应 Nginx 原理如下，`ESA实际回源CIDR` 必须替换，不能直接粘贴：

```nginx
set_real_ip_from ESA实际回源CIDR;
# 每个实际网段分别写一行，包括适用的 IPv6 网段。
real_ip_header ali-real-client-ip;
real_ip_recursive off;
```

从当前站点控制台或官方支持获取适用于你的回源列表，不猜测网段。若使用 ESA 源站防护，其官方流程提供节点列表及更新方法。[ESA 源站防护](https://help.aliyun.com/zh/edge-security-acceleration/esa/user-guide/origin-protection/)

不要信任 `0.0.0.0/0` 或 `::/0`。只有真实连接来自可信网段时，OpenResty 才应相信该头。配置正确后，代理片段中的 `$remote_addr` 才会恢复为 ESA 声称的客户端地址；它仍可能是邮箱图片代理的 IP。[Nginx realip 模块](https://nginx.org/en/docs/http/ngx_http_realip_module.html)

**应用 → OpenResty 的信任：** `server.toml` 的 `trusted_proxies` 只填写 app 直接看到的 OpenResty 对端地址，不填写 ESA 网段。host 模式通过 Docker 桥接后通常是 `172.30.97.1`，以实际请求为准。

通过公网域名请求一张测试图片后，运行以下命令。只打印来源字段，不打印管理凭据或全部请求头：

```sh
docker compose exec -T app python -c 'import sqlite3,json; d=sqlite3.connect("/data/receipt.sqlite3"); r=d.execute("SELECT details FROM events ORDER BY created DESC LIMIT 1").fetchone(); x=json.loads(r[0]) if r else {}; print({k:x.get(k) for k in ("connection_ip","effective_source_ip","peer_is_trusted_proxy")})'
```

若确定的代理 `connection_ip` 不同，将 `trusted_proxies` 改成该 IP `/32`，IPv6 用 `/128`，再重启 app。不信任整个公网或所有 Docker 容器，不启用 Uvicorn `proxy_headers`。

通知中的 `connection_ip` 是 app 的直接对端；`effective_source_ip` 是可信代理链推导的地址。请求头是 app 实际收到的字段，可能经过代理改写，并非公网原始报文。

## 9. 按层排查，不要同时修改所有配置

| 检查 | 正常结果 | 失败时主要看 |
| --- | --- | --- |
| `docker compose ps` | app healthy | TOML、文件权限、数据库卷、app 日志 |
| 本机 `http://127.0.0.1:18000/health` | JSON | 映射端口、进程、RECEIPT_PORT |
| OpenResty 对应站点 | JSON | 网络模式、代理目标、重复 location、路径改写 |
| 公网 `https://域名/health` | 证书正常、返回 JSON | ESA 边缘证书、回源 Host/SNI/协议、源站证书校验 |
| 创建链接 | HTML 是公网域名 | public_base_url、API 422/429、ESA 挑战 |
| 请求图片后刷新 | 事件数增加 | 缓存、404、静态图片规则是否截走请求 |
| pending 变为 sent | SMTP 已接收 | 密码/授权码、SMTP 网络、发件地址限制 |
| sent 后收件箱无邮件 | 最终收到 | 垃圾箱、隔离区、邮箱服务商投递日志 |

若本机正常而 ESA 返回 502，检查 OpenResty 与 ESA 回源。源站自签名证书配上 ESA 强制验证，也可能导致回源失败。

需要绕过 ESA 时，在可信管理机检查源站：

```sh
curl --resolve receipt.example.com:443:你的源站IP https://receipt.example.com/health
```

自签名源站证书可能报验证错误。仅在这一次定位中可加 `-k` 看能否获取 JSON，以区分证书和反代问题；正式公网访问与客户端仍通过正常证书验证。

## OpenResty 使用 bridge 网络的分支

bridge 容器里的 `127.0.0.1` 指向它自己，不能照搬 host 方案。

1. 查实际网络：

   ```sh
   docker inspect --format '{{json .NetworkSettings.Networks}}' 你的OpenResty容器名
   ```

2. 在项目 `.env` 填 `RECEIPT_PROXY_NETWORK=你实际查到的网络名`。
3. 让 app 加入同一个已有网络：

   ```sh
   docker compose -f compose.yaml -f compose.proxy-network.yaml up -d --build app
   ```

4. 代理目标改成 `http://receipt-tool-app:8000`。这个网络别名由附加配置提供。app 创建后再保存、重载 OpenResty；重建 app 后若出现 502，重载代理让它重新解析地址。
5. `trusted_proxies` 改为 OpenResty 在共享网络的实际 IP，再按第 8 节核对，不沿用 host 网关。

后续 Compose 命令都带这两个 `-f` 参数。默认回环端口仍可用于宿主机诊断，没有对公网开放。
