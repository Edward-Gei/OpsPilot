# OpsPilot V1 API 设计

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 状态 | 竣工，与 `backend/app/api/v1/` 路由同步 |
| Base URL | `/api/v1` |
| 最终契约 | 运行中的 FastAPI OpenAPI：`/api/v1/docs` |

## 1. 通用约定

认证请求使用 `Authorization: Bearer <access_token>`。个人访问密钥 `opsp_` 也可作为 Bearer 凭证，但不能访问密钥管理接口。

成功响应：

```json
{"code": 0, "message": "ok", "data": {}}
```

失败响应沿用相同包裹结构，业务 code 与 HTTP 状态码同时表达错误。常见 code：`40001` 参数错误、`40101` 未认证、`40102` Token 过期、`40103` 需要 MFA、`40104` 需要绑定 MFA、`40105` 需要改密、`40301` 无权限、`40302` 对象越权、`40401` 不存在、`40901` 状态冲突、`42201` 业务拒绝、`42901` 限流/锁定、`50001` 内部错误。

分页请求为 `page` 和 `page_size`，最大 100；分页数据为 `{items,total,page,page_size}`。时间使用带时区 ISO8601。

## 2. 认证和个人账号

### 2.1 `/auth`

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| POST | `/auth/login` | 公开 | `{username,password}`；返回正式令牌、`mfa_token` 或 `mfa_token + mfa_setup` |
| POST | `/auth/forgot-password` | 公开 | `{email}`；所有分支 HTTP 200 且公开文案一致 |
| GET | `/auth/reset-password/validate` | 公开 | `token` 查询参数；返回 `valid`、`expired`、`used` 或 `invalid` |
| POST | `/auth/reset-password` | 公开 | `{token,new_password}`；成功后吊销全部 Refresh Token |
| GET | `/auth/password-policy` | 公开 | 返回当前密码策略 |
| POST | `/auth/refresh` | 公开 | `{refresh_token}`；旋转并吊销旧 Refresh Token |
| POST | `/auth/logout` | 登录 | 吊销当前 Refresh jti |
| GET | `/auth/me` | 登录 | 当前用户、角色、权限和菜单信息 |
| PUT | `/auth/me` | 登录 | 修改本人显示名和邮箱 |
| POST | `/auth/mfa/setup` | 登录或 `mfa_token` | 生成 TOTP secret 和绑定 URI |
| POST | `/auth/mfa/bind` | 登录或 `mfa_token` | `{code}` 完成绑定 |
| POST | `/auth/mfa/verify` | `mfa_token` | `{code}` 换正式令牌 |
| PUT | `/auth/password` | 登录或 `change_token` | 修改本人密码；强制改密成功后直接返回正式令牌 |
| GET | `/auth/sso/oidc/login` | 公开 | 跳转 OIDC Provider |
| GET | `/auth/sso/oidc/callback` | 公开 | OIDC 回调并重定向到前端换票 |
| POST | `/auth/sso/exchange` | 公开 | `{ticket}` 换正式令牌 |
| GET | `/auth/sso/options` | 公开 | 返回 OIDC 和密码找回入口是否启用 |

密码重置只面向本地且绑定邮箱的用户。令牌随机生成、Redis 只保存哈希、默认 30 分钟、校验不消费、提交原子单次消费。审计动作严格为 `pwd_reset_requested`、`pwd_reset_success`、`pwd_reset_failed`，失败原因写入详情。

### 2.2 `/user/tokens`

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/user/tokens` | 登录 JWT | 本人密钥列表，不返回明文/哈希 |
| POST | `/user/tokens` | 登录 JWT | `{name,expires_in_days}`；明文只返回一次，每人最多 10 个 |
| DELETE | `/user/tokens/{id}` | 登录 JWT | 删除本人密钥 |

## 3. 用户和角色

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/users` | `user:read` | 分页、关键字、状态、来源筛选 |
| POST | `/users` | `user:write` | 创建本地用户并分配角色 |
| PUT | `/users/{id}` | `user:write` | 更新资料、状态和角色，不能禁用自己 |
| PUT | `/users/{id}/password` | `user:write` | 管理员重置用户密码 |
| PUT | `/users/{id}/mfa` | `user:mfa` | 管理员启用、禁用或重置 MFA |
| GET | `/roles` | `role:read` | 角色、权限和成员数 |
| GET | `/roles/permissions` | `role:read` | 30 个权限点 |
| GET | `/roles/options` | `template:read` | 角色 ID/名称轻量选项 |
| POST | `/roles` | `role:write` | 创建自定义角色 |
| PUT | `/roles/{id}` | `role:write` | 更新角色；admin 权限矩阵不可改 |
| DELETE | `/roles/{id}` | `role:write` | 删除非内置且未被用户引用的角色 |

## 4. CMDB

### 4.1 `/cmdb/hosts`

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/cmdb/hosts` | `cmdb:read` | 分页、关键字、平台/区域/环境/状态筛选 |
| POST | `/cmdb/hosts` | `cmdb:write` | 新建主机，内网 IP 唯一 |
| GET | `/cmdb/hosts/{id}` | `cmdb:read` | 主机详情和关联应用 |
| PUT | `/cmdb/hosts/{id}` | `cmdb:write` | 编辑主机 |
| DELETE | `/cmdb/hosts/{id}` | `cmdb:delete` | 被应用关联时拒绝 |
| GET | `/cmdb/hosts/suggest` | `cmdb:read` | 平台/区域/主机系列补全 |
| GET | `/cmdb/hosts/import-template` | `cmdb:import` | 下载 Excel 模板 |
| POST | `/cmdb/hosts/import` | `cmdb:import` | 逐行校验、可选存在即更新 |
| GET | `/cmdb/hosts/export` | `cmdb:read` | 导出当前筛选 |

### 4.2 `/cmdb/apps`

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/cmdb/apps` | `cmdb:read` | 应用分页、部署方式和项目类型筛选 |
| POST | `/cmdb/apps` | `cmdb:write` | 创建应用和主机关联 |
| GET | `/cmdb/apps/{id}` | `cmdb:read` | 应用详情 |
| PUT | `/cmdb/apps/{id}` | `cmdb:write` | 编辑应用和主机关联 |
| DELETE | `/cmdb/apps/{id}` | `cmdb:delete` | 删除应用 |
| GET | `/cmdb/apps/export` | `cmdb:read` | 按当前筛选导出应用 |

## 5. 凭据和作业主机

### 5.1 `/credentials`

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/credentials` | `credential:read` 或 `secret:read` | 分页和凭据类型筛选，按权限仅返回 SSH 凭据或脚本密钥，永不返回密文 |
| POST | `/credentials` | SSH 类型需 `credential:write`；脚本密钥需 `secret:write` | 创建凭据，类型创建后不可修改 |
| PUT | `/credentials/{id}` | 同创建类型的写权限 | 更新；secret 为空表示保留原密文 |
| DELETE | `/credentials/{id}` | 同创建类型的删除权限 | SSH 凭据被作业主机引用、脚本密钥被模板或活动工单引用时拒绝 |

### 5.2 `/job-hosts`

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/job-hosts` | `job_host:read` | 分页、关键字和启用状态筛选 |
| POST | `/job-hosts` | `job_host:write` | 创建并绑定 SSH 凭据（拒绝脚本密钥） |
| GET | `/job-hosts/{id}` | `job_host:read` | 详情，不返回凭据密文 |
| PUT | `/job-hosts/{id}` | `job_host:write` | 编辑连接信息 |
| DELETE | `/job-hosts/{id}` | `job_host:delete` | 被工单模板引用时拒绝 |
| POST | `/job-hosts/{id}/test` | `job_host:write` | 执行 `echo ok` 连通性测试 |
| PUT | `/job-hosts/{id}/status` | `job_host:write` | 启用或停用 |

## 6. 模板和工单

### 6.1 `/process-templates`

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/process-templates` | `template:read` | 分页、关键字和状态筛选 |
| POST | `/process-templates` | `template:write` | 创建步骤、审批角色和执行策略 |
| GET | `/process-templates/{id}` | `template:read` | 流程及步骤详情 |
| PUT | `/process-templates/{id}` | `template:write` | 全量更新 |
| PUT | `/process-templates/{id}/status` | `template:write` | 启停 |
| DELETE | `/process-templates/{id}` | `template:delete` | 被工单模板引用时拒绝 |

### 6.2 `/templates`

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/templates` | `template:read` | 分页、关键字、类型和状态筛选 |
| POST | `/templates` | `template:write` | 创建工单入口和参数定义 |
| GET | `/templates/{id}` | `template:read` | 入口详情和流程引用 |
| PUT | `/templates/{id}` | `template:write` | 全量更新 |
| PUT | `/templates/{id}/status` | `template:write` | 启停 |
| DELETE | `/templates/{id}` | `template:delete` | 有进行中工单时拒绝 |

### 6.3 `/tickets`

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/tickets/templates` | `ticket:write` | 返回启用且当前角色可见的模板 |
| GET | `/tickets/templates/{template_id}/form` | `ticket:write` | 参数表单和作业主机/步骤只读预览 |
| POST | `/tickets/prepare` | `ticket:write` | 执行动态参数脚本并生成短期 `prepare_id` |
| POST | `/tickets` | `ticket:write` | `{template_id,params,prepare_id?}` 提交工单 |
| GET | `/tickets` | `ticket:read` | 分页、状态、创建人、关键字和时间筛选 |
| GET | `/tickets/todo` | `ticket:approve` | 当前角色待审批列表 |
| GET | `/tickets/todo/events` | `ticket:approve` | `since_seq`（整数，`>=0`，默认 `0`）回放当前用户的待办变更事件；无新事件最长等待 30 秒，返回 `{events:[{seq,kind:"todo.changed"}],last_seq}`，超时返回空 `events` 和原 `since_seq`。客户端必须直接赋值返回的 `last_seq`；若游标大于当前序号，返回一条合成 `todo.changed` 重同步事件和较小的 `last_seq` |
| GET | `/tickets/{id}` | `ticket:read` | 工单、快照、审批时间线和执行概要 |
| POST | `/tickets/{id}/approve` | `ticket:approve` | `{action:approve/reject,comment?}` |
| POST | `/tickets/{id}/cancel` | `ticket:write` | 创建人在 `approving` 或 `queued` 状态撤回 |
| POST | `/tickets/{id}/abort` | `execution:control` | 中止执行 |
| POST | `/tickets/{id}/pause` | `execution:control` | 暂停执行 |
| POST | `/tickets/{id}/resume` | `execution:control` | 恢复执行 |
| POST | `/tickets/{id}/force-abort` | `execution:force_control` | 强制中止并记录高风险审计 |

工单服务校验模板启用、可见角色、流程启用、参数定义和预生成结果；提交后只读快照。

## 7. 执行和实时日志

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/executions` | `execution:read` | 执行记录分页；工单号、状态、发起人和实际开始时间（`start/end`）筛选 |
| GET | `/executions/{id}` | `execution:read` | 执行详情和步骤状态 |
| GET | `/executions/{id}/logs` | `execution:read` | `step_order`、`offset`、`limit` 读取日志行 |
| GET | `/executions/{id}/events` | `execution:read` | 状态事件 REST 长轮询 |

日志保存在共享卷，事件用于低延迟刷新，前端按偏移增量读取正文。

## 8. 通知

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/notify/channels` | `notify:read` | 渠道配置、渠道×事件模板元数据和变量元数据 |
| PUT | `/notify/channels/{type}` | `notify:write` | 更新启用状态、非敏感配置和 secret |
| POST | `/notify/channels/{type}/test` | `notify:test` | 按事件和当前未保存配置发送测试 |
| GET | `/notify/events` | `notify:read` | 六事件映射矩阵 |
| PUT | `/notify/events` | `notify:write` | `{mappings:{event:[channel_type]}}` 全量更新 |
| GET | `/notify/records` | `notify:read` | 按事件、渠道、状态和时间分页查询 |

渠道模板存于 `config.templates[event]`：Email 为 `{title,content}`，Webhook 为 `{body}`，Teams 为 `{card}`。JSON 模板会在保存和渲染后解析；`{ref_id}` 和 `{receivers}` 可作为原生 number/array，也可作为字符串值使用。

站内信接口不要求独立权限，只能访问当前用户：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/notifications` | 本人分页站内信，可筛选未读 |
| GET | `/notifications/unread-count` | 本人未读数 |
| PUT | `/notifications/{id}/read` | 标记本人单条已读 |
| PUT | `/notifications/read-all` | 本人全部已读 |

## 9. 审计

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/audit/logs` | `audit:read` | 时间、操作人、模块、动作、结果和对象关键字组合筛选 |
| GET | `/audit/logs/export` | `audit:export` | 当前筛选导出 CSV 或 XLSX，最多 100000 行；导出行为写审计 |

## 10. 系统、工作台和搜索

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/system/configs` | `system:config` | 读取预置配置，敏感字段掩码 |
| PUT | `/system/configs` | `system:config` | 批量更新预置配置，掩码值保留原 secret |
| GET | `/healthz` | 公开 | DB/Redis 健康检查 |
| GET | `/dashboard/summary` | 登录 | 按权限返回概览各段 |
| GET | `/dashboard/ticket-trend` | `ticket:read` | 日/周/月/年提单趋势 |
| GET | `/search?keyword=` | 登录 | 按权限裁剪的主机、应用、工单和模板聚合搜索 |

## 11. 权限点全集

```text
user:read user:write user:mfa role:read role:write
cmdb:read cmdb:write cmdb:delete cmdb:import
credential:read credential:write credential:delete
secret:read secret:write secret:delete
template:read template:write template:delete
job_host:read job_host:write job_host:delete
ticket:read ticket:write ticket:approve
execution:read execution:control execution:force_control
audit:read audit:export
notify:read notify:write notify:test system:config
```
