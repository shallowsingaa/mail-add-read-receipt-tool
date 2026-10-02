# 验证记录

验证日期：2026-10-02（Asia/Shanghai）。

本文记录已经做过的测试与结论，供复查、复现和排查时对照。普通用户不需要阅读本文。

---

## 2026-10-03：网络冲突修复

**问题：** 旧版生产 Compose 固定了子网和容器 IP，会与服务器上已有 Docker 网络冲突。

**处理：** 移除固定子网和容器 IP，改由 Docker 自动分配网络。回归测试先在旧配置上失败，修复后 Windows 与 WSL 各 13 项测试全部通过。默认 Compose 与 bridge 附加文件均通过 `docker compose config --quiet`。

**隔离验证：** 本机创建测试网络占用旧的 `172.30.97.0/24` 后，使用新版模板的 app-only 部署仍能成功建网，Docker 自动分配 `172.18.0.0/16`，`/health` 返回 `status: ok`。测试结束后清理本轮创建的容器和网络，保留数据卷，未操作用户服务器上的网络。

**配置变化：** 代理信任默认改为 `[]`。部署文档要求先通过真实代理请求核对 `connection_ip`，再信任准确的代理 IP。完整 HTTPS/SMTP 调试脚本仍使用独立固定测试网段，不属于生产配置。

---

## 已通过项

| 环境 | 验证内容 | 结果 |
| --- | --- | --- |
| Windows 11 x64，Python 3.12.14 | `python -m pytest -q` | 12 项通过 |
| Windows 11 x64 | PyInstaller 生成 exe → `--smoke-test` → 实际 TUI 启动与 Ctrl+Q 退出 | 通过 |
| WSL2 Ubuntu 26.04，Python 3.14.4 | 相同锁定依赖、12 项测试；此前 `pip check` 通过 | 全部通过 |
| Docker Linux 引擎，Debian 13，Python 3.12.15 | 原 Dockerfile 构建、非 root UID/GID 10001、SQLite 数据卷、Compose 启动 | 通过 |
| Nginx 容器 | `nginx -t`、有效证书校验的本地 HTTPS、HTTP 308 重定向 | 通过 |
| Docker 完整链路 | 两次 GET + 一次 HEAD → 三条事件 → SMTP 接收器收到三封独立邮件 | 通过 |
| Docker 完整链路 | 透明 PNG 解码为 1×1、alpha=0；HEAD 无正文；禁止缓存响应头 | 通过 |
| Docker 完整链路 | 通知含方法、查询参数、请求头、来源 IP；伪造 `X-Forwarded-For` 被 Nginx 覆盖 | 通过 |
| Docker 完整链路 | 重启 app 后链接和三条已投递记录保留；随后停用 → 访问 404 → 删除成功 | 通过 |

单元与集成测试还覆盖：

- 管理凭据隔离；
- 并发创建限速；
- SMTP 失败保存与退避；
- 租约恢复；
- 72 小时失败期限；
- 手动重投；
- 90 天清理；
- TUI 的创建 / 历史恢复 / 状态刷新 / 停用 / 删除。

---

## 1Panel 部署修订的验证

旧默认 Compose 的端口回归测试**先失败**，明确检出 `nginx binds host port 80` 与 `nginx binds host port 443`。新版默认将 Nginx 放入 `standalone` profile，应用只发布 `127.0.0.1:18000`；同一检查通过。Docker Compose 实际渲染的默认服务列表**仅有 app**。

另起隔离项目 `receipt-apponly-check`，使用新版默认网络和端口映射，仅替换为本机测试配置：

- 实际启动成功且 healthy；
- `http://127.0.0.1:18000/health` 返回正常 JSON；
- 该项目没有启动 Nginx，没有绑定宿主机 80/443。

重新验证了调试部署中的 Nginx HTTPS、透明 PNG、3 次 GET/HEAD 对应 3 封 SMTP 邮件、请求详情，以及代理来源头覆盖。修改后的调试生成脚本移除了 standalone profile 限制以便显式测试 Nginx——该隔离测试**不等于**默认生产部署。

已核查 1Panel 官方 OpenResty 模板使用 host 网络，并据此提供常规接入步骤；bridge 方式提供独立附加 Compose 文件。**未连接**用户实际 1Panel、ESA 控制台或正式域名，因此实际源站证书策略、代理 peer IP、ESA 规则仍需按部署指南验证。用户已说明 ESA 对该域名完全不缓存，文档采用这一前提。

WSL 使用项目下独立的 `.venv-wsl`，不会替换 Windows 的 `.venv`。Ubuntu 初始缺少 ensurepip，使用官方 PyPA get-pip 在该虚拟环境中安装 pip，未安装系统级 Python 包。首次 WSL 测试出现已有 Windows pytest 缓存不可写的警告；本轮使用 `-o cache_dir=.venv-wsl/pytest-cache` 后警告消失。

FastAPI/Starlette 测试客户端对当前 httpx 兼容方式有弃用提示；本次所有功能测试通过。

---

## Docker 验证的隔离范围

使用 Docker Desktop 的 Linux 引擎，项目名 `receipt-debug`。调试配置由 `scripts/prepare-docker-debug.py` 根据生产模板生成：

| 项 | 隔离措施 |
| --- | --- |
| app | 使用原服务端镜像和数据卷 |
| Nginx | 仅映射宿主机回环的 18080/18443，未占用公网 80/443 |
| 证书 | 本机自签名 localhost 证书，并作为测试客户端信任的 CA；**未**关闭证书校验 |
| SMTP | 指向隔离网络中的临时接收器，不投递到真实邮箱 |
| 文件 | 调试文件、测试邮件、管理凭据和临时证书位于 `.docker-test`，已被 Git 和 Docker 构建上下文忽略 |
| 收尾 | 验证完成后关闭本次测试容器；未停止 Docker Desktop 或其他 WSL 发行版 |

---

## 复现步骤

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

---

## 尚需部署后验证

以下内容依赖真实环境，不能由本地测试替代：

1. 你的公网域名与正式证书；
2. SMTP 账号凭据；
3. 真实邮箱的最终收件情况；
4. 实际邮件编辑器是否会保留外链图片。

请在正式部署后按 [部署指南第 7 节](docs/deploy-1panel-esa.md) 的人工验收清单走一遍。
