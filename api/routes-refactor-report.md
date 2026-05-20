# api/routes.py 瘦身报告

## 结论

`api/routes.py` 已从 2026-05-19 基线 `10048` 行降到当前 `6618` 行,净减 `3430` 行,降幅约 `34.1%`。

本轮优化没有把 `api.routes` 改成包,也没有改写 `handle_get` / `handle_post` 的核心分发结构。主要策略是:

- 先抽纯 helper 到 `api/routes_helpers/`,保留 `api.routes` 顶层 re-export。
- 再按业务域把低风险 `_handle_*` 端点抽到 `api/routes_handlers/`。
- 对被源码测试锁定的 red/yellow 端点保持原位。
- 用 `scripts/scan_routes_contracts.py` 固化源码契约,避免搬迁破坏 `mock.patch("api.routes...")`、`inspect.getsource(...)` 和字面量扫描测试。

## 行数变化

数据来自 `git show <commit>:api/routes.py | wc -l`。当前行数来自 `wc -l api/routes.py`。

| 阶段 | commit | 日期 | `api/routes.py` 行数 | 较前一节点变化 | 说明 |
|---|---|---:|---:|---:|---|
| 基线 | `36ad535` | 2026-05-19 | 10048 | - | step1 前的 routes.py 大文件基线 |
| Step1 helper 抽离 | `63b51d7` | 2026-05-19 | 8977 | -1071 | 抽出 shared route helpers |
| Step1 cron 去重 | `85de792` | 2026-05-19 | 8605 | -372 | 移除重复 cron helpers |
| Step1 messaging helper | `ee0f8bc` | 2026-05-19 | 8560 | -45 | 抽出 messaging helpers |
| 契约扫描基线 | `ae52931` | 2026-05-19 | 8560 | 0 | 新增 contract scanner,行数不变 |
| POC handler | `633f4cb` | 2026-05-19 | 8533 | -27 | 抽出 MCP tools handler |
| POC handler | `40c6f43` | 2026-05-19 | 8504 | -29 | 抽出 memory read handler |
| POC handler | `0c0f70e` | 2026-05-19 | 8483 | -21 | skill save 改为 routes.py 薄壳代理 |
| Step2 稳定起点 | `690cd19` | 2026-05-19 | 8485 | +2 | 稳定 contract report |
| Z-PR1 | `b7d549e` | 2026-05-19 | 7745 | -740 | 抽出 profile handlers |
| Z-PR2 | `cde6901` | 2026-05-19 | 7531 | -214 | 抽出 skill / memory handlers |
| Z-PR3 | `06b4602` | 2026-05-19 | 7460 | -71 | 抽出剩余 MCP handlers |
| Z-PR4 | `0608014` | 2026-05-20 | 7274 | -186 | 抽出 cron read handlers |
| Z-PR5 | `9a2c478` | 2026-05-20 | 7116 | -158 | 抽出 file handlers |
| Z-PR6 | `28afb4f` | 2026-05-20 | 7056 | -60 | 抽出 workspace handlers |
| Z-PR7 | `c3ab395` | 2026-05-20 | 6980 | -76 | 抽出 approval / clarify handlers |
| Z-PR8 | `c9bee7c` | 2026-05-20 | 6921 | -59 | 抽出 session import / conversation rounds |
| 收尾 | `887d246` | 2026-05-20 | 6618 | -303 | 抽出 logs / sessions search / terminal 部分低风险 handlers,并收敛连续空行 |

## 当前模块拆分结果

当前新增/使用两类配套模块:

| 目录 | 文件数 | 行数合计 | 作用 |
|---|---:|---:|---|
| `api/routes_helpers/` | 9 | 1669 | 纯 helper、共享状态、解析/过滤/缓存/CSRF/SSE 辅助逻辑 |
| `api/routes_handlers/` | 13 | 2027 | 从 `routes.py` 外迁的 HTTP endpoint handler |

当前 `api/routes_handlers/` 中共有 47 个 `_handle_*` 定义。`api/routes.py` 继续保留顶层 re-export,原因是测试和外部代码会 patch 或读取 `api.routes._handle_xxx`。

## 已完成的优化

### Step1: helper 层拆分

完成内容:

- `api/routes_helpers/cron.py`: cron 运行状态、输出裁剪、日历统计、profile 解析、subprocess wrapper。
- `api/routes_helpers/csrf.py`: CSRF / origin / host-port 校验。
- `api/routes_helpers/profile_filter.py`: profile scoped session/project 过滤。
- `api/routes_helpers/live_models.py`: live models cache key、缓存读写和 provider alias。
- `api/routes_helpers/login_page.py`: login 页面 locale 和 HTML 文案。
- `api/routes_helpers/approval_sse.py`: approval SSE subscriber 存储与通知 helper。
- `api/routes_helpers/model_resolve.py`: session model/provider 解析和兼容逻辑。
- `api/routes_helpers/messaging.py`: CLI / gateway / messaging session 识别、排序、去重和 metadata 合并。

边界:

- helper 抽离只移动内部函数/状态,不移动 HTTP 路由分发。
- 保留 `api.routes` 顶层导入名,避免破坏测试和 monkeypatch surface。

### Step2: handler 层拆分

完成内容:

- `api/routes_handlers/profile.py`: profile user/memory/soul/agent 系列 endpoints。
- `api/routes_handlers/skill.py`: skill delete、community install、profile uninstall,以及 `_handle_skill_save` 的真实实现。
- `api/routes_handlers/memory.py`: memory read/write endpoints。
- `api/routes_handlers/mcp.py`: MCP tools / servers list / update / delete endpoints。
- `api/routes_handlers/cron_read.py`: cron status / recent / calendar / output 等只读 endpoints。
- `api/routes_handlers/file.py`: list/read/create/save/delete/rename/create-dir 等 file endpoints。
- `api/routes_handlers/workspace.py`: workspace add/remove/rename endpoints。
- `api/routes_handlers/approval.py`: approval / clarify pending、inject、respond 等低风险 endpoints。
- `api/routes_handlers/session_io.py`: session import、conversation rounds、sessions search。
- `api/routes_handlers/logs.py`: logs endpoint。
- `api/routes_handlers/terminal.py`: terminal start/input/resize/close endpoints。

特殊兼容:

- `_handle_skill_save` 在 `routes.py` 中保留薄壳,真实实现下沉到 `api/routes_handlers/skill.py`。原因是源码测试把 `def _handle_skill_save` 当作其他源码切片边界。
- `_handle_terminal_output` 仍留在 `routes.py`。原因是 SSE heartbeat 测试直接读 `api/routes.py`,要求 `term.output.get(timeout=_SSE_HEARTBEAT_INTERVAL_SECONDS)` 字面量仍在该文件里。
- red/yellow 端点没有强搬。它们要继续拆,应先做测试现代化。

### 契约与 CI 加固

完成内容:

- 新增 `scripts/scan_routes_contracts.py`。
- 生成并维护 `api/routes-handlers-contract.md`。
- CI `routes-contracts.yml` 对 `api/routes.py`、`api/routes_helpers/**`、`api/routes_handlers/**`、`api/routes-handlers-contract.md`、`scripts/scan_routes_contracts.py`、`tests/test_*.py` 的变更触发 contract scan。
- `.gitignore` 已 unignore routes refactor 相关 Markdown 文档,避免被全局 `*.md` 忽略规则挡住。

当前 contract scan:

```text
green=62 yellow=7 red=12
```

## 当前保留在 routes.py 的大块

这些不是漏做,而是当前阶段的风险边界:

| 类型 | 代表内容 | 保留原因 |
|---|---|---|
| dispatcher | `handle_get` / `handle_post` / `handle_patch` / `handle_delete` | 分发顺序、CSRF、body 读取、错误响应和 patch surface 交织,未做 dispatch table 重构 |
| red endpoint | `_handle_handoff_summary`、`_handle_live_models`、`_handle_session_import_cli`、SSE stream、cron run/history/detail、file path/reveal 等 | 被源码扫描、物理 def、`inspect.getsource` 或字面量测试锁定 |
| yellow endpoint | `_handle_session_compress`、`_handle_chat_start`、`_handle_chat_sync`、`_handle_approval_respond` 等 | 函数体含测试锁定字面量,搬迁需要先调整测试策略 |
| terminal output | `_handle_terminal_output` | 当前 SSE heartbeat 测试要求相关字面量在 `api/routes.py` |

## 验证记录

最近一次收尾提交 `887d246` 的验证:

```text
python -c "import api.routes; import api.routes_handlers.logs; import api.routes_handlers.terminal; import api.routes_handlers.session_io"
python -m compileall -q api/routes.py api/routes_handlers/logs.py api/routes_handlers/terminal.py api/routes_handlers/session_io.py
python scripts/scan_routes_contracts.py --check --no-report
git diff --check
uv run --with pyyaml --with pytest --with pytest-timeout python -m pytest tests/test_logs_endpoint.py tests/test_logs_ui_static.py tests/test_sprint7.py tests/test_sprint3.py tests/test_sprint4.py tests/test_session_summary_redaction.py tests/test_embedded_workspace_terminal.py tests/test_issue1623_sse_heartbeat_alignment.py -q
```

结果:

```text
routes contract scan: green=62 yellow=7 red=12
88 passed, 15 skipped
```

`15 skipped` 是本机没有 `hermes-agent`,不是 routes 拆分回归。

Step2 到 Z-PR8 的全套回归记录已写入 `api/routes-refactor-step2.md`: `4823 passed, 57 skipped, 3 xpassed, 4 warnings, 8 subtests passed`。

## 后续边界

当前已经是合理停止点。若继续把 `api/routes.py` 从 `6618` 行继续明显降到 5000 行级别,不应再按“小瘦身”处理,而应另开 step3:

1. 现代化源码契约测试:从只扫 `api/routes.py` 改成扫 `api/**`,或改成行为级断言。
2. 再迁移 red/yellow endpoints。
3. 最后再评估 `handle_get` / `handle_post` 是否值得改成显式 route registry / dispatch table。

在 step3 之前,不要强搬 red/yellow,也不要把核心 dispatcher 重写混入普通 refactor。
