// 凭据 / 工单模板 API（04-API设计 §5，V2）
import { request } from './http'
import type { PageResult } from './system'
import type { ExecStrategy } from './ticket'

// ---------- 凭据（密文任何接口不回显，PRD CRED-01） ----------

export interface CredentialItem {
  id: number
  name: string
  login_user: string | null
  auth_type: 'password' | 'private_key' | 'api_token' | 'username_password' | 'secret_file'
  has_passphrase: boolean
  file_name: string | null
  description: string | null
  created_at: string | null
  updated_at: string | null
}

/** 凭据表单：secret 编辑时留空 = 不变更密文 */
export interface CredentialForm {
  name: string
  login_user?: string | null
  auth_type: CredentialItem['auth_type']
  secret: string
  passphrase?: string
  file_name?: string
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
export type TicketParamSource = 'fixed' | 'user' | 'generated'
export type TicketInputType = 'text' | 'enum'

export interface TicketParam {
  name: string
  label?: string | null
  source: TicketParamSource
  input_type: TicketInputType
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
  exec_strategy: Partial<ExecStrategy>
  steps_count?: number | null
  ticket_template_refs?: number | null
  created_at: string | null
  updated_at: string | null
}

export interface ProcessTemplateForm {
  name: string
  description?: string
  exec_strategy: ExecStrategy
  steps: ProcessStep[]
}

export interface TicketTemplateForm {
  name: string
  type: TemplateType
  description?: string
  job_host_id: number
  process_template_id: number
  params_schema: TicketParam[]
  generator_script?: string | null
  generator_timeout?: number | null
  credential_refs: CredentialRef[]
  allow_withdraw: boolean
  notify_rules: NotifyRule[]
  visible_role_ids: number[]
  status?: TemplateStatus
}

export interface CredentialRef {
  alias: string
  credential_id: number
  credential_name?: string | null
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

/** 通知规则行：receivers 支持 creator / approver_role / role:<id>（TPL-05） */
export interface NotifyRule {
  event: string
  receivers: string[]
  channels: string[]
}

/** 工单模板列表行：包含业务入口和参数契约。 */
export interface TemplateItem {
  id: number
  name: string
  type: TemplateType
  description: string | null
  job_host_id: number
  process_template_id: number
  params_schema: TicketParam[]
  generator_script?: string | null
  generator_timeout?: number | null
  credential_refs?: CredentialRef[]
  allow_withdraw: boolean
  notify_rules: NotifyRule[]
  visible_role_ids: number[]
  status: TemplateStatus
  created_at: string | null
  updated_at: string | null
}

/** 工单模板详情：业务入口配置和流程引用。 */
export interface TemplateDetail extends TemplateItem {
  process_template?: { id: number; name: string; status: TemplateStatus }
}

export function listTemplates(params: {
  page?: number
  page_size?: number
  keyword?: string
  type?: string
  status?: string
  process_template_id?: number
}) {
  return request<PageResult<TemplateItem>>({ url: '/templates', method: 'get', params })
}

export function getTemplate(id: number) {
  return request<TemplateDetail>({ url: `/templates/${id}`, method: 'get' })
}

export function createTemplate(data: TicketTemplateForm) {
  return request<{ id: number }>({ url: '/templates', method: 'post', data })
}

export function updateTemplate(id: number, data: TicketTemplateForm) {
  return request<{ id: number }>({
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
