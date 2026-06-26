# api/features/files

## 职责

`api/features/files` 处理 **用户文件上传与附件落盘**，包括 multipart 解析、大小限制、workspace 安全路径与转写入口。

**负责：**

- `multipart/form-data` 解析（boundary、字段与文件分离）
- `POST /api/upload`：聊天附件写入 session workspace
- `POST /api/upload/extract`：压缩包解压到受控目录
- `POST /api/transcribe`：音频转写（调用上游能力）
- 文件名消毒、`safe_resolve_ws` 路径约束

**不负责：**

- Workspace 文件浏览/编辑 API（`api/routes_handlers/file.py`、`api/core/workspace.py`）
- Session 消息持久化（`api/core/models.py`）
- 路由分发与 CSRF（`api/routes_dispatcher.py`）

顶层 `api/upload.py` 为兼容 shim。

## 功能

| 模块 | 主要能力 |
|------|----------|
| `upload.py` | `parse_multipart`、`handle_upload`、`handle_upload_extract`、`handle_transcribe`；`CHAT_ATTACHMENT_MAX_UPLOAD_BYTES` / `MAX_UPLOAD_BYTES` 限制 |

关键流程：

1. **上传附件**：客户端 `multipart` POST → 解析 fields/files → `get_session` 定位 workspace → `safe_resolve_ws` → 写入磁盘 → 返回相对路径供 chat 引用。
2. **解压**：上传 zip/tar → 校验总大小与路径 → 解压到 workspace 子目录。
3. **转写**：上传音频 → 调用配置的转写后端 → 返回文本。

相关路由（`api/routes_dispatcher.py` POST 分支）：

- `POST /api/upload`
- `POST /api/upload/extract`
- `POST /api/transcribe`

## 依赖边界

**依赖：**

- `api/core/config.py`（上传大小常量）
- `api/core/helpers.py`（`j`、`bad`）
- `api/core/models.py`（`get_session`）
- `api/core/workspace.py`（`safe_resolve_ws`）

**被依赖：**

- `api/routes.py`（re-export handler）
- `api/routes_dispatcher.py`

**shim 关系：** 否。`api/upload.py` → `api.features.files.upload`。

## 溯源

| 类型 | 位置 |
|------|------|
| 实现 | `api/features/files/upload.py` |
| 兼容入口 | `api/upload.py` |
| 路由入口 | `api/routes_dispatcher.py`；`api/routes.py` import `handle_upload` 等 |
| 工作区文件 API | `api/routes_handlers/file.py`、`file_extra.py` |
| 契约检查 | `scripts/scan_routes_contracts.py --check` |