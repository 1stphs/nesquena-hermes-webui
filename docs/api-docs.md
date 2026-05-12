# API 接口文档说明

该文档用于后续保存前端对接 Hermes 后端时需要的接口配置、接口说明、请求/响应约定、鉴权方式和联调备注，方便前后端统一维护对接信息。

当前仅保留文档功能介绍；具体接口内容待后续需要时再更新。

后续更新约定：新增或调整本文档内容时，统一使用中文编写说明、备注和注释；不在本文档中写入密码、登录 token、会话 cookie、供应商 API key 等明文敏感信息。

## 接口记录

### WebUI：创建智能体 Skills 推荐与搜索

- 接口地址：`http://172.234.237.195:8787/api/profile/create-agent/skills`
- 请求方式：`GET`
- 主要作用：用于创建智能体表单中的“挂载 Skills”下拉区域。前端打开下拉时可不传参数获取常用推荐和可用 Skills；搜索框输入关键字时通过 `q` 参数过滤 Skills 库。
- 返回数据：返回当前查询命中的 Skills 列表、常用推荐列表和命中数量。
- 数据流说明：前端调用该接口读取 WebUI 当前可用的 Skills 元数据，并在用户点击添加后把返回的真实 `name` 保存到创建表单的 `skills` 数组中；该接口只负责推荐和搜索，不创建智能体，也不更新已创建智能体。

请求参数如下：

| 字段 | 位置 | 类型 | 必填 | 说明 |
|---|---|---|---|---|
| `q` | query | `string` | 否 | 搜索关键字。为空或不传时返回全部可用 Skills，同时返回常用推荐。搜索范围包含 skill 的 `name`、`description` 和 `category`。 |

示例请求：

```http
GET /api/profile/create-agent/skills
GET /api/profile/create-agent/skills?q=doc
```

示例返回结构如下，字段值仅作结构示例：

```json
{
  "query": "doc",
  "skills": [
    {
      "name": "doc-summary",
      "description": "Summarize files and documents",
      "category": "productivity"
    }
  ],
  "recommended": [
    {
      "name": "web-search",
      "description": "Search webpages and summarize sources",
      "category": "research"
    }
  ],
  "count": 1
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `query` | `string` | 本次请求使用的搜索关键字；未传 `q` 时为空字符串。 |
| `skills` | `array<object>` | 与 `q` 匹配的 Skills 列表；未传 `q` 时为全部可用 Skills。 |
| `skills[].name` | `string` | Skill 的真实名称。创建智能体时 `skills` 数组必须提交该值，不要提交中文展示名。 |
| `skills[].description` | `string` | Skill 描述。 |
| `skills[].category` | `string` | Skill 分类。 |
| `recommended` | `array<object>` | 常用推荐 Skills。前端可用于下拉面板中的“常用推荐”区域。 |
| `count` | `number` | `skills` 列表的数量。 |

联调备注：

- 该接口可以由前端直接调用，用于红框中的 Skills 下拉推荐和搜索。
- 点击加号时前端只需更新当前表单状态，不需要再次调用该接口。
- 最终创建智能体时，把已选择的 `skills[].name` 组成数组传给创建接口，例如 `["web-search", "doc-summary"]`。
- 若前端展示中文标签，需要在前端维护中文展示名与真实 `name` 的映射，提交时只提交真实 `name`。

### NocoBase Webhook：创建用户绑定智能体 Profile

- 接口地址：`https://www.foxuai.com/api/webhook:trigger/ne15m97163y`
- 请求方式：待联调确认；前端按 JSON 请求体传入创建所需字段。
- 主要作用：前端调用该接口后，NocoBase 保持当前 webhook 地址不变，按下方字段调用 WebUI 的 `POST /api/profile/create-agent`，在 Profile 目录中创建一个新智能体，然后同步更新 `Hermes-Profile` 表，新增 Profile 数据并绑定到当前用户。
- 返回数据：前端根据 NocoBase 返回结果判断创建是否成功，具体返回结构待联调确认。
- 数据流说明：前端传入当前用户标识、智能体基础展示信息、角色设定 Prompt 和挂载 Skills；NocoBase 触发 WebUI 创建流程，并将创建成功后的 Profile / 智能体数据写入 `Hermes-Profile` 表。

请求体结构如下，字段值仅作结构示例：

```json
{
  "user_id": "",
  "profile_id": "market-analyst",
  "avatar": "/uploads/market.png",
  "name": "市场分析助手",
  "description": "用简短的话描述智能体的核心能力或用途",
  "prompt": "你是一位专业的市场分析助手，擅长行业洞察、竞品研究与趋势分析，能够基于数据和事实输出结构化的分析与建议。",
  "skills": ["web-search", "doc-summary", "table-analysis", "meeting-notes"],
  "status": "active"
}
```

请求字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `user_id` | `string` | 当前用户标识，用于将新建 Profile 绑定到当前用户。 |
| `profile_id` | `string` | 可选。智能体对应的 Profile 标识；建议使用小写字母、数字、连字符或下划线，例如 `market-analyst`。不传时由 WebUI 根据名称生成。 |
| `avatar` | `string` | 智能体头像地址或前端上传后得到的头像标识。 |
| `name` | `string` | 智能体名称，对应 WebUI 创建接口的 `name` / `profile_name` / `display_name` 字段，最长 50 个字符。 |
| `description` | `string` | 一句话描述，对应 WebUI 创建接口的 `description` / `summary` / `one_liner` 字段，最长 80 个字符。 |
| `prompt` | `string` | 角色设定 Prompt，对应 WebUI 创建接口的 `prompt` / `system_prompt` 字段，最长 1000 个字符。 |
| `skills` | `array<string>` | 挂载的 Skills 名称列表。NocoBase 调用 WebUI 创建接口时应透传该字段；WebUI 会校验这些 skill 是否存在于 skills 库，并写入新建智能体的 `agent.skills`。 |
| `status` | `string` | 可选。智能体状态，取值为 `active` 或 `draft`，默认 `active`。 |
| `draft` | `boolean` | 可选。若传 `true`，等价于 `status: "draft"`。 |

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
      "skills": ["web-search", "doc-summary", "table-analysis", "meeting-notes"],
      "status": "active"
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
| `data.agent.avatar` | `string` | 智能体头像。 |
| `data.agent.description` | `string` | 智能体一句话描述。 |
| `data.agent.skills` | `array<string>` | 已写入当前创建智能体元数据中的 Skills 名称列表。 |
| `data.agent.status` | `string` | 智能体状态。 |

联调备注：

- 创建接口不再要求前端传入模型接口配置字段；若后续需要扩展 `base_url`、`api_key`、`clone_from` 或 `clone_config`，应按 WebUI 创建接口的可选字段单独补充，并避免在日志、错误提示或文档中输出真实密钥。
- 挂载 Skills 的候选列表由 WebUI `GET /api/profile/create-agent/skills?q=<关键字>` 提供；该查询接口只用于推荐和搜索，不创建智能体。
- 创建时提交的 `skills` 会写入当前新建智能体的 `webui/agent.json` 和 `profiles/default.md` 元数据中；后续如需“已创建后增删 Skills”，需要单独补充更新接口。
- 创建流程需要同时确认 WebUI Profile 目录中的智能体创建结果，以及 NocoBase `Hermes-Profile` 表中 Profile 数据和用户绑定关系的新增结果。
- 若 WebUI 创建成功但 NocoBase 写表失败，建议后端返回可区分的错误状态，方便前端提示、重试或触发补偿清理。

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

### NocoBase Webhook：当前用户绑定的 Profiles

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
      "path": "/home/hermeswebui/.hermes",
      "is_default": true,
      "is_active": true,
      "gateway_running": false,
      "model": null,
      "provider": null,
      "has_env": true,
      "skill_count": 0
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
| `active` | `string` | 当前激活的 Profile 名称。 |

联调备注：

- 前端展示时以 `profiles` 数组作为列表数据源，以 `active` 标识当前激活项。
- 若该 NocoBase webhook 后续需要鉴权、请求头或请求参数，补充到本文档时只记录字段名和用途，不写入真实密钥、token 或 cookie。

### NocoBase Webhook：用户注册

- 接口地址：`https://www.foxuai.com/api/webhook:trigger/3ahenbutb7a`
- 请求方式：待联调确认。
- 主要作用：用户注册接口。前端提交注册状态和注册相关信息，NocoBase 负责进行用户数据存储。
- 返回数据：前端根据 NocoBase 返回结果判断注册是否成功，具体返回结构待联调确认。
- 数据流说明：前端发起用户注册请求，NocoBase 接收并保存用户数据，然后向前端返回处理状态。

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
- NocoBase 负责保存用户数据，前端以接口返回状态更新注册结果展示。
- 若后续需要补充手机号、邮箱、用户名、第三方用户 ID 等请求字段，只记录字段名、类型和用途，不在本文档中写入真实用户隐私数据。

### NocoBase Webhook：用户登录

- 接口地址：`https://www.foxuai.com/api/webhook:trigger/ugyoa0123ft`
- 请求方式：待联调确认。
- 主要作用：用户登录接口。前端提交登录相关信息，NocoBase 处理登录校验并向前端返回登录状态。
- 返回数据：前端根据 NocoBase 返回结果判断登录是否成功，具体返回结构待联调确认。
- 数据流说明：前端发起用户登录请求，NocoBase 校验用户数据后返回处理状态，前端据此更新登录结果展示。

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
- 前端以接口返回状态更新登录成功、登录失败或异常提示。
- 若后续需要补充账号、手机号、邮箱、密码、验证码、登录 token 等请求或响应字段，只记录字段名、类型和用途，不在本文档中写入真实密码、验证码、token 或用户隐私数据。
