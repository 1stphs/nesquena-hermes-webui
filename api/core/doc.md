# api/core

## 职责

`api/core` 是 Hermes API service 的**底座层**：共享配置、进程级全局状态、会话模型、profile 上下文、workspace 路径与 HTTP 工具函数。

**负责：**

- 路径发现（agent 目录、`STATE_DIR`、`HERMES_HOME`）与环境变量解析
- 进程内 `SESSIONS` / `STREAMS` / `LOCK` 等运行时状态
- WebUI 自有 JSON session 的读写与索引（`Session` model）
- profile 切换与 per-request profile 隔离（cookie / thread-local）
- workspace 列表、安全路径解析、文件类型判断
- 通用 HTTP 响应辅助（JSON、错误脱敏、安全头、CORS）

**不负责：**

- HTTP 路由匹配与 handler 分发（`api/routes_dispatcher.py`、`api/routes_handlers/`）
- Agent 流式执行线程（`api/runtime/streaming.py`）
- 鉴权 cookie 签发（`api/authn/auth.py`）
- NocoBase 用户态业务数据

顶层 `api/config.py`、`api/models.py`、`api/profiles.py`、`api/helpers.py`、`api/workspace.py` 仅为兼容 shim，指向本目录实现。

## 功能

| 模块 | 主要能力 |
|------|----------|
| `config.py` | 服务监听地址、状态目录、agent 发现、`get_config` / `reload_config`、模型与 provider 解析、`SESSIONS` / `STREAMS` 全局表 |
| `models.py` | `Session` 类、内存会话存储、`_index.json` 维护、session 列表/分页数据源、importable agent session 元数据桥接 |
| `profiles.py` | profile 列表/创建/切换/克隆、`get_active_hermes_home`、per-request `hermes_profile` cookie 上下文 |
| `workspace.py` | profile 级 workspace 配置、`safe_resolve_ws`、目录浏览、文件读写边界 |
| `helpers.py` | `j` / `bad` / `require`、路径消毒、gzip、安全响应头 |

关键流程：

1. **启动**：`bootstrap.py` / `server.py` 导入 `api.core.config`，初始化 `STATE_DIR` 与 agent `sys.path`。
2. **请求**：`server.py` 按 cookie 设置 thread-local profile → handler 通过 `get_session` / `get_active_hermes_home` 读写正确目录。
3. **会话持久化**：`Session.save()` 原子写 JSON + 更新 `_index.json`；与 `api/runtime/session_recovery.py` 的 `.bak` 机制配合防数据丢失。

## 依赖边界

**依赖：**

- 标准库、`pathlib`、本地 `STATE_DIR` 与 Hermes home 文件系统
- `api/features/chat/agent_sessions.py`（`models.py` 读取 agent session 元数据）
- 可选：`hermes-agent` 源码（经 `config.py` 注入 `sys.path`）

**被依赖：**

- 几乎所有 `api/*` 模块、`server.py`、`api/routes.py`
- `api/runtime/streaming.py`、`api/authn/*`、`api/providers_runtime/*`、`api/features/*`

**shim 关系：** 否。本目录是真实实现；顶层同名模块通过 `api/_compat.alias_module` 转发。

## 溯源

| 类型 | 位置 |
|------|------|
| 实现 | `api/core/config.py`、`models.py`、`profiles.py`、`workspace.py`、`helpers.py` |
| 兼容入口 | `api/config.py`、`api/models.py`、`api/profiles.py`、`api/helpers.py`、`api/workspace.py` |
| 路由消费 | `api/routes.py`（re-export）、`api/routes_dispatcher.py`（session/profile/workspace 相关路径） |
| 启动 | `server.py`、`bootstrap.py` |
| 测试 | `api/test_sessions_pagination.py`、`api/test_profile_name_length.py`、`api/test_profile_installed_skills.py` |
| 契约检查 | `python scripts/scan_routes_contracts.py --check`（间接覆盖 `api.routes` 对 core 符号的 import surface） |