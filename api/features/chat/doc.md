# api/features/chat

## 职责

`api/features/chat` 提供 **聊天相关的横切能力**：与 Hermes Agent `state.db` 的互操作、流式计量、用量遥测、以及 agent 澄清（clarify）交互状态。

**负责：**

- 从 `state.db` 读取/规范化 agent session 行（gateway、CLI、cron 等来源）
- 可选将 WebUI session 元数据同步到 `state.db`（`sync_to_insights`）
- SSE `metering` 事件：TPS / high / low 统计
- 聊天完成后向 NocoBase 写入用量事件（可选）
- Clarify 提示的 pending 队列、SSE 订阅与 gateway 回调注册

**不负责：**

- 聊天 HTTP handler 与路由（`api/routes_handlers/chat.py`）
- Agent 执行主循环（`api/runtime/streaming.py`，但会调用本目录模块）
- WebUI JSON `Session` 持久化（`api/core/models.py`）
- Approval 流程（`api/routes_handlers/approval.py`）

顶层 `api/agent_sessions.py`、`api/state_sync.py`、`api/metering.py`、`api/usage_telemetry.py`、`api/clarify.py` 为兼容 shim。

## 功能

| 模块 | 主要能力 |
|------|----------|
| `agent_sessions.py` | `read_importable_agent_session_rows`、`normalize_agent_session_source`、session lineage；供 session 列表与 gateway watcher 复用 |
| `state_sync.py` | `sync_session_to_state_db`（opt-in）；绝对 token 计数写入 insights |
| `metering.py` | `meter()` 单例：`begin_session`、`record_token`、`get_interval`；供 streaming ticker 发 SSE |
| `usage_telemetry.py` | `record_chat_usage_done_async`；队列 worker 写 NocoBase `hermes_chat_usage_events` |
| `clarify.py` | `submit_pending`、`get_pending`、`register_gateway_notify`；clarify SSE 订阅表 |

关键流程：

1. **Session 列表聚合**：`routes_dispatcher` / `gateway_watcher` → `agent_sessions` 读 DB → 与 WebUI `SESSIONS` 合并展示。
2. **流式计量**：`streaming._run_agent_streaming` → `meter().record_*` → SSE `metering` 事件。
3. **Clarify**：agent 工具请求澄清 → `clarify` 注册 pending → `GET /api/clarify/stream` SSE → `POST /api/clarify/respond`。
4. **用量遥测**：turn 结束 → `usage_telemetry` 异步 POST NocoBase（`HERMES_USAGE_TELEMETRY_ENABLED`）。

相关路由：

- `GET /api/clarify/pending`、`/api/clarify/stream`、`POST /api/clarify/respond`
- Metering / telemetry 无独立 REST 路径，经 chat stream SSE 与后台 worker 生效

## 依赖边界

**依赖：**

- `api/core/profiles.py`、`api/core/config.py`（`state.db` 路径）
- 可选：`hermes_state.SessionDB`
- NocoBase REST（`usage_telemetry`）
- `api/runtime/streaming.py` 调用本目录（反向：metering / clarify / telemetry 在 streaming 内 import）

**被依赖：**

- `api/core/models.py`（agent session 元数据）
- `api/runtime/gateway_watcher.py`、`api/runtime/streaming.py`
- `api/routes_dispatcher.py`（clarify 路径）

**shim 关系：** 否。顶层 shim 指向 `api.features.chat.*`。

## 溯源

| 类型 | 位置 |
|------|------|
| 实现 | `api/features/chat/*.py` |
| 兼容入口 | `api/agent_sessions.py`、`api/state_sync.py`、`api/metering.py`、`api/usage_telemetry.py`、`api/clarify.py` |
| 路由入口 | `api/routes_dispatcher.py`（clarify）；chat/stream 经 `api/routes_handlers/chat.py`、`streaming.py` |
| 测试 | `api/test_usage_telemetry.py` |
| 契约检查 | `scripts/scan_routes_contracts.py --check` |