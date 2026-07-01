# Digital Employee API 接口文档

本文档描述 **digital_employee** 用户端前端对接的两套后端能力：

1. **Hermes WebUI API Service**（本仓库 `nesquena-hermes-webui`）— 会话、对话流、定时任务、技能工坊、记忆、文件等
2. **NocoBase**（`https://www.foxuai.com`）— 用户登录注册、智能体绑定、通讯录、人才市场、用量追踪、邮箱账号等

> 事实源顺序：本仓库以 `api/routes_dispatcher.py` 和对应 handler 为准；NocoBase 以 `digital_employee/src/api/nocobase.js` 和实际 Network 请求为准。本文档不写入密码、token、cookie、API key 等敏感信息。

**最后更新：** 2026-06-26

> **文档范围**：覆盖 NocoBase 与 Hermes WebUI 主链路。RustMailer（`/rustmailer/*`）与 `scheduledTasks.js` 本地 task board 状态不在本文档范围内；详见 `digital_employee/src/api/agent.md`。

---

## 架构与代理

```text
digital_employee Vue 前端
  ├─ /hermes/*   → Caddy/Vite proxy → Hermes WebUI :8787 → 本仓库 /api/*
  └─ /nocobase/* → Caddy/Vite proxy → https://www.foxuai.com/api/*
```

| 层级 | 前端前缀 | 服务路由 | 职责 |
|------|----------|----------|------|
| Hermes WebUI | `/hermes/api/*` | `/api/*` | 会话、聊天流、cron、技能工坊写操作、记忆、上传 |
| NocoBase | `/nocobase/api/*` | `/api/*` | 用户态业务表、workflow、附件上传 |

- 登录身份来源是 **NocoBase**，不是 Hermes WebUI。
- Hermes 业务请求在 NocoBase 登录成功后，通过 `POST /hermes/api/auth/token-login` 建立 `hermes_session` HttpOnly cookie。
- 用户 ID 来自登录响应 `data.id`，前端写入 `localStorage`（`digital_employee.userId`）和 cookie（`X-User-Id`）。

---

## 通用约定

### Hermes WebUI 请求

| 项 | 约定 |
|----|------|
| 实现 | `digital_employee/src/api/hermes.js` → `hermesFetch()` |
| URL | `{VITE_HERMES_PROXY_PATH \|\| '/hermes'}/api/...` |
| Cookie | `credentials: 'include'`，携带 `hermes_session` |
| 用户上下文 | Header `X-User-Id: <data.id>`（部分接口必填） |
| CSRF | `POST`/`PATCH`/`DELETE` 需同源 Origin/Referer |
| SSE | `EventSource(url, { withCredentials: true })`，无法自定义 header，依赖 cookie |

### NocoBase 请求

| 项 | 约定 |
|----|------|
| 实现 | `digital_employee/src/api/nocobase.js` |
| URL | `{VITE_NOCOBASE_PROXY_PATH \|\| '/nocobase'}/api/...` |
| Cookie | `credentials: 'include'` |
| 用户上下文 | Header `X-User-Id: <data.id>` |
| 固定 Header | `X-Hostname: www.foxuai.com`、`X-Authenticator: basic` |
| Authorization | 由代理层（Vite/Caddy）从 `FOXUAI_NOCOBASE_AUTHORIZATION` 注入，不进前端 bundle |
| Workflow 响应 | 通常有外层 `{ code, message, data }` wrapper |

### Hermes 鉴权要点

| 机制 | 说明 |
|------|------|
| `hermes_session` | 密码或 token 登录后写入的 HttpOnly cookie |
| 公开路径 | `/health`、`/api/auth/status`、`/api/auth/login`、`/api/auth/token-login` 等 |
| `X-User-Id` | 用户上下文，**不是**可信鉴权凭证；`/api/user-ai-providers*`、`/api/user-skills*` 等必填 |
| `hermes_profile` cookie | 当前激活的 Hermes Profile，由 `POST /api/profile/switch` 设置 |

---

## NocoBase 接口

所有路径均通过 `/nocobase` 前缀访问。完整 URL 示例：`/nocobase/api/hermes_profiles:list`。

### 认证与注册

#### 用户登录

| 项 | 值 |
|----|-----|
| **路径** | `POST /nocobase/api/webhook:trigger/ugyoa0123ft` |
| **Workflow** | Hermes登录 (`ugyoa0123ft`) |
| **Helper** | `loginWithHermes()` |
| **页面** | `Login.vue` |

请求体：

```json
{
  "email": "user@example.com",
  "password": "<base64(encodeURIComponent(password))>"
}
```

成功响应示例：

```json
{
  "code": "200",
  "message": "登录成功",
  "data": {
    "id": 1001,
    "name": "example_user",
    "role": "用户",
    "email": "user@example.com",
    "department": "",
    "avatar_url": null
  }
}
```

- 前端以 `data.id` 作为当前用户 ID。
- 登录成功后继续调用 Hermes `token-login` 建立 WebUI 会话。

#### 用户注册

| 项 | 值 |
|----|-----|
| **路径** | `POST /nocobase/api/webhook:trigger/3ahenbutb7a` |
| **Workflow** | Register (`3ahenbutb7a`) |
| **Helper** | `registerWithHermes()` |
| **页面** | `Register.vue` |

请求体：`{ name, email, password }`（密码明文，由 workflow 处理）

成功时 `code === "200"`。

---

### 智能体 Profile

用户绑定的智能体数据存储在 NocoBase `hermes_profiles` 表；创建/更新/删除通过 workflow 编排，并同步 WebUI Profile 目录。

> 对话侧栏「我的智能体」**主读源**是 NocoBase `hermes_profiles:list`，不是 Hermes `GET /api/profiles`。

#### 列出当前用户智能体

| 项 | 值 |
|----|-----|
| **路径** | `GET /nocobase/api/hermes_profiles:list` |
| **Collection** | `hermes_profiles` |
| **Helper** | `listCurrentUserHermesProfiles()`、`listCurrentUserManagedHermesAgents()`、`listCurrentUserHermesProfilesWithTalentMarket()` |

查询参数：

| 参数 | 说明 |
|------|------|
| `paginate=false` | 不分页 |
| `filter[user_id]=<currentUserId>` | 按当前用户过滤 |
| `appends=hermes_skills` | 可选，附带已绑定技能 |
| `appends=hermes_talent_market` | 可选，招募场景附带人才市场来源 |

响应 `data[]` 主要字段：`id`、`profile_name`、`display_name`、`soul`、`description`、`path`、`hermes_providers_id`、`hermes_skills`。

前端归一化为 `{ active, profiles }` 供聊天侧栏和智能体管理使用。

| Helper | 场景 |
|--------|------|
| `listCurrentUserHermesProfiles()` | 聊天侧栏、定时任务、邮箱页 |
| `listCurrentUserManagedHermesAgents()` | 智能体管理页（`appends=hermes_skills`） |
| `listCurrentUserHermesProfilesWithTalentMarket()` | 招募页（`appends=hermes_talent_market`） |

#### 创建智能体

| 项 | 值 |
|----|-----|
| **路径** | `POST /nocobase/api/webhook:trigger/ne15m97163y` |
| **Workflow** | Create Profile (`ne15m97163y`) |
| **Helper** | `createUserBoundHermesProfile()` |

请求体：

```json
{
  "profile_name": "market-analyst",
  "display_name": "市场分析助手",
  "avatar": "/uploads/market.png",
  "description": "一句话描述智能体能力",
  "prompt": "角色设定 Prompt，映射到 WebUI SOUL",
  "is_default": false
}
```

- NocoBase workflow 内部调用 WebUI `POST /api/profile/create-agent`。
- 默认从 `company-assistant` 克隆配置（`clone_from` / `clone_config` 由 workflow 处理）。
- 创建成功后前端可调用 Hermes `POST /api/user-ai-providers/sync-profile` 同步模型配置。

#### 更新智能体

| 项 | 值 |
|----|-----|
| **路径** | `POST /nocobase/api/webhook:trigger/7hfvjmyroug` |
| **Workflow** | Update Profile (`7hfvjmyroug`) |
| **Helper** | `updateUserBoundHermesProfile()` |

请求体：

```json
{
  "profile_name": "market-analyst",
  "display_name": "市场分析助手",
  "soul": "角色设定内容",
  "description": "一句话描述",
  "avatar": "/uploads/market.png"
}
```

用户上下文通过 `X-User-Id` header 传递，不放入 body。

#### 更新智能体 Skills

| 项 | 值 |
|----|-----|
| **路径** | `POST /nocobase/api/webhook:trigger/onqtsk997ty` |
| **Workflow** | Update Profile Skills (`onqtsk997ty`) |
| **Helper** | `updateUserBoundHermesProfileSkills()` |

请求体：

```json
{
  "profile_name": "market-analyst",
  "skills": [1, 2, 3]
}
```

- `skills` 传 **Skill 模板 ID**（`hermes_skills_templates.id`），不是展示名。
- 未知 Skill ID 时 WebUI 返回错误，例如 `Unknown skill(s): <id>`。

#### 删除智能体

| 项 | 值 |
|----|-----|
| **路径** | `POST /nocobase/api/webhook:trigger/wum2qyn7etu` |
| **Workflow** | Delete Profile (`wum2qyn7etu`) |
| **Helper** | `deleteUserBoundHermesProfile()` |

请求体：`{ profile_name, profile_id }`

- 不可删除 `default` profile。
- 同步清理 WebUI Profile 目录和 `hermes_profiles` 绑定记录。

#### 上传头像附件

| 项 | 值 |
|----|-----|
| **路径** | `POST /nocobase/api/attachments:create` |
| **Helper** | `uploadNocobaseAttachment()` |

请求：`FormData`，字段 `file`。

响应：`{ data: { url, preview?, ... } }` → 前端取 `url` 用于创建/更新智能体。

---

### AI 渠道配置

#### 列出全局 AI 渠道

| 项 | 值 |
|----|-----|
| **路径** | `GET /nocobase/api/hermes_providers:list` |
| **Collection** | `hermes_providers` |
| **Helper** | `listHermesProviders()` / `listEnabledHermesProviders()` |

查询：`paginate=false`，可选 `filter[is_enable]=true`。

主要字段：`id`、`display_name`、`provider_name`、`model_name`、`model_level`、`api_mode`、`is_enable`、`is_default`。

`api_mode` 枚举：`anthropic`、`openai-chat-complete`、`openai-response`。

#### Profile 渠道绑定

| 操作 | 路径 / Helper | 说明 |
|------|---------------|------|
| 读当前 profile 绑定 | 首屏 `hermes_profiles:list` 的 `hermes_providers_id` | 聊天页主链路 |
| 读 profile 渠道（旧兼容） | `getCurrentUserHermesProfileProviderSelection()` | `UserAiProviderSettingsModal.vue` |
| 启用渠道 | `POST /hermes/api/user-ai-providers/enable` | `{ profile_id, provider_id }` |
| 恢复默认 | `POST /hermes/api/user-ai-providers/disable` | `{ profile_id }` |

写操作走 Hermes，不再直接 PATCH NocoBase `hermes_profiles`。

---

### AI 员工招募

#### 人才市场列表

| 项 | 值 |
|----|-----|
| **路径** | `GET /nocobase/api/hermes_talent_market:list` |
| **Collection** | `hermes_talent_market` |
| **Helper** | `listHermesTalentMarketAgents()` |
| **页面** | `agentRecruitment/Index.vue` |

查询：`page`、`pageSize`（默认 12），可选 `filter[categories][$includes]`、`filter[title_cn][$includes]`。

响应字段：`id`、`title`、`title_cn`、`soul`、`description`、`categories`、`avatar`、`path`。

#### 人才详情

`GET /nocobase/api/hermes_talent_market:get?filterByTk=<id>`

#### 默认虚拟员工头像

`GET /nocobase/api/hermes_virtual_employee_images:list?paginate=false&pageSize=200`

#### 招入公司

| 项 | 值 |
|----|-----|
| **路径** | `POST /nocobase/api/webhook:trigger/yoz7obm18sg` |
| **Workflow** | Recruit Talent (`yoz7obm18sg`) |
| **Helper** | `recruitHermesTalentMarketAgent()` |

请求体：`{ id: <hermes_talent_market.id> }`，用户上下文仅通过 `X-User-Id`。

招募页通过 `listCurrentUserHermesProfilesWithTalentMarket()` 判断哪些人才已招入（`appends=hermes_talent_market`）。

---

### 通讯录

| 操作 | 路径 | Helper |
|------|------|--------|
| 列出个人联系人 | `GET /nocobase/api/hermes_users_contacts:list` + 批量 `GET /nocobase/api/hermes_users:list` | `listContacts()` |
| 联系人 ID 快查 | `GET /nocobase/api/hermes_users_contacts:list` | `listPersonalContactUserIds()` |
| 搜索可添加用户 | `GET /nocobase/api/hermes_users:list` | `listContactCandidateUsers()` |
| 添加联系人 | `POST /nocobase/api/hermes_users_contacts:create` | `addContact()` |
| 删除联系人 | `POST /nocobase/api/hermes_users_contacts:destroy?filterByTk=<id>` | `deleteContact()` |
| 编辑联系人卡片 | `POST /nocobase/api/hermes_users_contacts:update?filterByTk=<id>` | `updateContactRelationProfile()` |
| 编辑用户资料（管理） | `POST /nocobase/api/hermes_users:update?filterByTk=<userId>` | `updateContactUserProfile()` |

联系人关系表：`hermes_users_contacts`；用户主表：`hermes_users`。

`listContacts()` 服务端过滤：`filter[affiliated_user_id]=<currentUserId>`，可选 `filter[nickname][$includes]` 搜索。

---

### 技能模板与用户技能

技能模板有 **两条读路径**，按页面区分：

| 页面 | 读路径 | Helper |
|------|--------|--------|
| 技能市场、审核、应用 | NocoBase 直连 | `nocobase.js` → `listHermesSkillTemplates()` |
| 聊天页 picker、智能体管理 picker | Hermes 代理 | `hermes.js` → `listHermesSkillTemplates()` → `GET /hermes/api/skill-templates` |

#### Skills 模板列表（NocoBase 直连 — 市场/审核主路径）

| 项 | 值 |
|----|-----|
| **路径** | `GET /nocobase/api/hermes_skills_templates:list` |
| **Collection** | `hermes_skills_templates` |
| **Helper** | `nocobase.js` → `listHermesSkillTemplates()` |
| **页面** | `SkillMarket.vue`、`SkillReview.vue`、`SkillApplications.vue` |

查询：`page`、`pageSize`、`except=content`，可选 `filter[categories][$includes]`、`filter[$or][title_cn/title/summary]`、`filter[market_review_status]`。

主要字段：`id`、`name`、`title`、`title_cn`、`description`、`summary`、`category` / `categories`、`market_review_status`。

#### Skills 模板列表（Hermes 代理 — picker 路径）

| 项 | 值 |
|----|-----|
| **路径** | `GET /hermes/api/skill-templates` |
| **Helper** | `hermes.js` → `listHermesSkillTemplates()` |
| **页面** | `skills/Index.vue` picker、`useChatPage.js` |

查询：`?page`、`?pageSize`、`?category`、`?keyword`。

#### Skills 模板详情

`GET /nocobase/api/hermes_skills_templates:get?filterByTk=<id>` — 含完整 `content`（`SkillReviewDetail.vue`）。

#### 用户技能列表（NocoBase 只读）

| 项 | 值 |
|----|-----|
| **路径** | `GET /nocobase/api/hermes_user_skills:list` |
| **Collection** | `hermes_user_skills` |
| **Helper** | `listCurrentUserHermesUserSkills()` → `hermes.js` 的 `listHermesUserSkills()` 包装 |

查询：`paginate=false`，`filter[user_id]=<currentUserId>`。

> `GET /hermes/api/user-skills` 是 WebUI 兼容面，**不是**前端技能工坊列表的主读源。

写操作（导入、发布、安装到 Profile 等）走 Hermes `/api/user-skills/*`。

---

### 用量追踪

| 项 | 值 |
|----|-----|
| **路径** | `GET /nocobase/api/hermes_chat_usage_events:list` |
| **Collection** | `hermes_chat_usage_events` |
| **页面** | `PlaceholderPage.vue`（`/index/usage-tracking`） |

查询：`page`、`pageSize`（默认 20）、`sort=-createdAt`。

Filter 由 `buildHermesChatUsageEventsFilter()` 构建 JSON，支持 `createdAt.$gte` / `$lte`、`model`、`$or` 关键词搜索。

页面使用 `listCurrentUserHermesChatUsageEventsAllPages()` 自动翻页拉全量。

额外 Header：`X-Role: admin`、`X-Locale: zh-CN`、`X-Timezone: +08:00`、`X-App: main`。

事件字段：`user_id`、`profile_name`、`session_id`、`model`、`input_tokens`、`output_tokens`、`total_tokens`、`estimated_cost`、`duration_seconds`。

用户展示名通过二次查询 `hermes_users:list?filter[id][$in][]=<user_id>` 合并。

---

### 邮箱账号

| 操作 | 路径 | Helper |
|------|------|--------|
| 列出邮箱 | `GET /nocobase/api/hermes_user_emails:list` | `listCurrentUserEmails()` |
| 创建邮箱 | `POST /nocobase/api/hermes_user_emails:create` | `createCurrentUserEmail()` |
| 更新邮箱 | `POST /nocobase/api/hermes_user_emails:update?filterByTk=<id>` | `updateCurrentUserEmail()` |

Collection：`hermes_user_emails`；按 `hermes_users_id` 过滤当前用户。

---

## Hermes WebUI 接口

所有路径均通过 `/hermes` 前缀访问。服务自身路由为 `/api/*`。

以下按 **digital_employee 实际使用优先级** 排列；完整路由清单见 `api/routes_dispatcher.py`（约 150+ 路径）。

### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/auth/status` | 鉴权状态 `{auth_enabled, logged_in}`，公开 |
| POST | `/api/auth/token-login` | API token 登录，写 `hermes_session`，**CSRF 豁免** |
| POST | `/api/auth/login` | 密码登录 |
| POST | `/api/auth/logout` | 登出，清除 cookie |

Token 登录请求体：`{ "token": "<api_token>" }`

响应：`{ "ok": true, "token_id": "..." }`

---

### 模型与 Provider

| 方法 | 路径 | 说明 | 备注 |
|------|------|------|------|
| GET | `/api/models` | 模型目录 | 可选 `?profile_id`；可选 `X-User-Id` |
| GET | `/api/models/live` | 实时拉取供应商模型列表 | `?provider=` |
| GET | `/api/user-ai-providers` | 用户 Provider 列表 | **必填** `X-User-Id`；`?profile_id=` |
| POST | `/api/user-ai-providers/enable` | 为 Profile 启用渠道 | `{ profile_id, provider_id }` |
| POST | `/api/user-ai-providers/disable` | 恢复系统默认 | `{ profile_id }` |
| POST | `/api/user-ai-providers/sync-profile` | 单 Profile 配置同步 | `{ profile_name, dry_run? }` |
| POST | `/api/user-ai-providers/sync` | 批量 sync | `{ mode, dry_run? }` |

> 用户自定义上传渠道（create/update/delete/test custom provider）当前后端返回 **405 `provider_write_disabled`**，不支持用户自行上传渠道。

---

### 会话

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sessions` | 会话列表；`?hermes_profile=`（**必填**，WebUI profile 标识）、`?page`、`?page_size` |
| GET | `/api/session` | 单会话详情；`?session_id`（必填）、`?messages=1`、`?resolve_model=0` |
| POST | `/api/session/new` | 创建会话；`{ workspace?, model?, model_provider?, profile?, project_id? }` |
| POST | `/api/session/rename` | 重命名；`{ session_id, title }` |
| POST | `/api/session/delete` | 删除；`{ session_id }` |
| POST | `/api/session/clear` | 清空消息 |
| POST | `/api/session/pin` | 置顶/取消 |
| POST | `/api/session/archive` | 归档 |
| POST | `/api/session/move` | 移动到项目 |
| GET | `/api/sessions/search` | 搜索；`?q=` |
| GET | `/api/session/export` | 导出 JSON |

#### 会话变更（聊天页）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/session/branch` | 分叉；`{ session_id, keep_count?, title? }` |
| POST | `/api/session/truncate` | 截断消息；`{ session_id, keep_count }` |
| POST | `/api/session/retry` | 重试上一轮 |
| POST | `/api/session/undo` | 撤销上一轮 |
| POST | `/api/session/compress` | 压缩上下文；`{ session_id, focus_topic? }` |
| POST | `/api/session/conversation-rounds` | 对话轮次统计；`{ session_id, since? }` |

创建会话响应含 `{ session: { id, title, workspace, model, ... } }`。

---

### 对话与流式

典型聊天流程：

```text
1. POST /api/session/new        → 获得 session_id
2. POST /api/chat/start         → 获得 stream_id
3. GET  /api/chat/stream        → SSE 订阅事件
4. GET  /api/chat/cancel        → 可选，取消流
```

#### 启动对话

| 项 | 值 |
|----|-----|
| **路径** | `POST /api/chat/start` |
| **Body** | `{ session_id, message, attachments?, workspace?, model?, model_provider?, profile? }` |
| **响应** | `{ stream_id, session_id, pending_started_at, effective_model?, effective_model_provider? }` |

#### SSE 事件流

| 项 | 值 |
|----|-----|
| **路径** | `GET /api/chat/stream?stream_id=<id>` |
| **格式** | Server-Sent Events |
| **Cookie** | `withCredentials: true` |

常见事件（与 `streaming.py` / `openHermesStream()` 一致）：

| 事件 | 说明 |
|------|------|
| `token` | 模型输出 token，`{ text }` |
| `reasoning` | 推理内容，`{ text }` |
| `tool` | 工具开始，`{ event_type, name, preview, args }` |
| `tool_complete` | 工具完成 |
| `clarify` | 需要用户澄清 |
| `metering` | 用量统计 tick |
| `compressing` / `compressed` | 上下文压缩 |
| `done` | 单轮完成 |
| `stream_end` | 流结束 |
| `cancel` | 取消 |
| `warning` | 警告 |
| `apperror` | 应用错误 |

#### 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/chat/stream/status` | 检查流是否存活 |
| GET | `/api/chat/cancel` | 取消当前流 |
| POST | `/api/chat` | 同步对话（非流式兜底） |
| POST | `/api/btw` | 旁路追问 |
| POST | `/api/background` | 后台任务 |
| GET | `/api/background/status` | 后台任务结果 |

---

### 定时任务

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/crons` | 列出**当前激活 Profile** 的 cron 任务 |
| POST | `/api/crons/batch` | **多 Profile 批量拉取**；`{ profile_names: [...] }` |
| POST | `/api/crons/calendar` | 日历视图；`{ profile_names, start_date, end_date }`（可选 `month`） |
| POST | `/api/crons/create` | 创建；`{ prompt, schedule, name?, deliver?, skills?, model?, profile? }` |
| POST | `/api/crons/update` | 更新；`{ job_id, ... }` |
| POST | `/api/crons/delete` | 删除；`{ job_id }` |
| POST | `/api/crons/run` | 手动触发 |
| POST | `/api/crons/pause` | 暂停 |
| POST | `/api/crons/resume` | 恢复 |
| GET | `/api/crons/output` | 输出文件；`?job_id` |
| GET | `/api/crons/history` | 运行历史 |
| GET | `/api/crons/status` | 运行状态 |
| POST | `/api/crons/calendar/create` | 创建日历事件；`{ start_time, date?, title?, profile?, all_day?, location? }` |

前端定时任务页数据流（`scheduledTasks/Index.vue`）：

1. 从 NocoBase `hermes_profiles:list` 获取 Profile 列表
2. `POST /api/crons/batch` 拉取各 Profile 任务看板
3. `POST /api/crons/calendar` 拉取日历视图（传 `start_date` / `end_date`）

---

### 技能

####  bundled / Profile 技能（WebUI 本地）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/skills` | 列出 bundled skills |
| GET | `/api/skills/content` | 读取 skill 内容；`?name` |
| GET | `/api/profile/installed-skills` | Profile 已安装技能 |
| POST | `/api/skills/install-community` | 安装社区 skill |
| POST | `/api/skills/uninstall-profile` | 从 Profile 卸载 |

#### 用户技能工坊（写操作，需 `X-User-Id`）

列表读源见上文 NocoBase `hermes_user_skills:list`；以下为 Hermes 写操作：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/user-skills/files` | 文件树；`?skill_slug` |
| GET | `/api/user-skills/file` | 读文件 |
| GET | `/api/user-skills/file/raw` | 原始文件；`?skill_slug`、`?path`、`?inline`、`?download` |
| POST | `/api/user-skills/file/update` | 更新文件 |
| POST | `/api/user-skills/update` | 更新技能元数据 |
| POST | `/api/user-skills/create` | 从 `SKILL.md` 文本创建单文件 Skill |
| POST | `/api/user-skills/import` | 导入 ZIP |
| POST | `/api/user-skills/import/cancel` | 取消导入 |
| POST | `/api/user-skills/install-to-profile` | 安装到 Profile |
| POST | `/api/user-skills/publish-from-profile` | 从 Profile 发布 |
| POST | `/api/user-skills/publish-to-market-review` | 提交市场审核 |
| POST | `/api/user-skills/test-security` | 安全扫描 |
| POST | `/api/user-skills/test-availability` | 启动可用性测试 |
| GET | `/api/user-skills/test-availability/status` | 轮询测试；`?task_id` |

#### 技能模板（Hermes 代理 — 审核写操作）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/skill-templates` | picker 列表；`?page`、`?pageSize`、`?category`、`?keyword` |
| GET | `/api/skill-templates/review-list` | 审核队列（审核员） |
| POST | `/api/skill-templates/approve` | 审核通过 |
| POST | `/api/skill-templates/reject` | 审核拒绝 |

> 技能市场/审核页的**列表读**走 NocoBase 直连（见上文），Hermes `skill-templates` 主要用于 picker 和审核写操作。

---

### 记忆与 Profile 文件

#### 当前激活 Profile

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/memory` | 读取 active profile 的 MEMORY.md + USER.md |
| POST | `/api/memory/write` | 写入；`{ section: "memory"\|"user", content }` |

#### 按 Profile 路径读写

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/profile/memory` | 读 MEMORY.md；`?path=` |
| POST | `/api/profile/memory` | 写 MEMORY.md；`{ path, content }` |
| GET | `/api/profile/user` | 读 USER.md；`?path=` |
| POST | `/api/profile/user` | 写 USER.md；`{ path, content }` |
| GET | `/api/profile/soul` | 读 SOUL.md；`?path=` |
| POST | `/api/profile/soul` | 写 SOUL.md；`{ path, content }`（`updateHermesProfileFile({ type: 'soul' })`） |

`path` 接受 Hermes root 或 `profiles/` 下的目录路径，例如 `/.hermes/profiles/agent-xxx`。

- 读取时文件不存在返回 `content: ""`
- 写入为**整文件覆盖**，非追加
- 只允许 Hermes profile 目录，禁止任意系统路径

示例 — 读取 MEMORY.md：

```http
GET /hermes/api/profile/memory?path=/.hermes/profiles/agent-c59d60cc
```

```json
{
  "path": "/.hermes/profiles/agent-c59d60cc",
  "profile_path": "/home/hermeswebui/.hermes/profiles/agent-c59d60cc",
  "content": "# Memory\n"
}
```

---

### Profile 与智能体

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/profiles` | 列出 profiles + `active`（对话侧栏非主读源） |
| GET | `/api/profile/active` | 当前激活 profile |
| POST | `/api/profile/switch` | 切换；`{ name }`，写 `hermes_profile` cookie |
| POST | `/api/profile/create` | 创建 profile |
| POST | `/api/profile/delete` | 删除 profile |
| GET | `/api/profile/agents` | 智能体列表 |
| POST | `/api/profile/create-agent` | 创建智能体（NocoBase workflow 也会调用） |
| POST | `/api/profile/update-agent` | 更新智能体元数据 |
| GET | `/api/profile/create-agent/skills` | legacy 技能候选；`?q=`（管理页 picker 已改用模板列表） |

`POST /api/profile/create-agent` 请求体：

```json
{
  "profile_name": "market-analyst",
  "description": "一句话描述",
  "prompt": "角色设定",
  "avatar": "",
  "skills": [],
  "is_default": false,
  "clone_from": "company-assistant",
  "clone_config": true
}
```

响应：`{ ok, profile: { name, path }, agent: { profile_id, profile_name, avatar, description, skills, status } }`

---

### 文件与上传

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/upload` | 上传到会话工作区；multipart `session_id` + `file` |
| POST | `/api/upload/extract` | 上传并解压归档 |
| GET | `/api/file` | 读文件；`?session_id`、`?path` |
| GET | `/api/file/raw` | 原始字节/附件预览；`?session_id`、`?path`、`?inline`、`?download` |
| POST | `/api/file/save` | 保存文件 |
| POST | `/api/file/delete` | 删除 |
| GET | `/api/list` | 列目录 |

上传大小限制：前端约定 100MB。

---

### 终端

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/terminal/start` | 启动 PTY；`{ session_id, rows?, cols? }` |
| GET | `/api/terminal/output` | 终端 SSE |
| POST | `/api/terminal/input` | 发送输入 |
| POST | `/api/terminal/resize` | 调整大小 |
| POST | `/api/terminal/close` | 关闭 |

---

### 健康检查与其他

| 方法 | 路径 | 说明 | 公开 |
|------|------|------|------|
| GET | `/health` | 进程健康 | 是 |
| GET | `/api/health/agent` | Agent/gateway 健康 | 否 |
| GET | `/api/system/health` | 系统健康 | 否 |
| GET | `/api/gateway/status` | 消息网关状态 | 否 |
| GET | `/api/insights` | 使用分析；`?days=` | 否 |
| GET | `/api/logs` | 日志 tail | 否 |
| GET | `/api/projects` | 项目列表 | 否 |
| GET | `/api/workspaces` | 工作区列表 | 否 |

#### 审批与澄清（流式交互）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/approval/pending` | 待审批 |
| GET | `/api/approval/stream` | 审批 SSE |
| POST | `/api/approval/respond` | 响应审批 |
| GET | `/api/clarify/pending` | 待澄清 |
| GET | `/api/clarify/stream` | 澄清 SSE |
| POST | `/api/clarify/respond` | 响应澄清 |

#### Kanban（看板）

`/api/kanban/*` 下提供 boards、tasks、events 等 CRUD 与 SSE，详见 `api/kanban_bridge.py`。

---

## Workflow 速查表

| 名称 | Key | 方法 | 用途 |
|------|-----|------|------|
| Hermes登录 | `ugyoa0123ft` | POST | 用户登录 |
| 用户注册 | `3ahenbutb7a` | POST | 用户注册 |
| 创建智能体 | `ne15m97163y` | POST | 创建 Profile + WebUI 智能体 |
| 删除智能体 | `wum2qyn7etu` | POST | 删除 Profile |
| 编辑智能体 | `7hfvjmyroug` | POST | 更新展示名/Soul/描述 |
| 编辑 Skills | `onqtsk997ty` | POST | 更新 Profile 绑定技能 |
| 招入公司 | `yoz7obm18sg` | POST | 从人才市场招募 |

完整路径：`/nocobase/api/webhook:trigger/<key>`

---

## Collection 速查表

| Collection | 常用 Action | 功能域 |
|------------|-------------|--------|
| `hermes_profiles` | list | 用户智能体 |
| `hermes_providers` | list | 全局 AI 渠道 |
| `hermes_skills_templates` | list, get | 技能市场模板 |
| `hermes_user_skills` | list | 用户技能（只读） |
| `hermes_talent_market` | list, get | AI 员工招募 |
| `hermes_virtual_employee_images` | list | 默认头像 |
| `hermes_users` | list, update | 用户 / 通讯录 |
| `hermes_users_contacts` | list, create, update, destroy | 通讯录关系 |
| `hermes_user_emails` | list, create, update | 邮箱账号 |
| `hermes_chat_usage_events` | list | 用量追踪 |
| `attachments` | create | 附件上传 |
| `hermes_user_ai_providers` | list, create, update, destroy | 历史兼容；主链路已切 Hermes Provider 编排 |

---

## 附录：错误与限流

| 状态码 / code | 场景 | 前端提示 |
|---------------|------|----------|
| 401 | 未登录 / `hermes_session` 无效 | 需重新登录 |
| 400 | 参数错误、`user_context_mismatch` | `X-User-Id` 不一致 |
| 403 | CSRF 校验失败 | — |
| 405 | `provider_write_disabled` | 不支持用户自定义上传渠道 |
| 429 | 通用限流（上传、创建会话、启动聊天等） | — |
| 429 + `REQUEST_CONCURRENCY_LIMIT` | 并发限流 | 「当前使用人数较多，请稍后再试」 |
| 503 + `SERVER_MEMORY_PRESSURE` | 服务器内存压力 | 「请求人数超过80，请稍后再试～」 |

---

## 维护约定

1. 新增或调整接口时，同步更新本文档对应章节。
2. 统一使用中文编写说明；示例值脱敏。
3. NocoBase workflow 返回值若有 wrapper，分别记录外层和内层结构。
4. 与 `digital_employee/src/api/agent.md` 保持互补：本文档偏接口目录，agent.md 偏前端接线细节。
