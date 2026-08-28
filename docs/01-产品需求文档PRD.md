# OpsPilot V1 产品需求文档

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.0 |
| 状态 | 竣工，与当前代码同步 |
| 产品名称 | OpsPilot 自动化运维平台 |
| 部署形态 | 单机 Docker Compose，适用于企业内网 |
| 目标读者 | 研发、测试、运维、项目干系人 |

本文只描述当前实现的产品范围和行为。接口、字段和状态以代码及自动生成的 OpenAPI 为准。

## 1. 产品概述

OpsPilot 围绕“工单驱动的作业执行”提供 CMDB 资产台账、作业主机、模板化流程、审批、执行控制、实时日志、通知和审计。

核心链路：

```text
CMDB 维护主机/应用资产
  -> 作业主机绑定 SSH 凭据
  -> 流程模板定义串行 Shell/Playbook 步骤、策略和步骤前审批
  -> 工单模板定义提交入口、参数、动态生成、通知和可见角色
  -> 提单人选择启用模板并填写参数
  -> 服务端生成作业主机/流程/参数快照
  -> 审批通过后 Worker 在作业主机 workspace 串行执行
  -> 日志、通知和审计记录全过程
```

CMDB 主机和应用是资产台账；实际执行节点由工单模板绑定的作业主机确定。

## 2. 范围

### 2.1 已实现功能

| 模块 | 能力 |
| --- | --- |
| 登录鉴权 | 本地账号、LDAP、OIDC、JWT Access/Refresh、密码策略、登录锁定 |
| MFA | TOTP 绑定、验证、关闭/可选/强制策略、管理员重置 |
| 密码重置 | 本地账号邮箱找回、反枚举响应、Redis 单次令牌、SMTP 安全邮件 |
| RBAC | 用户、角色、33 个权限点，权限取角色并集 |
| CMDB | 主机、应用、应用-主机关联、Excel 导入/导出 |
| 作业资源 | AES-256-GCM 加密 SSH 凭据；作业主机 CRUD、连通性测试、启停 |
| 模板 | 工单模板、可复用流程模板、参数和动态生成、通知规则、可见角色 |
| 工单 | 模板提交、参数校验、预生成、快照、列表、待办、审批、撤回 |
| 执行 | Worker、串行步骤、Shell/Playbook、超时、失败策略、暂停/恢复/中止 |
| 日志 | 文件持久化、事件长轮询、日志按行偏移增量读取、执行详情展示 |
| 通知 | Email、Webhook、Teams；六类事件、渠道映射、事件模板、重试 |
| 站内通知 | 本人通知列表、未读数、单条/全部已读 |
| 审计 | 全链路记录、组合检索、CSV/XLSX 导出、按月分区清理 |
| 工作台/搜索 | 按权限裁剪的概览、趋势和聚合搜索 |

### 2.2 不在范围内

告警中心、巡检中心、AI 助手、可观测平台、配置中心、复杂流程编排、插件市场、云资源自动同步和多节点部署均不属于 V1。

## 3. 角色和权限

用户可以绑定多个角色，权限取并集；权限点不会自动继承同模块的其他权限。系统内置 `admin`、`ops`、`approver`、`auditor` 四个角色，内置角色不可删除；`admin` 的权限矩阵不可修改。

权限点全集：

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

默认角色能力：

- `admin`：全部权限。
- `ops`：CMDB 查看/管理/导入、SSH 凭据和脚本密钥查看、作业主机查看、模板查看/管理、工单查看/创建、执行查看/控制。
- `approver`：CMDB 查看、工单查看/审批、执行查看。
- `auditor`：CMDB 查看、执行查看、审计查看/导出。

## 4. 功能需求

### 4.1 认证与账号

- 本地账号密码使用 bcrypt 存储；密码策略由 `password.policy` 控制。
- Access Token 默认 15 分钟，Refresh Token 默认 7 天；有效期可由 `token.policy` 覆盖。
- 登录失败按配置计数并锁定；登出吊销当前 Refresh Token。
- LDAP/OIDC 首次登录创建对应来源用户，并使用配置的默认角色。
- 强制 MFA 时，用户完成 TOTP 绑定前不能进入工作台。
- 个人访问密钥以 `opsp_` 开头，数据库只保存 SHA-256；明文只在创建响应中返回一次，每人最多 10 个。

### 4.2 密码重置

密码重置仅面向 `source=local` 且绑定非空邮箱的账号。`POST /auth/forgot-password` 对未知邮箱、非本地账号、未绑定邮箱、限流和发送失败均返回相同 HTTP 200 和相同公开文案，防止账号枚举。

服务端生成随机令牌，只在 Redis 保存 SHA-256；默认有效期 30 分钟，新令牌覆盖同一账号旧令牌。校验接口不消费令牌，提交接口原子消费并只能使用一次。成功后吊销该用户全部 Refresh Token、清除登录失败计数和锁定状态，并将 `must_change_password` 置为 false。

密码重置只使用 `pwd_reset_requested`、`pwd_reset_success`、`pwd_reset_failed` 三类审计动作，具体失败原因写入 `detail.reason`。

### 4.3 CMDB

主机包含主机名、IP、平台、区域、操作系统、资源规格、环境、状态、SSH 端口和说明；IP 唯一。应用包含名称、说明、语言、部署方式，并通过关联表连接多个主机。主机被应用关联时不能删除。Excel 支持模板下载、逐行校验、失败明细、存在即更新和当前筛选导出。

### 4.4 凭据与作业主机

凭据在同一管理页维护 SSH 凭据和脚本密钥：SSH 密码（`password`）与 SSH 私钥（`private_key`）仅用于作业主机登录；脚本密钥支持 API Token、用户名密码和 UTF-8 文本密钥文件（最大 128 KiB）。敏感字段使用 AES-256-GCM 加密，任何接口不回显明文；凭据类型创建后不可修改。作业主机只能关联 SSH 凭据，包含名称、IP、SSH 端口、工作目录、启用状态和最近连通性测试结果；IP+端口唯一。停用作业主机不能被新模板使用；被工单模板引用时不能删除。

### 4.5 模板和工单

工单模板维护名称、类型、作业主机、流程模板引用、参数、脚本密钥引用、动态脚本、撤回开关、通知规则、可见角色和启用状态。脚本密钥使用唯一别名绑定，模板和工单只保存别名、ID 与名称，不保存密文。流程模板维护名称、说明、步骤、步骤前审批角色和执行策略。两者边界和快照规则见《模板管理与流程模板设计》。

工单提交只传模板 ID、参数和可选预生成 ID。服务端校验模板可见性、模板/流程启用状态、固定参数、用户参数和动态结果，然后保存工单快照。标题使用模板名称；没有草稿状态。

工单状态为 `approving`、`queued`、`running`、`paused`、`success`、`failed`、`rejected`、`cancelled`、`interrupted`。审批驳回、撤回和执行中断分别记录原因；审批通过后自动入队。

### 4.6 执行和日志

Worker 通过 SSH 连接作业主机，在其 workspace 执行步骤。Shell 步骤和动态参数脚本可按工单/模板引用注入脚本密钥：API Token 与用户名密码通过环境变量注入，文本密钥文件写入远端随机临时目录并在结束时清理；日志、错误摘要和动态参数输出均精确脱敏。Playbook 步骤不注入脚本密钥。步骤严格串行，按步骤超时和 `fail_fast` 策略推进。

执行控制包括暂停、恢复、中止和强制中止。普通中止在当前步骤结束后生效；强制中止尽力终止作业主机上的当前进程，并单独记录高风险审计。日志按执行 ID 和步骤顺序落盘；前端通过 `/events` 长轮询获取状态，通过 `/logs` 按行偏移增量读取。

### 4.7 通知和站内信

支持 Email、Webhook、Teams 三个落地渠道。事件为：`ticket.pending_approval`、`ticket.approved`、`ticket.rejected`、`execution.success`、`execution.failed`、`execution.interrupted`。

渠道映射决定事件走哪些渠道；模板按“渠道 × 事件”存储。Email 使用 `title/content`，Webhook 使用 `body`，Teams 使用 `card`。Webhook 和 Teams 模板保存前、渲染后都必须是有效 JSON；未知占位符只能原样保留在 JSON 字符串值中。通知记录失败时按 1 分钟、5 分钟、15 分钟退避，达到上限后置为 `failed`。站内信按用户写入，用户可查看未读数并标记已读。

### 4.8 审计

审计覆盖认证、MFA、用户/角色、CMDB、凭据、作业主机、模板、工单、审批、执行控制、通知配置和导出。记录时间、操作人、来源 IP、模块、动作、对象、结果和 JSON 详情。审计只读，支持组合检索和 CSV/XLSX 导出；Worker 定期删除超过 `AUDIT_RETENTION_DAYS` 的分区。

## 5. 核心流程

### 5.1 提交与执行

```text
选择启用工单模板
  -> 加载作业主机、流程步骤和参数定义
  -> 运行动态参数预生成（如有）
  -> 填写用户参数并提交
  -> 保存快照
  -> 有审批角色则 approving，否则 queued
  -> Worker 串行执行
  -> success / failed / interrupted
```

### 5.2 登录与 MFA

```text
账号密码或 SSO
  -> 未需 MFA：返回 Access/Refresh
  -> 需要验证：返回 mfa_token，校验 TOTP 后换正式令牌
  -> 强制绑定：返回 mfa_token + mfa_setup，绑定成功后换正式令牌
```

## 6. 非功能要求

- 单机 Compose 部署，API、Worker、Nginx、MySQL、Redis 容器异常自动重启。
- MySQL 使用 `utf8mb4`；执行日志与数据库分离保存。
- 生产环境关闭 `DEBUG`，只开放 Nginx 端口；凭据、MFA secret 和 JWT 密钥不进入仓库。
- 公开密码重置接口保持统一响应，审计保存内部失败原因。
- 页面、API、Worker 对同一状态和快照使用统一字段含义。

## 7. 验收标准

1. 五容器健康启动，`/healthz` 和 `/api/v1/ping` 返回成功。
2. admin 首次登录完成改密和 MFA；本地、LDAP/OIDC 认证分支按配置工作。
3. 主机/应用、凭据、作业主机 CRUD 和删除保护正确。
4. 工单模板与流程模板边界正确，参数预生成、快照、启停和引用保护正确。
5. 9 态工单状态、步骤审批、执行控制和 Worker 恢复行为正确。
6. 日志可实时查看和历史回看，通知六事件映射、模板渲染、失败重试和站内信正确。
7. 审计可查询、导出、按保留策略清理，敏感数据不回显。
8. 后端 `python -m pytest -q`、前端 `npm.cmd run type-check` 和 `npm.cmd run build` 通过。
