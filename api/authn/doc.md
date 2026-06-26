# api/authn

## 职责

`api/authn` 处理 **WebUI API service 的登录态与 OAuth 凭据链接**，覆盖可选密码保护、session cookie、API token 登录，以及 onboarding 阶段的 provider OAuth 设备流。

**负责：**

- 可选密码认证（`HERMES_WEBUI_PASSWORD` / 存储哈希）
- `hermes_session` HttpOnly cookie 的签发、校验、过期与持久化
- `POST /api/auth/token-login`（外部前端主入口）
- 公开路径白名单（`PUBLIC_PATHS`）
- Onboarding OAuth：OpenAI Codex device flow、Anthropic credential linking；token 写入 profile `auth.json`

**不负责：**

- CSRF 检查（`api/routes_helpers/csrf.py`）
- NocoBase 用户身份或业务权限（`X-User-Id` 仅为上下文）
- Provider API key CRUD（`api/providers_runtime/providers.py`）
- 独立 Hermes gateway 的 messaging 鉴权

顶层 `api/auth.py`、`api/oauth.py` 为兼容 shim。

## 功能

| 模块 | 主要能力 |
|------|----------|
| `auth.py` | `is_auth_enabled`、`check_auth`、`create_session` / `destroy_session`、token-login、登录限速、`PUBLIC_PATHS` |
| `oauth.py` | `start_onboarding_oauth_flow`、`poll_onboarding_oauth_flow`、`cancel_onboarding_oauth_flow`；Codex / Anthropic 设备授权；`auth.json` credential_pool 写入 |

关键流程：

1. **Token login**：`POST /api/auth/token-login` → 校验 API token → 写 `hermes_session` cookie → 后续请求经 `server.py` / `check_auth` 放行。
2. **密码 login**（可选）：`POST /api/auth/login` → 速率限制 → 同上 cookie 流程。
3. **OAuth onboarding**：`POST /api/onboarding/oauth/start` → 浏览器轮询 `user_code` / `verification_uri` → `poll` 完成后服务端持久化 token → `api/runtime/streaming.py` 运行时读取。

对外路由（经 `api/routes_dispatcher.py`）：

- `GET /api/auth/status`
- `POST /api/auth/login`、`/api/auth/token-login`、`/api/auth/logout`
- `POST /api/onboarding/oauth/start`、`/poll`、`/cancel`

## 依赖边界

**依赖：**

- `api/core/config.py`（`STATE_DIR`、`load_settings`）
- `api/runtime/streaming.py`（`_ENV_LOCK`，Anthropic env 与 runtime provider 解析）
- `api/core/profiles.py`（active profile home、`auth.json` 路径）
- `api/providers_runtime/providers.py`（`_write_env_file`，OAuth 后 env 同步）
- `api/providers_runtime/onboarding.py`（onboarding 状态机调用 oauth 模块）

**被依赖：**

- `server.py`（请求级鉴权）
- `api/routes_dispatcher.py`、`api/routes.py`
- `api/runtime/streaming.py`（运行时 OAuth provider 解析）
- `api/providers_runtime/onboarding.py`

**shim 关系：** 否。`api/auth.py` → `api.authn.auth`，`api/oauth.py` → `api.authn.oauth`。

## 溯源

| 类型 | 位置 |
|------|------|
| 实现 | `api/authn/auth.py`、`api/authn/oauth.py` |
| 兼容入口 | `api/auth.py`、`api/oauth.py` |
| 路由入口 | `api/routes_dispatcher.py`（`/api/auth/*`、`/api/onboarding/oauth/*`） |
| 服务壳 | `server.py`（`check_auth`、`PUBLIC_PATHS`） |
| 测试 | `api/test_server_pressure.py`（`token-login` 压力短路） |
| 契约检查 | `scripts/scan_routes_contracts.py --check` |