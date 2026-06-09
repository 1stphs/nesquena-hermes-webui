# Nesquena Hermes API Service

这是基于开源 `hermes-webui` 二次开发的内部 fork,当前定位是 Hermes API service,用于承载 token-login、外部前端代理对接和 routes 拆分维护。

本仓库已删除内置静态 WebUI、PWA、UI 原型文档和旧 UI 测试。后续维护优先看这份 README,再看相邻代码和 `docs/` 中仍保留的运行文档。

## 1. 快速入口

核心入口:

- `server.py`: HTTP server shell,只负责通用请求生命周期。
- `bootstrap.py`: 本地启动、依赖检查、health wait、可选打开浏览器。
- `start.sh`: shell 启动包装。
- `ctl.sh`: 常驻进程生命周期管理。
- `Dockerfile`: 容器镜像。
- `docker-compose.yml`: 当前部署 compose 配置。
- `requirements.txt`: API service 最小 Python 依赖,当前只有 `pyyaml>=6.0`。

API:

- `api/routes.py`: 稳定 API 路由入口。
- `api/routes_dispatcher.py`: 主 dispatch 实现,按需同步 `api.routes` 全局绑定。
- `api/routes_handlers/`: 从 `api/routes.py` 拆出的 endpoint handlers。
- `api/routes_helpers/`: routes 共享 helper。

## 2. 本地启动

```bash
python3 bootstrap.py
```

或使用 shell 启动脚本:

```bash
./start.sh
```

常驻进程:

```bash
./ctl.sh start
./ctl.sh status
./ctl.sh logs --lines 100
./ctl.sh restart
./ctl.sh stop
```

## 3. Docker 与部署

当前生产部署以仓库内 `docker-compose.yml` 为准。172 服务器部署 runbook 位置:

- `docs/deploy-172.md`: 从本地电脑提交/push、SSH 到服务器拉取代码、`docker compose` 重建、服务器路径、容器关系和 smoke test 步骤。

其他保留文档:

- `docs/docker.md`: Docker 通用说明。
- `docs/supervisor.md`: process supervisor 说明。
- `docs/troubleshooting.md`: 常见故障排查。
- `docs/api-docs.md`: API 说明。

本 fork 不再维护 GitHub Actions workflow。部署和验证以本地命令、服务器 runbook 和实际容器状态为准。

## 4. 架构速查

Hermes API service 是一个 Python HTTP API 服务,不再承载内置浏览器 UI:

- `server.py` 启动 `ThreadingHTTPServer`,设置 socket keepalive、请求日志、鉴权和 profile cookie 上下文。
- `api/routes.py` 是稳定 public route entrypoint,暴露 `handle_get`、`handle_post`、`handle_patch`、`handle_delete`。
- `api/routes_dispatcher.py` 承载主 dispatch 实现,按需同步 `api.routes` 全局绑定,保留历史 monkeypatch surface。
- `api/routes_handlers/` 承载从 `api/routes.py` 拆出的 endpoint handlers。
- `api/routes_helpers/` 承载 routes 共享 helper,例如 CSRF、cron、model resolve、SSE approval 等。

运行时状态默认写在 `HERMES_WEBUI_STATE_DIR`;Hermes profile/home 相关路径由 `HERMES_HOME`、`HERMES_CONFIG_PATH`、`HERMES_WEBUI_AGENT_DIR` 等环境变量控制。

API-only 路由约定:

- `GET /`: 返回 JSON 服务信息。
- `GET /health`: 运维健康检查。
- `GET /index.html`、`/login`、`/session/*`、`/static/*`、`/manifest.json`: 返回 JSON `410`,表示内置 WebUI 已移除。
- `GET /sw.js`: 返回卸载旧 Service Worker 的临时迁移脚本,用于清理存量浏览器缓存。
- 外部前端继续通过 `POST /api/auth/token-login` 换取 `HttpOnly hermes_session` cookie,后续请求带 `credentials: include`。

## 5. 请求链路

1. `server.py` 的 `Handler` 接收 HTTP request。
2. `check_auth()` 验证登录态;`OPTIONS` 直接返回 CORS preflight。
3. `get_profile_cookie()` 设置本请求 profile context。
4. `handle_get/post/patch/delete()` 进入 `api.routes`。
5. `api.routes` 调用 dispatcher 或薄壳 handler。
6. 具体 endpoint 在 `api/routes_handlers/`、`api/routes_helpers/` 或业务模块中完成逻辑。
7. 所有响应通过 `j()`、`t()`、`bad()` 等 helper 统一输出。

## 6. 路由拆分契约

`api/routes.py` 仍是对外稳定入口,即使真实实现迁移到 `api/routes_dispatcher.py` 或 `api/routes_handlers/*`,也要保持以下约束:

- `server.py` 只 import `api.routes.handle_*`。
- 既有测试和外部 patch 可能仍使用 `patch("api.routes.<name>")`。
- 搬 handler 时优先保留 `api.routes` 薄壳或 re-export。
- 不要因为实现下沉就移除被 source-level tests 锁定的函数名、字符串或 import surface。
- 每次 routes 拆分后运行 `python scripts/scan_routes_contracts.py --check`。

路由拆分事实源:

- `api/routes-handlers-contract.md`
- `api/routes-refactor-step1.md`
- `api/routes-refactor-step2.md`
- `api/routes-refactor-followups.md`
- `api/routes-refactor-report.md`

`api/routes_dispatcher.py` 在 call time 同步 `api.routes` 的 globals。这是为了让旧测试里的 monkeypatch 仍然命中运行时逻辑。新增 dispatcher 逻辑时,不要绕过这个同步机制。

## 7. Handler 分层

`api/routes_handlers/` 按业务面拆分:

- approval / approval_extra
- chat / streaming
- cron_read / cron_write
- file / file_extra
- handoff
- live_models
- logs
- mcp
- memory
- profile
- session_io / session_extra
- skill
- terminal
- workspace / workspace_extra

新增 handler 以现有 `_base.py` 和相邻模块风格为准,不要引入新 routing framework。

## 8. 外部前端接入

当前服务通过 `/api/*` 提供 profile、session、chat、stream、token-login 等接口。外部前端接入细节见本地专用文档:

- `ljl-docs/frontend-hermes-integration.md`

注意:`ljl-docs/` 是本地专用资料区,默认不进入 Git 跟踪。

## 9. 验证命令

全量测试:

```bash
pytest tests/ -v --timeout=60
```

routes 拆分契约检查:

```bash
python scripts/scan_routes_contracts.py --check
```

Python 语法/导入面轻量检查:

```bash
python -m compileall -q api
```

`tests/conftest.py` 会为测试进程设置隔离的 `HERMES_WEBUI_STATE_DIR`、`HERMES_HOME`、`HERMES_CONFIG_PATH` 和测试端口。不要让测试读写真实 `~/.hermes`。

用户 Skill 安全扫描 / 有效性评测上线前，需要先检查 NoCoBase `hermes_user_skills` 是否存在测试结果字段:

```bash
python scripts/ensure_user_skill_test_fields.py
```

缺字段时脚本默认只读退出，不会改 schema。确认生产 schema 写操作后再运行:

```bash
python scripts/ensure_user_skill_test_fields.py --apply
```

`--apply` 只创建缺失的 `security_test_result`、`security_tested_at`、`availability_test_result`、`availability_tested_at` 字段，不修改或删除已有记录；执行前仍要按生产 schema 变更流程确认。

## 10. 清理边界

不要删除:

- `LICENSE`: MIT license 要求保留原许可声明。
- `requirements.txt`: bootstrap 和 Docker init 仍依赖它安装 API service 最小依赖。
- `api/routes-*.md` / `api/routes-handlers-contract.md`: routes 拆分的无上下文续跑资料。
- `docs/deploy-172.md`: 当前部署事实源。

## 11. 许可

本项目基于 MIT License 开源项目二次开发,原许可声明保留在 `LICENSE`。
