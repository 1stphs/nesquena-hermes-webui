# api/features/cron

## 职责

`api/features/cron` 存放 **cron 相关业务中不属于通用 cron service 的 feature 代码**。当前仅包含 WebUI 传统日历事件存储，与 Hermes Agent 的 cron job 调度互补。

**负责：**

- `{HERMES_HOME}/cron/calendar_events.json` 的 CRUD 与原子持久化
- 日历事件字段校验（时间、全天、标题等）
- 为 cron 日历 API 提供 `load_calendar_events` / `create_calendar_event` 等函数

**不负责：**

- Cron job 的创建、执行、输出抓取（`api/services/cron_service.py`、`api/routes_handlers/cron_*.py`）
- Agent 内置 cron 定义格式与 `hermes-agent` scheduler
- 路由匹配与 CSRF（`api/routes_dispatcher.py`、`api/routes_helpers/cron.py`）

顶层 `api/calendar_events.py` 为兼容 shim；路由 handler 通过 `from api.calendar_events import ...` 导入，实际落到本目录。

## 功能

| 模块 | 主要能力 |
|------|----------|
| `calendar_events.py` | `load_calendar_events`、`save_calendar_events`、`create_calendar_event`、`calendar_event_for_api`、`calendar_event_dates`；线程锁 + tmp 原子写 |

关键流程：

1. **读日历**：`GET /api/crons/calendar` → `cron_read` handler → `load_calendar_events()` → 与 cron job 列表合并返回。
2. **写事件**：`POST /api/crons/calendar/create` → `cron_write` handler → `create_calendar_event(body)` → 写入 profile cron 目录。

数据路径：`$HERMES_HOME/cron/calendar_events.json`（随 active profile 变化）。

## 依赖边界

**依赖：**

- 进程环境 `HERMES_HOME`（`get_hermes_home()`）
- 标准库文件 IO；不直接依赖 NocoBase

**被依赖：**

- `api/routes_handlers/cron_read.py`、`api/routes_handlers/cron_write.py`
- `api/routes_helpers/cron.py`（日历文件签名缓存）

**shim 关系：** 实现位于本目录；对外可能以 `api.calendar_events` 模块名被 import（兼容层）。

## 溯源

| 类型 | 位置 |
|------|------|
| 实现 | `api/features/cron/calendar_events.py` |
| 兼容入口 | `api/calendar_events.py` |
| 路由 handler | `api/routes_handlers/cron_read.py`、`api/routes_handlers/cron_write.py` |
| 路由入口 | `api/routes_dispatcher.py`（`/api/crons/calendar`、`/api/crons/calendar/create` 等） |
| Cron 服务 | `api/services/cron_service.py` |
| 测试 | `api/test_cron_service.py`（`test_handle_cron_calendar_includes_calendar_events`） |
| 契约检查 | `scripts/scan_routes_contracts.py --check` |