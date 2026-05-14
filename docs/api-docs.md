# API 接口文档说明

该文档用于后续保存前端对接 Hermes 后端时需要的接口配置、接口说明、请求/响应约定、鉴权方式和联调备注，方便前后端统一维护对接信息。

当前仅保留文档功能介绍；具体接口内容待后续需要时再更新。

后续更新约定：新增或调整本文档内容时，统一使用中文编写说明、备注和注释；不在本文档中写入密码、登录 token、会话 cookie、供应商 API key 等明文敏感信息。

## 接口记录

### NocoBase：Skills 列表展示

- 接口地址：`http://localhost:5173/nocobase/api/hermes_skills_templates:list?paginate=false`
- 请求方式：`GET`
- 主要作用：用于前端展示 NocoBase 中维护的 Skills 模板列表，例如 Skills 列表页、创建任务或编辑智能体 Skills 时的候选技能列表。
- 返回数据：返回 `hermes_skills_templates` 表中的 Skills 模板记录列表。
- 数据流说明：前端调用该接口读取 Skills 模板记录，用于列表展示、分类分组和本地搜索过滤；该接口只返回 NocoBase 表数据，不返回 WebUI `SKILL.md` 完整内容，也不执行、创建、修改或删除 Skill。

请求参数如下：

| 字段 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `paginate` | query | `boolean` | 否 | 固定传 `false`，表示不分页，直接返回 Skills 模板记录数组。 |

示例请求：

```http
GET /nocobase/api/hermes_skills_templates:list?paginate=false
```

示例返回结构如下，字段值仅作结构示例：

```json
{
  "data": [
    {
      "id": 1,
      "name": "doc-summary",
      "description": "Summarize files and documents",
      "category": "productivity",
      "createdAt": "2026-05-13T00:00:00.000Z",
      "updatedAt": "2026-05-13T00:00:00.000Z"
    },
    {
      "id": 2,
      "name": "web-search",
      "description": "Search webpages and summarize sources",
      "category": "research",
      "createdAt": "2026-05-13T00:00:00.000Z",
      "updatedAt": "2026-05-13T00:00:00.000Z"
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `data` | `array<object>` | NocoBase 返回的 Skills 模板记录列表。 |
| `data[].id` | `number` | NocoBase 记录 ID。 |
| `data[].name` | `string` | Skill 模板名称或标识，用于列表展示和搜索匹配。 |
| `data[].description` | `string` | Skill 模板描述，用于列表副标题、搜索匹配或详情预览入口。 |
| `data[].category` | `string` | Skill 模板分类。前端可按该字段分组展示；为空时可归入通用分类。 |
| `data[].createdAt` | `string` | NocoBase 记录创建时间。 |
| `data[].updatedAt` | `string` | NocoBase 记录更新时间。 |

联调备注：

- 该接口替代 WebUI `GET /api/skills` 作为前端 Skills 列表展示数据源。
- `api/routes.py` 中旧 WebUI `GET /api/skills` 只返回 `{ "skills": [...] }`；替换为 NocoBase list 接口后，前端应从 `data` 数组读取列表。
- 如 NocoBase 表字段后续调整，以接口实际返回字段为准，并同步更新本文档字段说明。
- 若该 NocoBase 接口后续需要鉴权、请求头或请求参数，补充到本文档时只记录字段名和用途，不写入真实密钥、token 或 cookie。

### NocoBase Webhook：创建用户绑定智能体 Profile

- 接口地址：`https://www.foxuai.com/api/webhook:trigger/ne15m97163y`
- 请求方式：待联调确认；前端按 JSON 请求体传入创建所需字段。
- 主要作用：前端调用该接口后，NocoBase 保持当前 webhook 地址不变，按下方字段调用 WebUI 的 `POST /api/profile/create-agent`，在 Profile 目录中创建一个新智能体，然后同步更新 `Hermes-Profile` 表，新增 Profile 数据并绑定到当前用户。
- 返回数据：前端根据 NocoBase 返回结果判断创建是否成功，具体返回结构待联调确认。
- 数据流说明：前端传入智能体基础展示信息、角色设定 Prompt 和是否默认；NocoBase 触发 WebUI 创建流程，WebUI 创建时默认从 `company-assistant` 克隆 Profile 配置，并将创建成功后的 Profile / 智能体数据写入 `Hermes-Profile` 表。当前用户绑定关系由 NocoBase 根据登录态或流程上下文处理，不要求前端在请求体中传 `user_id`，也不再传入 `skills` 字段。

请求体结构如下，字段值仅作结构示例：

```json
{
  "profile_name": "market-analyst",
  "display_name": "市场分析助手",
  "avatar": "/uploads/market.png",
  "description": "用简短的话描述智能体的核心能力或用途",
  "prompt": "你是一位专业的市场分析助手，擅长行业洞察、竞品研究与趋势分析，能够基于数据和事实输出结构化的分析与建议。",
  "is_default": false,
  "clone_from": "company-assistant",
  "clone_config": true
}
```

请求字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `profile_name` | `string` | 智能体 / Profile 名称字段，对应 WebUI 创建接口的 `profile_name` 字段，最长 50 个字符。 |
| `display_name` | `string` | 新增入参，智能体前端展示名称；NocoBase 调用 WebUI `POST /api/profile/create-agent` 时可映射到 WebUI 的 `profile_name` 展示字段，最长 50 个字符。 |
| `avatar` | `string` | 可选。智能体头像地址或前端上传后得到的头像标识；也可使用 `avatar_url` / `icon` 传入，未传时 WebUI 创建接口保存为空字符串。 |
| `description` | `string` | 一句话描述，对应 WebUI 创建接口的 `description` / `summary` / `one_liner` 字段，最长 80 个字符。 |
| `prompt` | `string` | 角色设定 Prompt，对应 WebUI 创建接口的 `prompt` / `system_prompt` 字段，最长 1000 个字符。 |
| `is_default` | `boolean` | 是否将新建 Profile 设置为默认 Profile，具体生效规则待联调确认。 |
| `clone_from` | `string` | WebUI 创建 Profile 时克隆配置的来源 Profile；当前默认使用 `company-assistant`。 |
| `clone_config` | `boolean` | 是否克隆来源 Profile 的配置文件；当前默认使用 `true`。 |

示例返回结构如下，字段值仅作结构示例，实际字段以后端联调结果为准：

```json
{
  "ok": true,
  "status": "success",
  "message": "创建成功",
  "data": {
    "user_id": "<user_id>",
    "profile": {
      "name": "market-analyst",
      "path": "/home/hermeswebui/.hermes/profiles/market-analyst"
    },
    "agent": {
      "profile_id": "market-analyst",
      "profile_name": "市场分析助手",
      "avatar": "/uploads/market.png",
      "description": "用简短的话描述智能体的核心能力或用途",
      "skills": [],
      "status": "active",
      "is_default": false
    }
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | `boolean` | 表示本次创建请求是否处理成功。 |
| `status` | `string` | 创建处理状态，例如 `success` 或 `failed`，具体枚举待联调确认。 |
| `message` | `string` | 面向前端展示或调试的状态说明。 |
| `data` | `object 或 null` | 创建成功后返回的相关数据，具体结构待联调确认。 |
| `data.user_id` | `string` | 当前用户标识，字段名称和是否返回待联调确认。 |
| `data.profile` | `object` | WebUI 创建出的 Profile 基础信息。 |
| `data.profile.name` | `string` | 新建 Profile 标识，通常与 `profile_id` 一致。 |
| `data.profile.path` | `string` | 新建 Profile 在 WebUI 服务器上的本地目录路径。 |
| `data.agent` | `object` | WebUI 创建接口返回的智能体展示与配置元数据。 |
| `data.agent.profile_id` | `string` | 智能体对应的 Profile 标识。 |
| `data.agent.profile_name` | `string` | 智能体展示名称。 |
| `data.agent.avatar` | `string` | 智能体头像；创建请求未传头像时为空字符串。 |
| `data.agent.description` | `string` | 智能体一句话描述。 |
| `data.agent.skills` | `array<string>` | 创建接口未传入 Skills 时返回空数组；后续如需增删 Skills，使用编辑用户绑定智能体 Profile Skills 接口。 |
| `data.agent.status` | `string` | 智能体状态。 |
| `data.agent.is_default` | `boolean` | 是否为默认 Profile，字段名称和是否返回待联调确认。 |

联调备注：

- 创建接口不再要求前端传入模型接口配置字段；WebUI 当前默认使用 `clone_from: "company-assistant"` 和 `clone_config: true` 克隆配置。若后续需要扩展 `base_url` 或 `api_key`，应按 WebUI 创建接口的可选字段单独补充，并避免在日志、错误提示或文档中输出真实密钥。
- 创建接口请求体按截图字段记录为 `profile_name`、`display_name`、`avatar`、`description`、`prompt`、`is_default`；不再要求前端传 `user_id`、`profile_id`、`name`、`status`、`draft` 或 `skills`。
- 创建时不再提交 `skills`，新建智能体元数据中的 `skills` 默认为空数组；后续如需“已创建后增删 Skills”，使用下方“编辑用户绑定智能体 Profile Skills”接口。
- 创建流程需要同时确认 WebUI Profile 目录中的智能体创建结果，以及 NocoBase `Hermes-Profile` 表中 Profile 数据和用户绑定关系的新增结果。
- 若 WebUI 创建成功但 NocoBase 写表失败，建议后端返回可区分的错误状态，方便前端提示、重试或触发补偿清理。

### NocoBase Webhook：编辑智能体

- 接口地址：`https://www.foxuai.com/api/webhook:trigger/7hfvjmyroug`
- 请求方式：`POST`
- 主要作用：用于编辑用户绑定的智能体基础信息和角色设定内容。
- 请求 Header：需要在 headers 中传入 `x-user-id`，用于标识当前用户。
- 返回数据：前端根据 NocoBase 返回结果判断编辑是否成功，具体返回结构以 NocoBase 联调结果为准。

请求体结构如下，字段值仅作结构示例：

```json
{
  "display_name": "市场分析助手",
  "soul": "你是一位专业的市场分析助手，擅长行业洞察、竞品研究与趋势分析。",
  "description": "用于行业洞察、竞品研究与趋势分析",
  "profile_name": "market-analyst"
}
```

请求 Header 说明：

| Header | 类型 | 必填 | 说明 |
|---|---|---|---|
| `x-user-id` | `string` | 是 | 当前用户标识；NocoBase 根据该用户标识定位用户绑定的智能体 / Profile 数据。 |

请求字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `display_name` | `string` | 是 | 智能体展示名称。 |
| `soul` | `string` | 是 | 智能体角色设定内容，对应 Profile 中的 Soul / Prompt 内容。 |
| `description` | `string` | 是 | 智能体一句话描述。 |
| `profile_name` | `string` | 是 | 要编辑的智能体 / Profile 标识或名称。 |

联调备注：

- 前端调用该 NocoBase webhook 时，`x-user-id` 放在请求 headers 中，不放入 JSON 请求体。
- 该接口用于编辑智能体基础信息和 Soul 内容；若只编辑挂载 Skills，使用下方“编辑用户绑定智能体 Profile Skills”接口。

### NocoBase Webhook：编辑用户绑定智能体 Profile Skills

- 接口地址：`https://www.foxuai.com/api/webhook:trigger/onqtsk997ty`
- 请求方式：待联调确认；前端按 JSON 请求体传入 `skills` 和 `profile_id`。
- 主要作用：用于编辑已创建 Profile 中智能体挂载的 Skills。
- 返回数据：前端根据 NocoBase 返回结果判断编辑是否成功，具体返回结构待联调确认。
- 数据流说明：前端只传入目标 Profile 标识和新的 Skills 列表；NocoBase 根据 `profile_id` 定位用户绑定的 Profile，并更新该 Profile 的 Skills 配置。若底层 WebUI 更新接口需要完整智能体字段，由 NocoBase 根据现有 Profile 数据补齐，前端不需要传入名称、头像、描述或 Prompt。

请求体结构如下，字段值仅作结构示例：

```json
{
  "skills": ["id1","id2","id3","id4"],
  "profile_id": "market-analyst"
}
```

请求字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `skills` | `array<string>` | 是 | 更新后的 Skill ID 数组。前端提交 Skill ID，不能提交中文展示名或 Skill 名称。 |
| `profile_id` | `string` | 是 | 要编辑的 Profile 标识，用于定位当前用户绑定的目标智能体 Profile。 |

示例返回结构如下，字段值仅作结构示例，实际字段以后端联调结果为准：

```json
{
  "ok": true,
  "status": "success",
  "message": "编辑成功",
  "data": {
    "profile": {
      "name": "market-analyst",
      "path": "/home/hermeswebui/.hermes/profiles/market-analyst"
    },
    "agent": {
      "profile_id": "market-analyst",
      "profile_name": "市场分析助手",
      "avatar": "/uploads/market.png",
      "description": "用简短的话描述智能体的核心能力或用途",
      "skills": ["skill_web_search", "skill_doc_summary", "skill_table_analysis"],
      "status": "active"
    }
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | `boolean` | 表示本次编辑请求是否处理成功。 |
| `status` | `string` | 编辑处理状态，例如 `success` 或 `failed`，具体枚举待联调确认。 |
| `message` | `string` | 面向前端展示或调试的状态说明。 |
| `data` | `object 或 null` | 编辑成功后返回的相关数据，具体结构待联调确认。 |
| `data.profile` | `object` | 被编辑的 Profile 基础信息。 |
| `data.profile.name` | `string` | 被编辑的 Profile 标识。 |
| `data.profile.path` | `string` | 被编辑 Profile 在 WebUI 服务器上的本地目录路径。 |
| `data.agent` | `object` | 更新后的智能体展示与配置元数据。 |
| `data.agent.profile_id` | `string` | 智能体对应的 Profile 标识。 |
| `data.agent.profile_name` | `string` | 智能体展示名称。 |
| `data.agent.avatar` | `string` | 智能体头像。 |
| `data.agent.description` | `string` | 智能体一句话描述。 |
| `data.agent.skills` | `array<string>` | 更新后写入当前智能体元数据中的 Skill ID 数组。 |
| `data.agent.status` | `string` | 智能体状态。 |

联调备注：

- 该接口用于编辑已创建 Profile 的 Skills；不是 Skills 候选列表接口，也不是创建新智能体接口。
- 前端提交 `skills` 时应提交 Skill ID 数组，不要提交中文展示名或 Skill 名称。
- 该 NocoBase webhook 的前端入参只有 `skills` 和 `profile_id`；不要额外传 `avatar`、`name`、`description` 或 `prompt`。
- 若底层 WebUI 更新接口需要名称、描述或 Prompt 等完整字段，由 NocoBase 从已有 Profile 数据中读取并补齐。
- 若传入未知 Skill ID，WebUI 会返回错误，例如 `Unknown skill(s): <skill-id>`，并不会写入 Profile 文件。
- 若该 NocoBase webhook 后续需要鉴权、请求头或请求参数，补充到本文档时只记录字段名和用途，不写入真实密钥、token 或 cookie。

### NocoBase Webhook：删除用户绑定智能体

- 接口地址：`https://www.foxuai.com/api/webhook:trigger/wum2qyn7etu`
- 请求方式：待联调确认；前端按 JSON 请求体传入删除所需字段。
- 主要作用：前端调用该接口后，通过 WebUI 接口删除 Profile 中对应的智能体，并删除 NocoBase `Hermes-Profile` 表里当前用户绑定的智能体记录。
- 返回数据：前端根据 NocoBase 返回结果判断删除是否成功，具体返回结构待联调确认。
- 数据流说明：前端传入用户标识和智能体名称，NocoBase 触发删除流程，同步清理 WebUI Profile 中的智能体以及 `Hermes-Profile` 表中的绑定关系。

请求体结构如下，字段值仅作结构示例：

```json
{
  "name": "",
  "user_id": ""
}
```

请求字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | `string` | 需要删除的智能体名称，对应 Profile 中的智能体标识。 |
| `user_id` | `string` | 当前用户标识，用于定位 `Hermes-Profile` 表中该用户绑定的智能体记录。 |

示例返回结构如下，字段值仅作结构示例，实际字段以后端联调结果为准：

```json
{
  "ok": true,
  "status": "success",
  "message": "删除成功",
  "data": {
    "name": "<agent_name>",
    "user_id": "<user_id>"
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | `boolean` | 表示本次删除请求是否处理成功。 |
| `status` | `string` | 删除处理状态，例如 `success` 或 `failed`，具体枚举待联调确认。 |
| `message` | `string` | 面向前端展示或调试的状态说明。 |
| `data` | `object 或 null` | 删除成功后返回的相关数据，具体结构待联调确认。 |
| `data.name` | `string` | 已删除的智能体名称，字段名称和是否返回待联调确认。 |
| `data.user_id` | `string` | 当前用户标识，字段名称和是否返回待联调确认。 |

联调备注：

- 前端调用时只传递 `name` 和 `user_id`，不要在请求体中写入登录 token、会话 cookie 或其他敏感信息。
- 删除流程需要同时确认 WebUI Profile 中的智能体删除结果，以及 NocoBase `Hermes-Profile` 表中用户绑定关系的删除结果。
- 若 WebUI 删除成功但 NocoBase 绑定关系删除失败，或反向失败，建议后端返回可区分的错误状态，方便前端提示和重试。

### NocoBase Webhook：显示用户绑定的 Profiles

- 接口地址：`https://www.foxuai.com/api/webhook:trigger/cz0i1c3gjn8`
- 请求方式：待联调确认。
- 主要作用：用于展示当前用户所绑定的 Profiles。
- 返回数据：与 [api/routes.py](/Users/cxg/Desktop/Hermes/api/routes.py) 中 `GET /api/profiles` 接口返回数据保持一致。
- 数据来源说明：`GET /api/profiles` 返回 `profiles` 列表和当前激活的 `active` profile 名称。

示例返回结构如下，字段值仅作结构示例：

```json
{
  "profiles": [
    {
      "name": "default",
      "path": "/home/user/.hermes",
      "is_default": true,
      "is_active": true,
      "gateway_running": false,
      "model": "gpt-5.4",
      "provider": "openai",
      "has_env": true,
      "skill_count": 12,
      "avatar": "/uploads/agent.png"
    }
  ],
  "active": "default"
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `profiles` | `array` | 当前用户可展示的 Profiles 列表。 |
| `profiles[].name` | `string` | Profile 名称。 |
| `profiles[].path` | `string` | Profile 对应的本地路径。 |
| `profiles[].is_default` | `boolean` | 是否为默认 Profile。 |
| `profiles[].is_active` | `boolean` | 是否为当前激活 Profile。 |
| `profiles[].gateway_running` | `boolean` | 当前 Profile 的 gateway 是否运行中。 |
| `profiles[].model` | `string 或 null` | 当前 Profile 配置的模型名称。 |
| `profiles[].provider` | `string 或 null` | 当前 Profile 配置的模型供应商。 |
| `profiles[].has_env` | `boolean` | 当前 Profile 是否存在环境配置文件。 |
| `profiles[].skill_count` | `number` | 当前 Profile 下可用技能数量。 |
| `profiles[].avatar` | `string` | 当前 Profile / 智能体头像地址或上传资源路径。 |
| `active` | `string` | 当前激活的 Profile 名称。 |

联调备注：

- 前端展示时以 `profiles` 数组作为列表数据源，以 `active` 标识当前激活项。
- 若该 NocoBase webhook 后续需要鉴权、请求头或请求参数，补充到本文档时只记录字段名和用途，不写入真实密钥、token 或 cookie。

### WebUI：按 Profile 路径读取 MEMORY.md

- 接口地址：`http://172.234.237.195:8787/api/profile/memory`
- 请求方式：`GET`
- 主要作用：按传入的 Profile 本地路径读取该 Profile 下 `memories/MEMORY.md` 的完整内容。

请求参数如下：

| 字段 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `path` | query | `string` | 是 | Profile 本地目录路径，例如 `/.hermes/profiles/agent-c59d60cc` 或 `/home/hermeswebui/.hermes/profiles/agent-c59d60cc`。 |

示例请求：

```http
GET /api/profile/memory?path=/.hermes/profiles/agent-c59d60cc
```

示例返回：

```json
{
  "path": "/.hermes/profiles/agent-c59d60cc",
  "profile_path": "/home/hermeswebui/.hermes/profiles/agent-c59d60cc",
  "content": "# Memory\n"
}
```

### WebUI：按 Profile 路径修改 MEMORY.md

- 接口地址：`http://172.234.237.195:8787/api/profile/memory`
- 请求方式：`POST`
- 主要作用：按传入的 Profile 本地路径覆盖写入该 Profile 下 `memories/MEMORY.md` 的完整内容。

请求体如下：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `path` | `string` | 是 | Profile 本地目录路径，例如 `/.hermes/profiles/agent-c59d60cc` 或 `/home/hermeswebui/.hermes/profiles/agent-c59d60cc`。 |
| `content` | `string` | 是 | 要写入 `MEMORY.md` 的完整内容，允许为空字符串。 |

示例请求：

```json
{
  "path": "/.hermes/profiles/agent-c59d60cc",
  "content": "新的内容"
}
```

示例返回：

```json
{
  "ok": true,
  "path": "/.hermes/profiles/agent-c59d60cc",
  "profile_path": "/home/hermeswebui/.hermes/profiles/agent-c59d60cc",
  "memory_path": "/home/hermeswebui/.hermes/profiles/agent-c59d60cc/memories/MEMORY.md",
  "content": "新的内容",
  "mtime": 1778750000.0,
  "bytes": 12
}
```

联调备注：

- 这两个接口只接受 Hermes root profile 或 `profiles/` 下的 profile 目录路径，不允许用任意系统路径读写文件。
- 如果 `MEMORY.md` 不存在，读取接口返回 `content: ""`；写入接口会自动创建 `memories` 目录和 `MEMORY.md`。
- 修改接口是整文件覆盖，不是追加或局部 patch；前端保存前应提交完整内容。
- 公网 WebUI 未登录访问时会返回鉴权错误；浏览器跨域 POST 仍受现有 CSRF 规则限制。

### WebUI：按 Profile 路径读取 USER.md

- 接口地址：`http://172.234.237.195:8787/api/profile/user`
- 请求方式：`GET`
- 主要作用：按传入的 Profile 本地路径读取该 Profile 下 `memories/USER.md` 的完整内容。

请求参数如下：

| 字段 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `path` | query | `string` | 是 | Profile 本地目录路径，例如 `/.hermes/profiles/agent-c59d60cc` 或 `/home/hermeswebui/.hermes/profiles/agent-c59d60cc`。 |

示例请求：

```http
GET /api/profile/user?path=/.hermes/profiles/agent-c59d60cc
```

示例返回：

```json
{
  "path": "/.hermes/profiles/agent-c59d60cc",
  "profile_path": "/home/hermeswebui/.hermes/profiles/agent-c59d60cc",
  "content": "# User\n"
}
```

### WebUI：按 Profile 路径修改 USER.md

- 接口地址：`http://172.234.237.195:8787/api/profile/user`
- 请求方式：`POST`
- 主要作用：按传入的 Profile 本地路径覆盖写入该 Profile 下 `memories/USER.md` 的完整内容。

请求体如下：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `path` | `string` | 是 | Profile 本地目录路径，例如 `/.hermes/profiles/agent-c59d60cc` 或 `/home/hermeswebui/.hermes/profiles/agent-c59d60cc`。 |
| `content` | `string` | 是 | 要写入 `USER.md` 的完整内容，允许为空字符串。 |

示例请求：

```json
{
  "path": "/.hermes/profiles/agent-c59d60cc",
  "content": "新的用户信息"
}
```

示例返回：

```json
{
  "ok": true,
  "path": "/.hermes/profiles/agent-c59d60cc",
  "profile_path": "/home/hermeswebui/.hermes/profiles/agent-c59d60cc",
  "user_path": "/home/hermeswebui/.hermes/profiles/agent-c59d60cc/memories/USER.md",
  "content": "新的用户信息",
  "mtime": 1778750000.0,
  "bytes": 18
}
```

联调备注：

- `USER.md` 和 `MEMORY.md` 实际位于同一个 `memories` 目录下，但使用独立接口，避免前端传文件名。
- 如果 `USER.md` 不存在，读取接口返回 `content: ""`；写入接口会自动创建 `memories` 目录和 `USER.md`。
- 修改接口是整文件覆盖，不是追加或局部 patch；前端保存前应提交完整内容。

### NocoBase Webhook：用户注册

- 接口地址：`https://www.foxuai.com/api/webhook:trigger/3ahenbutb7a`
- 请求方式：待联调确认；前端按 JSON 请求体传入用户名、邮箱和密码。
- 主要作用：用户注册接口。前端提交 `name`、`email` 和 `password`，NocoBase 负责进行用户数据存储。
- 返回数据：前端根据 NocoBase 返回结果判断注册是否成功，具体返回结构待联调确认。
- 数据流说明：前端发起用户注册请求，NocoBase 接收并保存用户数据，然后向前端返回处理状态。

请求体结构如下，字段值仅作结构示例：

```json
{
  "name": "",
  "email": "",
  "password": "<password>"
}
```

请求字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | `string` | 用户注册名称或账号展示名称。 |
| `email` | `string` | 用户注册邮箱，用于登录和识别用户。 |
| `password` | `string` | 用户注册密码，属于敏感字段，文档中只记录占位符。 |

示例返回结构如下，字段值仅作结构示例，实际字段以后端联调结果为准：

```json
{
  "ok": true,
  "status": "success",
  "message": "注册成功",
  "data": {
    "user_id": "<user_id>"
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | `boolean` | 表示本次注册请求是否处理成功。 |
| `status` | `string` | 注册处理状态，例如 `success` 或 `failed`，具体枚举待联调确认。 |
| `message` | `string` | 面向前端展示或调试的状态说明。 |
| `data` | `object 或 null` | 注册成功后返回的用户相关数据，具体结构待联调确认。 |
| `data.user_id` | `string` | 用户数据存储后的用户标识，字段名称和是否返回待联调确认。 |

联调备注：

- 前端提交注册信息时，只传递完成注册所需的业务字段。
- 当前注册请求入参为 `name`、`email` 和 `password`，不要在日志、错误提示或文档中输出真实密码。
- NocoBase 负责保存用户数据，前端以接口返回状态更新注册结果展示。
- 若后续需要补充手机号、第三方用户 ID 等请求字段，只记录字段名、类型和用途，不在本文档中写入真实密码、token 或用户隐私数据。

### NocoBase Webhook：用户登录

- 接口地址：`https://www.foxuai.com/api/webhook:trigger/ugyoa0123ft`
- 请求方式：待联调确认；前端按 JSON 请求体传入邮箱和密码。
- 主要作用：用户登录接口。前端提交 `email` 和 `password`，NocoBase 处理登录校验并向前端返回登录状态。
- 返回数据：前端根据 NocoBase 返回结果判断登录是否成功，具体返回结构待联调确认。
- 数据流说明：前端发起用户登录请求，NocoBase 校验用户数据后返回处理状态，前端据此更新登录结果展示。

请求体结构如下，字段值仅作结构示例：

```json
{
  "email": "user@example.com",
  "password": "<password>"
}
```

请求字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `email` | `string` | 用户登录邮箱，用于定位登录用户。 |
| `password` | `string` | 用户登录密码，属于敏感字段，文档中只记录占位符。 |

示例返回结构如下，字段值仅作结构示例，实际字段以后端联调结果为准：

```json
{
  "ok": true,
  "status": "success",
  "message": "登录成功",
  "data": {
    "user_id": "<user_id>"
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `ok` | `boolean` | 表示本次登录请求是否处理成功。 |
| `status` | `string` | 登录处理状态，例如 `success` 或 `failed`，具体枚举待联调确认。 |
| `message` | `string` | 面向前端展示或调试的状态说明。 |
| `data` | `object 或 null` | 登录成功后返回的用户相关数据，具体结构待联调确认。 |
| `data.user_id` | `string` | 登录用户标识，字段名称和是否返回待联调确认。 |

联调备注：

- 前端提交登录信息时，只传递完成登录校验所需的业务字段。
- 当前登录请求入参为 `email` 和 `password`，不要在日志、错误提示或文档中输出真实密码。
- 前端以接口返回状态更新登录成功、登录失败或异常提示。
- 若后续需要补充账号、手机号、验证码、登录 token 等请求或响应字段，只记录字段名、类型和用途，不在本文档中写入真实密码、验证码、token 或用户隐私数据。
