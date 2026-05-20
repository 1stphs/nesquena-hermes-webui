# routes 拆分 step2 收尾:跨拆分 PR 的运维债务

## 目标 / 范围

step2 阶段 3(Z-PR1 ~ Z-PR8)推进过程中,**与拆分本身无关、但每次跑全套测试都干扰信号**的若干债务清理。也包括拆分工程留痕和 CI 加固。

本文档不规划新的拆分批次,只覆盖:

- 跑全套测试时噪音化的"既有失败/污染"
- CI gate 的触发面加固
- step2.md 的实际进度回填

## 不在范围(明确写出来)

- 任何 `_handle_*` 端点的新搬迁(走 Z-PR4 ~ Z-PR8)
- 测试现代化(那是 step3 提案,涉及把"扫 routes.py 字面量"改成"扫 api/**")
- `scan_routes_contracts.py` 的契约覆盖扩展 —— 实测 `_check_contracts` 已经覆盖了 R1 类回归会触发的契约面(`assert "X" in routes_src` / `def _handle_X(` / re-export / 全限定调用),不需重复造轮子
- routes.py 顶部 re-export 块的精简 —— 显式列举是 `mock.patch("api.routes._handle_xxx")` 兼容性所必需,**主动不动**

## 当前状态(2026-05-20 回填)

| 项 | 状态 | 证据 / 处理 |
|---|---|---|
| A1 `tests/test_profile_memory_api.py` 全套污染 | 可关闭 | 当前定向 `tests/test_profile_memory_api.py tests/test_profile_workspace_default.py tests/test_title_sanitization.py` 通过 18/18;后续已有 `1e53a43 test(profile): fully isolate api.profiles reload from sys.modules and parent package` |
| A2 `tests/test_profile_workspace_default.py` 全套污染 | 已完成 | `1e53a43` 修改 `tests/test_profile_workspace_default.py`,当前定向通过 |
| A3 `api/streaming.py` 顶部 CJK docstring | 已完成 | `11945df style(streaming): drop CJK paraphrase from module docstring`,当前 `api/streaming.py` 顶部为纯英文 docstring |
| B1 routes contract workflow 覆盖 `tests/test_*.py` | 已完成 | `beda8fa ci(routes): extend contract gate to tests changes`,当前 pull_request/push paths 均包含 `tests/test_*.py` |
| C1 `routes-refactor-step2.md` 进度回填 | 已完成 | 本轮在 `api/routes-refactor-step2.md` 追加 Z-PR1 到 Z-PR8 实际记录和 red/yellow 留存清单 |
| `.gitignore` unignore routes refactor docs | 已完成 | 当前 `.gitignore` 已包含 `!api/routes-refactor-step1.md`、`!api/routes-refactor-step2.md`、`!api/routes-refactor-followups.md`、`!api/routes-handlers-contract.md` |

当前 step2 不再继续规划新的 handler 搬迁。剩余 red/yellow 端点进入 step3:先测试现代化,再谈迁移。

## A 类:测试基础设施债务

### A1. `tests/test_profile_memory_api.py` 6 个测试在全套中污染

**2026-05-20 状态**:可关闭。当前未复现;定向运行 `tests/test_profile_memory_api.py tests/test_profile_workspace_default.py tests/test_title_sanitization.py` 为 18/18 通过。后续已有 `1e53a43 test(profile): fully isolate api.profiles reload from sys.modules and parent package`,该提交隔离 `api.profiles` reload 和 parent package 引用。

**现状**:6 个测试单独跑全过(单文件 23/23),嵌入全套时挂 6 个。每个测试都 `monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", base)` + 用 `tmp_path`——表面看 fixture 隔离对了。

**怀疑路径**:

1. `api/routes_handlers/profile.py` 通过 `_routes_binding` 动态读 `api.routes` 的绑定,但某些路径在 import 时把 `_DEFAULT_HERMES_HOME` 复制到自己的 namespace(缓存)。`monkeypatch.setattr(profiles, "...", base)` 改的是 `api.profiles` 模块的绑定,handler 那侧拿到的是 stale 快照。
2. 先前运行的某个 test 留下了 `~/.hermes/...` 之类的真实目录污染(没用 `tmp_path`),后续 tests 读到。

**改法**:

1. **先定位是哪个先前 test 污染了**。`pytest tests/ -x --lf` 在全套挂时,逐步往前回溯前置 test(`pytest tests/test_xxx.py tests/test_profile_memory_api.py` 二分查找)。
2. 找到污染源后,要么给污染源补 fixture 隔离,要么给 `profile_memory_api` 加 autouse fixture 强制重置 `profiles._DEFAULT_HERMES_HOME`、清缓存、清 `~/.hermes` 残留。
3. 不接受"全局 autouse 暴力清 `~/.hermes`"——会破坏真实开发环境。隔离必须用 `tmp_path`。

**验证**:全套 `pytest tests/` 跑,该 6 个测试不再出现在失败列表;单跑仍全过。

**风险**:污染源可能在远处(不是 profile 系列文件),修改可能扩散。控制改动半径:**只改污染源 + profile_memory_api 文件,不动 `api/routes_handlers/profile.py` 和 `api/profiles.py`**。

**commit 策略**:独立 commit `test(profile-memory): isolate from cross-test pollution`,不混任何 Z-PR。

### A2. `tests/test_profile_workspace_default.py` 全套挂 / 单跑过

**2026-05-20 状态**:已完成。`1e53a43` 已改 `tests/test_profile_workspace_default.py`,当前定向验证通过。

**现状**:`test_load_workspaces_falls_back_to_named_profile_workspace_dir` 单跑过,全套挂。又一个污染。

**改法**:跟 A1 同样的二分定位流程。可能跟 A1 同源(都跟 profile 模块状态有关),如果是,A1 修了 A2 顺带过。先做 A1,跑全套看 A2 是否还在。

**commit 策略**:如果 A1 没顺带修好,单独一 commit。

### A3. `tests/test_title_sanitization::test_title_generation_source_has_no_cjk_literals`

**2026-05-20 状态**:已完成。`11945df style(streaming): drop CJK paraphrase from module docstring` 已把 `api/streaming.py` 顶部中文 paraphrase 移除,当前定向验证通过。

**现状**:测试断言 `api/streaming.py` 全文不含 `[一-鿿]` 范围字符。当前 [api/streaming.py:4-7](api/streaming.py#L4) 顶部 docstring 有"中文说明:Hermes Web UI 的 SSE streaming engine..."——直接挂。

```python
# tests/test_title_sanitization.py:66
def test_title_generation_source_has_no_cjk_literals(self):
    src = Path("api/streaming.py").read_text(encoding="utf-8")
    self.assertNotRegex(src, r"[一-鿿]", "title generation code should stay English-only")
```

**改法两选**:

#### 选项 1:把 streaming.py 顶部中文 docstring 改成纯英文 paraphrase

```python
"""
Hermes Web UI -- SSE streaming engine and agent thread runner.
Includes Sprint 10 cancel support via CANCEL_FLAGS.
"""
```

- 优点:零改动测试,行为完全无变化,test 自然过
- 缺点:违反用户 CLAUDE.md "注释用中文" 偏好

#### 选项 2:把测试范围收紧到"只扫 title 生成相关函数体"而非整个文件

- 比如 `inspect.getsource(streaming.<title_generation_func>)` 然后 grep CJK
- 优点:不删中文 docstring,符合项目"注释用中文"
- 缺点:要改测试代码,违反"零测试改动"原则;且需要测试作者本意确认(可能 ta 就想锁整个文件)

**建议**:选选项 1。理由:
- "注释用中文"是项目偏好,但 streaming.py 顶部那段中文是 docstring 不是 inline 注释(docstring 是程序内的 `__doc__` 字符串,英文更通用)
- 该 docstring 本质就是"重复一遍英文 docstring"——信息密度为零,删了零损失
- 真正的内联代码注释一律保留中文,不动

**验证**:`pytest tests/test_title_sanitization.py` 全过;`grep -n "中" api/streaming.py` 应该只剩函数体内的中文注释(如有)。

**风险**:streaming.py 是 step1 没动过的核心模块,改 docstring 要小心,但只动头 8 行的 docstring,风险最低。

**commit 策略**:`docs(streaming): drop CJK paraphrase from module docstring`,1 个独立 commit。

## B 类:CI gate 加固

### B1. `routes-contracts.yml` 触发 paths 扩展到 `tests/test_*.py`

**2026-05-20 状态**:已完成。`beda8fa ci(routes): extend contract gate to tests changes` 已在 pull_request 和 push 的 paths 中加入 `tests/test_*.py`。

**现状**:CI workflow 当前 paths 只覆盖:

```yaml
- 'api/routes.py'
- 'api/routes_helpers/**'
- 'api/routes_handlers/**'
- 'api/routes-handlers-contract.md'
- 'scripts/scan_routes_contracts.py'
- '.github/workflows/routes-contracts.yml'
```

**风险**:如果有人在 `tests/test_*.py` 新加一个源码扫描契约(比如又一条 `assert "Foo" in ROUTES_PY`),scan 工具不会被强制重跑,`contract.md` 不会被强制更新,直到下一个改 routes 的 PR 才暴露——可能这时已经晚了一个迭代。

**改法**:

```yaml
on:
  pull_request:
    branches: [master]
    paths:
      - 'api/routes.py'
      - 'api/routes_helpers/**'
      - 'api/routes_handlers/**'
      - 'api/routes-handlers-contract.md'
      - 'scripts/scan_routes_contracts.py'
      - '.github/workflows/routes-contracts.yml'
      - 'tests/test_*.py'    # 新增
  push:
    branches: [master]
    paths:
      # ... 同上
      - 'tests/test_*.py'    # 新增
```

**代价**:几乎所有 tests/ 改动都会触发这个 workflow,但 scan 本身秒级(本地实测 < 1s),CI 上 setup-python 慢一点,整体 < 30s,值得。

**验证**:故意改一个无关的 `tests/test_*.py`(比如加注释),push 个测试分支,看 routes-contracts workflow 是否触发。

**commit 策略**:`ci(routes): extend contract gate to tests changes`,独立 commit。

## C 类:工程留痕

### C1. `routes-refactor-step2.md` 阶段 3 实际进度回填

**2026-05-20 状态**:已完成。本轮已在 `api/routes-refactor-step2.md` 追加实际进度记录、当前 routes.py 行数、Z-PR1 到 Z-PR8 实际表、red/yellow 留存清单和 step3 边界。

**现状**:step2.md 表格写的预估和 Z-PR1~Z-PR3 实际值偏差很大:

| 项 | step2.md 预估 | 实际 |
|---|---|---|
| Z-PR1 profile | ~500 行 | 740 行 |
| Z-PR2 skill+memory | ~250 行 | 214 行(skill 3 + memory 1) |
| Z-PR3 mcp 补齐 | ~200 行 | 71 行 |
| 终态 routes.py | 5500-6500 行 | 外推 ~4300-4500 行 |

**改法**:等 Z-PR8 全部跑完,**在 step2.md 末尾增加一节 `## 实际进度记录`**,完整表格:

```markdown
## 实际进度记录(回填)

| PR | 端点数 | 实际降行 | 预估 | 偏差原因 |
|---|---|---|---|---|
| Z-PR1 profile | 10 | 740 | 500 | profile 单 handler 平均更大 |
| Z-PR2 skill+memory | 4 | 214 | 250 | 体量低估 |
| Z-PR3 mcp | 3 | 71 | 200 | 体量大幅高估 |
| ...
| 合计 | N | X | 2500 | |

终态 routes.py: A 行(起点 10043 - X)
```

**用途**:下次有类似 8000+ 行级别拆分时,有真实参考数据点。

**风险**:零。纯文档。

**commit 策略**:Z-PR8 验证通过后一并 commit `docs(routes): backfill stage 3 actuals`。

## 优先顺序与节奏

**2026-05-20 状态**:本节已归档。A/B/C 项均已有当前状态标注;不要再按下面旧排期继续执行。若要继续处理 routes 拆分,从 step3 测试现代化另开文档。

原计划按时间紧迫度和 ROI 排:

| 序 | 项 | 何时做 | 阻塞 Z-PR4? |
|---|---|---|---|
| 1 | **A3** docstring | 任何空闲时段 | 否,但每次跑全套都噪音化 |
| 2 | **B1** CI paths 扩展 | Z-PR4 之前 | 否,但越早越好 |
| 3 | **A1** profile_memory_api 污染定位 | Z-PR4 之前 / 与 Z-PR4 并行 | 否 |
| 4 | **A2** profile_workspace_default | 跟 A1 一起 / A1 后 | 否 |
| 5 | **C1** 进度回填 | Z-PR8 后 | 否(本就是收尾) |

**归档说明**:原建议节奏已经执行完或不再适用。当前不要并行起 Z-PR4 ~ Z-PR8;这些 PR 已收口到 Z-PR8。

## 实施时间预估

**2026-05-20 状态**:历史估时,仅保留作复盘参考。

| 项 | 估时 |
|---|---|
| A1 profile_memory_api(含定位 + 修) | 1-2 小时(主要在二分定位) |
| A2 profile_workspace_default | 0.5 小时(若与 A1 同源则顺带) |
| A3 streaming docstring | 5 分钟 |
| B1 CI paths | 5 分钟 |
| C1 step2.md 回填 | 15 分钟(Z-PR8 后) |
| 合计 | ~2-3 小时(分散在 Z-PR4~8 期间) |

## .gitignore 提醒

**2026-05-20 状态**:已完成。当前 [.gitignore](../.gitignore) 已包含:

```gitignore
!api/routes-refactor-step1.md
!api/routes-refactor-step2.md
!api/routes-refactor-followups.md
!api/routes-handlers-contract.md
```

`~/.gitignore_global` 的 `*.md` 规则会忽略本文件。落盘后需要在 [.gitignore](../.gitignore) 加一行:

```
!api/routes-refactor-followups.md
```

否则本文档不会进 git。`api/routes-refactor-step1.md` 也一直没被 unignore(step2 时就该顺手加),可以一并补:

```
!api/routes-refactor-step1.md
!api/routes-refactor-followups.md
```
