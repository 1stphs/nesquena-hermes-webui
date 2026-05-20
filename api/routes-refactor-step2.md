# api/routes.py 拆分第二步:端点子模块化

## 目标重定

放弃 step1 文档里"routes.py 降到 ~7300"作为后续硬指标——它原本只描述 step1 的产出,不是整个拆分的终点目标。第二步基于实际测试契约调研后重新定调:

- 建立 `api/routes_handlers/` 包,把**经契约扫描确认安全**的 `_handle_*` 端点函数外迁。
- routes.py 仍保留 dispatcher + 锁死的端点 + re-export 兼容层。
- routes.py 行数**预期下降到 5500–6500**(具体看扫描工具实际跑出的绿区端点数)。
- `handle_get / handle_post / handle_patch / handle_delete` 的 if/elif 结构**不动**——多处源码契约锁死字面量 `if parsed.path == ...`。
- **零行为变化 + 现有测试不改一行**仍是硬约束。

## 不再追的目标(明确说出来)

- routes.py 改成 prefix-map 派发——测试契约锁死字面量 `if parsed.path == ...`,改派发风格直接挂。
- 把 routes.py 改成 `api/routes/` 包——`(REPO_ROOT/"api"/"routes.py").read_text()` 测试断言会找不到文件。
- routes.py 降到 ~3500 行的"薄壳"形态——这需要修改源码扫描测试,本步不做。

## 形态

新建 `api/routes_handlers/` 包,与 step1 建的 `api/routes_helpers/` 同级、分工不同:

- `api/routes_helpers/`:被 dispatcher 和多个 `_handle_*` 共用的工具 / 共享状态(step1 已建)
- `api/routes_handlers/`:HTTP 端点处理函数本身(本步建)

routes.py 顶部用 `from api.routes_handlers.xxx import (...)` 显式 re-export 搬走的 `_handle_*`。dispatcher 体内调用形如 `return _handle_xxx(handler, body)` **保持短名**(不要写成全限定),否则 `mock.patch("api.routes._handle_xxx")` 失效。

## 红区:确认不可搬,或函数体/字面量被锁

下面这些必须留在 routes.py(或者函数体严禁修改,即使靠 re-export 搬走也不推荐挪)。

### 顶层 def 锁死(AST 或 `def xxx(` 字符串扫描)

| 函数 | 锁定来源 |
|---|---|
| `_cron_job_subprocess_main` / `_run_cron_tracked` / `_handle_cron_run` | [tests/test_cron_run_job_import.py](../tests/test_cron_run_job_import.py)(AST) |
| `_handle_live_models` | [tests/test_byok_model_dropdown.py](../tests/test_byok_model_dropdown.py)(`re.search(r"def _handle_live_models\(.*?\ndef ", src)`,内部字符串顺序) |
| `_handle_approval_sse_stream` / `_approval_sse_subscribe` / `_approval_sse_unsubscribe` / `_approval_sse_notify` | [tests/test_approval_sse.py](../tests/test_approval_sse.py)(`assert "def _xxx(" in ROUTES_SRC`) |
| `_clear_stale_stream_state` | [tests/test_stale_stream_cleanup.py](../tests/test_stale_stream_cleanup.py) |
| `handle_get` 函数本体 | [tests/test_security_redaction.py:238](../tests/test_security_redaction.py#L238)(`inspect.getsource(routes.handle_get)` 必须含 `redact_session_data`) |
| `_handle_session_export` | [tests/test_security_redaction.py:275](../tests/test_security_redaction.py#L275)(同上,函数可搬,但函数体不能动) |
| `_handle_media` / `_serve_file_bytes` | [tests/test_issue1800_file_html_interactions.py](../tests/test_issue1800_file_html_interactions.py)(`_slice_after(ROUTES_PY, "def _handle_media", 4000)` 扫函数后 4000 字符) |

### dispatcher 内 if/elif 分支锁死(原文字符串 + 块内特定符号)

| 分支 | 锁定来源 |
|---|---|
| `if parsed.path == "/api/session/delete":` + 块内 `SESSION_INDEX_FILE` | [tests/test_regressions.py:320](../tests/test_regressions.py#L320) |
| `if parsed.path == "/api/session/duplicate":` + 块内 3000 字符多断言 | [tests/test_stage268_opus_followups.py:54](../tests/test_stage268_opus_followups.py#L54) |
| `parsed.path == "/api/system/health"` + `build_system_health_payload()` | [tests/test_issue693_system_health_panel.py](../tests/test_issue693_system_health_panel.py) |
| `parsed.path == "/api/health/agent"` + `build_agent_health_payload()` | [tests/test_issue716_agent_heartbeat.py](../tests/test_issue716_agent_heartbeat.py) |
| `/api/terminal/{start,input,output,resize,close}` 5 个路径字面量 | [tests/test_embedded_workspace_terminal.py:188](../tests/test_embedded_workspace_terminal.py#L188) |
| `"session/toolsets"` 字面量 | [tests/test_issue1431_toolsets_chip_responsive.py](../tests/test_issue1431_toolsets_chip_responsive.py) |
| `parsed.path.startswith("/session/")` | [tests/test_session_cross_tab_sync.py:62](../tests/test_session_cross_tab_sync.py#L62) |
| `"/api/approval/stream"` 字面量 | tests/test_approval_sse.py |
| `"/api/models/live"` 字面量 | tests/test_issues_373_374_375 等 |

### 函数体内字符串锁死(routes.py 文本必须仍含)

| 字符串 / 表达式 | 锁定来源 / 当前所在 |
|---|---|
| `text/html`, `application/xhtml+xml`, `image/svg+xml`, `dangerous_types` | [tests/test_sprint29.py:560](../tests/test_sprint29.py#L560);当前在 `_handle_file_raw` |
| `if event in (...) ... break` + `cancel` | [tests/test_regressions.py:177](../tests/test_regressions.py#L177);当前在 `_handle_sse_stream` |
| `queue.Queue(maxsize=`, `queue.Full`, `queue.pop(0)` 等 SSE 队列模式 | tests/test_approval_queue.py、tests/test_approval_sse.py |
| `resolve_runtime_provider_with_anthropic_env_lock` | [tests/test_issue1362_codex_oauth_onboarding.py:551](../tests/test_issue1362_codex_oauth_onboarding.py#L551) |
| `s.active_stream_id = stream_id` / `s.pending_user_message = msg` / `s.pending_attachments = ...` / `pending_started_at` | tests/test_turn_duration_display.py、tests/test_v050253_opus_followups.py |
| `Read-only imported sessions cannot be deleted/archived` | tests/test_claude_code_session_import.py |
| `provider_model_ids`、`https://api.openai.com/v1` 黑名单、`not_supported` 黑名单 | tests/test_opencode_providers.py 等 |
| `platform='webui'` 出现 ≥ 2 次,`platform='cli'` = 0 次 | [tests/test_webui_platform_hint.py:38](../tests/test_webui_platform_hint.py#L38) |
| `logger = logging.getLogger(__name__)` | [tests/test_sprint43.py:25](../tests/test_sprint43.py#L25) |
| `_CLIENT_DISCONNECT_ERRORS` | [tests/test_pr1355_sse_handler_no_deadlock.py](../tests/test_pr1355_sse_handler_no_deadlock.py) |

## 绿区:候选可搬清单(每个端点搬前必须用扫描工具二次确认)

粗筛后的候选,任何一个端点搬之前都要跑契约扫描工具确认没有未发现的锁定。

```
approval / clarify 系列:
  _handle_approval_pending、_handle_approval_inject、_handle_approval_respond、
  _handle_clarify_pending、_handle_clarify_inject、_handle_clarify_respond
  (_handle_approval_sse_stream / _handle_clarify_sse_stream 待核 SSE 字符串)

profile 系列:
  _handle_profile_soul_read、_handle_profile_change_soul、
  _handle_profile_agent_skills、_handle_profile_agents_list、
  _handle_profile_agent_create、_handle_profile_agent_update、
  _handle_profile_memory_read、_handle_profile_memory_write、
  _handle_profile_user_read、_handle_profile_user_write

skill 系列:
  _handle_skill_save、_handle_skill_delete、
  _handle_skill_install_community、_handle_skill_uninstall_profile

mcp 系列:
  _handle_mcp_tools_list、_handle_mcp_servers_list、
  _handle_mcp_server_delete、_handle_mcp_server_update

memory 系列:
  _handle_memory_read、_handle_memory_write

cron 只读 / 状态:
  _handle_cron_history、_handle_cron_run_detail、_handle_cron_output、
  _handle_cron_status、_handle_cron_recent、_handle_cron_calendar
  (_handle_cron_run 红区已锁,不搬;
   _handle_cron_create/update/delete/pause/resume 待核)

file 系列(_handle_file_raw 除外):
  _handle_file_save、_handle_file_create、_handle_file_rename、
  _handle_create_dir、_handle_file_reveal、_handle_file_path、
  _handle_file_delete、_handle_file_read、_handle_list_dir

workspace 系列(_handle_workspace_reorder 除外,因有测试直接 import):
  _handle_workspace_add、_handle_workspace_remove、_handle_workspace_rename

杂项:
  _handle_sessions_search、_handle_sessions_cleanup、_handle_btw、_handle_background、
  _handle_session_import、_handle_session_import_cli、_handle_handoff_summary、
  _handle_conversation_rounds
  (_handle_chat_start / _handle_chat_sync / _handle_session_compress 待核流相关字符串)
```

粗估 30-40 个端点可搬,routes.py 能降 ~2000-3000 行。

## 实施路径(4 阶段)

### 阶段 1:契约扫描工具(只读,单 commit)

新增 `scripts/scan_routes_contracts.py`,做以下事情:

1. 扫描 `tests/test_*.py`,识别所有源码契约模式:
   - `(REPO/"api"/"routes.py").read_text(...)` → 提取后续 `in ROUTES_xxx` 断言里的字面量
   - `inspect.getsource(routes.xxx)` → 记录被扫函数名
   - `ast.parse(routes_src)` → 记录 AST 模式(找 FunctionDef 名字)
   - `assert "xxx" in src` / `assert re.search(pattern, src)` → 提取字面量 / 正则
2. 解析 `api/routes.py`,提取每个 `_handle_*` 顶层 def 的源码范围。
3. 对每个 `_handle_*` 做交叉检查:
   - 函数体是否含被锁字面量?
   - 函数 def 自身是否被 AST / getsource 测试锁定?
   - 输出三色评级:**绿**(可搬)/ **黄**(条件可搬,需在 routes.py 其他位置保留某些字符串)/ **红**(不可搬)。
4. 输出报告到 `api/routes-handlers-contract.md`(machine-readable section + 人读 section)。
5. 提供 `--check` 模式:当前已搬走的端点状态全检,若契约被破坏立即非零退出,作为后续 CI gate 用。

**这一步零代码变更,跑测试不会挂。**

### 阶段 2:绿区 POC 批次(当前实现状态)

先用契约扫描工具评估端点,再搬当前真实绿区。2026-05-18 的扫描结果显示:

1. `_handle_mcp_tools_list`(读取型,无外部依赖)→ `api/routes_handlers/mcp.py`
2. `_handle_memory_read`(单纯文件读)→ `api/routes_handlers/memory.py`
3. `_handle_skill_save` 被 [tests/test_issue1013_handoff_dock.py](../tests/test_issue1013_handoff_dock.py) 用 `"\ndef _handle_skill_save"` 当作 `_handle_handoff_summary` 的源码切片边界,所以 **不能物理移除 routes.py 里的 `def _handle_skill_save`**。当前只允许保留 routes.py 薄壳,把真实实现下沉到 `api/routes_handlers/skill.py`。

每个 commit 走完整流程:搬出 → routes.py re-export → 跑 `scan_routes_contracts.py --check` → 跑 `pytest tests/` 全套 → 通过才合下一个。

CI gate 已接入 `.github/workflows/routes-contracts.yml`:凡 PR / push 改到 `api/routes.py`、`api/routes_helpers/**`、`api/routes_handlers/**`、`api/routes-handlers-contract.md` 或 `scripts/scan_routes_contracts.py`,都会自动跑 `python scripts/scan_routes_contracts.py --check`,并用 `git diff --exit-code` 确认报告没有被重新生成改脏。这个 gate 只验证 routes.py 源码契约,不能替代全套 pytest。

**这一阶段的核心目的是验证扫描工具的判断对,以及 re-export / 薄壳代理机制对 mock.patch / monkeypatch 真的兼容。**

### 阶段 3:绿区批量搬迁(按业务前缀分批,每个前缀一 PR)

POC 验证机制 OK 后,按业务前缀分批,每前缀一个 PR、PR 内每端点一 commit:

| PR | 子模块 | 候选端点数 | 预估降行 |
|---|---|---|---|
| Z-PR1 | `routes_handlers/profile.py` | ~10 | ~500 |
| Z-PR2 | `routes_handlers/skill.py` + `routes_handlers/memory.py`(补齐 POC 已起头的) | ~6 | ~250 |
| Z-PR3 | `routes_handlers/mcp.py`(补齐 POC 已起头的) | ~4 | ~200 |
| Z-PR4 | `routes_handlers/cron_read.py`(只搬只读 cron 端点) | ~6 | ~250 |
| Z-PR5 | `routes_handlers/file.py`(不含 `_handle_file_raw`、`_handle_media`、`_serve_file_bytes`) | ~9 | ~300 |
| Z-PR6 | `routes_handlers/workspace.py`(不含 `_handle_workspace_reorder`) | ~3 | ~80 |
| Z-PR7 | `routes_handlers/approval.py`(不含 SSE stream / notify) | ~6 | ~300 |
| Z-PR8 | `routes_handlers/session_io.py`(import / handoff / conversation) | ~4 | ~600 |

合计 ~48 端点、~2500 行外迁。每个 PR 前重跑扫描工具,确保新发现的契约没有冲突。

阶段 3 推进约束:

1. 不并行做多个业务前缀 PR。按 Z-PR1 → Z-PR8 串行推进,一次只让一个前缀进入工作区。
2. 每搬完一个端点,都跑 `python scripts/scan_routes_contracts.py --check` 和 `pytest tests/ -q` 全套,不再只跑 smoke / 敏感测试。
3. 全套 pytest 有失败时,先把每个失败用例单独跑一次,形如 `pytest tests/path.py::test_name -v`。单独通过的标记为测试相互污染,不算 stop-the-line;单独仍失败的才按真回归处理。
4. Z-PR1 profile 系列要逐个核对 `monkeypatch.setattr(routes, "...")` / `patch("api.routes....")` 路径。scanner 只能发现字面量/源码契约,不能发现动态 patch binding 回归。
5. Z-PR2 可以搬 `_handle_skill_delete`、`_handle_skill_install_community`、`_handle_skill_uninstall_profile` 和 `_handle_memory_write`;但 `_handle_skill_save` 必须继续保留 routes.py 薄壳,因为 `tests/test_issue1013_handoff_dock.py` 依赖 `"\ndef _handle_skill_save"` 作为源码切片边界。
6. 带 `tests/conftest.py` session 级 `test_server` fixture 的 pytest 命令不要并行跑。它们共用同一个 `HERMES_WEBUI_TEST_PORT` / test state dir,并会在启动前杀端口、清状态目录;并行执行会互相干扰,表现为 connection reset、connection refused 或 state dir `FileExistsError`,容易被误判成拆分回归。需要拆分定向测试时,这些命令串行跑。

### 阶段 4:红区中长期方案(本步不做,留作 step 3 提案)

红区端点(cron run / live_models / SSE stream / session_export / 字面量锁定的端点)留在 routes.py。中长期想动这部分,需先现代化源码扫描测试——把它们改成扫整个 `api/` 而不是只扫 `api/routes.py`。这是 step 3 的事,需要单独立项,**不在 step 2 范围**。

## 兼容性策略

延续 step1 的形态:

```python
# api/routes_handlers/mcp.py
def _handle_mcp_tools_list(handler):
    # ... 原 routes.py 中的完整函数体
```

```python
# api/routes.py 顶部新增 re-export
from api.routes_handlers.mcp import (
    _handle_mcp_tools_list,
    _handle_mcp_servers_list,
    _handle_mcp_server_delete,
    _handle_mcp_server_update,
)
```

dispatcher 内调用保持短名:

```python
# handle_get 体内不变
if parsed.path == "/api/mcp/tools":
    return _handle_mcp_tools_list(handler)
```

**禁止**改成 `return mcp._handle_mcp_tools_list(handler)`,否则 `mock.patch("api.routes._handle_mcp_tools_list")` 失效。

## 验证方法(每个 commit 后跑)

跟 step1 同一套 + 新增契约扫描:

1. `python -c "import api.routes"` 不报错。
2. 关键 re-export 符号可达。
3. `python scripts/scan_routes_contracts.py --check` —— 若任何契约因当前改动破坏,立刻非零退出。
4. `pytest tests/ -q`(本地装好 pytest 后跑)。
5. `python -m pytest tests/test_cron_run_job_import.py tests/test_security_redaction.py tests/test_approval_sse.py tests/test_regressions.py -q` —— 4 个最敏感的源码扫描测试,优先跑。
6. 冒烟启动 `server.py`,浏览器目视触发该端点。

每步全过再做下一步。**不要批量改完一起验证**——挂了不好定位。

## 已知陷阱(在 step1 经验之上补充)

- **getsource 跟 `__code__.co_filename`**:函数搬到 handler 后 `inspect.getsource(routes.xxx)` 取到的是 handler 文件源码——只要函数体没改,字符串扫描断言仍能过。但 **AST 扫 ROUTES_PY 的测试不吃这一套**,函数 def 物理位置必须在 routes.py。
- **mock.patch 路径**:测试用 `patch("api.routes._handle_xxx")` 修改的是 routes.py 模块的绑定。搬出去后,只要 routes.py 顶部 `from … import _handle_xxx`,绑定还在;但调用方必须用短名 `_handle_xxx(...)`,不能写 `api.routes_handlers.xxx._handle_xxx(...)`。
- **monkeypatch.setattr(routes, "get_session", ...)** 之类:routes.py 顶部 import 来的符号(`get_session`、`load_settings`、`j`、`load_workspaces`、`save_workspaces` 等)绑定要保留——这些是 step1 没动的,step 2 也别动。
- **共享可变状态搬走后的赋值陷阱**:子模块若 `from api.routes_helpers.live_models import _LIVE_MODELS_CACHE` 然后 `_LIVE_MODELS_CACHE = {}` 整体替换,routes.py 的绑定会失同步。所有共享 dict / set 只能 `.clear()` 或就地修改。step1 已遵守,step 2 继续。
- **黄区端点**:扫描工具可能输出"函数体含被锁字符串"但字符串只是巧合(比如常见日志消息)。这类要人工复核,不要盲信工具。
- **新引入的源码契约**:tests/ 在 step 2 期间可能继续增加,定期重跑扫描工具刷新契约清单,避免后续 PR 翻车。

## 不在第二步范围

- 任何源码扫描测试本身的修改(留给 step 3,如果决定做)。
- `handle_get` / `handle_post` / `handle_patch` / `handle_delete` 体内 if/elif 结构改写。
- 红区端点的搬迁。
- `_handle_logs`、`_handle_llm_wiki_status`、`_handle_insights`、`_handle_health`、`_handle_plugins` 这几个 step1 留下的 endpoint helper——它们已经是顶层 def,但更接近"端点逻辑"而不是"helper"。归 `routes_helpers/` 还是 `routes_handlers/`,等阶段 1 扫描工具跑完看其评级再决定,不预定。

## 实际进度记录(2026-05-20 回填)

### 当前终态

- Z-PR8 已推到 `master`: `c9bee7c refactor(routes): 抽出会话导入端点处理器`。
- 当前 `api/routes.py`: 6921 行。
- 当前 `api/routes_handlers/`: 41 个 `_handle_*` 定义,覆盖 9 个 handler 子模块。
- 当前 contract scan: `green=62 yellow=7 red=12`。
- 当前全套回归: `4823 passed, 57 skipped, 3 xpassed, 4 warnings, 8 subtests passed`。
- `_handle_skill_save` 是特殊薄壳:真实实现已在 `api/routes_handlers/skill.py`,但 `api/routes.py` 仍保留同名 `def` 作为源码契约边界。

### Z-PR1 到 Z-PR8 实际记录

起点按 `690cd19 chore(routes): stabilize contract report` 后的 8485 行计算。Z-PR1 到 Z-PR8 合计从 `api/routes.py` 降 1564 行,终态 6921 行。若从契约扫描 commit `ae52931` 的 8560 行起算,step2 已累计降 1639 行。

| PR | commit | 子模块 | 实际搬迁端点数 | `routes.py` 行数 | 实际降行 | 原预估降行 | 结果说明 |
|---|---|---|---:|---:|---:|---:|---|
| Z-PR1 | `b7d549e` | `routes_handlers/profile.py` | 10 | 7745 | 740 | ~500 | profile 单 handler 平均体量高于预估 |
| Z-PR2 | `cde6901` | `routes_handlers/skill.py` + `routes_handlers/memory.py` | 4 | 7531 | 214 | ~250 | 只补齐 green 端点,`_handle_skill_save` 继续保留 routes.py 薄壳 |
| Z-PR3 | `06b4602` | `routes_handlers/mcp.py` | 3 | 7460 | 71 | ~200 | `_handle_mcp_tools_list` 已在 POC 搬出,本 PR 只补剩余 3 个 |
| Z-PR4 | `0608014` | `routes_handlers/cron_read.py` | 4 | 7274 | 186 | ~250 | 只搬 cron 读/状态 green 端点,`cron_history`/`cron_run_detail` red 留在 routes.py |
| Z-PR5 | `9a2c478` | `routes_handlers/file.py` | 7 | 7116 | 158 | ~300 | `file_path`/`file_reveal` red 留在 routes.py,`file_raw`/media 链路不动 |
| Z-PR6 | `28afb4f` | `routes_handlers/workspace.py` | 3 | 7056 | 60 | ~80 | 不含 `_handle_workspace_reorder` |
| Z-PR7 | `c3ab395` | `routes_handlers/approval.py` | 5 | 6980 | 76 | ~300 | 只搬 approval/clarify green 端点,`_handle_approval_respond` 和 SSE 链路留在 routes.py |
| Z-PR8 | `c9bee7c` | `routes_handlers/session_io.py` | 2 | 6921 | 59 | ~600 | 只搬 `_handle_session_import`、`_handle_conversation_rounds`; session CLI/handoff/compress/export 留在 routes.py |

POC 期在 Z-PR1 前已搬/代理 3 个端点: `_handle_mcp_tools_list`、`_handle_memory_read`、`_handle_skill_save` 实现下沉。Z-PR1 到 Z-PR8 之后,`routes_handlers` 当前实际承载 41 个 `_handle_*` 定义。

### 仍留在 `api/routes.py` 的 red/yellow

这些端点不再属于 step2 继续搬迁范围。要继续移动它们,先做 step3:把源码契约测试现代化为扫 `api/**` 或改为行为级断言,再逐个迁移。

| status | 函数 | 留存原因 |
|---|---|---|
| red | `_handle_approval_sse_stream` | routes.py 物理 def 和 SSE 队列/断线字面量被测试锁定 |
| red | `_handle_background` | routes.py 物理 def 被测试锁定 |
| red | `_handle_clarify_sse_stream` | routes.py 物理 def 和 SSE 队列/断线字面量被测试锁定 |
| red | `_handle_cron_history` | routes.py 物理 def 被测试锁定 |
| red | `_handle_cron_run` | routes.py 物理 def 被测试锁定 |
| red | `_handle_cron_run_detail` | routes.py 物理 def 被测试锁定 |
| red | `_handle_file_path` | routes.py 物理 def 被测试锁定 |
| red | `_handle_file_reveal` | routes.py 物理 def 被测试锁定 |
| red | `_handle_handoff_summary` | routes.py 物理 def 和 runtime provider 字面量被测试锁定 |
| red | `_handle_live_models` | routes.py 物理 def、`/api/models/live`、provider model 字面量被测试锁定 |
| red | `_handle_session_import_cli` | routes.py 物理 def 被测试锁定 |
| red | `_handle_skill_save` | routes.py 物理 def 被 `handoff_summary` 源码切片边界锁定,当前保留薄壳 |
| yellow | `_handle_approval_respond` | 函数体含 approval queue 字面量 |
| yellow | `_handle_chat_start` | 函数体含 pending turn 字面量 |
| yellow | `_handle_chat_sync` | 函数体含 runtime provider env-lock 字面量 |
| yellow | `_handle_gateway_sse_stream` | 函数体含 SSE disconnect 字面量 |
| yellow | `_handle_session_compress` | 函数体含 runtime provider env-lock 和 pending turn 字面量 |
| yellow | `_handle_session_export` | `inspect.getsource(routes._handle_session_export)` 被测试使用 |
| yellow | `_handle_sse_stream` | 函数体含 SSE disconnect 字面量 |

### 当前结论

step2 的保守拆分到 Z-PR8 已收口。当前终态没有达到 5500-6500 行预估,主要原因是 red/yellow 端点和若干未纳入 Z-PR1-8 的 green 端点继续留在 routes.py。后续不应在 step2 内继续硬搬 red/yellow;需要单独立项 step3 测试现代化。
