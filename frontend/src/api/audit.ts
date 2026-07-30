// 审计日志 API（04-API设计 §9）
import http, { request } from './http'
import type { PageResult } from './system'

export interface AuditLogItem {
  id: number
  created_at: string | null
  actor_id: number | null
  actor_name: string | null
  source_ip: string | null
  module: string
  action: string
  target_type: string | null
  target_id: string | null
  target_name: string | null
  result: string
  detail: Record<string, unknown> | null
}

/** 组合筛选参数（列表与导出共用，保证导出内容与筛选一致） */
export interface AuditQuery {
  start?: string
  end?: string
  actor?: string
  module?: string
  action?: string
  result?: string
  keyword?: string
}

/** 分页检索审计日志 */
export function listAuditLogs(params: AuditQuery & { page?: number; page_size?: number }) {
  return request<PageResult<AuditLogItem>>({ url: '/audit/logs', method: 'get', params })
}

/** 按当前筛选导出审计日志（CSV/xlsx blob 下载，文件名取响应头） */
export async function exportAuditLogs(format: 'csv' | 'xlsx', params: AuditQuery) {
  const resp = await http.get('/audit/logs/export', {
    params: { ...params, format },
    responseType: 'blob',
  })
  // 从 Content-Disposition 解析后端生成的带时间戳文件名，缺省兜底
  const cd: string = resp.headers?.['content-disposition'] || ''
  const match = /filename\*=UTF-8''([^;]+)/.exec(cd)
  const filename = match ? decodeURIComponent(match[1]) : `审计日志.${format}`
  const link = document.createElement('a')
  link.href = URL.createObjectURL(resp.data as Blob)
  link.download = filename
  link.click()
  URL.revokeObjectURL(link.href)
}
