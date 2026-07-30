// 全局搜索 API（04-API设计 §12，SEARCH-03）：登录即可访问，无权段为 null
import { request } from './http'

/** 搜索分段：items 最多 5 条 + 真实总数（下拉面板"共 N 条"用） */
export interface SearchSegment<T> {
  items: T[]
  total: number
}

/** 聚合搜索结果：无对应权限的段为 null（前端整组不显示） */
export interface SearchResult {
  hosts: SearchSegment<{ id: number; hostname: string; ip: string }> | null
  apps: SearchSegment<{ id: number; name: string }> | null
  tickets: SearchSegment<{ id: number; ticket_no: string; title: string }> | null
  templates: SearchSegment<{ id: number; name: string; type: string }> | null
}

/** 聚合搜索：keyword 必填非空，每段限 5 条 */
export function globalSearch(keyword: string) {
  return request<SearchResult>({ url: '/search', method: 'get', params: { keyword } })
}
