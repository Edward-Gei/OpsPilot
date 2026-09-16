// 域名管理 API：Zone 台账、记录快照、发现绑定与 Excel 导入导出。
import http, { request } from './http'
import type { PageResult } from './system'

export type DomainProvider = 'tencent_dnspod' | 'aws_route53' | 'google_cloud_dns'
export type DomainSyncStatus = 'success' | 'failed' | 'syncing'
export type DomainSortField =
  | 'zone_name'
  | 'provider'
  | 'credential_name'
  | 'description'
  | 'record_count'
  | 'sync_status'
  | 'last_synced_at'
  | 'last_sync_error'
export type DomainSortOrder = 'asc' | 'desc'

export interface DomainItem {
  id: number
  provider: DomainProvider
  remote_zone_id: string
  zone_name: string
  credential_name: string | null
  description: string | null
  record_count: number
  sync_status: DomainSyncStatus
  last_synced_at: string | null
  last_sync_error: string | null
  created_at: string | null
  updated_at: string | null
}

export interface DomainQuery {
  page?: number
  page_size?: number
  keyword?: string
  provider?: DomainProvider
  sync_status?: DomainSyncStatus
  sort_by?: DomainSortField
  sort_order?: DomainSortOrder
}

export interface ProviderCredential {
  id: number
  name: string
  auth_type: string
  description: string | null
}

export interface DiscoveredZone {
  remote_zone_id: string
  zone_name: string
}

export interface ZoneBindSelection {
  remote_zone_id: string
  zone_name?: string
  description?: string
}

export type ZoneBindTaskStatus = 'queued' | 'running' | 'success' | 'partial_failed' | 'failed'
export type ZoneBindTaskItemStatus = 'pending' | 'running' | 'success' | 'skipped' | 'failed'

export interface ZoneBindTaskItem {
  remote_zone_id: string
  zone_id: number | null
  zone_name: string | null
  status: ZoneBindTaskItemStatus
  reason: string | null
  started_at: string | null
  finished_at: string | null
}

export interface ZoneBindTask {
  id: number
  provider: DomainProvider
  credential_id: number
  status: ZoneBindTaskStatus
  total_count: number
  success_count: number
  skipped_count: number
  failed_count: number
  last_error: string | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  items?: ZoneBindTaskItem[]
}

export interface ZoneImportResult {
  success_count: number
  skipped_rows: ImportRowResult[]
  failed_rows: ImportRowResult[]
}

export interface ImportRowResult {
  row: number
  reason: string
}

export interface DnsRecordSet {
  id: number
  record_name: string
  record_type: string
  ttl: number | null
  values: string[]
  read_only: boolean
  read_only_reason: string | null
  created_at: string | null
  updated_at: string | null
}

export interface RecordQuery {
  page?: number
  page_size?: number
  keyword?: string
  record_type?: string
  read_only?: boolean
}

export interface RecordCreateForm {
  owner_name: string
  record_type: string
  ttl: number
  values: string[]
}

export interface RecordUpdateForm {
  ttl: number
  values: string[]
}

export function listDomains(params: DomainQuery) {
  return request<PageResult<DomainItem>>({ url: '/domains/zones', method: 'get', params })
}

export function getDomain(id: number) {
  return request<DomainItem>({ url: `/domains/zones/${id}`, method: 'get' })
}

export function updateDomainDescription(id: number, description: string | null) {
  return request<null>({ url: `/domains/zones/${id}`, method: 'put', data: { description } })
}

export function deleteDomain(id: number) {
  return request<null>({ url: `/domains/zones/${id}`, method: 'delete' })
}

export function syncDomain(id: number) {
  return request<DomainItem>({ url: `/domains/zones/${id}/sync`, method: 'post' })
}

export function listCompatibleCredentials(provider: DomainProvider) {
  return request<{ items: ProviderCredential[] }>({
    url: '/domains/credentials',
    method: 'get',
    params: { provider },
  })
}

export function discoverZones(data: { provider: DomainProvider; credential_id: number }) {
  return request<{ items: DiscoveredZone[] }>({ url: '/domains/zones/discover', method: 'post', data })
}

export function bindZones(data: {
  provider: DomainProvider
  credential_id: number
  selections: ZoneBindSelection[]
}) {
  return request<ZoneBindTask>({ url: '/domains/zones', method: 'post', data })
}

export function getZoneBindTask(taskId: number) {
  return request<ZoneBindTask>({
    url: `/domains/zone-bind-tasks/${taskId}`,
    method: 'get',
    silentTransportError: true,
  })
}

export function listRecords(zoneId: number, params: RecordQuery) {
  return request<PageResult<DnsRecordSet>>({
    url: `/domains/zones/${zoneId}/records`,
    method: 'get',
    params,
  })
}

export function createRecord(zoneId: number, data: RecordCreateForm) {
  return request<null>({ url: `/domains/zones/${zoneId}/records`, method: 'post', data })
}

export function updateRecord(zoneId: number, recordId: number, data: RecordUpdateForm) {
  return request<null>({ url: `/domains/zones/${zoneId}/records/${recordId}`, method: 'put', data })
}

export function deleteRecord(zoneId: number, recordId: number) {
  return request<null>({ url: `/domains/zones/${zoneId}/records/${recordId}`, method: 'delete' })
}

export function importDomains(file: File) {
  const form = new FormData()
  form.append('file', file)
  return request<ZoneImportResult>({ url: '/domains/zones/import', method: 'post', data: form })
}

async function downloadXlsx(url: string, filename: string, params?: Record<string, unknown>) {
  const resp = await http.get(url, { params, responseType: 'blob' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(resp.data as Blob)
  link.download = filename
  link.click()
  URL.revokeObjectURL(link.href)
}

export function downloadDomainImportTemplate() {
  return downloadXlsx('/domains/zones/import-template', 'Zone导入模板.xlsx')
}

export function exportDomains(params: Omit<DomainQuery, 'page' | 'page_size'>) {
  return downloadXlsx('/domains/zones/export', 'Zone台账.xlsx', params)
}
