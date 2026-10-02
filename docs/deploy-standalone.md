# 独立 Nginx 部署（没有现成反向代理时）

**适用场景：** 服务器上**没有**其他程序占用 80/443，希望由本项目自己提供 HTTPS 入口。

**不要用本文的情况：** 服务器已有 1Panel / OpenResty。那请改看 [1Panel＋ESA 部署指南](deploy-1panel-esa.md)。

---

## 一、准备什么

| 需要 | 说明 |
| --- | --- |
| Docker Engine + Compose 插件 | 安装参见 [Debian 官方说明](https://docs.docker.com/engine/install/debian/) |
| 指向本服务器的域名 | 例如 `receipt.example.com` |
| 匹配该域名的有效证书 | 项目不自动签发 / 续期，见第三节 |

### 准备文件

```sh
test -f server.toml || cp server.example.toml server.toml
mkdir -p certs
```

### 编辑 `server.toml`

至少核对下面几项：

| 项 | 填什么 |
| --- | --- |
| `public_base_url` | 实际公网 HTTPS 域名，如 `https://receipt.example.com` |
| `database` | 保持 `/data/receipt.sqlite3` |
| `trusted_proxies` | **先保持 `[]`**。启动后按真实请求核对 Nginx 的 `connection_ip`，再填写该地址的 `/32` 或 `/128`。方法见 [主指南第 8 节](deploy-1panel-esa.md) |
| `[smtp]` | 按 [运维手册](operations.md) 填写；也可先留空 |

初始 `trusted_proxies = []` 时仍能记录和发信，只是来源 IP 与创建限速暂时按代理地址计算。

### 准备证书

把 `deploy/nginx.conf` 中两处 `receipt.example.com` 换成实际域名。

证书文件按容器约定放在：

```text
certs/fullchain.pem    # 证书完整链
certs/privkey.pem      # 私钥
```

这些文件名是**容器配置约定**，不是要求你改动证书本身。

- 证书若在其他受管理目录，可把 Compose 里 `./certs:/etc/nginx/tls:ro` 的源路径改过去；容器内目标路径和 Nginx 配置里的证书路径保持对应。
- 若文件名也不同，同步改 Nginx 配置里的文件名。
- 私钥只由 Nginx 读取，**app 不读私钥**。

配置文件权限（容器用户 UID/GID 为 10001）：

```sh
sudo chown root:10001 server.toml
sudo chmod 640 server.toml
```

---

## 二、显式启用 Nginx 并启动

默认启动**不包含** Nginx。只有带上 `--profile standalone`，才会绑定公网 80/443。

```sh
docker compose --profile standalone config --quiet
docker compose --profile standalone up -d --build
docker compose --profile standalone ps
docker compose --profile standalone exec nginx nginx -t
curl -fsS https://你的域名/health
```

若报端口占用：先用 `sudo ss -ltnp` 和 `docker ps` 查是谁占了端口。**不要**为了腾出端口去停与本项目无关的服务。

---

## 三、证书如何申请、续期

项目**不会**自动签发或续期证书。

- 已有可用证书：直接按第一节放置即可。
- 还没有证书：按 [Certbot 官方说明](https://certbot.eff.org/instructions) 选择适合系统的安装与验证方式。

### 用 Certbot standalone 验证的示例

standalone 验证需要 80 端口空闲，因此要在**启动本项目 Nginx 之前**申请：

```sh
sudo certbot certonly --standalone -d 你的域名
sudo cp /etc/letsencrypt/live/你的域名/fullchain.pem certs/fullchain.pem
sudo cp /etc/letsencrypt/live/你的域名/privkey.pem certs/privkey.pem
sudo chmod 600 certs/privkey.pem
```

### 续期后

把新证书同步到 `certs/`，再校验并重载：

```sh
docker compose --profile standalone exec nginx nginx -t
docker compose --profile standalone exec nginx nginx -s reload
```

若续期仍用 standalone HTTP 验证，需要临时释放 80 端口；也可改用 DNS 验证，避免占用 80。

证书的获取与续期交给你的证书管理方案即可，不必给 app 添加任何 TLS 配置。

---

## 四、日常修改与停止

| 改了什么 | 之后执行 |
| --- | --- |
| `server.toml` | `docker compose --profile standalone restart app` |
| Nginx 配置 / 证书 | 先 `nginx -t`，再 `nginx -s reload` |

常用命令（都带上 profile）：

```sh
docker compose --profile standalone restart app
docker compose --profile standalone logs --tail=80 app
docker compose --profile standalone down
```

**不要加 `-v`**，否则会删除 SQLite 数据卷。

通知状态、备份、更新等规则见 [运维手册](operations.md)。
