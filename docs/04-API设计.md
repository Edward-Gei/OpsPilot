# OpsPilot API 设计

| 项目 | 内容 |
| --- | --- |
| 文档定位 | 当前路由与 Schema 参考 |
| Base URL | `/api/v1` |
| 最终契约 | 运行中的 OpenAPI：`/api/v1/openapi.json`；交互文档：`/api/v1/docs` |

本文描述当前接口；数据库管理见§14，权限共47项。实际运行契约以当前部署的OpenAPI为准，真实目标兼容性另按验收范围记录。

## 1. 通用约定

认证请求使用 `Authorization: Bearer <access_token>`。个人访问密钥 `opsp_` 也可作为 Bearer 凭证，但不能访问密钥管理接口。

成功响应：

```json
{"code": 0, "message": "ok", "data": {}}
```

失败响应沿用相同包裹结构，业务 code 与 HTTP 状态码同时表达错误。常见 code：`40001` 参数错误、`40101` 未认证、`40102` Token 过期、`40103` 需要 MFA、`40104` 需要绑定 MFA、`40105` 需要改密、`40301` 无权限、`40302` 对象越权、`40401` 不存在、`40901` 状态冲突、`42201` 业务拒绝、`42901` 限流/锁定、`50001` 内部错误。

除明确注明的根路径外，本文接口均相对于 `/api/v1`；应用配置章节还须加上该章节注明的模块前缀。

分页请求通常为 `page` 和 `page_size`，常用上限为 100，具体限制以接口 Schema 为准；分页数据为 `{items,total,page,page_size}`。时间使用带时区 ISO8601。

## 2. 认证和个人账号

### 2.1 `/auth`

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| POST | `/auth/login` | 公开 | `{username,password}`；返回正式令牌、`mfa_token` 或 `mfa_token + mfa_setup` |
| POST | `/auth/forgot-password` | 公开 | `{email}`；所有分支 HTTP 200 且公开文案一致 |
| GET | `/auth/reset-password/validate` | 公开 | `token` 查询参数；返回 `valid`、`expired`、`used` 或 `invalid` |
| POST | `/auth/reset-password` | 公开 | `{token,new_password}`；成功后吊销全部 Refresh Token |
| GET | `/auth/password-policy` | 公开 | 返回当前密码策略 |
| POST | `/auth/refresh` | 公开 | `{refresh_token}`；旋转并吊销旧 Refresh Token；不得延长首次登录截止时间，到点返回 40102 |
| POST | `/auth/logout` | 登录 | 吊销当前 Refresh jti |
| GET | `/auth/me` | 登录 | 当前用户、角色、权限和菜单信息 |
| PUT | `/auth/me` | 登录 | 修改本人显示名和邮箱 |
| POST | `/auth/mfa/setup` | 登录或 `mfa_token` | 生成 TOTP secret 和绑定 URI |
| POST | `/auth/mfa/bind` | 登录或 `mfa_token` | `{code}` 完成绑定 |
| POST | `/auth/mfa/verify` | `mfa_token` | `{code}` 换正式令牌 |
| PUT | `/auth/password` | 登录或 `change_token` | 修改本人密码；强制改密成功后直接返回正式令牌。不吊销 Refresh Token，既有访问令牌也在有效期内继续可用 |
| GET | `/auth/sso/oidc/login` | 公开 | 跳转 OIDC Provider |
| GET | `/auth/sso/oidc/callback` | 公开 | OIDC 回调并重定向到前端换票 |
| POST | `/auth/sso/exchange` | 公开 | `{ticket}` 换正式令牌 |
| GET | `/auth/sso/options` | 公开 | 返回 OIDC 和密码找回入口是否启用 |

密码重置只面向本地且绑定邮箱的用户。令牌随机生成、Redis 只保存哈希、默认 30 分钟、校验不消费、提交原子单次消费。审计动作严格为 `pwd_reset_requested`、`pwd_reset_success`、`pwd_reset_failed`，失败原因写入详情。

`token.policy` 只接受整数 `session_hours`（1-168，缺省 8）；Access Token 固定 15 分钟，Refresh Token 最长 7 天，两者有效期均受首次登录截止时间限制。新签发的 Access/Refresh JWT 都含 Unix 秒 `session_exp`，刷新沿用原截止时间，`expires_in` 为实际 Access TTL。旧数据库配置中的 `access_minutes` / `refresh_days` 不再生效，读取接口只返回 `session_hours`，缺失时回退 8 小时；新写入含旧字段返回 40001。无 `session_exp` 的旧正式令牌在部署新版本后的首次校验返回 40102，用户需重新登录。

### 2.2 `/user/tokens`

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| GET | `/user/tokens` | 登录 JWT | 本人密钥列表，不返回明文/哈希 |
| POST | `/user/tokens` | 登录 JWT | `{name,expires_in_days}`；明文只返回一次，每人最多 10 个 |
| DELETE | `/user/tokens/{id}` | 登录 JWT | 删除本人密钥 |

## 3. 用户和角色

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/users` | `user:read` | 分页；`keyword` 匹配用户名、显示名、角色名、来源、状态和 MFA 可见文案，另可筛选状态、来源 |
| POST | `/users` | `user:write` | 创建本地用户并分配角色 |
| PUT | `/users/{id}` | `user:write` | 更新资料、状态和角色，不能禁用自己 |
| PUT | `/users/{id}/password` | `user:write` | 管理员重置本地用户密码：写入新哈希、置首登强制改密并解除锁定；SSO 用户返回业务拒绝。不吊销该用户的 Refresh Token |
| PUT | `/users/{id}/mfa` | `user:mfa` | 管理员启用、禁用或重置 MFA；用户列表同时返回 `mfa_enabled`（是否要求动态码）和 `mfa_bound`（是否已有密钥），禁用只暂停校验并保留密钥 |
| GET | `/roles` | `role:read` | 角色、权限和成员数 |
| GET | `/roles/permissions` | `role:read` | 46 个权限点，见 §12 |
| GET | `/roles/options` | `template:read` | 角色 ID/名称轻量选项 |
| POST | `/roles` | `role:write` | 创建自定义角色 |
| PUT | `/roles/{id}` | `role:write` | 更新角色；admin 权限矩阵不可改 |
| DELETE | `/roles/{id}` | `role:write` | 删除非内置且未被用户引用的角色 |

## 4. CMDB

### 4.1 `/cmdb/hosts`

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/cmdb/hosts` | `cmdb:read` | 分页、可见业务列关键字、`host_id` 精确筛选、平台/区域/环境/状态筛选及列排序 |
| POST | `/cmdb/hosts` | `cmdb:write` | 新建主机，内网 IP 唯一 |
| GET | `/cmdb/hosts/{id}` | `cmdb:read` | 主机详情、关联应用及数据库实例元信息；不含实例连接地址或凭据 |
| PUT | `/cmdb/hosts/{id}` | `cmdb:write` | 编辑主机 |
| DELETE | `/cmdb/hosts/{id}` | `cmdb:delete` | 被应用关联时拒绝 |
| GET | `/cmdb/hosts/suggest` | `cmdb:read` | 平台/区域/主机系列补全 |
| GET | `/cmdb/hosts/import-template` | `cmdb:import` | 下载 Excel 模板 |
| POST | `/cmdb/hosts/import` | `cmdb:import` | 逐行校验、可选存在即更新 |
| GET | `/cmdb/hosts/export` | `cmdb:read` | 导出当前筛选（含 `host_id`），并沿用指定列排序 |

主机列表的 `sort_by` 可取 `hostname`、`ip`、`public_ip`、`project`、`ri`、`host_series`、`platform`、`region`、`os`、`spec`、`environment`、`status`、`ssh_port`、`created_at`；`sort_order` 为 `asc` 或 `desc`。排序先于分页，文本列（包括 IP）按数据库文本顺序，SSH 端口按数值；资源配置 `spec` 按 CPU、内存、磁盘数值依次比较，空值始终置后，同值按 ID 倒序。默认列表按 ID 倒序，默认导出按 ID 升序。关键词匹配除时间、操作外的显示业务列，包括资源配置的单位和缺失值占位文本及状态中文文案；列表和导出使用相同规则。`name_only=true` 保留原主机名/内网 IP 关键词范围，供全局搜索使用。

### 4.2 `/cmdb/apps`

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/cmdb/apps` | `cmdb:read` | 应用分页；关键词匹配显示业务列；可用 `app_id` 或已有所属系统名称精确筛选，并支持部署方式、项目类型、所属业务线和服务级别筛选及列排序 |
| GET | `/cmdb/apps/systems` | `cmdb:read` | 返回去重的已有非空所属系统名称，供应用列表下拉筛选 |
| POST | `/cmdb/apps` | `cmdb:write` | 创建应用和主机关联 |
| GET | `/cmdb/apps/{id}` | `cmdb:read` | 应用详情，含关联主机、配置文件元信息、关联工单模板（名称、流程模板、状态）及数据库实例（ID、名称、引擎、启用状态）；不含配置正文、数据库地址或凭据；目标页面另检查对应模块权限 |
| PUT | `/cmdb/apps/{id}` | `cmdb:write`；提交 `config_file_ids`、`ticket_template_ids`、`database_instance_ids` 分别另需 `config:write`、`template:write`、`database:write` | 编辑应用并全量替换主机关联；请求体省略 `host_ids` 时按默认空列表处理，等同解除全部关联，客户端须回读详情后提交完整列表。后三个可选关联字段分别全量替换配置文件、工单模板和数据库实例关联；省略保留，`[]` 清空，显式 `null`、重复、非正整数及不存在的 ID 拒绝。允许关联已归档配置文件和已停用工单模板；所有校验通过后同事务更新，不修改关联目标字段或实例查询授权 |
| DELETE | `/cmdb/apps/{id}` | `cmdb:delete` | 删除应用 |
| GET | `/cmdb/apps/export` | `cmdb:read` | 按当前筛选导出完整用户可见应用台账，支持 `app_id`、`system_name` 精确筛选 |

应用编辑页的关联候选分别复用 `GET /application-configs/files`、`GET /templates` 和 `GET /database/instances?scope=management`，需满足对应候选接口的读取条件；数据库候选可凭 `database:write` 读取。缺少对应写权限时仅回显已关联名称，不提交该关联字段；关联不授予目标查询权。

应用列表的 `sort_by` 可取 `name`、`language`、`deploy_type`、`project_type`、`host_count`、`business_line`、`system_name`、`service_level`、`ops_owner`、`dev_owner`、`service_port`、`cpu_quota`、`mem_quota`、`description`、`created_at`；`sort_order` 为 `asc` 或 `desc`。排序先于分页，关联主机数按数值排序，服务端口和配额作为文本排序；未指定时按应用 ID 倒序。关键词匹配除时间、操作外的显示业务列，包括“1 台”格式的关联主机数及前端/后端等显示文案；列表与导出共用匹配规则。`name_only=true` 保留原应用名、所属系统、运维负责人、开发负责人、服务端口的关键词范围，供全局搜索使用。

## 5. 凭据和作业主机

### 5.1 `/credentials`

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/credentials` | `credential:read` 或 `secret:read` | 分页；`keyword` 匹配可见凭据列，另可筛选类型；按权限仅返回 SSH 凭据或脚本密钥，密文既不返回也不参与检索 |
| POST | `/credentials` | SSH 类型需 `credential:write`；脚本密钥需 `secret:write` | 创建凭据，类型创建后不可修改 |
| PUT | `/credentials/{id}` | 同创建类型的写权限 | 更新；secret 为空表示保留原密文 |
| DELETE | `/credentials/{id}` | 同创建类型的删除权限 | 被作业主机、DNS Zone、配置平台实例、数据库实例或数据库活动/恢复任务引用时拒绝；脚本密钥被模板或活动工单引用时也拒绝 |

### 5.2 `/job-hosts`

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/job-hosts` | `job_host:read` | 分页；`keyword` 匹配可见业务列，最近测试仅匹配结果而不匹配时间；另可筛选启用状态 |
| POST | `/job-hosts` | `job_host:write` | 创建并绑定 SSH 凭据（拒绝脚本密钥） |
| GET | `/job-hosts/{id}` | `job_host:read` | 详情，不返回凭据密文 |
| PUT | `/job-hosts/{id}` | `job_host:write` | 编辑连接信息 |
| DELETE | `/job-hosts/{id}` | `job_host:delete` | 被工单模板引用时拒绝 |
| POST | `/job-hosts/{id}/test` | `job_host:write` | 执行 `echo ok` 连通性测试 |
| PUT | `/job-hosts/{id}/status` | `job_host:write` | 启用或停用 |

## 6. 域名管理

所有域名接口的前缀为 `/domains`。Zone 与记录读取只查询本地快照，不会隐式访问服务商；同步和记录变更才访问服务商。认证字段、服务商定位元数据和记录键不会出现在响应中。

### 6.1 凭据发现、Zone 绑定与台账

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/domains/credentials?provider=` | `domain:write` | 返回指定服务商兼容凭据的 ID、名称、认证类型和描述，不返回认证明文 |
| POST | `/domains/zones/discover` | `domain:write` | 使用 `{provider, credential_id}` 发现可访问的公网 Zone |
| POST | `/domains/zones` | `domain:write` | 创建 Zone 绑定任务并返回 `202`；请求含 `provider`、`credential_id` 和 `selections`，每个选择项可携带已发现的 `zone_name` |
| GET | `/domains/zone-bind-tasks/{task_id}` | `domain:read` | 查询绑定任务及每个 Zone 的处理进度与结果 |
| GET | `/domains/zones` | `domain:read` | 分页读取 Zone 台账，`keyword` 匹配非时间可见列；支持 `provider`、`sync_status` 筛选及列排序 |
| GET | `/domains/zones/{zone_id}` | `domain:read` | 查询单个 Zone 快照详情 |
| PUT | `/domains/zones/{zone_id}` | `domain:write` | 仅更新本地 `description` |
| DELETE | `/domains/zones/{zone_id}` | `domain:delete` | 解除本地绑定并删除本地记录快照，不删除远端 Zone 或记录 |
| POST | `/domains/zones/{zone_id}/sync` | `domain:write` | 手动刷新单个 Zone 的本地记录快照 |

`sort_by` 可取 `zone_name`、`provider`、`credential_name`、`description`、`record_count`、`sync_status`、`last_synced_at` 或 `last_sync_error`；`sort_order` 可取 `asc` 或 `desc`。未同时提供时按 Zone ID 倒序。

服务商取值为 `tencent_dnspod`、`aws_route53`、`google_cloud_dns`。Route 53 和 DNSPod 复用 `username_password` 凭据类型，Google Cloud DNS 复用 `secret_file` 凭据类型；服务端会拒绝服务商与凭据类型不匹配的组合。绑定请求只接受前一发现结果中的公网 Zone。创建任务后由 Worker 串行处理，每个 Zone 的已有记录快照和任务计数逐项持久化；首次绑定失败时会保留 `failed` 状态、记录数为零的 Zone 台账和错误摘要，已有快照保持不变。任务状态为 `queued`、`running`、`success`、`partial_failed` 或 `failed`，子项状态为 `pending`、`running`、`success`、`skipped` 或 `failed`。

### 6.2 DNS 记录集

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/domains/zones/{zone_id}/records` | `domain:read` | 分页读取本地记录快照，`keyword` 匹配记录名、类型、TTL、记录值、只读状态与原因；另可按类型和只读状态筛选 |
| POST | `/domains/zones/{zone_id}/records` | `domain:write` | 新增简单记录集：`owner_name`、`record_type`、`ttl`、`values` |
| PUT | `/domains/zones/{zone_id}/records/{record_id}` | `domain:write` | 更新已有记录集的 `ttl` 和 `values`，名称与类型不可修改 |
| DELETE | `/domains/zones/{zone_id}/records/{record_id}` | `domain:delete` | 删除可编辑记录集 |

可写类型为 `A`、`AAAA`、`CNAME`、`MX`、`TXT`、`CAA`、`SRV`，`ttl` 必须介于 300 与 86400。SOA、NS 和高级路由记录返回 `read_only=true` 与原因，不能更新或删除。更新、删除前服务端核对远端记录和快照，远端已漂移时返回 `40901`；DNS 服务商暂时不可用时返回 `50201`。若写入结果未知，Zone 快照会标为失败，用户需手动同步确认。前端在所有记录写入前展示变更前后预览并要求确认。

### 6.3 Excel 导入导出

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/domains/zones/import-template` | `domain:write` | 下载“Zone 导入”工作表模板 |
| POST | `/domains/zones/import` | `domain:write` | 上传 `.xlsx`，逐行返回成功数、跳过行和失败行 |
| GET | `/domains/zones/export` | `domain:read` | 按 Zone 列表筛选导出“Zone 台账”和“记录集”两个工作表 |

导入列为服务商、凭据名称、Zone 名称、远端 Zone ID 和描述；最大 5000 行。导出可包含凭据名称和 DNS 记录值，但不会包含凭据认证字段。

## 7. 模板和工单

### 7.1 `/process-templates`

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/process-templates` | `template:read` | 分页；`keyword` 匹配流程名、步骤数、引用数、执行策略与状态显示文案；`process_id` 按 ID 精确筛选；`name_only=true` 时仅匹配名称 |
| POST | `/process-templates` | `template:write` | 创建步骤、审批角色和执行策略 |
| GET | `/process-templates/{id}` | `template:read` | 流程及步骤详情 |
| PUT | `/process-templates/{id}` | `template:write` | 全量更新 |
| PUT | `/process-templates/{id}/status` | `template:write` | 启停 |
| DELETE | `/process-templates/{id}` | `template:delete` | 被工单模板引用时拒绝 |

### 7.2 `/templates`

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/templates` | `template:read` | 分页；`keyword` 匹配模板名、类型、作业主机、流程模板、可见角色与状态显示文案；`template_id` 按 ID 精确筛选；`name_only=true` 时仅匹配名称 |
| POST | `/templates` | `template:write` | 创建工单入口、参数定义和可选同模板并发控制 |
| GET | `/templates/{id}` | `template:read` | 入口详情和流程引用 |
| PUT | `/templates/{id}` | `template:write` | 全量更新；并发控制开关对后续提交立即生效 |
| PUT | `/templates/{id}/status` | `template:write` | 启停 |
| DELETE | `/templates/{id}` | `template:delete` | 有进行中工单时拒绝 |

`GET /templates` 的列表项返回 `job_host_name`、`process_template_name`，供列表直接展示关联名称，不受作业主机和流程模板候选的分页范围影响。应用、工单模板等页面的关联链接打开目标详情时，同时在目标列表搜索框回填名称并按 ID 精确展示该记录；用户修改搜索词后解除 ID 限制。

模板创建/更新可提交 `app_ids: [应用ID, ...]`，仅允许存在且不重复的正整数 ID；`[]` 解除全部绑定。创建时省略表示不绑定，更新时省略则保留原关联，以兼容旧客户端；显式 `null` 无效。创建非空绑定及更新显式提交 `app_ids` 时额外要求 `cmdb:read`，候选应用复用分页 `GET /cmdb/apps`。

`GET /templates/{id}` 额外返回 `app_ids` 和 `apps: [{id,name}]`；`GET /cmdb/apps/{id}` 额外返回 `ticket_templates: [{id,name,status,process_template_id,process_template_name}]`，流程模板已缺失时名称为 `null`。应用详情以“模板名称、流程模板、状态”三列表格展示关联工单模板，名称可跳转详情。双向关联仅提供名称等元信息，应用详情不会返回模板脚本或密钥引用。完整详情分别沿用 `template:read` 与 `cmdb:read` 权限。

### 7.3 `/tickets`

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/tickets/templates` | `ticket:write` | 返回启用且当前角色可见的模板 |
| GET | `/tickets/templates/{template_id}/form` | `ticket:write` | 参数表单和作业主机/步骤只读预览 |
| POST | `/tickets/prepare` | `ticket:write` | 执行动态参数脚本并生成短期 `prepare_id` |
| POST | `/tickets` | `ticket:write` | `{template_id,params,prepare_id?}` 提交工单；同模板并发控制开启且已有活跃执行时返回 `40901`、占用工单号和执行状态 |
| GET | `/tickets` | `ticket:read` | 分页、工单状态、创建人、关键字（工单号/标题/作业主机快照名/状态文案/提交人）、创建时间，以及最新执行实例的 `execution_status`、`has_execution`、`execution_active` 筛选；列表项包含可空 `execution` 摘要 |
| GET | `/tickets/todo` | `ticket:approve` | 当前角色待审批列表：服务端取出全部 `approving` 工单后逐单读取当前步骤快照角色过滤，再在内存分页；`total` 兼作菜单角标计数 |
| GET | `/tickets/todo/events` | `ticket:approve` | `since_seq`（整数，`>=0`，默认 `0`）回放当前用户的待办变更事件；无新事件最长等待 30 秒，返回 `{events:[{seq,kind:"todo.changed"}],last_seq}`，超时返回空 `events` 和原 `since_seq`。客户端必须直接赋值返回的 `last_seq`；若游标大于当前序号，返回一条合成 `todo.changed` 重同步事件和较小的 `last_seq` |
| GET | `/tickets/{id}` | `ticket:read` | 工单、快照、审批时间线和执行概要 |
| POST | `/tickets/{id}/approve` | `ticket:approve` | `{action:approve/reject,comment?}` |
| POST | `/tickets/{id}/cancel` | `ticket:write` | 创建人在 `approving` 或 `queued` 状态撤回 |
| POST | `/tickets/{id}/abort` | `execution:control` | 中止执行 |
| POST | `/tickets/{id}/pause` | `execution:control` | 暂停执行 |
| POST | `/tickets/{id}/resume` | `execution:control` | 恢复执行 |
| POST | `/tickets/{id}/force-abort` | `execution:force_control` | 强制中止并记录高风险审计 |

正式提交校验模板启用、可见角色、流程启用、参数定义和模板并发控制；提供 `prepare_id` 时才消费预生成结果。预生成入口的可见性、输入一致性及并发消费限制见[动态参数说明](./06-模板管理与流程模板设计.md#42-动态参数预生成)。步骤与策略读取工单快照，但作业主机连接信息、凭据和通知规则仍有运行时读取，见[快照范围](./06-模板管理与流程模板设计.md#5-工单快照)。

开启同模板并发控制时，首个执行从 `queued` 起持有模板直到成功、失败、中止、撤回、驳回或崩溃恢复归档；关闭开关不影响已持有者，但后续提交不再受该模板互斥限制。`GET /tickets` 中的 `execution` 是每张工单最新执行实例的 `{id,status,total_steps,started_at,finished_at}` 摘要；`has_execution=true` 仅返回已创建执行实例的工单，`execution_active=true` 匹配最新执行状态为 `queued`、`running` 或 `paused` 的工单。

## 8. 执行和实时日志

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/executions` | `execution:read` | 执行记录分页；工单号、状态、发起人和实际开始时间（`start/end`）筛选 |
| GET | `/executions/{id}` | `execution:read` | 执行详情和步骤状态 |
| GET | `/executions/{id}/logs` | `execution:read` | `step_order`、`offset`、`limit` 读取日志行 |
| GET | `/executions/{id}/events` | `execution:read` | 状态事件 REST 长轮询 |

日志保存在共享卷，事件用于低延迟刷新，前端按偏移增量读取正文。

前端工单中心是唯一的列表入口；不提供 `/executions` 列表路由。以上执行接口和 `/executions/{id}` 执行详情页继续用于步骤、日志和控制。

## 9. 通知

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/notify/channels` | `notify:read` | 渠道配置、渠道×事件模板元数据和变量元数据 |
| PUT | `/notify/channels/{type}` | `notify:write` | 更新启用状态、非敏感配置和 secret |
| POST | `/notify/channels/{type}/test` | `notify:test` | 按事件和当前未保存配置发送测试 |
| GET | `/notify/events` | `notify:read` | 六事件映射矩阵 |
| PUT | `/notify/events` | `notify:write` | `{mappings:{event:[channel_type]}}` 全量更新 |
| GET | `/notify/records` | `notify:read` | 按事件、渠道、状态和时间分页查询；返回项包含完整 `content` 正文。密码重置记录的正文保存含明文令牌的重置链接，令牌在有效期内核发即失效前对该权限可见，不能把此接口视为仅元数据列表 |

渠道模板存于 `config.templates[event]`：Email 为 `{title,content}`，Webhook 为 `{body}`，Teams 为 `{card}`。JSON 模板会在保存和渲染后解析；`{ref_id}` 和 `{receivers}` 可作为原生 number/array，也可作为字符串值使用。

站内信接口不要求独立权限，只能访问当前用户：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/notifications` | 本人分页站内信，可筛选未读 |
| GET | `/notifications/unread-count` | 本人未读数 |
| PUT | `/notifications/{id}/read` | 标记本人单条已读 |
| PUT | `/notifications/read-all` | 本人全部已读 |

应用配置复用上述站内信接口，新增以下事件与关联对象，不加入 `/notify/events` 的外部渠道映射：

| `event` | 触发节点与接收人 | `ref_type` / `ref_id` |
| --- | --- | --- |
| `config.pending_approval` | 候选提交后，通知冻结审批角色的当前有效成员 | `config_approval` / 候选版本 ID |
| `config.approved`、`config.rejected` | 审批通过或驳回后，通知修改提交人；驳回通知附带审批原因 | `config_file` / 配置文件 ID |
| `config.publish_success`、`config.publish_failed` | 发布任务最终成功或失败后，通知修改提交人和本次任务发起人并去重 | `config_file` / 配置文件 ID |
| `config.sync_success`、`config.sync_failed` | 手动同步任务最终成功或失败后，仅通知本次同步任务发起人 | `config_file` / 配置文件 ID |

`config_approval` 通知点击进入 `/ticket/todo?tab=config`，`config_file` 通知进入 `/application-configs/{file_id}`；目标接口和页面继续校验当前审批身份或 `config:read`。发布结果包括自动发布、人工重试和确认重新发布；排队、执行及自动重试中不产生结果通知，接入不产生这些事件。同步读取成功时，`config.sync_success` 正文说明与基线一致、检测到漂移或远端配置不存在；漂移或远端缺失不改变同步成功的任务结果，`config.sync_failed` 附带安全的失败提示。系统不自动附带配置正文或凭据，也不生成外部渠道发送记录。

铃铛列表悬停消息时展示完整 `title` 和 `content`，正文为空时只展示标题；悬停不调用已读接口或触发导航。

## 10. 审计

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/audit/logs` | `audit:read` | 时间、操作人模糊匹配及模块/动作/结果精确筛选可组合；`keyword` 模糊匹配非时间可见列（操作人、来源 IP、模块文案、动作、对象和结果文案） |
| GET | `/audit/logs/export` | `audit:export` | 当前筛选导出 CSV 或 XLSX，最多 100000 行；导出行为写审计 |

安全审计页面的模块列表、详情及筛选使用中文名称，筛选值仍提交原始模块编码。当前写入模块为 `auth`（认证）、`user`（用户）、`cmdb`（资产）、`job`（作业管理）、`domain`（域名管理）、`config`（应用配置）、`ticket`（工单）、`execution`（执行）、`notify`（通知）、`system`（系统）、`audit`（审计）；筛选兼容 `role`（角色）、`credential`（凭据）、`template`（模板）历史编码。

审计列表与导出共用 `keyword` 筛选语义；对象列显示名称时不按隐藏的对象 ID 命中。原有仅匹配对象名称的 `keyword` 语义已扩展。

## 11. 系统、工作台和搜索

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/system/configs` | `system:config` | 读取配置，敏感字段掩码；返回键为预置键叠加 `system_config` 表中实际存在的行，含种子写入的内部标记 `notify.mapping_initialized`，不能假定响应只含设置页可编辑的键 |
| PUT | `/system/configs` | `system:config` | 批量更新预置配置，掩码值保留原 secret |
| GET | `/healthz` | 公开 | 根路径，不加 `/api/v1`；DB/Redis 健康检查 |
| GET | `/ping` | 公开 | API 存活检查 |
| GET | `/dashboard/summary` | 登录 | 按权限返回概览各段 |
| GET | `/dashboard/ticket-trend` | `ticket:read` | 日/周/月/年提单趋势 |
| GET | `/dashboard/activity-trend` | `ticket:read` 或 `execution:read` | 工单提交与完成执行双系列趋势；无对应权限的系列为 `null` |
| GET | `/search?keyword=` | 登录 | 按权限裁剪的主机、应用、Zone、工单和模板聚合搜索；保留原模块关键词范围，Zone 仅搜索名称、描述和服务商，不搜索 DNS 记录值 |

工作台展示主机、应用、托管 Zone、有效配置文件、本月工单和个人待办六项指标，以及工单与执行趋势、应用配置状态、主机环境与状态、应用部署方式、域名资源与同步、工单状态、DNS 记录类型、今日审计模块八类图表；各项按当前用户读取权限展示。主机状态来自 CMDB 登记值，域名与配置状态来自本地最近同步结果，不代表实时监控。

`GET /dashboard/summary` 保留原字段，并按权限新增以下统计：

| 字段 | 权限 | 内容与口径 |
| --- | --- | --- |
| `cmdb.host_environment_status` | `cmdb:read` | `[{environment,status,count}]`，按主机环境与登记状态计数 |
| `cmdb.app_deploy_type` | `cmdb:read` | `{部署方式:应用数量}` |
| `domain` | `domain:read` | `zone_total`、`record_total`、`provider_dist`、`sync_status`、`record_type_dist`；记录总量与类型均按有效 Zone 关联的本地记录集行计数，不按单条记录的值数量计数 |
| `config` | `config:read` | `file_total`、`status_dist`；排除已归档，先按 `drifted`、`remote_missing`、`sync_failed` 分类，其余按正式版本是否存在区分 `clean` 与 `unpublished`，五类互斥且合计等于文件总量 |
| `audit` | `audit:read` | `today_total`、`result_dist`、`module_dist`，按今日零点至当前时间统计 |
| `todo_breakdown` | 当前用户待办 | `{ticket,config}`，沿用个人工单与配置审批待办口径 |
| `attention` | 各对象的读取权限 | 最多六条 `[{module,id,name,status,updated_at}]`，包含未归档配置的漂移/远端缺失/同步失败、域名同步失败、执行失败/中断，合并按时间倒序；执行时间取完成时间，缺失时取创建时间；仅返回名称和状态等元信息 |

无读取权限的统计段为 `null`，关注事项不包含无权限模块。`activity-trend` 接受 `granularity=day|week|month|year`（默认 `day`），分别返回近 30 天、12 周、12 月、5 年的自然时间桶并补零，结构为 `{granularity,items:[{period,tickets,executions}]}`。工单按创建时间计数；完成执行只统计 `success/failed/terminated/interrupted` 且完成时间非空的记录，按完成时间计数；未来时间不计入。两项读取权限均无时返回 403；旧 `ticket-trend` 的响应结构保留。

## 12. 权限点全集

当前共 46 个权限点，事实来源为 `backend/app/core/constants.py` 的 `PERMISSIONS`。

```text
user:read user:write user:mfa role:read role:write
cmdb:read cmdb:write cmdb:delete cmdb:import
domain:read domain:write domain:delete
config:read config:write config:delete config:instance
credential:read credential:write credential:delete
secret:read secret:write secret:delete
template:read template:write template:delete
job_host:read job_host:write job_host:delete
ticket:read ticket:write ticket:approve
execution:read execution:control execution:force_control
audit:read audit:export
notify:read notify:write notify:test system:config
database:read database:write database:import database:delete database:query database:grant database:audit
```

## 13. 应用配置 `/application-configs`

本节表内路径相对于 `/api/v1/application-configs`，例如实例列表完整路径为 `/api/v1/application-configs/platform-instances`。

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET/POST | `/platform-instances` | `config:read` / `config:instance` | 实例列表、创建；凭据只返回 ID，不返回密钥 |
| PUT/DELETE | `/platform-instances/{id}` | `config:instance` | 修改实例；仅已停用且无引用可删除 |
| POST | `/platform-instances/{id}/probe` | `config:instance` | 连通性测试 |
| GET | `/platform-instances/{id}/namespaces` | `config:write` | 读取 Nacos 命名空间选项，仅返回名称与 ID；默认 `public` 的 ID 为空字符串 |
| GET | `/compatible-credentials` | `config:instance` | 按平台筛选凭据名称和认证类型 |
| GET | `/approval-roles`、`/cmdb-applications` | `config:read` | 角色与关联应用选项，不授予审批权；应用选项返回全量名称/ID，支持 `keyword` 按名称筛选 |
| GET | `/approval-todo` | 已登录用户 | 分页返回当前审批角色成员可处理的候选版本；admin 可见所有待审批候选，无 `ticket:approve` 或 `config:read` 要求 |
| GET | `/approval-todo/{version_id}/content` | 当前审批角色成员或 admin | 返回候选及冻结旧正式版本的差异对照；双方按 `secret:read` 脱敏，无权或候选已处理时返回 404 |
| POST | `/discover`、`/import-tasks` | `config:write` | 发现远端资源、创建多选接入任务；任务返回 `202` |
| GET | `/import-tasks` | `config:read` | 最近 20 项接入任务及逐项结果 |
| GET/POST | `/files` | `config:read` / `config:write` | 分页列表（可用 `file_id` 精确筛选）、手工新建本地配置与草稿 |
| GET/PUT | `/files/{id}` | `config:read` / `config:write` | 详情、名称、审批角色与 CMDB 多对多关联 |
| POST/DELETE | `/files/{id}/archive`、`/files/{id}` | `config:delete` | 先归档，再删除本地记录；永不删除远端 |
| GET/PUT/DELETE | `/files/{id}/draft` | `config:read` / `config:write` | 读取、保存、丢弃草稿 |
| GET | `/files/{id}/versions`、`/files/{id}/versions/{version_id}/content` | `config:read` | 版本与按权限脱敏的正文 |
| GET | `/files/{id}/versions/{version_id}/comparison` | `config:read` | 读取版本生成时旧正式版本与所选版本的只读对照；双方按 `secret:read` 脱敏 |
| POST | `/files/{id}/candidates` | `config:write` | 冻结草稿并提交审批 |
| POST | `/files/{id}/candidates/{version_id}/approve`、`/reject` | 当前审批角色成员或 admin | 批准时同事务创建远端发布任务，驳回则不发布；提交人可自审，无 `config:approve` 或 `config:write` 要求 |
| POST | `/files/{id}/sync`、`/files/{id}/versions/{version_id}/publish` | `config:write` | 创建手动同步任务；指定版本发布用于失败后人工重试，漂移弹窗重新发布时请求体传 `confirmed_snapshot_id`，返回 `202` |
| GET/POST | `/files/{id}/drift`、`/files/{id}/drift/import` | `config:read` / `config:write` | 双栏脱敏快照包含 `latest_snapshot_id`；导入仅创建内部正式版本，不写外部 |
| GET | `/tasks/{id}`、`/files/{id}/tasks` | `config:read` | 查询任务逐项进度、单文件最近 50 项任务摘要 |

`GET /files` 支持 `keyword` 匹配配置文件名、平台实例、远端定位值、格式、关联应用数、审批角色、当前版本和状态显示文案，不检索配置正文；另可用 `file_id` 按 ID 精确筛选、`platform_instance_id` 筛选实例、`status` 筛选标签（`clean` 一致、`drifted` 存在漂移、`remote_missing` 远端缺失、`sync_failed` 同步失败、`unpublished` 待首次发布、`archived` 已归档）。筛选可组合，计数和分页均基于筛选结果；归档优先于漂移状态，一致仅包含已有正式版本的活跃文件。`sort_by=last_synced_at` 配合 `sort_order=asc|desc` 按最近同步时间排序，未同步记录始终置后，同时间按 ID 降序；未指定排序字段时保持 ID 降序。应用详情中的关联配置文件进入配置文件列表，搜索框显示文件名且仅列出该文件，不自动进入查看详情。

文件格式限定 `properties`、YAML、JSON、TEXT 和 Consul KV；仅生产环境。正文响应基于 `secret:read` 脱敏，敏感键、服务账号 JSON 字符串和带签名/令牌参数的 URL 用占位符隐藏；无密钥权限的编辑只能保留这些既有值。任务错误和审计不包含正文与凭据。
版本比较响应包含 `version_id`、`version_no`、`content_format`、`content`、`base_version_id`、`base_version_no`、`base_content` 和 `has_changes`。审批正文接口额外保留 `file_id`、`file_name`。生成候选或导入漂移时冻结当前正式版本作为基准，跳过未生效候选；无旧正式版本时基准 ID/版本号为空、正文为空字符串。双方保留注释，`has_changes` 根据原始正文判断，敏感值脱敏后内容相同仍提示存在隐藏变更。历史版本不会随后续发布切换基准。旧数据由迁移 `0024` 按现存发布时间和版本号回填；此前重新发布已覆盖的发布时间无法完整还原，因此旧记录的回填仅使用仍留存的时间信息。

版本列表额外返回 `approval_role_name`、`submitter_name`、`approver_name`、`submitted_at`、`approved_at`、`completed_at` 和 `failure_reason`。用户名称优先显示名、其次用户名；角色或用户已不存在时名称为 null。`submitted_at` 为 OpsPilot 候选创建时间，外部导入为空；`completed_at` 优先取最近发布任务结束时间，无任务时回退正式版本发布时间或驳回时间。`failure_reason` 取最近发布失败原因或驳回原因，最近任务正在运行或成功时为空。此接口仍只需 `config:read`，不要求用户或角色管理权限。
审批通过仅表示发布任务已入队；Worker 写入并回读一致后版本才变为正式版本。远端暂不可用时首次执行后最多自动重试 3 次（间隔 2、4、8 秒）；写入结果不确定时只重试回读，不重复写入，普通人工重试也继承这一限制。明确确认漂移覆盖时，`confirmed_snapshot_id` 必须是当前文件最近一次同步的漂移/远端缺失快照，任务将它持久化为本次写入基线；Worker 执行时再次核对文件仍处于该漂移状态，远端正文在确认后再次变化也停止写入。此确认是新的人工发布操作，不沿用旧任务的不确定写入标记。远端漂移或确定性拒绝不自动重试，最终失败的已批准版本可人工重试。过期任务或丧失配置文件操作锁的 Worker 不得继续写入。
工作台 `/dashboard/summary` 的 `todo_total` 为工单审批与配置审批待办之和；无工单审批权限的用户只计算其配置待办。前端“待办审批”分为工单审批与配置审批页签；配置文件详情只展示版本记录，审批统一在待办页处理。

## 14. 数据库管理

路径相对于 `/api/v1/database`，成功沿用 code/message/data，分页为 items/total/page/page_size，选项为 items 数组。实现位于 `api/v1/database_management.py` 和 `schemas/database_management.py`；需求、模型与验收见[PRD §8](./01-产品需求文档PRD.md#8-数据库管理)、[数据库 §11](./03-数据库设计.md#11-数据库管理)、[开发验收 §5](./05-开发与验收指南.md#5-数据库管理开发与验收)。

### 14.1 权限与范围

| 场景 | 检查 |
| --- | --- |
| management范围 | database:read/write/delete/grant 任一；不自动授予查询 |
| 台账 Excel | 模板与导入要求 database:import 和 database:write；导出要求 database:read，不包含凭据及结果集 |
| query范围 | database:query 加多角色显式实例授权并集，管理员同样需要grant；工作台固定scope=query |
| sql/mongo_read/redis_read/metadata | 提交和访问目标前复查用户有效、实例启用、database:query及实例授权 |
| probe | database:write，不要求查询grant，仅连接/认证和固定SELECT 1或ping；不设置或校验只读，不执行用户操作或读取业务行 |
| 本人任务/历史 | owner固定本人；probe需write，其他需query，审计员/admin无跨用户任务后门 |
| 保存结果 | owner、ready、有效期；数据/metadata还检查当前启用状态和实例grant，probe本人凭write仅读安全摘要 |
| 独立审计 | database:audit看全员操作，不包含结果集/凭据，不提供他人结果入口 |

平台授权到实例，绑定只读账号限制实例内部范围，应用/主机/负责人关系不推导权限。功能权限不互相继承；取得数据库查询能力不等于取得 secret:read。

### 14.2 路由与关联

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `/instances` | scope=management或query，默认management；page默认1/page_size默认20最大100；keyword、engine、business_line、enabled、cluster_name（最长128字符）、probe_status、instance_id、app_id、host_id可组合，无environment筛选参数；支持sort_by/sort_order，分页响应额外返回cluster_names候选 |
| GET | `/instances/import-template` | database:import 和 database:write；下载仅含安全台账字段的 Excel 模板 |
| POST | `/instances/import?upsert=false` | database:import 和 database:write；上传 Excel，按名称创建或在 upsert=true 时更新既有实例，返回逐行结果 |
| GET | `/instances/export` | database:read；按当前列表筛选导出安全台账字段，不含凭据、授权、关联、检测或查询结果 |
| POST | `/instances` | database:write；InstanceWrite，返回{id}；不能混入role_ids |
| GET | `/instances/{id}` | 管理权限或当前显式查询范围，返回安全InstanceDetail；详情可显示停用实例，查询时仍拒绝停用 |
| PUT | `/instances/{id}` | database:write；完整InstanceWrite，可选app_ids/host_ids省略保持；返回null |
| DELETE | `/instances/{id}` | database:delete；已停用且无queued/running/stopping/recovering任务，清三关联后删除；返回null |
| GET/PUT | `/instances/{id}/roles` | database:grant；GET items:[{id,name}]，PUT {role_ids:[...]}全量替换，[]清空 |
| GET | `/role-options` | database:grant，items:[{id,name}]；不依赖template:read |
| GET | `/compatible-credentials` | database:write；全部username_password安全候选，items:[{id,name,login_user,auth_type}]，无口令/密文 |
| GET | `/client-policy` | database:query；四项不带database.前缀的执行/行/字节/TTL运行限制，不读取完整系统设置 |
| POST | `/tasks` | 严格kind判别请求；202返回task_id/status/executed_statement/operation_summary，不表示已成功 |
| GET | `/tasks` | 本人历史；page/page_size默认1/20最大100；keyword最长255字符，另有instance_id/engine/status/kind/started_from/started_to；服务端组合筛选、计数后分页，时间按首次提交created_at筛选 |
| GET | `/tasks/{id}` | 本人TaskView；异步失败仍200，由status/error_code表达 |
| POST | `/tasks/{id}/cancel` | 本人幂等登记，返回TaskView；不等待目标停止，终态不重复取消 |
| GET | `/tasks/{id}/result` | 本人、当前权限及TTL；page默认1/page_size默认50最大100，只读保存结果，不连接目标 |
| GET | `/audit/logs` | database:audit；分页默认1/20最大100；actor_id/keyword/source_ip/instance_id/engine/database/kind/status/started_from/started_to；keyword匹配实例和操作者，时间取首次提交事件 |
| GET | `/audit/logs/{task_id}` | database:audit；按独立事件组task_id读取详情/events，无结果或导出 |

`GET /instances`的keyword匹配非时间展示列、关联名称、单个状态词及完整状态/检测组合词（如“启用/成功”“停用/失败”），支持成功/失败/未检测并保留连接正常/连接失败旧文案；还包含host、十进制port和组合地址host:port（IPv6为[host]:port），不搜索其余隐藏连接参数或秘密；关联采用EXISTS，筛选计数后分页，不重复实例。cluster_name按完整名称等值匹配；响应cluster_names:string[]为同一授权scope全部可见实例的非空集群名，去重排序，不受搜索、其他筛选、关联ID或分页限制，query范围不得包含未授权集群名。probe_status为untested/succeeded/failed，保留API兼容，台账下拉改用集群名称。批量删除仅前端串行复用1～100个明确ID的单删请求，无batch API；逐项反馈且不自动重试网络未知结果。

Excel 列为实例名称、引擎、连接地址、端口、环境、描述、业务线、集群名称、集群角色、版本。导入结果包含 `success_count`、`created_count`、`updated_count` 和 `failed_rows:[{row,reason}]`；失败行不影响其他有效行。新实例无凭据且默认停用，编辑页补齐凭据后才能启用；已有实例更新要求引擎不变，只修改表内其他字段，保留凭据、查询角色、应用/主机关联、最近检测、连接选项和启停状态。地址或端口变化递增连接版本。导出使用与列表一致的筛选语义，但仅输出上述十项安全字段。实例台账导出不改变工作台查询结果禁止导出的规则。

`GET /tasks`的keyword去除首尾空白后进行字面包含匹配，空白等同未传；百分号、下划线和反斜线不作为通配符。范围为本人列表可见的非时间戳字段：带#前缀的操作编号、实例名、引擎与上下文、操作摘要与类型、中文状态、格式化执行耗时及结果摘要；结果摘要包括当前可见的条数/大小、截断及可用性标签、连接测试安全消息。条数使用en-US千位分隔，大小沿用B/KiB/MiB显示，耗时保留三位小数秒；结果已过期或不可用时不搜索界面已隐藏的数量。提交及到期时间戳、SQL全文、隐藏连接/凭据快照、业务结果块均不参与检索。活动耗时与到期判断在一次请求中使用同一服务端时刻，筛选及total计算后按ID倒序分页，不扩大owner、当前query/write类型权限和历史保留范围。接口保留instance_id筛选；本人历史页面改用keyword和单个时间范围控件，不改变独立审计keyword的匹配范围。

`sort_by` 可取 `name`、`description`、`engine`、`business_line`、`cluster_name`、`cluster_role`、`version`、`address`、`apps`、`hosts`、`status`、`created_at`；`sort_order` 为 `asc` 或 `desc`，默认 `asc`。未指定 `sort_by` 时按实例 ID 倒序。排序在筛选后、分页前完成，同值按 ID 倒序，空值或空关联始终置后；文本按数据库文本顺序，地址依次比较 host 和数值 port，关联应用/主机分别取最小应用名/主机名，状态依次比较 enabled 和 last_probe_status。无效排序键或方向由请求校验拒绝，不接受任意字段。

实例app_ids/host_ids和应用database_instance_ids省略保持、[]清空，null/重复/非正/不存在ID拒绝（最多1000）。实例侧提交关系还需cmdb:read；应用侧修改需cmdb:write+database:write，无权只读回显并省略字段；保留现有host_ids全量替换语义。仅改关系，不改对象本身。

前端已有实例编辑入口及旧 `/database/instances/:id/edit` 路由接受 `database:write` 或 `database:grant` 任一；新建仍需 `database:write`。编辑弹窗使用单个“保存”，按实际改动调用现有实例与角色接口；实例内容保存仍检查 `database:write`，角色读取、候选与保存仍检查 `database:grant`。两个请求不组成原子操作，实例成功而授权失败时保留部分成功状态，重试只提交授权。

角色有变化时，前端在提交前重读当前集合；已等于本次选择则不重复写入，与加载基线及本次选择均不同时要求重新加载确认。该检查不包含服务端版本条件，GET 与 PUT 之间的并发更新仍可能互相覆盖。

创建 POST 已发出但未收到 HTTP 响应时（如网络中断或请求超时），客户端禁止在当前弹窗再次创建，刷新台账并提示人工按名称核对；若已有记录，从其编辑入口继续维护授权。明确返回的 HTTP 或业务错误允许修正后重试；无响应时不自动重发 POST 或恢复创建 ID，现有接口不提供幂等创建保证。

凭据复用username_password；非Redis必须有login_user，Redis区分password_only和acl。删除被实例/活动恢复任务引用的凭据拒绝；删除应用/主机/角色只清关系，不删另一端实例、历史及审计。实例登记信息、说明和业务线变化不递增连接版本；engine/host/port/credential_id/connection_options变化递增config_revision。

### 14.3 严格请求与响应

所有请求extra=forbid，ID严格正整数，bool不冒充整数。地址只允许主机名/IP，禁止URI、认证材料、路径和空白；用户不能透传任意驱动参数或覆盖平台约束。

| DTO | 字段与约束 |
| --- | --- |
| InstanceWrite | name1～128、engine五引擎枚举、environment=demo/stage/prod（默认demo）、description≤255、enabled严格bool、host≤255、port1～65535、credential_id可空（仅停用的待配置实例）；business_line=mitrade/tradingkey或null、cluster_name/version≤128、cluster_role≤64；connection_options；concurrency_limit可空2～100；可选app_ids/host_ids。无system_name/ops_owner/dev_owner |
| connection_options | tls=false、tls_verify=true；SQL可default_database，PG另default_schema；Mongo auth_database默认admin及default_database，固定directConnection不接受发现选项；Redis database0～15默认0、auth_mode=acl/password_only默认acl；不适用的选项拒绝 |
| InstanceItem/Detail | Item含id/name/description/engine/business_line/cluster_name/cluster_role/version/host/port/enabled/needs_configuration/created_at/updated_at、apps[{id,name}]、hosts[{id,hostname,ip}]、last_probe_*；`needs_configuration` 表示未绑定凭据。Detail追加environment/可空credential_id、credential安全摘要、connection_options/config_revision/concurrency_limit、app_ids/host_ids。详情仅在具备cmdb:read时额外返回apps[].language/deploy_type与hosts[].environment，无权时省略这些键；列表不增加这些CMDB字段 |
| SqlTaskRequest | kind=sql、instance_id、context:{database?,schema?}、完整sql；schema仅PG适用 |
| ProbeTaskRequest | kind=probe、instance_id，不接受任意context/命令；仅固定SELECT 1或ping验证连接与认证，不设置或校验只读 |
| MetadataTaskRequest | kind=metadata、instance_id、context:{database?,schema?,table?,collection?}、operation=databases/schemas/tables/columns/collections/fields，组合按引擎校验 |
| MongoReadTaskRequest | kind=mongo_read、instance_id、context:{database,collection}、operation对象以type判别find/count_documents/distinct |
| Mongo operation | find:{type,filter={},projection?,sort:[{field,direction:1或-1}],limit=1000}；count_documents:{type,filter={}}；distinct:{type,field,filter={},limit=1000}；limit1～10000，sort最多16项，投影最多100个包含/排除字段，筛选另受语义深度/字节白名单限制 |
| RedisReadTaskRequest | kind=redis_read、instance_id、context:{database:0～15}、operation以type判别；scan:{pattern=*,cursor?,count=100}；type/ttl/length:{key}；string_preview:{key,offset=0,length=4096}；hash/set分页用cursor、list/zset用offset；count1～1000，预览length1～65536 |
| TaskSubmission | task_id、status=queued/rejected、executed_statement或null、operation_summary；拒绝额外reason，服务层http_status/error_code不作为普通成功data字段 |
| TaskView | task_id/kind/status/instance_id/instance_name/engine/context、submitted_text/executed_statement/operation_summary、created_at/started_at/finished_at/expires_at、stop_reason/error_code/error_message/stop_confirmation、result_summary/policy_snapshot及安全target_snapshot/credential_snapshot |
| ResultPage | columns[{ordinal,name,type}]、二维rows、returned_rows/returned_bytes/truncated/truncate_reason/expires_at/saved_total/page/page_size；returned_*为整份保存结果，saved_total不是目标表总数 |
| Audit | 列表按task_id汇总首次身份/提交时间与最新状态/终态摘要；详情另含上限内原文/实际操作、安全快照、submitted_bytes/submitted_sha256和events[{id,event_type,created_at,validation,stop_confirmation,error_code,error_message,result_summary}]，不查结果块 |

新probe的operation_summary为“连接状态检测”，成功更新last_probe_message为“连接检测成功”，适配器probe的summary.message为“连接和认证成功”。probe 认领后的执行预算固定5秒，与系统设置中的查询执行上限独立；排队时限仍单独计算。超时或连接失败且本地连接关闭已确认时，返回timed_out/failed终态；关闭事实无法确认时保留recovering与额度，前端停止本行加载并提示核对。这些摘要只描述连接/认证，不声明账号只读或远端停止已确认；既有历史记录和旧检测摘要不回填或清洗。SQL、metadata和原生数据查询的只读策略保持。

特殊值使用 `{"$type":"int|decimal|datetime|date|time|bytes|float","value":"..."}`；超JS安全整数标int、bytes为base64、非有限float为NaN/Infinity/-Infinity；其余JSON类型保留原值，前端纯文本展示。重名列按ordinal保序，不以列名对象覆盖。

MySQL/PG/ClickHouse结构入口当前提供库/Schema/表/列；Mongo提供库/集合和采样字段，标记推断；不承诺索引、约束、集合校验规则或完整拓扑清单。元数据只取账号可见对象；Redis DB编号不构成平台库级授权边界。

### 14.4 只读输入与拒绝留痕

SQL上限65536 UTF-8字节。按方言解析全部顶层语句，全部通过只读AST/函数白名单后才执行最后完整SELECT；字符串/注释/跨行不错误切分。混合写/SET/DDL、语法破损、未知节点/函数、写CTE/INTO/锁定/文件输出/执行注释/外部源和覆盖资源设置均整段拒绝，不降级执行原文或前一条。

Mongo不提供任意aggregate/runCommand/JavaScript；受控find/count和内部group+limit实现distinct，不先取得完整distinct数组。有效筛选最多16KiB、深度10、数组1000项，操作符采用白名单；写入和代码能力拒绝。Redis固定GETRANGE/SCAN/HSCAN/LRANGE/SSCAN/ZRANGE等路径，RESP2在bulk/array分配前限制字节/项目/深度；禁止任意命令、脚本、KEYS/CONFIG和全量大值再裁剪。游标10分钟HMAC绑定上下文，不授予权限。

认证/对象权限及严格外壳通过后，SQL和原生安全语义拒绝持久化rejected任务及独立审计，且无目标调用；超长SQL只留长度/hash和正文未保存原因。缺少字段、错误类型、未知kind等malformed envelope为普通422参数错误，不能据此假定生成了任务。未知/未授权对象的错误不得泄露目标信息。

### 14.5 状态、响应与错误

状态为queued/running/stopping/recovering/succeeded/failed/cancelled/timed_out/rejected/queue_expired。queued取消、过期或启动前复查失败均未执行；running→rejected仅允许尚未发出任何目标操作。真正访问前复查用户、按kind权限、实例启用及config_revision/credential_id/updated_at/login_user；变化记instance_changed，须人工重提。

取消或执行时限到达进入stopping，可信停止后才cancelled/timed_out；执行器失联、租约过期或停止/清理未确认进入recovering，不重放、不无证据释放目标占用。查询结束与本地句柄关闭是两个事实，close不能充当目标终止证明。成功要求有界结果ready且可信结束。

| 情况 | HTTP / code / data |
| --- | --- |
| 受理 | 202 / 0 / task_id、queued、实际操作摘要 |
| 已授权语义拒绝或队列满 | 422 / 42201 / task_id、rejected、reason；先提交拒绝事实，不能因响应错误回滚 |
| malformed请求外壳 | 422 / 40001 / null，沿用参数校验响应 |
| 异步失败详情 | 200 / 0，查看status/error_code/安全原因 |
| 结果未ready | 409 / 40901，无半成品 |
| 结果已过期 | 410 / 40901 / reason=result_expired；物理删除后仍按保留摘要识别 |
| 功能权限/对象范围/不存在 | 40301/40302/40401沿用既有协议；停用实例为422业务拒绝 |

执行总时限从认领开始处理覆盖连接、操作和读取，排队另计；新开始任务快照策略，运行任务不改变既定截止时间。短请求轮询不受单次查询60秒影响，浏览器断开或离页不等于取消。

### 14.6 系统配置合同

数据库配置存储于system_config，经/system/configs和system:config管理；/client-policy只公开execution_timeout_seconds/max_result_rows/max_result_bytes/result_ttl_minutes四键。只接受整数，拒绝bool/浮点；先合并校验整批再同事务写入。

| 键（均以database.为前缀） | 默认 | 范围 |
| --- | ---: | --- |
| execution_timeout_seconds | 60 | 1～600秒 |
| max_result_rows | 1000 | 1～10000条 |
| max_result_bytes | 5242880 | 65536～16777216字节 |
| result_ttl_minutes | 30 | 1～1440分钟 |
| history_retention_days | 30 | 1～365天 |
| audit_retention_days | 90 | 1～1095天 |
| global_concurrency | 20 | 2～200 |
| instance_concurrency_default | 5 | 2～50 |
| user_concurrency | 2 | 1～20 |
| queue_max_tasks | 200 | 1～10000 |
| user_queue_max_tasks | 10 | 1～1000 |
| queue_timeout_seconds | 120 | 1～3600秒 |
| metadata_reserved_concurrency | 2 | 1～199 |
| result_storage_budget_bytes | 1073741824 | 33554432～10737418240字节 |

全局并发必须大于元数据保留值，用户排队不超过平台排队；密文预算至少容纳 `2*max_result_bytes+65536`。实例并发覆盖值2～100。首四业务值在开始时快照，TTL在完成时计算；历史/审计清理读取当前周期，二者不要求大小关系，不改变通用审计365天默认值。

输出计量为加密前canonical UTF-8 `{columns,rows}`（ensure_ascii=False、紧凑分隔、allow_nan=False），摘要不置于包内，避免计量自指。加密manifest/rows-only chunks完整后原子ready，预留上界与1GiB载荷预算不等于MySQL物理磁盘上限。结果/历史/审计独立清理，活动恢复证据不因普通保留期删除；详细生命周期与物理连接计量见[架构 §14](./02-技术架构设计.md#14-数据库管理)。
