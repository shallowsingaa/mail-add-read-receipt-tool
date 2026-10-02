# 没有现成反向代理时：独立 Nginx 部署

**已有 1Panel/OpenResty 的服务器不要使用这份步骤。** 请阅读 [1Panel＋ESA 指南](deploy-1panel-esa.md)。这条路线用于没有其他程序占用 80/443、希望本项目同时提供 HTTPS 入口的服务器。

## 准备文件

要求：Docker Engine 与 Compose 插件、指向服务器的域名、匹配该域名的有效证书。Docker 安装参照 [Debian 官方安装说明](https://docs.docker.com/engine/install/debian/)。

```sh
test -f server.toml || cp server.example.toml server.toml
mkdir -p certs
```

编辑 `server.toml`：

- `public_base_url` 为实际公网 HTTPS 域名。
- `database` 保持 `/data/receipt.sqlite3`。
- `trusted_proxies` 改为 **`["172.30.97.2/32"]`**，对应这条方案中 Nginx 的固定地址，而非默认 host 方案的网关。
- 按 [运维手册](operations.md) 填 SMTP，也可先保持未配置状态。

修改 `deploy/nginx.conf` 的两处 `receipt.example.com`，换成实际域名。证书完整链和私钥分别放在：

```text
certs/fullchain.pem
certs/privkey.pem
```

这些名字是容器配置约定，不是要求你改变证书本身。证书如果保存在其他受管理的目录，可以把 Compose 中 `./certs:/etc/nginx/tls:ro` 的源路径换成那个目录，容器目标路径和 Nginx 证书路径保持对应。若目录里文件名也不同，调整 Nginx 配置中的文件名。

私钥由 Nginx 读取，app 不读私钥。服务端 TOML 应允许 UID/GID 10001 读取：

```sh
sudo chown root:10001 server.toml
sudo chmod 640 server.toml
```

## 显式启用 Nginx

```sh
docker compose --profile standalone config --quiet
docker compose --profile standalone up -d --build
docker compose --profile standalone ps
docker compose --profile standalone exec nginx nginx -t
curl -fsS https://你的域名/health
```

默认启动不包含 Nginx；必须带 `--profile standalone` 才会绑定公网 80/443。若报端口占用，先查 `sudo ss -ltnp` 与 `docker ps`，不要停止与本项目无关的服务来强行腾出端口。

## 证书如何申请、续期

项目不会自动签发或续期证书。已有可用证书直接使用即可。没有证书时按 [Certbot 官方说明](https://certbot.eff.org/instructions) 选择适合系统的安装及验证方式。

例如 standalone 验证需要 80 端口空闲，在启动本项目 Nginx **之前**运行：

```sh
sudo certbot certonly --standalone -d 你的域名
sudo cp /etc/letsencrypt/live/你的域名/fullchain.pem certs/fullchain.pem
sudo cp /etc/letsencrypt/live/你的域名/privkey.pem certs/privkey.pem
sudo chmod 600 certs/privkey.pem
```

续期后同步新证书，再重载：

```sh
docker compose --profile standalone exec nginx nginx -t
docker compose --profile standalone exec nginx nginx -s reload
```

若续期仍使用 standalone HTTP 验证，需要临时释放 80；可改用 DNS 验证避免占用这个端口。证书本身的获取与续期应交给你的证书管理方案，不需向 app 添加 TLS 配置。

## 修改配置与停止

修改 `server.toml` 后重启 app；修改 Nginx 配置后先 `nginx -t`，再 reload。日常 Compose 操作继续带上该 profile：

```sh
docker compose --profile standalone restart app
docker compose --profile standalone logs --tail=80 app
docker compose --profile standalone down
```

不要加 `-v`，否则删除 SQLite 数据卷。通知和数据规则、备份方法见 [运维手册](operations.md)。

