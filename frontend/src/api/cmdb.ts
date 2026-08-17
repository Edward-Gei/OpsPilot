// CMDB 主机 / 应用 API（04-API设计 §4）
import http, { request } from './http'
import type { PageResult } from './system'

export interface HostItem {
  id: number
  hostname: string
  ip: string
  platform: string | null
  region: string | null
  os: string | null
  cpu_cores: number | null
  memory_gb: number | null
  disk_gb: number | null
  environment: string
  status: string
  ssh_port: number
  description: string | null
  created_at: string | null
  updated_at: string | null
}

export interface AppItem {
  id: number
  name: string
  description: string | null
  language: string | null
  deploy_type: string
  host_count: number
  // 关联主机 IP 清单（列表多行展示）
  host_ips: string[]
  created_at: string | null
}

/** 主机详情：附反查的关联应用 */
export interface HostDetail extends HostItem {
  apps: AppItem[]
}

/** 应用详情：附关联主机清单 */
export interface AppDetail extends AppItem {
  hosts: HostItem[]
}

export interface HostQuery {
  page?: number
  page_size?: number
  keyword?: string
  platform?: string
  region?: string
  environment?: string
  status?: string
  sort_by?: 'created_at'
  sort_order?: 'asc' | 'desc'
}

export interface HostForm {
  hostname: string
  ip: string
  platform?: string
  region?: string
  os?: string
  cpu_cores?: number | null
  memory_gb?: number | null
  disk_gb?: number | null
  environment: string
  status: string
  ssh_port: number
  description?: string
}

export interface ImportResult {
  success_count: number
  failed_rows: { row: number; reason: string }[]
}

// ---------- 主机 ----------

export function listHosts(params: HostQuery) {
  return request<PageResult<HostItem>>({ url: '/cmdb/hosts', method: 'get', params })
}

export function getHost(id: number) {
  return request<HostDetail>({ url: `/cmdb/hosts/${id}`, method: 'get' })
}

export function createHost(data: HostForm) {
  return request<{ id: number }>({ url: '/cmdb/hosts', method: 'post', data })
}

export function updateHost(id: number, data: HostForm) {
  return request<null>({ url: `/cmdb/hosts/${id}`, method: 'put', data })
}

export function deleteHost(id: number) {
  return request<null>({ url: `/cmdb/hosts/${id}`, method: 'delete' })
}

/** 平台/区域自由文本自动补全 */
export function suggestHostField(field: 'platform' | 'region', q?: string) {
  return request<{ items: string[] }>({
    url: '/cmdb/hosts/suggest',
    method: 'get',
    params: { field, q },
  })
}

/** Excel 导入（multipart）；upsert=存在（按 IP）即更新 */
export function importHosts(file: File, upsert: boolean) {
  const form = new FormData()
  form.append('file', file)
  return request<ImportResult>({
    url: '/cmdb/hosts/import',
    method: 'post',
    params: { upsert },
    data: form,
  })
}

/** blob 下载并触发浏览器保存（模板/导出共用） */
async function downloadXlsx(url: string, filename: string, params?: Record<string, unknown>) {
  const resp = await http.get(url, { params, responseType: 'blob' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(resp.data as Blob)
  link.download = filename
  link.click()
  URL.revokeObjectURL(link.href)
}

export function downloadImportTemplate() {
  return downloadXlsx('/cmdb/hosts/import-template', '主机导入模板.xlsx')
}

/** 按当前筛选导出主机 */
export function exportHosts(params: Omit<HostQuery, 'page' | 'page_size'>) {
  return downloadXlsx('/cmdb/hosts/export', '主机列表.xlsx', params)
}

// ---------- 应用 ----------

export interface AppQuery {
  page?: number
  page_size?: number
  keyword?: string
  language?: string
  deploy_type?: string
  sort_by?: 'language' | 'created_at'
  sort_order?: 'asc' | 'desc'
}

export function listApps(params: AppQuery) {
  return request<PageResult<AppItem>>({ url: '/cmdb/apps', method: 'get', params })
}

/** 按当前筛选导出应用 */
export function exportApps(params: Omit<AppQuery, 'page' | 'page_size'>) {
  return downloadXlsx('/cmdb/apps/export', '应用列表.xlsx', params)
}

export function getApp(id: number) {
  return request<AppDetail>({ url: `/cmdb/apps/${id}`, method: 'get' })
}

export function createApp(data: {
  name: string
  description?: string
  language?: string
  deploy_type: string
  host_ids: number[]
}) {
  return request<{ id: number }>({ url: '/cmdb/apps', method: 'post', data })
}

export function updateApp(
  id: number,
  data: { name: string; description?: string; language?: string; deploy_type: string; host_ids: number[] },
) {
  return request<null>({ url: `/cmdb/apps/${id}`, method: 'put', data })
}

export function deleteApp(id: number) {
  return request<null>({ url: `/cmdb/apps/${id}`, method: 'delete' })
}
