# 自动化运维平台 V1 API 设计

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v2.1（竣工基线：接口清单与已实现代码逐个核对对齐） |
| 状态 | 已实现（与代码同步） |
| 上游文档 | [01-PRD](./01-产品需求文档PRD.md)、[02-技术架构设计](./02-技术架构设计.md)、[03-数据库设计](./03-数据库设计.md) |

## 1. 通用约定

- **Base URL**：`/api/v1`。运行时以 FastAPI 自动生成的 OpenAPI（`/api/v1/docs`）为最终契约，本文与已实现代码对齐。连通性探测：`GET /api/v1/ping`（公开，返回 `{pong:true}`）；健康检查 `GET /healthz`（见 §11）。实时刷新全部走 REST 长轮询（见 §8，WebSocket 已弃用）。
- **认证**：`Authorization: Bearer <access_token>`。个人访问密钥（`opsp_` 前缀，见 §2.1）可作为 Bearer 凭证直调平台 API，权限与所属用户实时一致。
- **响应包裹**：

```json
{ "code": 0, "message": "ok", "data": { ... } }          // 成功
{ "code": 40001, "message": "参数错误: ip 格式不合法", "data": null }  // 失败
```

- **分页请求**：`?page=1&page_size=20`（page_size ≤ 100）；**分页响应** `data = { total, page, page_size, items: [] }`。
- **时间**：ISO8601 带时区（`2026-07-27T10:00:00+08:00`）。
- **错误码**（HTTP 状态码 + 业务 code 双轨）：

| code | HTTP | 含义 |
| --- | --- | --- |
| 0 | 200 | 成功 |
| 40001 | 400 | 参数校验失败 |
| 40101 | 401 | 未认证/Token 无效 |
| 40102 | 401 | Token 过期（前端触发 refresh） |
| 40103 | 401 | 需要 MFA 验证（携带 mfa_token 续走） |
| 40104 | 401 | 需要绑定 MFA（强制模式） |
| 40105 | 401 | 需要修改初始密码 |
| 40301 | 403 | 无权限点 |
| 40302 | 403 | 对象级越权（非创建人等） |
| 40401 | 404 | 资源不存在 |
| 40901 | 409 | 状态冲突（如非法状态迁移、重复 IP） |
| 42201 | 422 | 业务规则拒绝（如删除保护） |
| 42901 | 429 | 账号锁定/频率限制 |
| 50001 | 500 | 服务内部错误 |

- **鉴权标注**：下文每个接口标注所需权限点；`※` 表示登录即可。

## 2. 认证 Auth（`/auth`）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| POST | `/auth/login` | 公开 | 入参 `{username, password}`；出参三态：①`{access_token, refresh_token, user}` ②`{mfa_token}`（code=40103，需 TOTP）③`{mfa_token, mfa_setup:true}`（code=40104，需绑定） |
| POST | `/auth/mfa/setup` | mfa_token / ※ | 生成 TOTP secret + `otpauth://` URI；双通道：登录流程传 mfa_token，个人中心自助绑定用登录态 access |
| POST | `/auth/mfa/bind` | mfa_token / ※ | `{code}` 校验并完成绑定；登录流程内绑定成功续发正式令牌，个人中心绑定仅置状态 |
| POST | `/auth/mfa/verify` | mfa_token | `{code}` 校验 TOTP，返回正式令牌（登录第二步，40103 后续走） |
| POST | `/auth/refresh` | 公开 | `{refresh_token}` → 新令牌对（refresh 旋转：旧 refresh 吊销） |
| POST | `/auth/logout` | ※ | 吊销当前 refresh jti |
| GET | `/auth/sso/oidc/login` | 公开 | 302 跳转 OIDC 提供方（授权码模式；未配置返回 40401） |
| GET | `/auth/sso/oidc/callback` | 公开 | 授权码回调，认证/建用户后生成一次性 ticket，重定向前端 `/login?ticket=xxx` |
| POST | `/auth/sso/exchange` | 公开 | `{ticket}` 换正式令牌（复用登录三态出口，SSO 亦受 MFA 策略约束） |
| GET | `/auth/sso/options` | 公开 | 登录页 SSO 选项（返回 `{oidc_enabled}`，据此显隐 SSO 入口） |
| GET | `/auth/me` | ※ | 当前用户信息 + 权限点集合 + 菜单可见性 |
| PUT | `/auth/me` | ※ | 个人中心自助修改资料 `{display_name, email}`（仅限本人，其余字段不开放） |
| PUT | `/auth/password` | change_token / ※ | `{old_password, new_password}`（本地账号）；双通道：强制改密传 change_token 并续发令牌，个人中心用登录态 access |

### 2.1 个人访问密钥（`/user/tokens`）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/user/tokens` | ※ | 本人密钥列表（新建在前），出参 `{items, limit}`；条目含 `id/name/prefix/expired/expires_at/last_used_at/created_at`，永不含明文/哈希 |
| POST | `/user/tokens` | ※ | `{name, expires_in_days}`（空=永不过期）；出参额外携 `token` 明文，**仅此一次**；每人上限 10 个（超限 42201） |
| DELETE | `/user/tokens/{id}` | ※ | 删除本人密钥，立即失效；不存在/非本人一律 40401 |

安全约束：

- 密钥明文格式 `opsp_` + 随机串，库中仅存 SHA-256 哈希；列表仅展示 `prefix`。
- `Bearer opsp_xxx` 可直调所有 API，但**无权访问 `/user/tokens` 管理接口**（40301，防密钥自我繁殖），密钥管理必须使用登录态 JWT。
- 过期/删除/所属账号被禁用 → 密钥鉴权一律 40101；`last_used_at` 60 秒节流更新。
- 创建/删除写审计（`token.create` / `token.delete`）；资料修改写 `user.profile_update`。

## 3. 用户与角色（`/users` `/roles`）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/users` | `user:read` | 分页；筛选 username/status/source/role_id |
| POST | `/users` | `user:write` | 建本地用户 `{username, display_name, email, password, role_ids}` |
| PUT | `/users/{id}` | `user:write` | 基本信息 + status(active/disabled) + role_ids（状态并入本接口，禁止禁用自己） |
| PUT | `/users/{id}/password` | `user:write` | `{new_password}` 重置密码，置 must_change_password 并解锁 |
| PUT | `/users/{id}/mfa` | `user:mfa` | `{action: enable/disable/reset}`（enable 开启 / disable 关闭保留密钥 / reset 清除密钥待重绑；默认仅 admin 具备 user:mfa） |
| GET | `/roles` | `role:read` | 全量（含权限码与成员数） |
| POST | `/roles` | `role:write` | `{code, name, description, permission_codes}` |
| PUT | `/roles/{id}` | `role:write` | 自定义角色可全量修改；**内置角色权限不可改**，仅可改名称/说明 |
| DELETE | `/roles/{id}` | `role:write` | 内置/仍被用户引用的角色拒绝（42201） |
| GET | `/roles/permissions` | `role:read` | 权限点全量（角色编辑页按 module 分组勾选） |
| GET | `/roles/options` | `template:read` | 轻量角色选项（仅 id/name，供模板审批节点/可见范围/收件人下拉；无需 role:read） |

## 4. CMDB（`/cmdb`）

### 4.1 主机

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/cmdb/hosts` | `cmdb:read` | 分页；`keyword`(主机名/IP 模糊)、platform、region、environment、status |
| POST | `/cmdb/hosts` | `cmdb:write` | 单条新增，IP 冲突返回 40901 |
| GET | `/cmdb/hosts/{id}` | `cmdb:read` | 详情 + 关联应用列表 |
| PUT | `/cmdb/hosts/{id}` | `cmdb:write` | |
| DELETE | `/cmdb/hosts/{id}` | `cmdb:write` | 删除保护触发 42201，message 说明被谁引用 |
| GET | `/cmdb/hosts/suggest` | `cmdb:read` | `?field=platform|region&q=xx` 自动补全 |
| GET | `/cmdb/hosts/import-template` | `cmdb:write` | 下载 xlsx 模板 |
| POST | `/cmdb/hosts/import` | `cmdb:write` | multipart 上传；`?upsert=true` 存在即更新；返回 `{success_count, failed_rows:[{row, reason}]}` |
| GET | `/cmdb/hosts/export` | `cmdb:read` | 按当前筛选导出 xlsx（流式） |

### 4.2 应用

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/cmdb/apps` | `cmdb:read` | 分页；keyword/language/deploy_type；items 含关联主机数 |
| POST | `/cmdb/apps` | `cmdb:write` | `{name, description, language, deploy_type, host_ids}` |
| GET | `/cmdb/apps/{id}` | `cmdb:read` | 详情 + 关联主机列表 |
| PUT | `/cmdb/apps/{id}` | `cmdb:write` | 含 host_ids 全量替换 |
| DELETE | `/cmdb/apps/{id}` | `cmdb:write` | 进行中工单引用时 42201 |

## 5. 凭据与工单模板（`/credentials` `/templates`）

模板管理负责工单全部规则配置：基本信息/目标应用/步骤编排（内嵌脚本）/执行策略/审批规则/通知规则/权限范围/版本。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/credentials` | `credential:read` | 列表（不含密文）；作业主机关联与模板引用凭据选择用 |
| POST | `/credentials` | `credential:write` | `{name, login_user, auth_type, secret, passphrase?}` |
| PUT | `/credentials/{id}` | `credential:write` | secret 传空 = 不变更 |
| DELETE | `/credentials/{id}` | `credential:write` | 被作业主机（`job_host.credential_id`）或模板（`ticket_template.credential_refs`）引用时 42201 |
| GET | `/templates` | `template:read` | 分页；keyword/type/status |
| POST | `/templates` | `template:write` | 全量配置：`{name, type, description, app_id, steps:[{name, script_type, content, params_schema, credential_id, timeout}], exec_strategy, approval_enabled, approval_nodes:[{node_order, role_id, approve_mode}], allow_withdraw, allow_transfer, allow_countersign, notify_rules, visible_role_ids, credential_refs:[{alias, credential_id}]}` → 创建 v1；credential_refs 为模板级引用凭据声明（alias 字母开头、大小写不敏感去重），shell 步骤执行时注入 `CRED_<ALIAS大写>_USER/_SECRET/_PASSPHRASE` 环境变量 |
| GET | `/templates/{id}` | `template:read` | 当前版本全量配置详情；credential_refs 回显附 credential_name |
| PUT | `/templates/{id}` | `template:write` | 全量更新；规则任一变更自动升版（含 credential_refs 变更）；仅改名/说明不升版 |
| PUT | `/templates/{id}/status` | `template:write` | `{status: enabled/disabled}`；禁用后不可被提交，不影响已提交工单 |
| DELETE | `/templates/{id}` | `template:write` | 被进行中工单引用时 42201 |
| GET | `/templates/{id}/versions` | `template:read` | 版本列表 |
| GET | `/templates/{id}/versions/{version}` | `template:read` | 历史版本全量快照 |

### 5.1 作业主机（`/job-hosts`，系统设置维护）

登录认证随关联凭据（凭据管理）走，主机侧不再填写任何账号/密文字段。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/job-hosts` | `job_host:read` | 分页；响应含 `credential_id / credential_name`，无任何密文字段 |
| POST | `/job-hosts` | `job_host:write` | `{name, ip, ssh_port?, credential_id, workdir?}`；credential_id 必填，凭据不存在 40401 |
| GET | `/job-hosts/{id}` | `job_host:read` | 详情 |
| PUT | `/job-hosts/{id}` | `job_host:write` | 编辑；`credential_id` 传值即切换关联凭据 |
| DELETE | `/job-hosts/{id}` | `job_host:write` | 被模板引用时 42201 |
| POST | `/job-hosts/{id}/test` | `job_host:write` | 连通性测试：以关联凭据实时取账号/密文建连 |
| PUT | `/job-hosts/{id}/status` | `job_host:write` | 启用/禁用；禁用后不可被新模板选用 |

## 6. 工单中心（`/tickets`）

工单中心只能使用模板：选模板 → 填参数 → 提交（标题=模板名，无草稿，提交即生效）。不提供模板/审批流/表单字段/通知/策略的任何配置能力。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/tickets/templates` | `ticket:write` | 可用模板列表（enabled + visible_role_ids 过滤） |
| GET | `/tickets/templates/{id}/form` | `ticket:write` | 提交表单描述：汇总参数（各步骤 params_schema 同名合并，排除 fixed）+ 目标主机/步骤/审批节点/策略只读预览 |
| POST | `/tickets` | `ticket:write` | 提交工单：`{template_id, params}`；服务端固化五重快照（主机/步骤内容/审批节点/策略/版本）；免审直接入执行队列，否则进入 approving 并发待审批通知 |
| GET | `/tickets` | `ticket:read` | 分页；status/creator/app/keyword/时间范围 |
| GET | `/tickets/todo` | `ticket:approve` | 待我审批（当前节点角色 ∩ 我的角色）+ 角标计数 |
| GET | `/tickets/{id}` | `ticket:read` | 详情（只读）：基本信息 + 参数 + 主机快照 + 步骤快照 + 引用凭据快照（`credential_refs`，含 credential_name，不含密文）+ 审批时间线 + execution 概要 |
| POST | `/tickets/{id}/approve` | `ticket:approve` | `{action: approve/reject, comment}`；校验当前节点角色归属（可审批自己创建的工单）；驳回 comment 必填；末节点通过→自动入执行队列 |
| POST | `/tickets/{id}/cancel` | `ticket:write` | 撤销（审批前作废）：仅创建人、approving 状态、且 allow_withdraw_snap=true；工单置 cancelled |
| POST | `/tickets/{id}/abort` | `execution:control` | 中止：queued/running/paused；停止派发后续目标（在跑目标不打断），未派发置 skipped，工单终态 interrupted(user_abort)；仅创建人或 admin |
| POST | `/tickets/{id}/pause` | `execution:control` | 暂停：仅 running；在跑目标跑完后停住（paused 非终态），不再派发；仅创建人或 admin |
| POST | `/tickets/{id}/resume` | `execution:control` | 恢复：仅 paused；从停住处继续派发（含批间暂停放行）；仅创建人或 admin |
| POST | `/tickets/{id}/force-abort` | `execution:control` | 强制中止：running/paused；在中止基础上强杀在跑目标（关闭 SSH 会话，best-effort）；高风险，独立审计；仅创建人或 admin |

控制类接口（abort/pause/resume/force-abort）均写审计（操作人/IP/时间/工单ID/当时状态）并触发通知；审批人不能代替提交人撤销（只能审批通过/驳回）。状态不匹配返回 40901。

**提交时服务端校验**：模板 enabled 且在我的可见范围；应用存在且关联主机 ≥1；凭据存在；参数满足汇总 params_schema（required/fixed）；审批节点角色存在。提交时冻结模板 credential_refs 为 `[{alias, credential_id, credential_name}]` 快照，执行时按 credential_id 实时取密文。

> 已删除：`PUT/DELETE /tickets/{id}`（无草稿可编辑）、`POST /tickets/{id}/submit`（并入创建）、`POST /tickets/{id}/copy`。

## 7. 执行记录（`/executions`）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/executions` | `execution:read` | 分页；ticket_no/app/creator/status/时间 |
| GET | `/executions/{id}` | `execution:read` | 汇总 + 步骤列表（状态/批次/统计） |
| GET | `/executions/{id}/hosts` | `execution:read` | `?step_order=&status=` 主机明细分页 |
| GET | `/executions/{id}/logs` | `execution:read` | `?step_order=&ip=&offset=&limit=` 读日志文件（按行偏移；历史回看 + 前端 2s 定时增量拉取实现实时刷新） |
| GET | `/executions/{id}/events` | `execution:read` | 实时状态长轮询主通道：`?since_seq=` 30s 挂起返回增量事件 |

> 执行域为**只读查询**；控制操作（中止/暂停/恢复/强制中止）在工单控制面（§6），权限 `execution:control` 且（创建人 或 admin）。原 `POST /executions/{id}/stop|resume` 已移除。

## 8. 实时通道（长轮询；WebSocket 已弃用）

执行详情页实时刷新采用纯长轮询方案（均复用 §7 只读接口，无独立协议）：

- **状态事件**：`GET /executions/{id}/events?since_seq=` 长轮询循环，服务端有新事件立即返回，否则挂起至 30s 返回空列表；`seq` 单调递增，客户端持有 `last_seq` 续拉；`finished=true` 时结束轮询。
- **实时日志**：`GET /executions/{id}/logs?step_order=&ip=&offset=` 每 2s 按行偏移增量拉取追加（Jenkins 式尾随）；切换主机即重置 `offset=0` 重拉，无订阅状态。

> **WebSocket 已弃用（2026-07）**：原 `/ws/executions/{id}?token=` 网关（snapshot/log/event/ping 协议）因切换目标主机后订阅失效导致日志无法实时更新，且断线重连/令牌过期链路复杂，已从应用摘除注册。代码保留在 `backend/app/api/ws_deprecated.py` 备查，回切需恢复 `main.py` 注册与 nginx `/ws/` 代理。

## 9. 审计（`/audit`）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/audit/logs` | `audit:read` | 分页；start/end、actor、module、action、result、keyword(target_name) |
| GET | `/audit/logs/export` | `audit:export` | `?format=csv|xlsx` + 同筛选；流式下载，上限 10 万行；本次导出行为自身写审计 |

## 10. 通知（`/notify`）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/notify/channels` | `notify:config` | 全渠道配置（敏感项掩码 `******`） |
| PUT | `/notify/channels/{type}` | `notify:config` | `{enabled, config, secret?}` secret 不传=不变更 |
| POST | `/notify/channels/{type}/test` | `notify:config` | 发送测试消息，同步返回结果 |
| GET | `/notify/events` | `notify:config` | 事件-渠道映射 |
| PUT | `/notify/events` | `notify:config` | 全量提交映射 |
| GET | `/notify/records` | `notify:config` | 发送记录分页；event/channel/status/时间 |

> **渠道级消息模板**：`config` 可含可选键 `title_template`（≤200 字）、`content_template`（≤2000 字），超长返回 `40001`。占位符格式 `{变量名}`，留空/未配置则使用系统默认文案，未知或缺失变量原样保留。模板在 `emit` 落库时按渠道即时渲染（发送记录即最终实发内容，改模板只对新通知生效）。`POST /notify/channels/{type}/test` 用示例工单数据渲染当前表单模板后发送，供保存前预览。
>
> 支持变量：`{event}`(事件中文名) `{default_title}` `{default_content}` `{receiver}` `{time}` `{ref_id}`（emit 基础）；`{ticket_no}` `{ticket_title}` `{app_name}` `{creator}`（工单）；`{node}` `{role}`（待审批）；`{approver}` `{comment}`（通过/驳回）；`{reason}`（中断）；`{detail}`（崩溃恢复）。

### 10.1 站内通知（`/notifications`，NOTIFY-06）

登录即可（不挂权限点），仅能访问本人数据；铃铛未读数 30 秒轮询。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/notifications` | 登录 | 我的站内信分页；`only_unread` 筛选，按 created_at 倒序 |
| GET | `/notifications/unread-count` | 登录 | 未读数 `{count}` |
| PUT | `/notifications/{id}/read` | 登录 | 单条已读；非本人数据返回 40401 |
| PUT | `/notifications/read-all` | 登录 | 全部已读，返回本次置已读条数 |

## 11. 系统设置（`/system`）

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/system/configs` | `system:config` | 全部 system_config（敏感项掩码） |
| PUT | `/system/configs` | `system:config` | 批量更新 `{configs: {key: value}}`（仅允许预置键：mfa.policy / token.policy / password.policy / ldap.config / oidc.config / ldap.default_role / oidc.default_role / ansible.job_host / exec.global_concurrency，共 9 键；敏感字段回传 `******` 保留原值） |
| POST | `/system/ansible-job-host/test` | `system:config` | SSH 连通性 + `ansible --version` 探测 |
| GET | `/healthz` | 公开 | 存活 + DB/Redis 探活（部署健康检查用） |

> 已删除：`GET/PUT /system/approval-flow`（审批规则下沉到模板内，无全局审批流）、`approval.enabled` 配置项。

## 12. 工作台（`/dashboard`）

聚合类接口：登录即可访问，无独立权限点；`summary` 内部按调用者权限集合裁剪各段（无权段为 null），`ticket-trend` 明确依赖 `ticket:read`（与工单列表同门槛）。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/dashboard/summary` | 登录 | 一次性返回 CMDB/工单/待办/执行/审计各段计数；无权限段为 null |
| GET | `/dashboard/ticket-trend` | `ticket:read` | `?granularity=day/week/month/year` 提单数趋势分桶补零（day=近30天 / week=近12周 / month=近12月 / year=近5年） |

## 13. 全局搜索（`/search`，SEARCH-03）

登录即可（不挂独立权限点）；接口内部按调用者权限决定各段返回与否，与 `/dashboard/summary` 同惯例。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/search?keyword=` | 登录 | 聚合搜索；每段 items 限 5 条 + total；无权段为 null；keyword 空/全空白 40001 |

返回结构（各段复用对应模块列表查询，keyword + page_size=5）：

```json
{
  "hosts":     {"items": [{"id", "hostname", "ip"}], "total": 0},        // 需 cmdb:read，否则 null
  "apps":      {"items": [{"id", "name"}], "total": 0},                  // 需 cmdb:read，否则 null
  "tickets":   {"items": [{"id", "ticket_no", "title"}], "total": 0},    // 需 ticket:read，否则 null
  "templates": {"items": [{"id", "name", "type"}], "total": 0}           // 需 template:read，否则 null
}
```

> 「功能菜单」搜索纯前端本地过滤侧边栏菜单项，不经后端。结果点击统一跳对应列表页并带 `?keyword=` 自动过滤（SEARCH-04）。

## 14. 权限点全集（与 §2.2 PRD 矩阵对应，共 20 个）

```
user:read user:write user:mfa role:read role:write
cmdb:read cmdb:write
credential:read credential:write
template:read template:write
ticket:read ticket:write ticket:approve
execution:read execution:control
audit:read audit:export
notify:config system:config
```

内置角色映射：admin=全部；ops=cmdb:*、credential:read、template:*、ticket:read/write、execution:read/control(本人)；approver=cmdb:read、ticket:read/approve、execution:read；auditor=cmdb:read、execution:read、audit:*。
