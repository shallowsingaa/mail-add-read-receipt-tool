# 验证记录

验证日期：2026-10-02（Asia/Shanghai）。

## 已通过

| 环境 | 验证 | 结果 |
| --- | --- | --- |
| Windows 11 x64，Python 3.12.14 | `python -m pytest -q` | 11 项通过 |
| Windows 11 x64 | PyInstaller 生成 exe，`--smoke-test`，实际 TUI 启动与 Ctrl+Q 退出 | 通过 |
| WSL2 Ubuntu 26.04，Python 3.14.4 | 相同锁定依赖、相同 11 项测试、`pip check` | 全部通过 |
| Docker Linux 引擎，Debian 13，Python 3.12.15 | 原 Dockerfile 构建、非 root UID/GID 10001、SQLite 数据卷、Compose 启动 | 通过 |
| Nginx 容器 | `nginx -t`、有效证书校验的本地 HTTPS 请求、HTTP 308 重定向 | 通过 |
| Docker 完整链路 | 两次 GET 和一次 HEAD → 三条事件 → SMTP 接收器实际收到三封独立邮件 | 通过 |
| Docker 完整链路 | 透明 PNG 解码为 1×1、alpha=0，HEAD 无正文，禁止缓存响应头 | 通过 |
| Docker 完整链路 | 邮件中的方法、查询参数、请求头、来源 IP；伪造 X-Forwarded-For 被 Nginx 覆盖 | 通过 |
| Docker 完整链路 | 重启 app 后链接和三条已投递记录保留；随后停用、访问返回 404、删除成功 | 通过 |

单元与集成测试还覆盖：管理凭据隔离、并发创建限速、SMTP 失败保存与退避、租约恢复、72 小时失败期限、手动重投、90 天清理，以及 TUI 创建/历史恢复/状态刷新/停用/删除。

WSL 使用项目下独立的 `.venv-wsl`；它不会替换 Windows 的 `.venv`。Ubuntu 初始缺少 ensurepip，使用官方 PyPA get-pip 在该虚拟环境中安装 pip，没有安装系统级 Python 包。WSL 测试出现已有 Windows pytest 缓存目录不可写的警告，不影响测试通过；以后使用 `-o cache_dir=.venv-wsl/pytest-cache` 可避免共享缓存目录。

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

WSL 的完整测试：

```sh
cd /mnt/e/Projects/mail-add-read-receipt-tool
.venv-wsl/bin/python -m pytest -q -o cache_dir=.venv-wsl/pytest-cache
.venv-wsl/bin/python -m pip check
```

## 尚需部署后验证

你的公网域名、正式证书、SMTP 账号凭据和真实邮箱最终收件情况，以及实际邮件编辑器能否保留外链图片。这些需要实际配置和目标邮箱，不能由本地测试替代。

