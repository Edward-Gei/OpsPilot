// 执行中心 API（04-API设计 §7/§8，M5）
// 执行域只读；控制操作挂工单控制面（§6：/tickets/{id}/abort 等）
import { request } from './http'
import type { PageResult } from './system'
import type { ExecStrategy } from './ticket'

/** 执行实例状态（03 §5.2） */
export type ExecutionStatus =
  | 'queued' | 'running' | 'paused'
  | 'success' | 'failed' | 'terminated' | 'interrupted'

/** 执行记录列表行 */
export interface ExecutionBrief {
  id: number
  ticket_id: number
  ticket_no: string
  title: string
  job_host_id: number
  job_host_name: string | null
  creator_id: number
  creator_name: string
  status: ExecutionStatus
  total_steps: number
  triggered_by: string
  started_at: string | null
  finished_at: string | null
  created_at: string | null
}

/** 步骤汇总行（详情步骤列表：作业主机上串行执行） */
export interface ExecutionStepInfo {
  step_order: number
  step_name: string
  script_type: string | null
  timeout: number | null
  status: string
  exit_code: number | null
  error_summary: string | null
  started_at: string | null
  finished_at: string | null
}

/** 执行详情（REST 详情装配结构） */
export interface ExecutionDetail {
  id: number
  ticket_id: number
  ticket_no: string | null
  title: string | null
  job_host_name: string | null
  ticket_status: string | null
  creator_id: number | null
  interrupt_reason: string | null
  exec_strategy: Partial<ExecStrategy>
  status: ExecutionStatus
  total_steps: number
  triggered_by: string
  started_at: string | null
  finished_at: string | null
  created_at: string | null
  steps: ExecutionStepInfo[]
}

export interface ExecutionQuery {
  page?: number
  page_size?: number
  ticket_no?: string
  creator_id?: number
  status?: string
  start?: string
  end?: string
}

/** 执行事件（长轮询载荷） */
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

/** 历史日志按行偏移增量读取（步骤粒度，作业主机单点执行） */
export function getExecutionLogs(
  id: number,
  params: { step_order: number; offset?: number; limit?: number },
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

