# api/providers_runtime

## 职责

`api/providers_runtime` 管理 **AI provider 配置、首次引导与用户级 provider 覆盖**，连接 Hermes 本地 `config.yaml` / `.env` 与外部 NocoBase provider 表。

**负责：**

- 本地 provider 列表、API key 读写、quota 探测（`providers.py`）
- 首次运行 onboarding：模型探测、workspace 初始化、provider setup（`onboarding.py`）
- 带 `X-User-Id` 请求的用户级 provider 解析与模型列表（`user_provider.py`）
- 用户 provider CRUD 与管理 payload（`user_provider_management.py`）
- Profile ↔ NocoBase provider 配置同步（`user_provider_config_sync.py`）
- 内部服务间 root-profiles provider 同步（`internal_provider_sync.py`）

**不负责：**

- Agent 运行时线程与 SSE（`api/runtime/streaming.py`）
- OAuth 设备流本身（`api/authn/oauth.py`，onboarding 会调用）
- NocoBase 表结构定义或 workflow（生产实例 `www.foxuai.com`）
- 普通聊天路由 handler（`api/routes_handlers/chat.py`）

顶层 `api/providers.py`、`api/onboarding.py`、`api/user_provider.py` 等为兼容 shim。

## 功能

| 模块 | 主要能力 |
|------|----------|
| `providers.py` | `get_providers`、`set_provider_key`、`remove_provider_key`、`get_provider_quota`；provider ↔ env var 映射；`config.yaml` 写入 |
| `onboarding.py` | `get_onboarding_status`、`apply_onboarding_setup`、`complete_onboarding`、provider probe |
| `user_provider.py` | `resolve_user_provider_for_chat`、NocoBase collection 读取、`build_user_provider_models_payload` |
| `user_provider_management.py` | 用户 AI provider 列表/创建/更新/删除 payload 构建 |
| `user_provider_config_sync.py` | profile 创建后同步 provider model config groups |
| `internal_provider_sync.py` | `POST /api/internal/provider-sync/root-profiles`（token 校验） |

关键流程：

1. **本地 provider 管理**：`GET/POST /api/providers` → 读写在 active profile 的 `config.yaml` / `.env`。
2. **用户级覆盖**：请求带 `X-User-Id` → `user_provider` 查 NocoBase `hermes_user_ai_providers` → streaming 层注入 runtime provider。
3. **Onboarding**：`/api/onboarding/status` → `setup` / OAuth `start`+`poll` → `complete`。
4. **内部同步**：NocoBase workflow 调用 internal endpoint，批量同步 root profile provider 记录。

对外路由（`api/routes_dispatcher.py`）：

- `GET/POST /api/providers`、`POST /api/providers/delete`
- `GET /api/onboarding/status`、`POST /api/onboarding/setup|complete|probe`
- 用户 provider 相关路径（与 `user_provider_management` 联动）
- `POST /api/internal/provider-sync/root-profiles`（`PUBLIC_PATHS` 白名单，独立 token）

## 依赖边界

**依赖：**

- `api/core/config.py`（配置读写、模型缓存）
- `api/core/profiles.py`（profile home、`_profiles_root`）
- `api/authn/auth.py`（onboarding 是否需认证）
- `api/authn/oauth.py`（OAuth onboarding）
- NocoBase REST（`HERMES_USER_PROVIDER_NOCOBASE_BASE_URL` 等，默认 `https://www.foxuai.com`）

**被依赖：**

- `api/runtime/streaming.py`（chat 时 provider 解析）
- `api/routes.py`、`api/routes_dispatcher.py`
- `api/authn/oauth.py`（`_write_env_file`）

**shim 关系：** 否。顶层 `api/providers.py` 等指向本目录。

## 溯源

| 类型 | 位置 |
|------|------|
| 实现 | `api/providers_runtime/*.py` |
| 兼容入口 | `api/providers.py`、`api/onboarding.py`、`api/user_provider.py`、`api/user_provider_management.py`、`api/user_provider_config_sync.py`、`api/internal_provider_sync.py` |
| 路由入口 | `api/routes_dispatcher.py` |
| 测试 | `api/test_internal_provider_sync.py` |
| 契约检查 | `scripts/scan_routes_contracts.py --check` |