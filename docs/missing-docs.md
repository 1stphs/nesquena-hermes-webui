# 项目缺失文档梳理

**最后更新：** 2026-06-26  
**范围说明：** 覆盖 Nesquena Hermes 数字员工平台全栈——本仓库 Hermes API Service、用户端前端（`digital_employee`）、NocoBase、独立 Hermes Agent gateway（`hermes:8642`）。不与 `docs/api-docs.md` 重复罗列 API 路径。

---

## 执行摘要

1. **缺少统一新人 Onboarding**：架构分散在多份 `AGENTS.md`，无「从零到可联调」单页 runbook。
2. **NocoBase ↔ Hermes Profile 数据模型无权威说明**：`hermes_profiles` 与 Hermes home 映射散落代码中，易写错层。
3. **三服务运维边界不完整**：`8787` / `8642` / NocoBase 职责有提及，但缺日常观测与误操作防护手册。
4. **测试命令与仓库现状不一致**：文档写 `pytest tests/`，实测用例在 `api/test_*.py`（9 个文件）。
5. **部分前端子系统无专项文档**：RustMailer 邮箱、日程看板本地态等被 `api-docs.md` 排除在外。

---

## 已有文档地图

| 文档 | 覆盖 | 新鲜度 |
|------|------|--------|
| `AGENTS.md`、`README.md`、`docs/README.md` | 架构、模块、部署摘要 | 较新 |
| `docs/api-docs.md` + `.html` | NocoBase + Hermes 主链路 API | 较新 |
| `docs/deploy-172.md` | 172 生产部署与回滚 | 较新 |
| `digital_employee/src/api/agent.md` | 前端接线总账 | 较新，需定期核对 |
| `docs/other/*` | Docker、排障、extension | **偏旧/历史** |
| `api/routes-handlers-contract.md` | routes 契约扫描 | 自动生成，缺阅读指南 |

---

## 缺失文档清单

> 共 **21** 条（P0: 6，P1: 8，P2: 7）。已剔除 MCP/Kanban、审批 SSE 集成、聊天能力面板、routes 拆分指南、部门群聊 PRD 等非阻塞项；同类项已合并。

### P0 — 阻塞上手或易引发生产事故

#### P0-01 平台新人 Onboarding 指南

- **影响**：新人常卡在「NocoBase 登录成功但 Hermes 401」「NocoBase 200 空 body」。
- **落点**：`docs/onboarding.md`
- **要点**：仓库约定 → `bootstrap.py` / `npm run dev` → 三代理 → 登录 → `token-login` → 最小 smoke
- **依据**：`bootstrap.py`、`vite.config.js`、`api/auth.py`、`agent.md`
- **负责人**：后端 + 前端

#### P0-02 NocoBase 与 Hermes Profile 数据模型

- **影响**：`hermes_profiles` 与 `/.hermes/profiles/` 对应关系不清，改 profile 易写错层。
- **落点**：`docs/data-model.md`
- **要点**：用户表、Profile 绑定、会话/记忆/Cron 文件态、技能双源、禁止跨层伪造数据
- **依据**：`docs/README.md` 数据归属、`nocobase.js`、`api/profiles.py`
- **负责人**：后端 + 产品

#### P0-03 鉴权与安全边界说明

- **影响**：`X-User-Id`、CSRF、`hermes_session`、NocoBase Authorization 混用，边界不清。
- **落点**：`docs/security.md`（含 CORS/CSRF 子章节）
- **要点**：信任边界图、公开路径、token 生命周期、代理层 Authorization、敏感信息清单
- **依据**：`api/auth.py`、`api/routes_helpers/csrf.py`
- **负责人**：后端 + 运维

#### P0-04 三服务运维边界与事故 runbook

- **影响**：误重启 `hermes:8642`、从错误 compose 目录起容器。
- **落点**：`docs/operations-runbook.md`
- **要点**：服务矩阵、只重建 webui 规则、健康检查层级、日志、回滚、常见误操作
- **依据**：`AGENTS.md`、`docs/deploy-172.md`、`docker-compose.yml`
- **负责人**：运维 + 后端

#### P0-05 前端环境变量与代理配置说明

- **影响**：`.env.example` 变量多，易漏配 `FOXUAI_NOCOBASE_AUTHORIZATION`。
- **落点**：`digital_employee/docs/environment.md`
- **要点**：`VITE_*` 分界、三代理前缀、workflow 路径、部署变量
- **依据**：`.env.example`、`vite.config.js`
- **负责人**：前端 + 运维

#### P0-06 全栈发布检查清单

- **影响**：前后端独立发布，顺序错误导致前端调用未部署 API。
- **落点**：`docs/release-checklist.md`
- **要点**：变更类型判断、双端部署顺序、`ensure_*fields.py` 时机、smoke 矩阵
- **依据**：`deploy-172.sh`、`digital_employee/AGENTS.md`
- **负责人**：运维

### P1 — 影响联调、治理或主功能交付

#### P1-01 前端子系统产品文档（邮箱 / 日程 / 用量 / 招募）

- **影响**：`api-docs.md` 未覆盖 RustMailer、日程看板 `localStorage` 与 Cron 合并逻辑、用量页、招募端到端流程。
- **落点**：`digital_employee/docs/subsystems.md`（单文档多章节）
- **要点**：RustMailer 代理与 `hermes_user_emails` 分工；看板本地态 vs `/api/crons/batch`；用量指标；招募 workflow 序列
- **依据**：`rustmailer.js`、`scheduledTasks.js`、`PlaceholderPage.vue`、`agentRecruitment/`
- **负责人**：前端 + 产品

#### P1-02 技能测试与市场上架审核流水线

- **影响**：安全/可用性测试、`publish-to-market-review` 与 NocoBase 字段同步缺运维说明。
- **落点**：`docs/skills-review-pipeline.md`
- **要点**：测试接口、`ensure_*fields.py` 只读与 `--apply`
- **依据**：`api/routes_handlers/skill.py`、`scripts/ensure_*.py`
- **负责人**：后端 + 管理员

#### P1-03 Provider 编排与 NocoBase 同步

- **影响**：`user-ai-providers/*` 与 `hermes_providers` 关系复杂，缺架构说明。
- **落点**：`docs/provider-sync.md`
- **要点**：`model_v1` 同步、`validate_user_provider_config_sync.py`、失败处理
- **依据**：`api/providers_runtime/`、`openspec/changes/add-user-ai-provider/`
- **负责人**：后端

#### P1-04 Caddy 生产路由与多前缀代理

- **影响**：`/hermes`、`/nocobase`、`/rustmailer`、SPA 回落规则仅在脚本注释中。
- **落点**：`digital_employee/docs/caddy-routing.md`
- **依据**：`deploy-foxu.sh`
- **负责人**：运维 + 前端

#### P1-05 Hermes home 目录结构与 Profile 隔离

- **影响**：卷挂载多路径，排障不知会话/技能文件位置。
- **落点**：`docs/hermes-home-layout.md`
- **依据**：`docker-compose.yml`、`api/profiles.py`
- **负责人**：后端 + 运维

#### P1-06 测试策略与目录现状

- **影响**：`pytest tests/` 与仓库不符，新人不知跑哪些测试。
- **落点**：`docs/testing.md`
- **要点**：更正为 `pytest api/ -v`、`scan_routes_contracts.py --check`
- **依据**：`api/test_*.py`（9 文件）、`AGENTS.md`
- **负责人**：后端

#### P1-07 Schema 迁移与数据修复脚本 runbook

- **影响**：`ensure_*fields.py`、`repair_workspace_user_turns.py` 等无统一入口。
- **落点**：`docs/scripts-runbook.md`
- **依据**：`scripts/` 目录
- **负责人**：后端 + 运维

#### P1-08 管理员与普通用户能力矩阵

- **影响**：`requiresAdmin` 路由与审核 API 未表格化。
- **落点**：`docs/admin-capability-matrix.md`
- **依据**：`router/index.js`、`skill-templates/review-list`
- **负责人**：产品 + 前端

### P2 — 完善体验与技术债（可延后）

#### P2-01 上游遗留 API 清单（onboarding / rollback / updates）

- **落点**：`docs/legacy-upstream-apis.md`
- **说明**：记录 `/api/onboarding/*`、`/api/rollback/*`、`/api/updates/*` 是否对平台开放或仅兼容保留
- **负责人**：后端

#### P2-02 独立 Hermes Agent gateway（8642）使用指南

- **落点**：`docs/hermes-gateway-8642.md`
- **说明**：与 WebUI `8787` 区分，外部 OpenAI-compatible 调用方登记
- **负责人**：运维

#### P2-03 监控与用量遥测

- **落点**：`docs/observability.md`
- **说明**：`usage_telemetry`、`/api/session/usage` 与 NocoBase `hermes_chat_usage_events` 关系
- **负责人**：后端

#### P2-04 并发限流与内存压力

- **落点**：并入 `docs/operations-runbook.md` 或 `docs/request-limits.md`
- **说明**：`429` / `SERVER_MEMORY_PRESSURE` 阈值与前端文案对应
- **负责人**：后端 + 运维

#### P2-05 Session 高级能力（projects / workspaces / handoff）

- **落点**：`docs/session-workspace-advanced.md`（按需）
- **说明**：代码完整，前端消费面未标注；非主链路可后补
- **负责人**：后端 + 前端

#### P2-06 前端产品体验补充（通讯录 / 技能页区分 / views 索引）

- **落点**：`digital_employee/docs/product-ux-notes.md`（按需）
- **说明**：降低优先级的产品说明，非阻塞联调
- **负责人**：产品 + 前端

#### P2-07 hermes-agent-src 副本升级策略

- **落点**：`docs/hermes-agent-src-sync.md`（按需）
- **说明**：挂载副本与上游同步流程
- **负责人**：后端

---

## 按领域速览

| 领域 | 已有 | 最优先补 |
|------|------|----------|
| 架构与边界 | `AGENTS.md` 架构图 | P0-02、P0-04、P2-02 |
| 本地联调 | 可启动，无 checklist | P0-01、P0-05 |
| API 与数据 | `api-docs.md` 主链路较全 | P0-02、P1-03 |
| 前端模块 | `agent.md` 部分覆盖 | P1-01 |
| 部署运维 | `deploy-172.md` | P0-04、P0-06、P1-04、P1-07 |
| 安全权限 | api-docs 简述 | P0-03、P1-08 |
| 测试质量 | 命令过时 | P1-06 |
| 管理员治理 | 实现有、SOP 无 | P1-02、P1-08 |

---

## 文档过期 / 需合并项

| 项 | 建议 |
|----|------|
| `docs/other/docker.md`、`troubleshooting.md` | 标「上游/单人 WebUI 参考」，链到 `onboarding.md` / `operations-runbook.md` |
| `docs/other/EXTENSIONS.md` | 标 **已废弃** |
| `AGENTS.md` `pytest tests/` | 改为 `pytest api/ -v` 或恢复 `tests/` |
| `agent.md` 历史绝对路径 | 以各仓库 `AGENTS.md` 为准，定期 diff |
| `api-docs.md` vs `agent.md` | 分工：路由目录 vs 前端接线 |
| `deploy-172.md` 镜像名 | 部署前 `docker inspect` 为准 |

---

## 建议编写顺序

### 第 1–2 周

1. `docs/onboarding.md`（P0-01）
2. `docs/security.md`（P0-03）
3. `docs/data-model.md`（P0-02）
4. `docs/testing.md`（P1-06）
5. `digital_employee/docs/environment.md`（P0-05）

### 第 3–4 周

6. `docs/operations-runbook.md`（P0-04）
7. `docs/release-checklist.md`（P0-06）
8. `docs/scripts-runbook.md`（P1-07）
9. `digital_employee/docs/caddy-routing.md`（P1-04）
10. `docs/hermes-home-layout.md`（P1-05）

### 第 5–8 周

11. `digital_employee/docs/subsystems.md`（P1-01）
12. `docs/skills-review-pipeline.md`（P1-02）
13. `docs/admin-capability-matrix.md`（P1-08）
14. `docs/provider-sync.md`（P1-03）

### 按需（P2）

15. `docs/legacy-upstream-apis.md`、`docs/hermes-gateway-8642.md`、`docs/observability.md` 等

---

## 维护约定

1. **事实源**：路由 → `routes_dispatcher.py`；前端 → `agent.md` + 源码；部署 → `docker inspect`。
2. **与 api-docs 分工**：`api-docs.md` 登记接口；本清单登记缺口；补完后更新「已有文档地图」并划掉对应条。
3. **不写敏感信息**：密码、token、真实 API key、`hermes_session` 样例。
4. **HTML 预览**：`docs/missing-docs.html` 本地 `python3 -m http.server` 查看。

---

*本清单 **21** 条（原 35 条，精简约 40%）。已剔除 MCP/Kanban、审批 SSE、终端嵌入、API token 轮换、routes 拆分指南、部门群聊 PRD 等待定或低影响项。*