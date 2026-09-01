// 工单中心 API（04-API设计 §6，V2）
// 工单中心只能使用模板：选模板 → 填参数 → 提交（标题=模板名，无草稿，提交即生效）
import { request } from './http'
import type { PageResult } from './system'

/** 执行策略（配置在模板内，提交时快照到工单 TPL-04） */
export interface ExecStrategy {
  timeout: number
  fail_fast: boolean
  kill_on_stop: boolean
}

/** 默认执行策略工厂：模板编辑器初始值 */
export function defaultExecStrategy(): ExecStrategy {
  return { timeout: 600, fail_fast: true, kill_on_stop: false }
}

/** V2 九态状态机（M5）：approving→queued→running(可paused)→终态；rejected/cancelled/interrupted */
export type TicketStatus =
  | 'approving' | 'queued' | 'running' | 'paused' | 'success' | 'failed'
  | 'rejected' | 'cancelled' | 'interrupted'

/** 提交时固化的流程快照；审批角色绑定在具体步骤上。 */
export interface ProcessSnapshot {
  process_name?: string
  params_schema?: FormParam[]
  generator?: { script?: string | null; timeout?: number | null }
  exec_strategy?: Partial<ExecStrategy>
  steps: {
    step_order: number
    name: string
    script_type: string
    content: string
    timeout: number
    approval_role_id?: number | null
  }[]
}

/** 工单列表/待办行 */
export interface TicketBrief {
  id: number
  ticket_no: string
  template_id: number
  process_template_id?: number | null
  process_name?: string | null
  type: string
  title: string
  job_host_id: number
  job_host_name: string | null
  status: TicketStatus
  current_step: number
  total_steps: number
  creator_id: number
  creator_name: string
  submitted_at: string | null
  finished_at: string | null
  created_at: string | null
  execution?: ExecutionBrief | null
}

export interface TodoEvent {
  seq: number
  kind: 'todo.changed'
}

/** 提交时固化的作业主机快照（登录认证随关联凭据，快照不含账号信息） */
export interface JobHostSnap {
  id: number
  name: string
  ip: string
  ssh_port: number
  workdir: string
}

/** 步骤快照（含脚本内容快照与生效参数） */
export interface TicketStepSnap {
  step_order: number
  step_name: string
  script_type: string
  content_snap: string
  params: Record<string, string>
  timeout: number
  approval_role_id?: number | null
  approval_role_name?: string | null
}

/** 审批时间线行 */
export interface ApprovalRecord {
  step_order: number
  action: 'approve' | 'reject'
  comment: string | null
  approver_id: number
  approver_name: string
  created_at: string | null
}

/** 执行概要（M5 前为 queued 打桩） */
export interface ExecutionBrief {
  id: number
  status: string
  total_steps: number
  created_at: string | null
  started_at?: string | null
  finished_at?: string | null
}

/** 工单详情（只读，全部来自快照） */
export interface TicketDetail extends TicketBrief {
  params: Record<string, string>
  exec_strategy: Partial<ExecStrategy>
  flow_snap: ProcessSnapshot
  allow_withdraw: boolean
  job_host: JobHostSnap | null
  steps: TicketStepSnap[]
  approvals: ApprovalRecord[]
  execution: ExecutionBrief | null
  /** 提单时冻结的模板引用凭据（执行时按 credential_id 实时取密文） */
  credential_refs?: { alias: string; credential_id: number; credential_name: string }[]
}

export interface TicketQuery {
  page?: number
  page_size?: number
  status?: string
  creator_id?: number
  keyword?: string
  start?: string
  end?: string
  execution_status?: string
  has_execution?: boolean
  execution_active?: boolean
}

// ---------- 可用模板与提交表单 ----------

/** 可提交模板行（enabled + visible_role_ids 过滤后） */
export interface UsableTemplate {
  id: number
  name: string
  type: string
  description: string | null
  job_host_id: number
  job_host_name?: string | null
  process_template_id: number
  process_name?: string
}

/** 提交表单参数行（汇总后，不含 fixed） */
export interface FormParam {
  name: string
  label: string | null
  default: string | null
  required: boolean
  description: string | null
  input_type?: 'text' | 'enum'
  options?: string[]
  source?: 'user' | 'generated' | 'fixed'
}

/** 提交表单描述：汇总参数 + 作业主机/步骤前审批/策略只读预览 */
export interface TemplateFormDesc {
  template: {
    id: number
    name: string
    type: string
    description: string | null
    process_template_id: number
    process_name: string
    allow_withdraw: boolean
  }
  params: FormParam[]
  job_host: JobHostSnap | null
  steps: {
    step_order: number
    name: string
    script_type: string
    timeout: number
    approval_role_id?: number | null
    approval_role_name?: string | null
  }[]
  generator?: { enabled: boolean; timeout?: number | null }
  exec_strategy: Partial<ExecStrategy>
}

/** 可用模板列表（工单提交入口） */
export function listUsableTemplates() {
  return request<{ items: UsableTemplate[] }>({ url: '/tickets/templates', method: 'get' })
}

/** 提交表单描述（选定模板后拉取） */
export function getTemplateFormDesc(templateId: number) {
  return request<TemplateFormDesc>({ url: `/tickets/templates/${templateId}/form`, method: 'get' })
}

// ---------- 工单 ----------

export function listTickets(params: TicketQuery) {
  return request<PageResult<TicketBrief>>({ url: '/tickets', method: 'get', params })
}

/** 待我审批（total 兼作菜单角标计数，FLOW-06） */
export function todoTickets(params: { page?: number; page_size?: number } = {}) {
  return request<PageResult<TicketBrief>>({ url: '/tickets/todo', method: 'get', params })
}

/** 待办角标事件长轮询（服务端最长挂起 30 秒）。 */
export function pollTodoEvents(sinceSeq: number, signal?: AbortSignal) {
  return request<{ events: TodoEvent[]; last_seq: number }>({
    url: '/tickets/todo/events',
    method: 'get',
    params: { since_seq: sinceSeq },
    timeout: 40000,
    signal,
    silentCancel: true,
    silentTransportError: true,
  })
}

export function getTicket(id: number) {
  return request<TicketDetail>({ url: `/tickets/${id}`, method: 'get' })
}

/** 提交工单：只填参数，标题=模板名；免审入队 queued，否则 approving（TICKET-01/M5） */
export function prepareTicket(templateId: number, params: Record<string, string>) {
  return request<{ prepare_id: string; values: Record<string, string>; options: Record<string, string[]>; expires_at: string }>({
    url: '/tickets/prepare', method: 'post', data: { template_id: templateId, params },
  })
}

export function createTicket(templateId: number, params: Record<string, string>, prepareId?: string) {
  return request<{ id: number; ticket_no: string; status: TicketStatus; current_step: number }>({
    url: '/tickets',
    method: 'post',
    data: { template_id: templateId, params, prepare_id: prepareId },
  })
}

/** 审批：驳回时 comment 必填（后端 40001 校验） */
export function approveTicket(id: number, action: 'approve' | 'reject', comment?: string) {
  return request<{ status: TicketStatus; current_step: number }>({
    url: `/tickets/${id}/approve`,
    method: 'post',
    data: { action, comment },
  })
}

/** 撤回：仅创建人、approving 状态、且模板允许撤回（TICKET-05） */
export function cancelTicket(id: number) {
  return request<{ status: TicketStatus }>({ url: `/tickets/${id}/cancel`, method: 'post' })
}
