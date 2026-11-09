// 数据库管理：实例台账、受控任务及独立审计；请求保持短时，执行状态由任务查询取得。
import http, { request, type RequestConfig } from './http'
import type { PageResult } from './system'

export type DatabaseEngine = 'mysql' | 'postgresql' | 'mongodb' | 'clickhouse' | 'redis'

export type TaskStatus = 'queued' | 'running' | 'stopping' | 'recovering' | 'succeeded' | 'failed' | 'cancelled' | 'timed_out' | 'rejected' | 'queue_expired'

export interface Relation {
  id: number
  name: string
}

export interface CredentialSummary extends Relation {
  login_user: string | null
  auth_type: string
}

export interface InstanceItem {
  id: number
  name: string
  description: string | null
  engine: DatabaseEngine
  business_line: 'mitrade' | 'tradingkey' | null
  cluster_name: string | null
  cluster_role: string | null
  version: string | null
  host: string
  port: number
  enabled: boolean
  needs_configuration: boolean
  created_at: string | null
  updated_at: string | null
  apps: (Relation & { language?: string | null; deploy_type?: string | null })[]
  hosts: {
    id: number
    hostname: string
    ip: string
    environment?: string | null
  }[]
  last_probe_at: string | null
  last_probe_status: 'untested' | 'succeeded' | 'failed'
  last_probe_message: string | null
}

export interface ConnectionOptions {
  tls: boolean
  tls_verify: boolean
  default_database?: string
  default_schema?: string
  auth_database?: string
  database?: number
  auth_mode?: 'password_only' | 'acl'
}

export interface InstanceWrite {
  name: string
  description: string | null
  engine: DatabaseEngine
  environment: 'demo' | 'stage' | 'prod'
  enabled: boolean
  host: string
  port: number
  credential_id: number | null
  business_line: 'mitrade' | 'tradingkey' | null
  cluster_name: string | null
  cluster_role: string | null
  version: string | null
  connection_options: ConnectionOptions
  app_ids?: number[]
  host_ids?: number[]
}

export interface InstanceDetail extends InstanceItem, InstanceWrite {
  config_revision: number
  credential: CredentialSummary | null
  app_ids: number[]
  host_ids: number[]
}

export interface InstanceQuery {
  scope: 'management' | 'query'
  page?: number
  page_size?: number
  keyword?: string
  engine?: DatabaseEngine
  business_line?: string
  enabled?: boolean
  probe_status?: string
  cluster_name?: string
  instance_id?: number
  app_id?: number
  host_id?: number
  sort_by?: 'name' | 'description' | 'engine' | 'business_line' | 'cluster_name' | 'cluster_role' | 'version' | 'address' | 'apps' | 'hosts' | 'status' | 'created_at'
  sort_order?: 'asc' | 'desc'
}

export interface QueryContext {
  database?: string
  schema?: string
  table?: string
  collection?: string
}

export type MetadataOperation = 'databases' | 'schemas' | 'tables' | 'columns' | 'collections' | 'fields'

export type MongoOperation =
  | {
    type: 'find'
    filter: Record<string, unknown>
    projection: Record<string, unknown> | null
    sort: {
      field: string
      direction: 1 | -1
    }[]
    limit: number
  }
  | {
    type: 'count_documents'
    filter: Record<string, unknown>
  }
  | {
    type: 'distinct'
    field: string
    filter: Record<string, unknown>
    limit: number
  }

export type RedisOperation =
  | {
    type: 'scan'
    pattern: string
    cursor?: string
    count: number
  }
  | {
    type: 'type' | 'ttl' | 'length'
    key: string
  }
  | {
    type: 'string_preview'
    key: string
    offset: number
    length: number
  }
  | {
    type: 'hash_page' | 'set_page'
    key: string
    cursor?: string
    count: number
  }
  | {
    type: 'list_page' | 'zset_page'
    key: string
    offset: number
    count: number
  }

export type TaskRequest =
  | {
    kind: 'probe'
    instance_id: number
  }
  | {
    kind: 'sql'
    instance_id: number
    context: QueryContext
    sql: string
  }
  | {
    kind: 'metadata'
    instance_id: number
    context: QueryContext
    operation: MetadataOperation
  }
  | {
    kind: 'mongo_read'
    instance_id: number
    context: {
      database: string
      collection: string
    }
    operation: MongoOperation
  }
  | {
    kind: 'redis_read'
    instance_id: number
    context: {
      database: number
    }
    operation: RedisOperation
  }

export interface TaskSubmission {
  task_id: number
  status: TaskStatus
  executed_statement: string | null
  operation_summary: string
}

export interface ClientPolicy {
  execution_timeout_seconds: number
  max_result_rows: number
  max_result_bytes: number
  result_ttl_minutes: number
}

export interface TaskView extends TaskSubmission {
  kind: TaskRequest['kind']
  instance_id: number
  instance_name: string
  engine: DatabaseEngine
  context: Record<string, unknown>
  submitted_text: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  expires_at: string | null
  stop_reason: string | null
  error_code: string | null
  error_message: string | null
  stop_confirmation: Record<string, unknown> | null
  result_summary: Record<string, unknown> | null
  policy_snapshot: Partial<ClientPolicy> | null
}

export interface ResultPage {
  columns: {
    ordinal: number
    name: string
    type: string
  }[]
  rows: unknown[][]
  returned_rows: number
  returned_bytes: number
  truncated: boolean
  truncate_reason: string | null
  expires_at: string
  saved_total: number
  page: number
  page_size: number
}

export interface TaskQuery {
  page?: number
  page_size?: number
  keyword?: string
  instance_id?: number
  engine?: DatabaseEngine
  kind?: string
  status?: TaskStatus
  started_from?: string
  started_to?: string
}

export interface AuditView {
  id: number
  task_id: number
  kind: TaskRequest['kind']
  status: TaskStatus
  actor_id: number
  actor_name: string
  source_ip: string | null
  instance_id: number
  instance_name: string
  engine: DatabaseEngine
  context: Record<string, unknown>
  operation_summary: string
  created_at: string
  finished_at: string | null
  error_code: string | null
  error_message: string | null
  result_summary: Record<string, unknown> | null
  submitted_text?: string | null
  executed_statement?: string | null
  target_snapshot?: Record<string, unknown>
  credential_snapshot?: Record<string, unknown>
  events?: {
    id: number
    event_type: string
    created_at: string
    validation?: Record<string, unknown> | null
    error_code?: string | null
    error_message?: string | null
    stop_confirmation?: Record<string, unknown> | null
    result_summary?: Record<string, unknown> | null
  }[]
}

const base = '/database'
export interface InstanceImportResult {
  success_count: number
  created_count: number
  updated_count: number
  failed_rows: { row: number; reason: string }[]
}

/** 导入只提交安全台账字段，凭据和关联由实例编辑入口补充。 */
export function importInstances(file: File, upsert: boolean) {
  const data = new FormData()
  data.append('file', file)
  return request<InstanceImportResult>({ url: `${base}/instances/import`, method: 'post', params: { upsert }, data })
}

async function downloadXlsx(url: string, filename: string, params?: Omit<InstanceQuery, 'page' | 'page_size'>) {
  const response = await http.get(url, { responseType: 'blob', params })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(response.data as Blob)
  link.download = filename
  try { link.click() }
  finally { URL.revokeObjectURL(link.href) }
}

export const downloadInstanceImportTemplate = () => downloadXlsx(`${base}/instances/import-template`, '数据库实例导入模板.xlsx')
export const exportInstances = (params: Omit<InstanceQuery, 'page' | 'page_size'>) => downloadXlsx(`${base}/instances/export`, '数据库实例列表.xlsx', params)
export const listInstances = (params: InstanceQuery) => request<PageResult<InstanceItem> & { cluster_names: string[] }>({ url: `${base}/instances`, params })
export const getInstance = (id: number) => request<InstanceDetail>({ url: `${base}/instances/${id}` })
export const createInstance = (data: InstanceWrite) => request<{
  id: number
}>({ url: `${base}/instances`, method: 'post', data })
export const updateInstance = (id: number, data: InstanceWrite) => request<null>({ url: `${base}/instances/${id}`, method: 'put', data })
export const deleteInstance = (id: number) => request<null>({
  url: `${base}/instances/${id}`,
  method: 'delete',
  silentTransportError: true
})
export const getInstanceRoles = (id: number) => request<{
  items: Relation[]
}>({ url: `${base}/instances/${id}/roles` })
export const updateInstanceRoles = (id: number, role_ids: number[]) => request<null>({
  url: `${base}/instances/${id}/roles`,
  method: 'put',
  data: { role_ids }
})
export const roleOptions = () => request<{
  items: Relation[]
}>({ url: `${base}/role-options` })
export const compatibleCredentials = (engine: DatabaseEngine) => request<{
  items: CredentialSummary[]
}>({ url: `${base}/compatible-credentials`, params: { engine } })
export const clientPolicy = () => request<ClientPolicy>({ url: `${base}/client-policy` })
// 页面可主动接管任务业务提示，默认仍由 HTTP 拦截器处理。
export const submitTask = (data: TaskRequest, signal?: AbortSignal, options?: Pick<RequestConfig, 'silentBusinessError'>) => request<TaskSubmission>({
  url: `${base}/tasks`,
  method: 'post',
  data,
  signal,
  silentCancel: true,
  silentTransportError: true,
  ...options
})
export const getTask = (id: number, signal?: AbortSignal, options?: Pick<RequestConfig, 'silentBusinessError'>) => request<TaskView>({
  url: `${base}/tasks/${id}`,
  signal,
  silentCancel: true,
  silentTransportError: true,
  ...options
})
export const listTasks = (params: TaskQuery) => request<PageResult<TaskView>>({ url: `${base}/tasks`, params })
export const getResult = (id: number, page = 1, page_size = 50, signal?: AbortSignal) => request<ResultPage>({
  url: `${base}/tasks/${id}/result`,
  params: { page, page_size },
  signal,
  silentCancel: true,
  silentTransportError: true
})
export const cancelTask = (id: number, signal?: AbortSignal) => request<TaskView>({
  url: `${base}/tasks/${id}/cancel`,
  method: 'post',
  signal,
  silentCancel: true,
  silentTransportError: true
})
export const listAudit = (params: TaskQuery & {
  actor_id?: number
  keyword?: string
  source_ip?: string
  database?: string
}) => request<PageResult<AuditView>>({ url: `${base}/audit/logs`, params })
export const getAudit = (id: number) => request<AuditView>({ url: `${base}/audit/logs/${id}` })
