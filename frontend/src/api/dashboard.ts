// 工作台 API（/dashboard）：按权限裁剪的聚合快照与工单/执行趋势
import { request } from './http'

/** 执行动态行（复用执行记录列表的 item 形态） */
export interface RecentExecution {
  id: number
  ticket_id: number
  ticket_no: string
  title: string
  job_host_id: number
  job_host_name: string | null
  creator_id: number
  creator_name: string
  status: string
  total_steps: number
  started_at: string | null
  finished_at: string | null
  created_at: string | null
}

/** 概览聚合：无权限的段为 null，前端据此隐藏对应分区 */
export interface DashboardSummary {
  cmdb: {
    host_total: number
    host_status: Record<string, number>
    app_total: number
    host_environment_status: { environment: string; status: string; count: number }[]
    app_deploy_type: Record<string, number>
  } | null
  domain: {
    zone_total: number
    record_total: number
    provider_dist: Record<string, number>
    sync_status: Record<string, number>
    record_type_dist: Record<string, number>
  } | null
  config: { file_total: number; status_dist: Record<string, number> } | null
  ticket: {
    today_total: number
    month_total: number
    month_success: number
    month_finished: number
    status_dist: Record<string, number>
  } | null
  todo_total: number | null
  todo_breakdown: { ticket: number; config: number }
  execution: {
    active: Record<string, number>
    recent: RecentExecution[]
  } | null
  audit_today: number | null
  audit: { today_total: number; result_dist: Record<string, number>; module_dist: Record<string, number> } | null
  attention: DashboardAttention[]
}

export interface DashboardAttention {
  module: 'config' | 'domain' | 'execution'
  id: number
  name: string
  status: string
  updated_at: string | null
}

export type TrendGranularity = 'day' | 'week' | 'month' | 'year'

export interface TrendPoint {
  period: string
  count: number
}

export interface ActivityTrendPoint {
  period: string
  tickets: number | null
  executions: number | null
}

/** 无对应读取权限的系列返回 null，前端保持缺席语义。 */
export function fetchActivityTrend(granularity: TrendGranularity) {
  return request<{ granularity: TrendGranularity; items: ActivityTrendPoint[] }>({
    url: '/dashboard/activity-trend',
    method: 'get',
    params: { granularity },
  })
}

export function fetchDashboardSummary(silentTransportError = false) {
  return request<DashboardSummary>({ url: '/dashboard/summary', method: 'get', silentTransportError })
}

export function fetchTicketTrend(granularity: TrendGranularity) {
  return request<{ granularity: TrendGranularity; items: TrendPoint[] }>({
    url: '/dashboard/ticket-trend',
    method: 'get',
    params: { granularity },
  })
}
