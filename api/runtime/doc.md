# api/runtime

## 职责

`api/runtime` 承载 **进程生命周期、Agent 执行、后台监听与运维探测**，是 WebUI 与 `hermes-agent` 运行时之间的执行层。

**负责：**

- SSE 流式聊天引擎与 agent 线程调度（含取消、错误分类、provider 解析挂钩）
- Gateway session 变更监听（`state.db` → SSE 推送）
- 启动时 session `.bak` 恢复、嵌入式 workspace 终端
- Agent / gateway 心跳与主机资源指标（VPS 面板）
- `/background`、`/btw` 任务跟踪；`/retry`、`/undo` 等 slash 命令的 session 变更
- 启动辅助（敏感文件 chmod、依赖探测）

**不负责：**

- Session JSON 模型定义（`api/core/models.py`）
- HTTP 路由表（`api/routes_dispatcher.py` + `api/routes_handlers/streaming.py` 等）
- Cron job 定义与调度逻辑主体（`api/services/cron_service.py`、`hermes-agent` cron）
- Kanban、文件上传等业务 feature（`api/features/*`）

顶层 `api/streaming.py`、`api/gateway_watcher.py`、`api/session_recovery.py` 等为兼容 shim。

## 功能

| 模块 | 主要能力 |
|------|----------|
| `streaming.py` | `_sse`、`_run_agent_streaming`、`cancel_stream`；`STREAMS` / `CANCEL_FLAGS`；metering / usage_telemetry / clarify / user_provider 挂钩 |
| `gateway_watcher.py` | 后台线程轮询 `state.db`，向 SSE 订阅者推送 gateway session 变更 |
| `session_recovery.py` | `recover_all_sessions_on_startup`、`recover_session`、`inspect_session_recovery_status` |
| `terminal.py` | `TerminalSession`：独立子进程 shell，不污染全局 `os.environ` |
| `background.py` | `track_background` / `track_btw` / `complete_background` 内存任务表 |
| `session_ops.py` | `retry_last`、`undo_last`、`status_summary`、`usage_summary` |
| `agent_health.py` | `build_agent_health_payload` → `GET /api/health/agent` |
| `system_health.py` | `build_system_health_payload` → `GET /api/system/health` |
| `startup.py` | `fix_credential_permissions`、agent 依赖自检 |

关键流程：

1. **聊天流**：`POST` chat → `routes_handlers/chat.py` → `streaming._run_agent_streaming` → SSE `GET` stream endpoint。
2. **启动**：`server.py` 调用 `recover_all_sessions_on_startup`、`start_watcher`、`fix_credential_permissions`。
3. **终端**：`POST /api/terminal/start` → `terminal.py` 起 PTY → `output` 长轮询或 SSE。

相关路由（分发在 `api/routes_dispatcher.py`，部分 handler 在 `api/routes_handlers/`）：

- `GET /api/health/agent`、`GET /api/system/health`
- `POST /api/session/recover`（recovery）
- `POST /api/terminal/*`、`GET /api/terminal/output`
- `GET /api/background/status`、`POST /api/background`
- Stream 相关路径由 `api/routes_handlers/streaming.py` 绑定 `api.streaming` shim

## 依赖边界

**依赖：**

- `api/core/config.py`、`api/core/models.py`、`api/core/workspace.py`、`api/core/helpers.py`
- `api/features/chat/metering.py`、`usage_telemetry.py`、`clarify.py`
- `api/providers_runtime/user_provider.py`、`api/authn/oauth.py`
- `api/routes_helpers/request_limits.py`
- `api/services/cron_service.py`（cronjob bridge 懒安装）
- 可选：`run_agent.AIAgent`（`hermes-agent`）、`gateway.status`、`hermes_state.SessionDB`

**被依赖：**

- `server.py`（启动恢复、watcher）
- `api/routes.py`、`api/routes_dispatcher.py`、`api/routes_handlers/streaming.py`
- `api/authn/oauth.py`（env lock）

**shim 关系：** 否。顶层 `api/streaming.py` 等指向本目录。

## 溯源

| 类型 | 位置 |
|------|------|
| 实现 | `api/runtime/*.py`（见上表） |
| 兼容入口 | `api/streaming.py`、`api/gateway_watcher.py`、`api/session_recovery.py`、`api/terminal.py`、`api/background.py`、`api/session_ops.py`、`api/agent_health.py`、`api/system_health.py`、`api/startup.py` |
| 路由入口 | `api/routes_dispatcher.py`；`api/routes_handlers/streaming.py`、`terminal.py` |
| 启动 | `server.py` |
| 测试 | `api/test_request_limits.py`（chat slot 与 streaming 联动） |
| 路由对照 | `api/routes-refactor-step2.md`（`/api/system/health`、`/api/health/agent` 与测试文件映射） |
| 契约检查 | `scripts/scan_routes_contracts.py --check` |