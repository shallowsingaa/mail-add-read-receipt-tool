# 在已有 1Panel、OpenResty 和阿里云 ESA 的服务器上部署

**适用场景：**

- 公网 Debian 服务器由 1Panel 管理；
- OpenResty 已监听 80/443；
- 域名已接入阿里云 ESA，并关闭了缓存。

**示例约定：** 域名 `receipt.example.com`，项目目录 `/opt/mail-add-read-receipt-tool`。请全部换成你的实际值。

> **升级用户请注意：** 旧版升级必须留在**原项目目录**。不要为了照抄示例另建目录，否则 Compose 项目名和数据卷可能改变，数据会“丢失”（其实是连上了新卷）。

普通用户只使用 Windows 客户端请看 [客户端使用说明](client-guide.md)；本文面向部署者。

---

## 1. 谁负责 HTTPS？证书放在哪？

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

**结论：** app **不处理**公网 TLS，也**不读取**私钥。你**不需要**把 1Panel 的证书复制到本项目目录。

| 方案 | 证书怎么做 |
| --- | --- |
| 本文默认（app-only） | HTTPS 由 ESA + 1Panel OpenResty 负责，项目内无需 `certs/` |
| 独立 Nginx（备用） | 才需要挂载证书；也可把证书放别处，只改挂载路径。见 [独立部署](deploy-standalone.md) |

### 现有通配符自签名证书还能继续用吗？

分清两段连接：

| 连接 | 看到的证书 | 要求 |
| --- | --- | --- |
| 客户端 → ESA | ESA 边缘证书 | 应被浏览器、邮箱图片代理、Python 信任，且匹配访问域名 |
| ESA → OpenResty | 你在 1Panel 配置的源站证书 | 能否用于 HTTPS 回源，取决于 ESA 的源站证书校验设置 |

**通配符**只决定域名覆盖范围，**不决定**证书是否受信任。

- 源站自签名证书可以留在 OpenResty。
- ESA 官方默认**不校验** HTTPS 回源证书；若启用强制校验，会检查有效期、域名和信任链，失败可能 502。参见 [ESA 回源证书说明](https://www.alibabacloud.com/help/en/edge-security-acceleration/esa/user-guide/back-to-source-protocols-and-ports)。
- 边缘证书独立于源站证书。参见 [ESA 边缘证书](https://www.alibabacloud.com/help/en/edge-security-acceleration/esa/user-guide/configure-edge-certificates/)。

**若当前域名访问正常，先原样保留，不要动。** 不要给 app 再配 HTTPS，也不要把源站自签名证书当成“公网客户端应信任的证书”。

---

## 2. 先确认 OpenResty 的网络模式

在服务器 SSH 中执行：

```sh
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
docker inspect --format '{{.HostConfig.NetworkMode}}' 你的OpenResty容器名
```

第二条的容器名：取第一条结果里镜像名含 `openresty` 的那一行的 `Names`。

| 第二条输出 | 怎么做 |
| --- | --- |
| **`host`** | 按下文常规步骤；代理目标用 `http://127.0.0.1:18000` |
| 某个 bridge 网络名 | 先看文末「OpenResty 使用 bridge 网络的分支」 |
| （OpenResty 直接装在宿主机） | 按 host 方案 |

已核实的 [1Panel 官方 OpenResty 模板](https://github.com/1Panel-dev/appstore/blob/dev/apps/openresty/1.31.1.1-2-4-noble/docker-compose.yml) 使用 host 网络；仍以你的 `inspect` 结果为准。

---

## 3. 从旧版迁移（解决 80 端口冲突）

**首次部署请直接跳到第 4 节。**

若启动时报：

```text
failed to bind host port ...:80 ... address already in use
```

原因：旧版本工具自带的 Nginx 也要绑定 80，而 OpenResty 已占用。
**要移除的是本工具的 nginx，不是 1Panel 的 OpenResty。**

在**原项目目录**执行：

```sh
docker compose ls
docker compose ps -a
docker compose stop nginx
docker compose rm -f nginx
```

若此前启动失败、从未生成该容器，提示“没有可操作的容器”可以忽略，继续往下。

然后：

1. 更新源码和 Compose 文件。
2. **保留**你已填好的 `server.toml`（不要用示例覆盖掉 SMTP 密码）。
3. 把 `trusted_proxies` 先改成 `[]`，启动后按第 8 节核对真实来源，再填写准确代理 IP。
4. 保留 `database = "/data/receipt.sqlite3"`。

**新版默认只启动 app。** 已有 1Panel 时：

- **不要**启用 `standalone` profile；
- **不要**运行 `down -v`（会删数据卷）；
- **不要**改变 Compose 项目名、目录名或卷名；
- 若你以前用了 `-p 名称`，之后每条 Compose 命令都继续加同一个名称。

---

## 4. 准备配置并启动应用

### 4.1 解压并进入目录

首次部署，把服务端包解压到项目目录后：

```sh
cd /opt/mail-add-read-receipt-tool
ls Dockerfile compose.yaml server.example.toml
test -f server.toml || cp server.example.toml server.toml
```

### 4.2 编辑 `server.toml`

至少核对下面三项（节选，其余参数和 `[smtp]` 段请保留）：

```toml
[service]
public_base_url = "https://receipt.example.com"
database = "/data/receipt.sqlite3"
trusted_proxies = []
```

| 项 | 填什么 | 不要填什么 |
| --- | --- | --- |
| `public_base_url` | 实际公网域名 | 源站 IP、`127.0.0.1`、带 `/api` 的路径 |
| `database` | 保持 `/data/receipt.sqlite3` | 随意改路径 |
| `trusted_proxies` | 先 `[]` | 整个公网、所有 Docker 网段 |

它决定生成的图片 URL。SMTP 可以稍后再填：`host` 与 `from_address` 都为空时，仍能创建链接、记录访问，通知先排队。

### 4.3 设置权限并启动

容器用户 UID/GID 为 10001，需要能读到配置：

```sh
sudo chown root:10001 server.toml
sudo chmod 640 server.toml
docker compose config --quiet
docker compose up -d --build --remove-orphans app
docker compose ps
docker compose logs --tail=80 app
curl -fsS http://127.0.0.1:18000/health
```

预期结果：

| 命令 | 正常表现 |
| --- | --- |
| `docker compose config --quiet` | 无输出 |
| `docker compose ps` | app 最终 `healthy` |
| `curl .../health` | `{"status":"ok","smtp_configured":false}`（已填 SMTP 时为 `true`） |

`true` 只表示配置了 SMTP，**不验证**密码或最终收件。

本路线**不需要**创建 `certs/`、改 `deploy/nginx.conf`，也**不需要**对公网开放 18000。

### 4.4 如果 18000 也被占用

在项目 `.env` 中写：

```env
RECEIPT_PORT=18001
```

然后：

```sh
docker compose up -d app
```

并把后面 OpenResty 的代理目标改为 `http://127.0.0.1:18001`。容器内部端口仍是 8000。

---

## 5. 在 1Panel 配置站点反向代理

建议使用**追踪专用域名**，例如 `receipt.example.com`。不要把另一个业务网站的整个根路径替换掉。

1. 在 1Panel「网站」中创建或打开该域名的站点。
2. 选择反向代理网站，或在已有站点添加路径 `/` 的代理。
3. 代理目标填 **`http://127.0.0.1:18000`**。
   （这里是本机 HTTP，与公网 HTTPS 不矛盾。）
4. **关闭**该代理缓存。ESA 已不缓存，OpenResty 这一层也应不缓存。
5. HTTPS 沿用 1Panel 已配置的源站证书和端口；ESA 回源的 Host/SNI 应匹配该站点。
6. 保存并**重载** OpenResty（不必停止整个 OpenResty 服务）。

按钮名称随面板版本变化。原则只有一条：**把站点请求原样转发给 app**，保留 `/api/*` 和 `/pixel/*` 路径。

若要核对站点配置，可参考 `deploy/openresty-location.conf`：

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

**放在该站点现有的 `server { ... }` 内。** 已有 `location /` 时请编辑或替换，不要添加同名 location。面板可能通过 include 生成代理配置，应修改**实际生效**的那段。保留 1Panel 管理的 `listen`、域名、证书和其他站点设置。

---

## 6. 核对 ESA 回源与缓存

前提：你已对该域名**完全不缓存**。请再确认：

- 规则覆盖 `/pixel/*` 和 `/api/*`；
- 没有更高优先级的缓存规则、边缘函数或页面托管把请求截走；
- 图片 URL 必须能**直接**返回 `image/png`。

邮箱图片加载程序和本工具的 TUI **无法**完成交互式 JS、验证码或滑块挑战。若 ESA 对这些路径启用了挑战，请配置允许直接回源的路径规则。避免把正常图片请求改写成防盗链错误页、登录页或 HTML 页面。

从外部检查公网入口（用 GET，**不要**对 `/health` 用 `curl -I`）：

```sh
curl -fsS https://receipt.example.com/health
```

**不要加 `-k`。** 此处应正常通过 ESA 边缘证书验证。若本机检查正常而这一步失败，先查 ESA / OpenResty，不必先改 Python 代码。

---

## 7. 填写 SMTP，并做一次人工验收

### 7.1 填 SMTP

格式示例（按邮箱服务商实际信息填写）：

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

- 端口 465 通常用 `security = "ssl"`，以服务商要求为准。
- `from_address` 应是该账号允许使用的地址。

保存后：

```sh
docker compose restart app
docker compose logs --tail=80 app
```

### 7.2 人工验收清单

1. 客户端 API 地址填**公网域名**，重启客户端。
2. 创建链接：通知邮箱填你能收信的地址，备注写“部署测试”。
3. 把生成的图片 URL **原样**复制到浏览器打开。透明小图肉眼看不见是正常的。
4. 在客户端点「刷新状态」：
   - 事件计数应增加；
   - 通知应从 `pending` 变为 `sent`；
   - 检查邮箱和垃圾箱。
5. 用下面两条 curl 测同一链接（每条都会产生一次访问和一封通知）：

   ```sh
   curl -fsS -o /dev/null 'https://receipt.example.com/pixel/替换成实际编号.png'
   curl -fsS -o /dev/null 'https://receipt.example.com/pixel/替换成实际编号.png'
   ```

6. 确认新增了两条事件。
   判断 ESA 是否真的回源，**以服务端计数为准**，不要只看浏览器“好像刷新过”。
7. 最后把 HTML 插入真实邮件并发送，确认邮件编辑器会保留外链图片。

---

## 8. 真实 IP：分两层配置

先把功能跑通，再恢复真实 IP。不恢复也能发通知，只是来源可能显示为 ESA 节点。

### 8.1 OpenResty → 信任 ESA

ESA 的托管转换可添加 `ali-real-client-ip`，表示“连接到 ESA 的客户端地址”。需要时启用该功能，再在 1Panel 真实 IP 设置中选择该请求头，并**仅信任实际 ESA 回源网段**。[ESA 托管转换](https://help.aliyun.com/zh/edge-security-acceleration/esa/user-guide/managed-conversion)

对应 Nginx 原理如下。`ESA实际回源CIDR` **必须替换**，不能直接粘贴：

```nginx
set_real_ip_from ESA实际回源CIDR;
# 每个实际网段分别写一行，包括适用的 IPv6 网段。
real_ip_header ali-real-client-ip;
real_ip_recursive off;
```

- 从当前站点控制台或官方支持获取适用的回源列表，**不要猜测网段**。
- 若使用 ESA 源站防护，官方流程会提供节点列表及更新方法。[ESA 源站防护](https://help.aliyun.com/zh/edge-security-acceleration/esa/user-guide/origin-protection/)
- **不要**信任 `0.0.0.0/0` 或 `::/0`。只有真实连接来自可信网段时，OpenResty 才应相信该头。
- 配置正确后，`$remote_addr` 才会恢复为 ESA 声称的客户端地址（它仍可能是邮箱图片代理的 IP）。[Nginx realip 模块](https://nginx.org/en/docs/http/ngx_http_realip_module.html)

### 8.2 应用 → 信任 OpenResty

`server.toml` 的 `trusted_proxies` **只填** app 直接看到的 OpenResty 对端地址，**不填** ESA 网段。

新版由 Docker 自动分配网络，不预设网关或容器 IP。初始 `[]` 表示先不信任转发头：

- 仍能记录、发通知；
- 但来源 IP 和创建限速可能暂时按同一个代理地址计算；
- 完成下面核对后再正式使用。

### 8.3 如何核对并填写

1. 通过公网域名请求一张测试图片。
2. 运行（只打印来源字段，不打印管理凭据或全部请求头）：

   ```sh
   docker compose exec -T app python -c 'import sqlite3,json; d=sqlite3.connect("/data/receipt.sqlite3"); r=d.execute("SELECT details FROM events ORDER BY created DESC LIMIT 1").fetchone(); x=json.loads(r[0]) if r else {}; print({k:x.get(k) for k in ("connection_ip","effective_source_ip","peer_is_trusted_proxy")})'
   ```

3. 确定该请求确实经过你的 OpenResty 后，把 `trusted_proxies` 改成输出里 `connection_ip` 对应的 IP `/32`（IPv6 用 `/128`），再重启 app。

   例如实际对端是 `172.22.0.1`，就填 `["172.22.0.1/32"]`。**不要照抄这个示例。**

4. 不要信任整个公网或所有 Docker 容器，也不要启用 Uvicorn 的 `proxy_headers`。
5. 重建网络或代理容器后，**重新核对**该地址。

字段含义：

| 字段 | 含义 |
| --- | --- |
| `connection_ip` | app 的直接对端地址 |
| `effective_source_ip` | 经可信代理链推导出的地址 |
| 请求头 | app 实际收到的字段，可能已被代理改写，并非公网原始报文 |

---

## 9. 按层排查：不要一次改所有配置

### 9.1 启动报 `Pool overlaps`

旧 Compose 固定使用 `172.30.97.0/24`，可能与服务器上已有 Docker 网段相交。新版去掉了 `ipam.config.subnet` 和两处 `ipv4_address`，由 Docker 自动选择可用网段。

**处理：**

1. 将新版 `compose.yaml` 放回**当前项目目录**。
2. 保留原 `server.toml` 和项目名。
3. 重新执行原启动命令。

**不要**删除 1Panel 或其他应用的网络，**不要** `network prune`，**不要** `down -v`。

若需手动改旧文件，把 app / nginx 两个服务的网络声明都改为：

```yaml
    networks:
      - receipt
```

文件末尾改为：

```yaml
networks:
  receipt: {}
```

网络名字 `receipt` 没变，变的是地址分配方式。

若本项目网络以前已成功创建并承载容器，Compose 可能提示网络配置需要重建。**仅在该提示出现时**，在本项目目录执行不带 `-v` 的 `docker compose down`，再启动。这会暂时停止本工具，但**不会**删除数据库卷；也不要操作其他项目。

若自动分配仍报地址池不足，需检查 Docker 守护进程的 `default-address-pools` 与当前网络分布。那是另一种情况，不能靠继续随机改网段解决。

### 9.2 分层检查表

按顺序从内到外查，正常了再查下一层：

| 检查 | 正常结果 | 失败时主要看 |
| --- | --- | --- |
| `docker compose ps` | app `healthy` | TOML、文件权限、数据库卷、app 日志 |
| 本机 `http://127.0.0.1:18000/health` | 返回 JSON | 映射端口、进程、`RECEIPT_PORT` |
| OpenResty 对应站点 | 返回 JSON | 网络模式、代理目标、重复 location、路径改写 |
| 公网 `https://域名/health` | 证书正常，返回 JSON | ESA 边缘证书、回源 Host/SNI/协议、源站证书校验 |
| 创建链接 | HTML 里是公网域名 | `public_base_url`、API 422/429、ESA 挑战 |
| 请求图片后刷新 | 事件数增加 | 缓存、404、静态图片规则是否截走请求 |
| `pending` 变为 `sent` | SMTP 已接收 | 密码/授权码、SMTP 网络、发件地址限制 |
| `sent` 后收件箱无邮件 | 最终收到 | 垃圾箱、隔离区、邮箱服务商投递日志 |

若本机正常而 ESA 返回 502，重点查 OpenResty 与 ESA 回源。源站自签名证书 + ESA 强制验证，也可能导致回源失败。

### 9.3 需要绕过 ESA 定位时

在可信管理机上检查源站：

```sh
curl --resolve receipt.example.com:443:你的源站IP https://receipt.example.com/health
```

自签名源站证书可能报验证错误。**仅在这一次定位中**可加 `-k` 看能否拿到 JSON，用来区分“证书问题”和“反代问题”。正式公网访问与客户端仍应使用正常证书验证。

---

## 10. OpenResty 使用 bridge 网络的分支

bridge 容器里的 `127.0.0.1` 指向它自己，**不能**照搬 host 方案。

1. 查实际网络名：

   ```sh
   docker inspect --format '{{json .NetworkSettings.Networks}}' 你的OpenResty容器名
   ```

2. 在项目 `.env` 中填写：

   ```env
   RECEIPT_PROXY_NETWORK=你实际查到的网络名
   ```

3. 让 app 加入同一个已有网络：

   ```sh
   docker compose -f compose.yaml -f compose.proxy-network.yaml up -d --build app
   ```

4. 把 1Panel 里的代理目标改成：

   ```text
   http://receipt-tool-app:8000
   ```

   这个网络别名由附加配置提供。app 创建后再保存、重载 OpenResty；若重建 app 后出现 502，重载代理让它重新解析地址。

5. `trusted_proxies` 填 OpenResty 在**共享网络里的实际 IP**（不要沿用 host 网关），再按第 8 节核对。

后续所有 Compose 命令都带这两个 `-f` 参数。默认回环端口仍可用于宿主机诊断，没有对公网开放。
