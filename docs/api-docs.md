# API 接口文档说明

该文档用于后续保存前端对接 Hermes 后端时需要的接口配置、接口说明、请求/响应约定、鉴权方式和联调备注，方便前后端统一维护对接信息。

当前仅保留文档功能介绍；具体接口内容待后续需要时再更新。

后续更新约定：新增或调整本文档内容时，统一使用中文编写说明、备注和注释；不在本文档中写入密码、登录 token、会话 cookie、供应商 API key 等明文敏感信息。

## 接口记录

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
