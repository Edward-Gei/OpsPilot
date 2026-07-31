# 设计文档：作业主机关联凭据 + 步骤脚本调用凭据

日期：2026-07-31
状态：已确认（用户认可）

## 背景与目标

当前凭据管理是"孤立数据"——没有任何业务方消费；作业主机的登录认证信息（login_user/auth_type/secret/passphrase）在作业主机表单中直填，与凭据管理重复。本设计打通两者：

1. **作业主机关联凭据**：作业主机配置不再直填认证信息，改为选择凭据管理中的凭据。
2. **脚本调用凭据**：模板可声明引用凭据，步骤脚本执行时以环境变量方式读取（`$CRED_<别名>_USER` / `$CRED_<别名>_SECRET` / `$CRED_<别名>_PASSPHRASE`）。

存量数据均为测试数据，不做自动迁移：存量作业主机升级后显示"未关联凭据"，编辑重新选择凭据即恢复可用。

## 关键决策

| 决策点 | 结论 | 理由 |
|---|---|---|
| 存量作业主机密文 | 直接删列，不迁移 | 用户确认存量为测试数据 |
| 凭据注入方式 | 环境变量（脚本前置 export 行） | asyncssh `env` 参数走 SSH SetEnv，受 sshd `AcceptEnv` 白名单限制不可靠；前置 export 写 stdin 不落盘、不进日志/快照 |
| 凭据引用声明位置 | 模板级（与全局参数同区域） | 与现有交互一致；提单人无感知、不可篡改 |
| 执行时取密文口径 | 按 credential_id 实时读表 | 与作业主机口径一致，改密码后重试即生效 |

## 一、数据模型与迁移（0009）

### job_host 表
- 新增 `credential_id`（UBIGINT 可空；API 层必填）
- 删除 `login_user` / `auth_type` / `secret_enc` / `passphrase_enc` 四列
- ORM：`backend/app/models/cmdb.py` JobHost 同步修改

### ticket_template 表
- 新增 `credential_refs` JSON 可空列，格式 `[{"alias": "mysql", "credential_id": 1}]`
- alias 校验：`^[A-Za-z][A-Za-z0-9_]*$`，同模板内不重名，长度 ≤32
- 模板版本快照（TemplateVersion.snapshot JSON）自然收录，无需 DDL

### ticket 表
- 新增 `credential_refs` JSON 可空快照列：提单时冻结 `[{alias, credential_id, credential_name}]`

### 迁移风格
- revision 0009，down_revision 0008；information_schema 探测幂等；downgrade 不支持

### 删除保护
- `credential_service.delete_credential`：被 job_host 或 ticket_template（credential_refs 含其 id）引用时报 42201

## 二、执行链路

### pipeline._load_context
- 加载作业主机后按 `job_host.credential_id` 加载 Credential 行；未关联凭据 → 执行失败并给出明确错误
- 按 `ticket.credential_refs` 批量加载引用凭据，解密组装 env 字典：
  - `CRED_<ALIAS大写>_USER` = login_user
  - `CRED_<ALIAS大写>_SECRET` = 解密后的密码/私钥文本
  - `CRED_<ALIAS大写>_PASSPHRASE` = 私钥口令（仅存在时注入）
- 引用凭据已被删除（边缘兜底）→ 该步骤按失败归档，错误信息明确指出凭据缺失

### ssh_runner
- `open_connection` 第三参改传真正的 Credential 行（鸭子类型不变，调用方替换）
- `run_shell_on_job_host` 新增 `env: dict[str, str] | None` 参数：值做单引号安全转义（`'` → `'\''`），以 `export K='V'` 行前置在脚本文本之前写入 stdin
- 明文不落盘、不进执行日志、不进快照；用户脚本内 `set -x` 追踪不到前置 export

### job_host_service.test_connectivity
- 加载关联凭据传给 open_connection；未关联凭据 → 返回失败并提示

### playbook 步骤（存量兼容）
- 不注入凭据 env（ansible 命令行内联 env 有 ps 泄露风险），代码注释注明

## 三、API 与 Schema

### 作业主机（backend/app/api/v1/job_hosts.py + schemas/job.py）
- CreateRequest：删除 login_user/auth_type/secret/passphrase，新增 `credential_id: int`（必填）
- UpdateRequest：`credential_id: int | None`
- 响应：删除 auth 相关派生字段，新增 `credential_id` / `credential_name`（join 凭据表）
- 路由层不再有 encrypt_text 逻辑
- service 层校验 credential_id 存在，否则 40401/42201

### 模板（templates.py + schemas/job.py + template_service.py）
- 模板创建/编辑请求新增 `credential_refs: list[{alias, credential_id}] | None`
- 校验：alias 格式/不重名；credential_id 存在
- 模板详情响应返回 credential_refs（含 credential_name，便于前端回显）

### 工单（ticket_service.py）
- 提单时从模板快照 credential_refs（alias + credential_id + credential_name）写入 ticket
- 工单详情返回 credential_refs（仅名称与别名，永不含密文）

## 四、前端

### JobHostPanel.vue（系统设置 → 作业主机）
- 表单：删除登录账号/认证方式/密文/口令四项，替换为"关联凭据"下拉（可搜索；选项显示"名称（登录账号 · 密码/私钥）"）
- 列表：删除登录账号/认证方式列，新增"关联凭据"列；未关联凭据的存量主机显示警示标记
- 类型（frontend/src/api/jobHost.ts）同步修改

### TemplateEditor.vue
- "全局参数"区域旁新增"引用凭据"区域：动态行（别名输入 + 凭据下拉），提示文案说明脚本内用 `$CRED_<别名大写>_USER` / `$CRED_<别名大写>_SECRET` 读取
- 别名前端同步校验（标识符格式、不重名）

### 工单详情（TicketDetailDrawer.vue）
- 展示引用凭据（别名 + 凭据名称），不含任何密文

### CredentialList.vue
- 删除失败（42201 被引用）时展示后端错误信息，无需额外改动

## 五、测试与交付

- 后端 pytest：
  - test_job_api：作业主机 credential_id 必填/不存在校验、响应含 credential_name、凭据删除保护
  - test_execution_engine：env 组装与单引号转义、未关联凭据执行失败、凭据缺失步骤失败归档
  - test_ticket_api：credential_refs 快照冻结
- 前端：`npm run build` + `npx vue-tsc --noEmit` 零错误
- 部署：重建 api/worker/nginx 镜像，0009 迁移随 api 启动自动执行（RUN_MIGRATIONS=1）
- docs：02-技术架构设计 / 03-数据库设计 / 04-API设计 同步更新

## 安全边界（不变量）

- 密文任何接口不回显（凭据/作业主机/模板/工单响应均无密文）
- 密文仅在建连/注入瞬间由 decrypt_text 即用即解
- 注入的环境变量明文不进执行日志、不进数据库快照、不在目标机落盘
