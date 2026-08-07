// 凭据 / 工单模板 API（04-API设计 §5，V2）
import { request } from './http'
import type { PageResult } from './system'
import type { ExecStrategy } from './ticket'

// ---------- 凭据（密文任何接口不回显，PRD CRED-01） ----------

export interface CredentialItem {
  id: number
  name: string
  login_user: string
  auth_type: 'password' | 'private_key'
  has_passphrase: boolean
  description: string | null
  created_at: string | null
  updated_at: string | null
}

/** 凭据表单：secret 编辑时留空 = 不变更密文 */
export interface CredentialForm {
  name: string
  login_user: string
  auth_type: 'password' | 'private_key'
  secret: string
  passphrase?: string
  description?: string
}

export function listCredentials(params: {
  page?: number
  page_size?: number
  keyword?: string
  auth_type?: string
}) {
  return request<PageResult<CredentialItem>>({ url: '/credentials', method: 'get', params })
}

export function createCredential(data: CredentialForm) {
  return request<{ id: number }>({ url: '/credentials', method: 'post', data })
}

export function updateCredential(id: number, data: CredentialForm) {
  return request<null>({ url: `/credentials/${id}`, method: 'put', data })
}

export function deleteCredential(id: number) {
  return request<null>({ url: `/credentials/${id}`, method: 'delete' })
}

// ---------- 工单模板（V2：全量规则配置，规则任一变更自动升版 TPL-06） ----------

export type TemplateType = 'release' | 'daily_ops' | 'other'
export type TemplateStatus = 'enabled' | 'disabled'
export type ScriptType = 'shell' | 'playbook'
export type ApproveMode = 'any' | 'all' | 'seq'

export type ProcessParamSource = 'fixed' | 'user' | 'generated'
export type ProcessInputType = 'text' | 'enum'

export interface ProcessParam {
  name: string
  label?: string | null
  source: ProcessParamSource
  input_type: ProcessInputType
  options: string[]
  default?: string | null
  required: boolean
  description?: string | null
}

export interface ProcessStep {
  step_order?: number
  name: string
  script_type: ScriptType
  content: string
  timeout: number
  approval_role_id?: number | null
}

export interface ProcessTemplateItem {
  id: number
  name: string
  description: string | null
  status: TemplateStatus
  params_schema: ProcessParam[]
  generator_script?: string | null
  generator_timeout?: number | null
  exec_strategy: Partial<ExecStrategy>
  steps_count?: number | null
  ticket_template_refs?: number | null
  created_at: string | null
  updated_at: string | null
}

export interface ProcessTemplateForm {
  name: string
  description?: string
  params_schema: ProcessParam[]
  generator_script?: string | null
  generator_timeout?: number | null
  exec_strategy: ExecStrategy
  steps: ProcessStep[]
}

export interface TicketTemplateForm {
  name: string
  type: TemplateType
  description?: string
  job_host_id: number
  process_template_id: number
  allow_withdraw: boolean
  notify_rules: NotifyRule[]
  visible_role_ids: number[]
  status?: TemplateStatus
}

export function defaultExecStrategy(): ExecStrategy {
  return { timeout: 600, fail_fast: true, kill_on_stop: false }
}

export function listProcessTemplates(params: { page?: number; page_size?: number; keyword?: string; status?: string } = {}) {
  return request<PageResult<ProcessTemplateItem>>({ url: '/process-templates', method: 'get', params })
}

export function getProcessTemplate(id: number) {
  return request<ProcessTemplateItem & { steps: ProcessStep[] }>({ url: `/process-templates/${id}`, method: 'get' })
}

export function createProcessTemplate(data: ProcessTemplateForm) {
  return request<{ id: number }>({ url: '/process-templates', method: 'post', data })
}

export function updateProcessTemplate(id: number, data: ProcessTemplateForm) {
  return request<{ id: number }>({ url: `/process-templates/${id}`, method: 'put', data })
}

export function setProcessTemplateStatus(id: number, status: TemplateStatus) {
  return request<{ status: TemplateStatus }>({ url: `/process-templates/${id}/status`, method: 'put', data: { status } })
}

export function deleteProcessTemplate(id: number) {
  return request<null>({ url: `/process-templates/${id}`, method: 'delete' })
}

/** 步骤参数定义行（03-数据库设计 §4.3）；fixed=锁定默认值，提交人不可见不可改 */
export interface TemplateParam {
  name: string
  label: string | null
  default: string | null
  required: boolean
  fixed: boolean
  description: string | null
}

/** 模板步骤（脚本内嵌，顺序即数组顺序 TPL-02；统一在模板配置的作业主机上执行） */
export interface TemplateStep {
  name: string
  script_type: ScriptType
  content: string
  params_schema: TemplateParam[]
  timeout: number
}

/** 审批节点：节点=角色；V1 仅 any（或签）生效，其余仅存配置（FLOW-01） */
export interface ApprovalNode {
  node_order: number
  role_id: number
  approve_mode: ApproveMode
}

/** 通知规则行：receivers 支持 creator / approver_role / role:<id>（TPL-05） */
export interface NotifyRule {
  event: string
  receivers: string[]
  channels: string[]
}

/** 模板引用凭据行：脚本内以 $CRED_<别名大写>_USER / _SECRET / _PASSPHRASE 读取 */
export interface CredentialRef {
  alias: string
  credential_id: number
  credential_name?: string | null
}

/** 模板列表行（不含步骤/节点明细） */
export interface TemplateItem {
  id: number
  name: string
  type: TemplateType
  description: string | null
  job_host_id: number
  approval_enabled: boolean
  status: TemplateStatus
  current_version: number
  created_at: string | null
  updated_at: string | null
}

/** 模板详情 = 主表全部规则 + 步骤 + 审批节点（当前生效配置） */
export interface TemplateDetail extends TemplateItem {
  exec_strategy: Partial<ExecStrategy>
  allow_withdraw: boolean
  allow_transfer: boolean
  allow_countersign: boolean
  notify_rules: NotifyRule[]
  visible_role_ids: number[]
  steps: (TemplateStep & { step_order: number })[]
  approval_nodes: ApprovalNode[]
  credential_refs: CredentialRef[]
}

/** 模板新建/编辑共用全量配置体（04-API §5） */
export interface TemplateForm {
  name: string
  type: TemplateType
  description?: string
  job_host_id: number
  steps: TemplateStep[]
  exec_strategy: ExecStrategy
  approval_enabled: boolean
  approval_nodes: ApprovalNode[]
  allow_withdraw: boolean
  allow_transfer: boolean
  allow_countersign: boolean
  notify_rules: NotifyRule[]
  visible_role_ids: number[]
  credential_refs?: CredentialRef[]
  changelog?: string
}

export interface TemplateVersionItem {
  id: number
  version: number
  changelog: string | null
  created_by: number | null
  created_at: string | null
}

/** 版本快照体：全量配置（steps 带 step_order 便于回看） */
export interface TemplateSnapshot {
  name: string
  type: TemplateType
  description: string | null
  job_host_id: number
  steps: (TemplateStep & { step_order: number })[]
  exec_strategy: Partial<ExecStrategy>
  approval_enabled: boolean
  approval_nodes: ApprovalNode[]
  allow_withdraw: boolean
  allow_transfer: boolean
  allow_countersign: boolean
  notify_rules: NotifyRule[]
  visible_role_ids: number[]
}

/** 历史版本快照（全量配置只读回看） */
export interface TemplateVersionDetail extends TemplateVersionItem {
  snapshot: TemplateSnapshot
}

export function listTemplates(params: {
  page?: number
  page_size?: number
  keyword?: string
  type?: string
  status?: string
}) {
  return request<PageResult<TemplateItem>>({ url: '/templates', method: 'get', params })
}

export function getTemplate(id: number) {
  return request<TemplateDetail>({ url: `/templates/${id}`, method: 'get' })
}

export function createTemplate(data: TemplateForm | TicketTemplateForm) {
  return request<{ id: number }>({ url: '/templates', method: 'post', data })
}

/** 编辑模板：响应带 version_bumped 标记是否生成了新版本 */
export function updateTemplate(id: number, data: TemplateForm | TicketTemplateForm) {
  return request<{ current_version: number; version_bumped: boolean }>({
    url: `/templates/${id}`,
    method: 'put',
    data,
  })
}

/** 启用/禁用：禁用后不可被提交工单，不影响已提交工单（TPL-03） */
export function setTemplateStatus(id: number, status: TemplateStatus) {
  return request<{ status: TemplateStatus }>({
    url: `/templates/${id}/status`,
    method: 'put',
    data: { status },
  })
}

export function deleteTemplate(id: number) {
  return request<null>({ url: `/templates/${id}`, method: 'delete' })
}

export function listTemplateVersions(id: number) {
  return request<{ items: TemplateVersionItem[] }>({
    url: `/templates/${id}/versions`,
    method: 'get',
  })
}

export function getTemplateVersion(id: number, version: number) {
  return request<TemplateVersionDetail>({
    url: `/templates/${id}/versions/${version}`,
    method: 'get',
  })
}
