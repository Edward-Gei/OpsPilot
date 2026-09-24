// 应用配置 API：正文仅在编辑器内持有，任务与列表响应不包含密文。
import { request } from './http'
import type { PageResult } from './system'

const prefix = '/application-configs'

export type ConfigProvider = 'apollo' | 'nacos' | 'consul'
export type ConfigFormat = 'properties' | 'yaml' | 'json' | 'text' | 'consul_kv'
export type DriftStatus = 'clean' | 'drifted' | 'remote_missing' | 'sync_failed'
export type TaskStatus = 'queued' | 'running' | 'success' | 'partial_failed' | 'failed'

export interface PlatformInstance {
  id: number
  name: string
  provider: ConfigProvider
  base_url: string
  credential_id: number
  description: string | null
  enabled: boolean
  first_bound_at: string | null
  compatibility_version: string | null
  last_probed_at: string | null
  last_probe_result: string | null
  last_probe_error: string | null
}

export interface ConfigFileBrief {
  id: number
  name: string
  description: string | null
  platform_instance_id: number
  platform_instance_name: string | null
  provider: ConfigProvider | null
  locator: Record<string, string>
  content_format: ConfigFormat
  approval_role_id: number
  approval_role_name: string | null
  application_ids: number[]
  application_count: number
  current_version_id: number | null
  current_version_no: number | null
  current_snapshot_id: number | null
  latest_snapshot_id: number | null
  status: 'active' | 'archived'
  drift_status: DriftStatus
  last_synced_at: string | null
  last_sync_error: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ConfigFileSelection {
  name: string
  locator: Record<string, string>
  content_format: ConfigFormat
  approval_role_id: number
  application_ids: number[]
  description?: string
  initial_content?: string
}

export interface DiscoveredResource {
  locator: Record<string, string>
  display_name: string
  revision: string | null
}

export interface NacosNamespace {
  id: string
  name: string
}

export interface ConfigTaskItem {
  id: number
  name: string | null
  status: 'pending' | 'running' | 'success' | 'skipped' | 'failed'
  config_file_id: number | null
  reason: string | null
}

export interface ConfigTask {
  id: number
  kind: 'import' | 'sync' | 'publish'
  config_file_id: number | null
  config_version_id: number | null
  status: TaskStatus
  total_count: number
  success_count: number
  skipped_count: number
  failed_count: number
  last_error: string | null
  created_at: string | null
  finished_at: string | null
  items?: ConfigTaskItem[]
}

export interface ConfigDraft {
  id: number | null
  content: string
  base_version_id: number | null
  base_snapshot_id: number | null
}

export interface ConfigVersion {
  id: number
  version_no: number
  status: 'pending_approval' | 'approved' | 'publishing' | 'published' | 'rejected' | 'invalidated'
  source: 'external_import' | 'opspilot_publish'
  approval_role_id: number | null
  submitted_by: number | null
  approved_by: number | null
  can_approve?: boolean
  created_at: string | null
  published_at: string | null
}

export interface ConfigApprovalTodo {
  version_id: number
  file_id: number
  file_name: string
  version_no: number
  approval_role_name: string | null
  submitter_name: string | null
  submitted_at: string
}

export interface ConfigApprovalContent {
  version_id: number
  file_id: number
  file_name: string
  version_no: number
  content_format: ConfigFormat
  content: string
}

export interface DriftView {
  drift_status: DriftStatus
  latest_snapshot_id: number | null
  baseline_version_id: number | null
  baseline_content: string | null
  external_exists: boolean
  external_content: string | null
}

export interface NamedOption { id: number; name: string }
export interface CredentialOption extends NamedOption { auth_type: string }

export function listConfigFiles(params: { page: number; page_size: number; keyword?: string }) {
  return request<PageResult<ConfigFileBrief>>({ url: `${prefix}/files`, method: 'get', params })
}

export function getConfigFile(id: number) {
  return request<ConfigFileBrief>({ url: `${prefix}/files/${id}`, method: 'get' })
}

export function createConfigFile(platformInstanceId: number, selection: ConfigFileSelection) {
  return request<{ id: number }>({
    url: `${prefix}/files`, method: 'post',
    data: { platform_instance_id: platformInstanceId, selection },
  })
}

export function updateConfigFile(id: number, data: {
  name?: string
  description?: string | null
  approval_role_id?: number
  application_ids?: number[]
}) {
  return request<ConfigFileBrief>({ url: `${prefix}/files/${id}`, method: 'put', data })
}

export function archiveConfigFile(id: number) {
  return request<null>({ url: `${prefix}/files/${id}/archive`, method: 'post' })
}

export function deleteConfigFile(id: number) {
  return request<null>({ url: `${prefix}/files/${id}`, method: 'delete' })
}

export function listPlatformInstances() {
  return request<{ items: PlatformInstance[] }>({ url: `${prefix}/platform-instances`, method: 'get' })
}

export function createPlatformInstance(data: {
  name: string; provider: ConfigProvider; base_url: string; credential_id: number
  description?: string; compatibility_version?: string
}) {
  return request<{ id: number }>({ url: `${prefix}/platform-instances`, method: 'post', data })
}

export function updatePlatformInstance(id: number, data: Partial<PlatformInstance>) {
  return request<PlatformInstance>({ url: `${prefix}/platform-instances/${id}`, method: 'put', data })
}

export function deletePlatformInstance(id: number) {
  return request<null>({ url: `${prefix}/platform-instances/${id}`, method: 'delete' })
}

export function probePlatformInstance(id: number) {
  return request<PlatformInstance>({ url: `${prefix}/platform-instances/${id}/probe`, method: 'post' })
}

export function listCompatibleCredentials(provider: ConfigProvider) {
  return request<{ items: CredentialOption[] }>({
    url: `${prefix}/compatible-credentials`, method: 'get', params: { provider },
  })
}

export function listApprovalRoles() {
  return request<{ items: NamedOption[] }>({ url: `${prefix}/approval-roles`, method: 'get' })
}

export function listConfigApprovalTodo(params: { page: number; page_size: number }) {
  return request<PageResult<ConfigApprovalTodo>>({ url: `${prefix}/approval-todo`, method: 'get', params })
}

export function getConfigApprovalContent(versionId: number) {
  return request<ConfigApprovalContent>({ url: `${prefix}/approval-todo/${versionId}/content`, method: 'get' })
}

export function listCmdbApplications(keyword?: string) {
  return request<{ items: NamedOption[] }>({
    url: `${prefix}/cmdb-applications`, method: 'get', params: { keyword },
  })
}

export function discoverConfigResources(platformInstanceId: number, query: Record<string, string>) {
  return request<{ items: DiscoveredResource[] }>({
    url: `${prefix}/discover`, method: 'post', data: { platform_instance_id: platformInstanceId, query },
  })
}

export function listNacosNamespaces(platformInstanceId: number) {
  return request<{ items: NacosNamespace[] }>({
    url: `${prefix}/platform-instances/${platformInstanceId}/namespaces`, method: 'get',
  })
}

export function createConfigImportTask(platformInstanceId: number, selections: ConfigFileSelection[]) {
  return request<ConfigTask>({
    url: `${prefix}/import-tasks`, method: 'post',
    data: { platform_instance_id: platformInstanceId, selections },
  })
}

export function listConfigImportTasks() {
  return request<{ items: ConfigTask[] }>({ url: `${prefix}/import-tasks`, method: 'get' })
}

export function getConfigTask(id: number) {
  return request<ConfigTask>({
    url: `${prefix}/tasks/${id}`, method: 'get', silentTransportError: true,
  })
}

export function listConfigFileTasks(id: number) {
  return request<{ items: ConfigTask[] }>({ url: `${prefix}/files/${id}/tasks`, method: 'get' })
}

export function getDraft(id: number) {
  return request<ConfigDraft>({ url: `${prefix}/files/${id}/draft`, method: 'get' })
}

export function saveDraft(id: number, content: string) {
  return request<null>({ url: `${prefix}/files/${id}/draft`, method: 'put', data: { content } })
}

export function discardDraft(id: number) {
  return request<null>({ url: `${prefix}/files/${id}/draft`, method: 'delete' })
}

export function listVersions(id: number) {
  return request<{ items: ConfigVersion[] }>({ url: `${prefix}/files/${id}/versions`, method: 'get' })
}

export function getVersionContent(id: number, versionId: number) {
  return request<{ version_id: number; content: string }>({
    url: `${prefix}/files/${id}/versions/${versionId}/content`, method: 'get',
  })
}

export function submitCandidate(id: number) {
  return request<ConfigVersion>({ url: `${prefix}/files/${id}/candidates`, method: 'post' })
}

export function approveCandidate(id: number, versionId: number) {
  return request<ConfigVersion>({
    url: `${prefix}/files/${id}/candidates/${versionId}/approve`, method: 'post',
  })
}

export function rejectCandidate(id: number, versionId: number, reason?: string) {
  return request<ConfigVersion>({
    url: `${prefix}/files/${id}/candidates/${versionId}/reject`, method: 'post', data: { reason },
  })
}

export function syncConfigFile(id: number) {
  return request<ConfigTask>({ url: `${prefix}/files/${id}/sync`, method: 'post' })
}

export function publishVersion(id: number, versionId: number, confirmedSnapshotId?: number) {
  return request<ConfigTask>({
    url: `${prefix}/files/${id}/versions/${versionId}/publish`, method: 'post',
    data: confirmedSnapshotId ? { confirmed_snapshot_id: confirmedSnapshotId } : undefined,
  })
}

export function getDriftView(id: number, versionId?: number) {
  return request<DriftView>({
    url: `${prefix}/files/${id}/drift`, method: 'get', params: versionId ? { version_id: versionId } : undefined,
  })
}

export function importDrift(id: number) {
  return request<ConfigVersion>({ url: `${prefix}/files/${id}/drift/import`, method: 'post' })
}
