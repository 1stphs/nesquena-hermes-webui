# api/features/misc

## 职责

`api/features/misc` 汇集 **不便归入 chat/cron/files 的辅助 feature**：命令注册表暴露、看板桥接、检查点回滚、扩展注入、自更新检查与 dashboard 探测。

**负责：**

- 将 `hermes_cli.commands.COMMAND_REGISTRY` 过滤后暴露给前端
- Kanban 全量 CRUD + SSE（`hermes_cli.kanban_db` 为唯一数据源）
- Agent `CheckpointManager` 检查点的 list/diff/restore
- 可选同源 extension 静态资源与 script/style 注入
- WebUI / agent git 自更新检查与应用
- 本机 loopback Hermes dashboard `GET /api/status` 安全探测

**不负责：**

- 核心聊天、session、profile（`api/core`、`api/runtime`）
- Kanban 数据库 schema（`hermes-agent` / `hermes_cli.kanban_db`）
- 外部前端静态资源构建（`digital_employee` 仓库）

顶层 `api/commands.py`、`api/kanban_bridge.py`、`api/rollback.py`、`api/extensions.py`、`api/dashboard_probe.py`、`api/updates.py` 为兼容 shim，指向本目录。

## 功能

| 模块 | 主要 API / 能力 |
|------|----------------|
| `commands.py` | `list_commands` → `GET /api/commands` |
| `kanban_bridge.py` | `/api/kanban/*`：tasks、boards、dependencies、comments、SSE stream |
| `rollback.py` | `GET /api/rollback/list|diff`、`POST /api/rollback/restore` |
| `extensions.py` | `EXTENSION_ROUTE_PREFIX` 静态服务；env 配置 script/stylesheet URL |
| `updates.py` | 自更新检查缓存、`git fetch` 对比 upstream；活跃 stream 时拒绝 apply |
| `dashboard_probe.py` | `GET /api/dashboard/status`、`/api/dashboard/config`；仅 loopback SSRF 防护 |

关键流程：

1. **命令列表**：前端加载 → `/api/commands` → 过滤 `gateway_only` 与 `_NEVER_EXPOSE` → 降级为空数组若 agent 未安装。
2. **Kanban**：`/api/kanban/tasks` 等 → `kanban_bridge` → `hermes_cli.kanban_db`；SSE 推送板面变更。
3. **回滚**：按 workspace 列 checkpoint → diff/restore shadow git 仓库。
4. **Dashboard 探测**：服务端请求 `127.0.0.1:9119/api/status`，不暴露任意 URL fetch。

## 依赖边界

**依赖：**

- `api/core/helpers.py`（`j`、`bad`、安全头）
- `api/core/config.py`（`REPO_ROOT`、`STREAMS`，updates 模块）
- 可选：`hermes_cli.commands`、`hermes_cli.kanban_db`
- Agent checkpoint 目录：`{hermes_home}/checkpoints/`

**被依赖：**

- `api/routes.py`、`api/routes_dispatcher.py`
- `server.py`（extensions 静态路由，若启用）

**shim 关系：** 否。各顶层模块为 `api._compat` shim。

## 溯源

| 类型 | 位置 |
|------|------|
| 实现 | `api/features/misc/*.py` |
| 兼容入口 | `api/commands.py`、`api/kanban_bridge.py`、`api/rollback.py`、`api/extensions.py`、`api/dashboard_probe.py`、`api/updates.py` |
| 路由入口 | `api/routes_dispatcher.py`（`/api/commands`、`/api/kanban/*`、`/api/rollback/*`、`/api/dashboard/*`） |
| 契约检查 | `scripts/scan_routes_contracts.py --check` |