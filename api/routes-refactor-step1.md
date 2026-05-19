# api/routes.py 拆分第一步:抽 helper 模块

## 目标 / 成功标准

- routes.py 从 **10043 行**降到约 **7300 行**,只保留 4 个 dispatcher 与 `_handle_*` 端点处理函数。
- **零行为变化**:`server.py` 入口不动,所有现有测试不修改一行直接通过。
- 抽出的全部是纯 helper / 共享状态,不含 HTTP 路径派发逻辑。
- 每块可独立提交、独立回滚。

## 模块路径

新建 **`api/routes_helpers/`** 平级包,与 `api/routes.py` 同级。

选这个的理由:

- routes.py 形态保持不变(仍是单文件 + 4 个 dispatcher),只把内部 helper 外迁。改动面最小,风险最低。
- 不把 routes.py 改成 `api/routes/` 包(虽然第二步可能这么做),避免第一步触碰 `from api.routes import …` 的导入路径解析。
- 命名直白,`routes_helpers` 是 `routes` 的配套,语义对得上。

第二步真要把 routes.py 改成包时,这个目录可以原地改名为 `api/routes/_helpers/`,只需一次 mv + grep 替换。

## 拆分块清单

8 块,每块都是"符号集合 → 单文件",前 2780 行的内部定义全部覆盖,但 logs / insights 两段端点逻辑留给第二步。

### 块 H:login 页面与 locale

- **文件**:`api/routes_helpers/login_page.py`
- **范围**:[routes.py:1905-2065](routes.py#L1905) (~160 行)
- **符号**:`_LOGIN_LOCALE`, `_LOGIN_PAGE_HTML`, `_resolve_login_locale_key`
- **测试依赖**:`tests/test_login_locale_parity.py` → `_resolve_login_locale_key`
- **外部依赖**:无逻辑依赖,字面量 + 一个函数。
- **风险**:几乎零。

### 块 F:live models 缓存

- **文件**:`api/routes_helpers/live_models.py`
- **范围**:[routes.py:709-820](routes.py#L709) (~110 行,符号集中)
- **符号**:`_PROVIDER_ALIASES`, `_OPENAI_COMPAT_ENDPOINTS`, `_LIVE_MODELS_CACHE_TTL`, `_LIVE_MODELS_CACHE`, `_LIVE_MODELS_CACHE_LOCK`, `_active_profile_for_live_models_cache`, `_live_models_cache_key`, `_get_cached_live_models`, `_set_cached_live_models`, `_clear_live_models_cache`
- **测试依赖**:暂未发现。
- **外部依赖**:profile 上下文(局部 import)。
- **风险**:自包含状态,低。

### 块 B:profile / session 过滤

- **文件**:`api/routes_helpers/profile_filter.py`
- **范围**:[routes.py:69-143](routes.py#L69) (~75 行)
- **符号**:`_profiles_match`, `_all_profiles_query_flag`, `_requested_sessions_profile`
- **测试依赖**:`tests/test_issue1611_session_profile_filtering.py` 大量使用以上三个。
- **外部依赖**:`api.profiles._is_root_profile`(保持局部 import)。
- **风险**:低。re-export 三个符号全列就行。

### 块 D:CSRF / 来源校验

- **文件**:`api/routes_helpers/csrf.py`
- **范围**:[routes.py:942-1050](routes.py#L942) (~110 行)
- **符号**:`_normalize_host_port`, `_ports_match`, `_allowed_public_origins`, `_env_truthy`, `_check_csrf`
- **测试依赖**:`tests/test_sprint29.py` → 上述全部。
- **外部依赖**:`api.config.get_config`(局部)。
- **风险**:低。

### 块 G:approval SSE pub-sub

- **文件**:`api/routes_helpers/approval_sse.py`
- **范围**:[routes.py:1797-1900](routes.py#L1797) (~100 行)
- **符号**:`_approval_sse_subscribe`, `_approval_sse_unsubscribe`, `_approval_sse_notify_locked`, `_approval_sse_notify`,以及内部使用的订阅者字典 + 锁。
- **测试依赖**:
  - `tests/test_approval_sse.py`(大量 `from api import routes as r`,通过 `r._approval_sse_*` 调用)
  - `tests/test_pr1350_sse_notify_correctness.py` → `_approval_sse_notify_locked`, `_lock`, `_pending`
- **关键预备动作**:测试能 `from api.routes import _lock, _pending`,但顶层 grep 不到这两个名字的赋值——它们大概率是从 `api.helpers` 通过某条 import 路径渗到顶层的。**做这块之前必须先验证**:

  ```
  python -c "import api.routes as r; print(r._lock, r._pending)"
  ```

  如果能跑,但你 grep 不到来源,**先在 routes.py 顶部显式补一行 `from api.helpers import _lock, _pending`**,然后再做块 G,避免搬迁顺序影响名字解析。
- **风险**:中。`_lock`/`_pending` 来源不明,需要先稳住。

### 块 A:cron helpers

- **文件**:`api/routes_helpers/cron.py`
- **范围**:[routes.py:53-740](routes.py#L53) (~690 行)
- **符号**(完整列举):
  - 状态:`_RUNNING_CRON_JOBS`, `_RUNNING_CRON_LOCK`, `_CRON_OUTPUT_CONTENT_LIMIT`, `_CRON_OUTPUT_HEADER_CONTEXT`
  - 函数:`_mark_cron_running`, `_mark_cron_done`, `_is_cron_running`, `_cron_response_marker_index`, `_cron_output_content_window`, `_cron_job_for_api`, `_cron_jobs_for_api`, `_normalize_cron_profile_lookup_name`, `_available_cron_profile_names`, `_normalize_cron_profile_value`, `_profile_home_for_cron_job`, `_profile_home_for_cron_profile_name`, `_parse_cron_calendar_month`, `_cron_job_frequency`, `_parse_iso_date`, `_days_from_next_run`, `_all_days`, `_parse_int_set`, `_cron_dow_value`, `_cron_expr_days`, `_weekday_days`, `_cron_calendar_days_for_job`, `_cron_calendar_entry`, `_cron_job_subprocess_main`, `_cron_subprocess_result_timeout_seconds`, `_run_cron_job_in_profile_subprocess`, `_run_cron_tracked`
- **测试依赖**:
  - `tests/test_issue617_cron_profile_selector.py` → `_cron_job_for_api`, `_normalize_cron_profile_value`
  - `tests/test_sprint10.py` → `_cron_output_content_window`
- **外部依赖**:`api.config`、`api.helpers._sanitize_error`、cron 调度后端。保持局部 import。
- **风险**:体量最大,但函数互相调用闭环清晰,无 HTTP 派发。

### 块 E:模型 / Provider 解析

- **文件**:`api/routes_helpers/model_resolve.py`
- **范围**:[routes.py:1052-1407](routes.py#L1052) (~355 行)
- **符号**:`_normalize_provider_id`, `_catalog_provider_id_sets`, `_catalog_has_provider`, `_model_matches_active_provider_family`, `_catalog_model_id_matches`, `_clean_session_model_provider`, `_split_provider_qualified_model`, `_should_attach_codex_provider_context`, `_resolve_compatible_session_model_state`, `_resolve_compatible_session_model`, `_normalize_session_model_in_place`, `_resolve_effective_session_model_for_display`, `_resolve_effective_session_model_provider_for_display`, `_session_model_state_from_request`
- **测试依赖**:暂未发现直接 import。
- **外部依赖**:`api.config`、`api.providers`、`api.models` 的 catalog——这些当前是在 routes.py 顶层的大 import 块取的,搬到新模块后要在 `model_resolve.py` 顶部自己 import。注意循环依赖,必要时改局部 import。
- **风险**:中。import 时序敏感。

### 块 C:messaging session helpers

- **文件**:`api/routes_helpers/messaging.py`
- **范围**:[routes.py:144-1800](routes.py#L144) 之间的 messaging 相关符号,**按符号挑而不是按行号切**(中间夹了别块的工具函数,以及 logs/insights 等)
- **符号**:
  - 状态:`_MESSAGING_RAW_SOURCES`, `_MESSAGING_SESSION_METADATA_CACHE`, `_MESSAGING_SESSION_METADATA_LOCK`, `_STALE_MESSAGING_END_REASONS`, `CLI_VISIBLE_SESSION_CAP`
  - 函数:`_normalize_messaging_source`, `_is_known_messaging_source`, `_safe_first`, `_gateway_session_metadata_path`, `_load_gateway_session_identity_map`, `_lookup_gateway_session_identity`, `_lookup_cli_session_metadata`, `_messaging_session_identity`, `_session_messaging_raw_source`, `_has_durable_messaging_identity`, `_numeric_count`, `_should_hide_stale_messaging_session`, `_is_messaging_session_record`, `_is_messaging_session_id`, `_session_sort_timestamp`, `_is_cli_session_for_settings`, `_cap_recent_cli_sessions`, `_merge_cli_sidebar_metadata`, `_messaging_source_key`, `_keep_latest_messaging_session_per_source`
- **测试依赖**:暂未发现。
- **外部依赖**:`api.agent_sessions`、`api.models`(部分函数)。
- **风险**:**最高**。符号穿插,需要逐个搬而不是整段挪。放最后做。

## 兼容性策略

每块都用"内部模块 + routes.py 显式 re-export"的形态。例子:

```python
# api/routes_helpers/cron.py
import threading
_RUNNING_CRON_JOBS: dict[str, float] = {}
_RUNNING_CRON_LOCK = threading.Lock()
_CRON_OUTPUT_CONTENT_LIMIT = 8000
# ... 完整搬过来
```

```python
# api/routes.py 顶部,替换原行 53-740
from api.routes_helpers.cron import (
    _RUNNING_CRON_JOBS,
    _RUNNING_CRON_LOCK,
    _CRON_OUTPUT_CONTENT_LIMIT,
    _CRON_OUTPUT_HEADER_CONTEXT,
    _mark_cron_running,
    _mark_cron_done,
    # ... 全部符号显式列举
)
```

显式列举(而不是 `import *`)的原因:

- 删除符号时 routes.py 立刻报错,等于一份明文契约。
- IDE 跳转 / 静态检查友好。
- 不会顺带带入 `threading`、`time` 这类副带符号污染顶层命名空间。

## 实施顺序与提交策略

按块独立 commit,合到 2 个 PR。顺序由"风险递增 + 体量递增"决定。

**PR 1:小块批次**(5 个 commit,共约 ~555 行外迁)

| 顺序 | commit | 块 | 行数 |
|---|---|---|---|
| 1 | `refactor(routes): 抽出 login 页 helper 到 routes_helpers/login_page.py` | H | ~160 |
| 2 | `refactor(routes): 抽出 live models 缓存到 routes_helpers/live_models.py` | F | ~110 |
| 3 | `refactor(routes): 抽出 profile/session 过滤到 routes_helpers/profile_filter.py` | B | ~75 |
| 4 | `refactor(routes): 抽出 CSRF helper 到 routes_helpers/csrf.py` | D | ~110 |
| 5 | `refactor(routes): 抽出 approval SSE 到 routes_helpers/approval_sse.py` | G | ~100 |

PR1 提交前先做块 G 的预备动作:核实 `_lock`/`_pending` 顶层可见性,若需要补 import 单独一个 commit。

**PR 2:大块批次**(3 个 commit,共约 ~1740 行外迁)

| 顺序 | commit | 块 | 行数 |
|---|---|---|---|
| 6 | `refactor(routes): 抽出 cron helper 到 routes_helpers/cron.py` | A | ~690 |
| 7 | `refactor(routes): 抽出模型解析 helper 到 routes_helpers/model_resolve.py` | E | ~355 |
| 8 | `refactor(routes): 抽出 messaging session helper 到 routes_helpers/messaging.py` | C | ~散落 |

PR2 之间各 commit 互不依赖,如果块 C 在搬迁中发现风险过高,可单独拆出来留给 PR3。

## 验证方法(每个 commit 后跑一遍)

1. **静态导入**:`python -c "import api.routes"` 不报错。
2. **关键 re-export 可达**:

   ```
   python -c "import api.routes as r; print(
       r._cron_job_for_api,
       r._check_csrf,
       r._profiles_match,
       r._resolve_login_locale_key,
       r._approval_sse_notify_locked,
   )"
   ```

3. **测试全绿**:跑全套 `pytest tests/`,**禁止改测试**通过。如果挂,定位是 re-export 漏了还是真的搬错——前者补 routes.py 顶部 import,后者回滚那个 commit。
4. **冒烟运行**:起 `server.py`,浏览器手动点:登录页加载、crons 列表、profile 切换、live models 拉取、approval SSE 订阅(开两个窗口验证)。

每步走完这 4 步再做下一步。**不要批量改完一起验证**——挂了不好定位。

## 已知陷阱

- **`_lock` / `_pending` 来源不明**:测试 `from api.routes import _lock, _pending` 当前可用,但 grep routes.py 顶层没有这两个名字的赋值。块 G 实施前必须用 `python -c "import api.routes as r; print(r._lock, r._pending)"` 确认它们目前怎么来的,必要时先补一行显式 `from api.helpers import _lock, _pending` 到 routes.py 顶部。
- **`from api.models import …`** 在 [routes.py:1719](routes.py#L1719) 等位置的顶层(但延后的) import 块**保持原位**,第一步不动它们。这些 import 不是 helper,是 routes.py 自己用的依赖。
- **可变模块状态**(`_RUNNING_CRON_JOBS`、`_MESSAGING_SESSION_METADATA_CACHE`、`_LIVE_MODELS_CACHE`)搬走后,所有访问点都通过 routes.py 顶部 re-export 的绑定。Python 的 `from … import x` 绑定**指向同一对象**,所以 `_RUNNING_CRON_JOBS[k] = v` 这种就地修改是共享的,**但** `_RUNNING_CRON_JOBS = {}` 整体替换会断开绑定。grep 确认没人整体替换,如果有就改成 `.clear()`。
- **`mock.patch("api.routes._mcp_runtime_status_by_name")`** 等测试 patch 依赖 routes.py 顶部的绑定。re-export 后,patch 修改的是 routes.py 的绑定,而 routes.py 内部代码用的也是这个绑定,**所以 patch 仍有效**。但实施时**不要把调用方改成 `api.routes_helpers.xxx.foo()` 这种全限定调用**,否则 patch 失效。保持短名。
- **块 E 的 import 时序**:模型解析依赖 `api.providers` 和 `api.models` 的 catalog,如果搬到新模块顶层 import 会触发循环,需要改为局部 import。`api/routes_helpers/model_resolve.py` 顶部尽量只放标准库 + `api.config`,其他依赖延迟到函数体内。

## 不在第一步范围

- 任何 `_handle_*` 端点处理函数(留给第二步按业务前缀拆 dispatcher)。
- logs endpoint helpers [routes.py:2067-2140](routes.py#L2067) 与 insights endpoint helpers [routes.py:2141-2525](routes.py#L2141)——它们是端点逻辑,不是纯 helper,跟着第二步的 `/api/logs`、`/api/insights` 一起搬更整齐。
- handle_get / handle_post / handle_patch / handle_delete 的 if/elif 结构。
- routes.py 顶部 `from api.config/helpers/models/workspace/streaming/...` 的 import 块位置。
