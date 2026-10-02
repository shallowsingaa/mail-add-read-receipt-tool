# 验证记录

验证日期：2026-10-02（Asia/Shanghai）。

## 2026-10-03：网络冲突修复

生产 Compose 已移除固定子网和容器 IP，让 Docker 分配可用网络。新增回归测试先在旧配置上失败，修复后 Windows 与 WSL 各 13 项测试全部通过。默认 Compose 和 bridge 附加文件均通过 `docker compose config --quiet`。

本机创建隔离测试网络占用旧的 `172.30.97.0/24` 后，使用新版模板的 app-only 部署仍成功创建网络，Docker 自动分配 `172.18.0.0/16`，健康接口返回 `status: ok`。测试结束后清理本轮创建的容器和网络，保留数据卷。未操作用户服务器上的网络。

代理信任默认改为 `[]`，部署文档要求通过真实代理请求核对 connection_ip，再信任准确的代理 IP。完整 HTTPS/SMTP 调试脚本仍使用独立的固定测试网段，不属于生产配置。

## 已通过

| 环境 | 验证 | 结果 |
| --- | --- | --- |
| Windows 11 x64，Python 3.12.14 | `python -m pytest -q` | 12 项通过 |
| Windows 11 x64 | PyInstaller 生成 exe，`--smoke-test`，实际 TUI 启动与 Ctrl+Q 退出 | 通过 |
| WSL2 Ubuntu 26.04，Python 3.14.4 | 相同锁定依赖、12 项测试；此前 `pip check` 通过 | 全部通过 |
| Docker Linux 引擎，Debian 13，Python 3.12.15 | 原 Dockerfile 构建、非 root UID/GID 10001、SQLite 数据卷、Compose 启动 | 通过 |
| Nginx 容器 | `nginx -t`、有效证书校验的本地 HTTPS 请求、HTTP 308 重定向 | 通过 |
| Docker 完整链路 | 两次 GET 和一次 HEAD → 三条事件 → SMTP 接收器实际收到三封独立邮件 | 通过 |
| Docker 完整链路 | 透明 PNG 解码为 1×1、alpha=0，HEAD 无正文，禁止缓存响应头 | 通过 |
| Docker 完整链路 | 邮件中的方法、查询参数、请求头、来源 IP；伪造 X-Forwarded-For 被 Nginx 覆盖 | 通过 |
| Docker 完整链路 | 重启 app 后链接和三条已投递记录保留；随后停用、访问返回 404、删除成功 | 通过 |

单元与集成测试还覆盖：管理凭据隔离、并发创建限速、SMTP 失败保存与退避、租约恢复、72 小时失败期限、手动重投、90 天清理，以及 TUI 创建/历史恢复/状态刷新/停用/删除。

## 1Panel 部署修订的验证

旧默认 Compose 的端口回归测试先失败，明确检出 `nginx binds host port 80` 与 `nginx binds host port 443`。新版默认将 Nginx 放入 `standalone` profile，应用只发布 `127.0.0.1:18000`；同一检查通过。Docker Compose 实际渲染的默认服务列表仅有 app。

另起隔离的 `receipt-apponly-check` 项目，使用新版默认网络和端口映射，仅替换成本机测试配置，实际启动成功且 healthy；通过 `http://127.0.0.1:18000/health` 获取正常 JSON。该项目没有启动 Nginx，没有绑定宿主机 80/443。

重新验证了调试部署的 Nginx HTTPS、透明 PNG、3 次 GET/HEAD 对应 3 封 SMTP 邮件、请求详情和代理来源头覆盖。修改后的调试生成脚本移除了 standalone profile 限制以便显式测试 Nginx，这个隔离测试不等于默认生产部署。

已核查 1Panel 官方 OpenResty 模板使用 host 网络，并据此提供常规接入步骤；bridge 方式提供独立附加 Compose 文件。未连接用户实际 1Panel、ESA 控制台或正式域名，因此实际源站证书策略、代理 peer IP、ESA 规则仍需按部署指南验证。用户已说明 ESA 对该域名完全不缓存，文档采用这一前提。

WSL 使用项目下独立的 `.venv-wsl`；它不会替换 Windows 的 `.venv`。Ubuntu 初始缺少 ensurepip，使用官方 PyPA get-pip 在该虚拟环境中安装 pip，没有安装系统级 Python 包。首次 WSL 测试出现已有 Windows pytest 缓存不可写的警告；本轮使用 `-o cache_dir=.venv-wsl/pytest-cache` 后该警告消失。

FastAPI/Starlette 的测试客户端对当前 httpx 兼容方式有弃用提示；本次所有功能测试通过。

## Docker 验证的隔离范围

使用 Docker Desktop 的 Linux 引擎，项目名 `receipt-debug`。调试配置由 `scripts/prepare-docker-debug.py` 根据生产模板生成：

- app 使用原服务端镜像和数据卷。
- Nginx 仅映射宿主机回环地址的 18080/18443，未占用公网 80/443。
- 使用本机自签名 localhost 证书，并将其作为测试客户端信任的 CA；未关闭证书校验。
- SMTP 指向隔离网络中的临时接收器；不投递到真实邮箱。
- 调试文件、测试邮件、管理凭据和临时证书位于 `.docker-test`，已被 Git 和 Docker 构建上下文忽略。
- 验证完成后关闭本次测试容器，未停止 Docker Desktop 或其他 WSL 发行版。

## 复现

在完整源码目录中，使用已安装开发依赖的 Python：

```sh
python scripts/prepare-docker-debug.py
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout .docker-test/certs/privkey.pem \
  -out .docker-test/certs/fullchain.pem -days 1 \
  -subj /CN=localhost -addext subjectAltName=DNS:localhost,IP:127.0.0.1
docker compose -p receipt-debug -f .docker-test/compose.yaml build app
docker compose -p receipt-debug -f .docker-test/compose.yaml up -d --wait
python scripts/check-docker-debug.py
docker compose -p receipt-debug -f .docker-test/compose.yaml restart app
# 等待 /health 恢复后执行：
python scripts/check-docker-debug.py --after-restart
docker compose -p receipt-debug -f .docker-test/compose.yaml down
```

验证默认 app-only 模式（不要同时运行另一个占用 18000 的应用）：

```sh
docker compose -p receipt-apponly-check -f .docker-test/app-only.yaml up -d --wait app
curl -fsS http://127.0.0.1:18000/health
docker compose -p receipt-apponly-check -f .docker-test/app-only.yaml ps
docker compose -p receipt-apponly-check -f .docker-test/app-only.yaml down
```

WSL 的完整测试：

```sh
cd /mnt/e/Projects/mail-add-read-receipt-tool
.venv-wsl/bin/python -m pytest -q -o cache_dir=.venv-wsl/pytest-cache
.venv-wsl/bin/python -m pip check
```

## 尚需部署后验证

你的公网域名、正式证书、SMTP 账号凭据和真实邮箱最终收件情况，以及实际邮件编辑器能否保留外链图片。这些需要实际配置和目标邮箱，不能由本地测试替代。
