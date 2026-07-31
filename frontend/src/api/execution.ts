// 执行中心 API（04-API设计 §7/§8，M5）
// 执行域只读；控制操作挂工单控制面（§6：/tickets/{id}/abort 等）
import { request } from './http'
import type { PageResult } from './system'
import type { ExecStrategy } from './ticket'

/** 执行实例状态（03 §5.2） */
export type ExecutionStatus =
  | 'queued' | 'running' | 'paused'
  | 'success' | 'failed' | 'terminated' | 'interrupted'

/** 主机粒度执行状态 */
export type HostExecStatus =
  | 'pending' | 'running' | 'success' | 'failed'
  | 'timeout' | 'skipped' | 'terminated'

/** 执行记录列表行 */
export interface ExecutionBrief {
  id: number
  ticket_id: number
  ticket_no: string
  title: string
  app_id: number
  app_name: string | null
  creator_id: number
  creator_name: string
  status: ExecutionStatus
  total_steps: number
  total_hosts: number
  triggered_by: string
  started_at: string | null
  finished_at: string | null
  created_at: string | null
}

/** 步骤汇总行（详情/矩阵表头） */
export interface ExecutionStepInfo {
  step_order: number
  step_name: string
  script_type: string | null
  timeout: number | null
  status: string
  current_batch: number
  total_batch: number
  success_count: number
  failed_count: number
  started_at: string | null
  finished_at: string | null
}

/** 执行详情（REST 详情与 WS snapshot 共用结构） */
export interface ExecutionDetail {
  id: number
  ticket_id: number
  ticket_no: string | null
  title: string | null
  app_name: string | null
  ticket_status: string | null
  creator_id: number | null
  interrupt_reason: string | null
  exec_strategy: Partial<ExecStrategy>
  status: ExecutionStatus
  total_steps: number
  total_hosts: number
  triggered_by: string
  started_at: string | null
  finished_at: string | null
  created_at: string | null
  steps: ExecutionStepInfo[]
  hosts?: ExecutionHostRow[] // WS snapshot 附带全量主机
}

/** 主机明细行（步骤×主机矩阵数据源） */
export interface ExecutionHostRow {
  id: number
  step_order: number
  ticket_host_id: number
  hostname: string
  ip: string
  batch_no: number
  status: HostExecStatus
  exit_code: number | null
  error_summary: string | null
  started_at: string | null
  finished_at: string | null
}

export interface ExecutionQuery {
  page?: number
  page_size?: number
  ticket_no?: string
  app_id?: number
  creator_id?: number
  status?: string
  start?: string
  end?: string
}

/** 执行事件（长轮询/WS 共用载荷） */
export interface ExecutionEvent {
  seq: number
  kind: 'execution_status' | 'step_status' | 'host_status'
  data: Record<string, unknown>
  ts: string
}

export function listExecutions(params: ExecutionQuery) {
  return request<PageResult<ExecutionBrief>>({ url: '/executions', method: 'get', params })
}

export function getExecution(id: number) {
  return request<ExecutionDetail>({ url: `/executions/${id}`, method: 'get' })
}

export function listExecutionHosts(
  id: number,
  params: { step_order?: number; status?: string; page?: number; page_size?: number } = {},
) {
  return request<PageResult<ExecutionHostRow>>({
    url: `/executions/${id}/hosts`,
    method: 'get',
    params: { page_size: 500, ...params },
  })
}

/** 历史日志按行偏移增量读取；Ansible 步骤 ip 固定传 "ansible" */
export function getExecutionLogs(
  id: number,
  params: { step_order: number; ip: string; offset?: number; limit?: number },
) {
  return request<{ lines: string[]; next_offset: number; eof: boolean }>({
    url: `/executions/${id}/logs`,
    method: 'get',
    params,
  })
}

/** 事件实时主通道：长轮询以 since_seq 拉增量事件（服务端最长挂 30s，有事件立即返回） */
export function pollExecutionEvents(id: number, sinceSeq: number) {
  return request<{ events: ExecutionEvent[]; last_seq: number; finished: boolean }>({
    url: `/executions/${id}/events`,
    method: 'get',
    params: { since_seq: sinceSeq },
    timeout: 40000, // 覆盖默认 30s：服务端挂起上限 30s + 网络余量
  })
}

// ---------- 工单控制面（04 §6：权限 execution:control，仅创建人或 admin） ----------

export type ControlOp = 'abort' | 'pause' | 'resume' | 'force-abort'

/** 下发控制信号：abort=排队/执行/暂停中；pause=执行中；resume=暂停中；force-abort=执行/暂停中 */
export function controlTicket(ticketId: number, op: ControlOp) {
  return request<{ status: string; execution_id: number; signal: string }>({
    url: `/tickets/${ticketId}/${op}`,
    method: 'post',
  })
}

