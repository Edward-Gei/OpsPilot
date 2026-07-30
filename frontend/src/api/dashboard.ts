// 工作台 API（/dashboard）：概览聚合 + 提单数趋势
import { request } from './http'

/** 执行动态行（复用执行记录列表的 item 形态） */
export interface RecentExecution {
  id: number
  ticket_id: number
  ticket_no: string
  title: string
  app_id: number
  app_name: string
  creator_id: number
  creator_name: string
  status: string
  total_steps: number
  total_hosts: number
  triggered_by: string
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
  } | null
  ticket: {
    today_total: number
    month_total: number
    month_success: number
    month_finished: number
    status_dist: Record<string, number>
  } | null
  todo_total: number | null
  execution: {
    active: Record<string, number>
    recent: RecentExecution[]
  } | null
  audit_today: number | null
}

export type TrendGranularity = 'day' | 'week' | 'month' | 'year'

export interface TrendPoint {
  period: string
  count: number
}

export function fetchDashboardSummary() {
  return request<DashboardSummary>({ url: '/dashboard/summary', method: 'get' })
}

export function fetchTicketTrend(granularity: TrendGranularity) {
  return request<{ granularity: TrendGranularity; items: TrendPoint[] }>({
    url: '/dashboard/ticket-trend',
    method: 'get',
    params: { granularity },
  })
}
