# api/routes.py 瘦身报告

## 结论

`api/routes.py` 已从 2026-05-19 基线 `10048` 行降到当前 `1762` 行,净减 `8286` 行,降幅约 `82.5%`。

本次同步对比范围是 `master-backup-cxg..master`。`master` 比备份分支多出两个 routes 相关提交:

- `f84e8c3 refactor(routes): 拆分主路由分发与扩展处理器`
- `dd4a0ea refactor!(api): 转为 API-only 服务`

相对 `master-backup-cxg` 的 `6618` 行终态,当前 `master` 的 `api/routes.py` 继续减少 `4856` 行。主要变化是把四个 HTTP method dispatcher 从 `api/routes.py` 下沉到 `api/routes_dispatcher.py`,再把此前保留的 red/yellow endpoint 大块迁入扩展 handler 模块。`dd4a0ea` 进一步删除 WebUI shell / login HTML 相关逻辑,服务形态转为 API-only。

整体优化始终没有把 `api.routes` 改成包;早期阶段保守保留 dispatcher 原位,当前 `master` 已把 dispatcher 下沉到独立模块。主要策略是:

- 先抽纯 helper 到 `api/routes_helpers/`,保留 `api.routes` 顶层 re-export。
- 再按业务域把 `_handle_*` 端点抽到 `api/routes_handlers/`。
- 把 `handle_get` / `handle_post` / `handle_patch` / `handle_delete` 的主分发逻辑抽到 `api/routes_dispatcher.py`。
- `api/routes.py` 保留稳定入口、顶层 re-export 和少量必须原位保留的函数。
- 用 repo-wide 版 `scripts/scan_routes_contracts.py` 固化源码契约,扫描范围已经扩展到 `api/routes.py`、`api/routes_dispatcher.py`、`api/routes_handlers/*.py` 和 `api/routes_helpers/**/*.py`。

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
| Dispatcher 拆分 | `f84e8c3` | 2026-05-21 | 1844 | -4774 | 新增 `api/routes_dispatcher.py`,把主路由分发和扩展处理器抽出 |
| API-only | `dd4a0ea` | 2026-05-21 | 1762 | -82 | 删除 WebUI shell / login HTML 逻辑和 `routes_helpers/login_page.py` |

## 当前模块拆分结果

当前新增/使用三类配套模块:

| 目录 | 文件数 | 行数合计 | 作用 |
|---|---:|---:|---|
| `api/routes_helpers/` | 8 | 1512 | 纯 helper、共享状态、解析/过滤/缓存/CSRF/SSE 辅助逻辑 |
| `api/routes_handlers/` | 22 | 4788 | 从 `routes.py` 外迁的 HTTP endpoint handler |
| `api/routes_dispatcher.py` | 1 | 2273 | `GET` / `POST` / `PATCH` / `DELETE` 主路由分发 |

当前 `api/routes_handlers/` 中共有 76 个 `_handle_*` 定义。`api/routes.py` 继续保留顶层 re-export,原因是测试和外部代码会 patch 或读取 `api.routes._handle_xxx`。`api/routes_dispatcher.py` 在每次 dispatch 前同步 `api.routes` 的模块绑定,用于保留历史 `mock.patch("api.routes.<name>")` 兼容面。

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
- `api/routes_handlers/approval_extra.py`: approval respond。
- `api/routes_handlers/chat.py`: chat start/sync/background/btw/session compress 以及 chat start 前置整理逻辑。
- `api/routes_handlers/cron_write.py`: cron create/update/delete/run/pause/resume/history/detail/batch。
- `api/routes_handlers/file_extra.py`: file path/raw/reveal/media 以及文件字节服务。
- `api/routes_handlers/handoff.py`: handoff summary 生成、匹配、持久化。
- `api/routes_handlers/live_models.py`: live models endpoint。
- `api/routes_handlers/session_extra.py`: session export、CLI session import、sessions cleanup 以及 CLI message refresh helpers。
- `api/routes_handlers/streaming.py`: chat / gateway / approval / clarify SSE stream。
- `api/routes_handlers/workspace_extra.py`: workspace reorder。
- `api/routes_dispatcher.py`: 四个 method dispatcher 的完整分发链路。

特殊兼容:

- `_handle_skill_save` 在 `routes.py` 中保留薄壳,真实实现下沉到 `api/routes_handlers/skill.py`。原因是源码测试把 `def _handle_skill_save` 当作其他源码切片边界。
- `_handle_terminal_output` 仍留在 `routes.py`。原因是 SSE heartbeat 测试直接读 `api/routes.py`,要求 `term.output.get(timeout=_SSE_HEARTBEAT_INTERVAL_SECONDS)` 字面量仍在该文件里。
- 主 dispatcher 虽然下沉到 `api/routes_dispatcher.py`,但会动态同步 `api.routes` 绑定,避免破坏历史 patch surface。
- repo-wide contract scan 后,原先大量 red/yellow 端点已可迁移。当前只剩 `_handle_cron_run`、`_handle_session_export`、`_handle_session_import_cli` 三个 yellow,red 为 0。

### API-only 调整

`dd4a0ea` 删除了 WebUI shell 和 login 页面模板逻辑:

- 删除 `api/routes_helpers/login_page.py`。
- `GET /` 返回 API 服务状态 JSON: `service=hermes-api`、`mode=api-only`、`status=ok`。
- `/index.html`、`/session/*`、`/static/*`、`/manifest.json`、`/favicon.ico` 返回 `410 WebUI frontend has been removed`。
- `/login` 返回 `410 WebUI login page has been removed`,并提示认证入口 `/api/auth/token-login`。
- `/sw.js` 返回注销旧 WebUI service worker 的脚本,用于清理浏览器旧缓存。

### 契约与 CI 加固

完成内容:

- 新增 `scripts/scan_routes_contracts.py`。
- 生成并维护 `api/routes-handlers-contract.md`。
- CI `routes-contracts.yml` 对 `api/routes.py`、`api/routes_dispatcher.py`、`api/routes_helpers/**`、`api/routes_handlers/**`、`api/routes-handlers-contract.md`、`scripts/scan_routes_contracts.py`、`tests/test_*.py` 的变更触发 contract scan。
- `.gitignore` 已 unignore routes refactor 相关 Markdown 文档,避免被全局 `*.md` 忽略规则挡住。

当前 contract scan:

```text
green=80 yellow=3 red=0
```

## 当前保留在 routes.py 的内容

`api/routes.py` 当前已不是主分发大文件,主要保留稳定入口和兼容绑定:

| 类型 | 代表内容 | 保留原因 |
|---|---|---|
| public entrypoint | `handle_get` / `handle_post` / `handle_patch` / `handle_delete` | 作为薄壳调用 `dispatch_get` / `dispatch_post` / `dispatch_patch` / `dispatch_delete` |
| compatibility re-export | 从 `routes_handlers` / `routes_helpers` 导入的大量 `_handle_*` 和 helper | 保留 `api.routes._handle_xxx` patch/import surface |
| local endpoint/helper | `_handle_health`、`_handle_insights`、`_handle_llm_wiki_status`、`_handle_plugins` 等 | 体量较小,继续留在入口模块 |
| terminal output | `_handle_terminal_output` | 当前 SSE heartbeat 测试要求相关字面量在 `api/routes.py` |
| cron worker | `_cron_job_subprocess_main`、`_run_cron_tracked` | cron subprocess / tracked run helper 继续原位 |

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

当前 `master` 追加拆分后的报告同步依据:

```text
git diff --stat master-backup-cxg..master -- api
git diff --name-status master-backup-cxg..master -- api
git show f84e8c3:api/routes.py | wc -l
git show dd4a0ea:api/routes.py | wc -l
wc -l api/routes.py api/routes_dispatcher.py api/routes_handlers/*.py api/routes_helpers/*.py
```

已观察到的当前 contract scan 摘要来自 `api/routes-handlers-contract.md`:

```text
green=80 yellow=3 red=0
```

## 后续边界

当前 `api/routes.py` 已降到 `1762` 行,继续瘦身的主要价值不再是单文件行数,而是清理兼容 re-export、收敛 dispatcher 绑定同步方式、以及决定 API-only 后是否要删除更多前端兼容路由。后续建议另开独立任务:

1. 现代化剩余 yellow 源码契约:重点是 `_handle_cron_run`、`_handle_session_export`、`_handle_session_import_cli`。
2. 评估 `api/routes_dispatcher.py` 是否继续保持长 if/elif,或进一步演进为显式 route registry / dispatch table。
3. API-only 形态稳定后,清理无用前端兼容路径、静态资源兼容逻辑和相关测试。
4. 精简 `api.routes` 顶层 re-export 前,必须先确认外部调用和测试不再依赖 `mock.patch("api.routes._handle_xxx")`。

不要把 dispatcher registry 重写、兼容面收缩和 API-only 清理混在普通 routes 瘦身里做;这三类变更的风险点不同,应分开验证。
