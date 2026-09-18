<script setup lang="ts">
// Zone 台账：筛选、绑定、快照同步、Excel 导入导出与本地解绑。
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  CloudSyncOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  EyeOutlined,
  GlobalOutlined,
  LinkOutlined,
  SearchOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue'
import * as domainApi from '@/api/domain'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const route = useRoute()
const router = useRouter()
const canWrite = userStore.hasPerm('domain:write')
const canDelete = userStore.hasPerm('domain:delete')

const providerText: Record<domainApi.DomainProvider, string> = {
  aws_route53: 'AWS Route 53',
  tencent_dnspod: '腾讯云 DNSPod',
  google_cloud_dns: 'Google Cloud DNS',
}
const providerOptions = (Object.keys(providerText) as domainApi.DomainProvider[]).map((value) => ({
  value,
  label: providerText[value],
}))

const syncText: Record<domainApi.DomainSyncStatus, { text: string; color: string }> = {
  success: { text: '已同步', color: 'success' },
  failed: { text: '同步失败', color: 'error' },
  syncing: { text: '同步中', color: 'processing' },
}
const syncOptions = (Object.keys(syncText) as domainApi.DomainSyncStatus[]).map((value) => ({
  value,
  label: syncText[value].text,
}))

const loading = ref(false)
const items = ref<domainApi.DomainItem[]>([])
const total = ref(0)
const query = reactive({
  page: 1,
  page_size: 20,
  keyword: '',
  provider: undefined as domainApi.DomainProvider | undefined,
  sync_status: undefined as domainApi.DomainSyncStatus | undefined,
  sort_by: undefined as domainApi.DomainSortField | undefined,
  sort_order: undefined as domainApi.DomainSortOrder | undefined,
})

const columns = ref(makeResizable([
  { title: 'Zone 名称', dataIndex: 'zone_name', key: 'zone_name', width: 210, ellipsis: true, sorter: true },
  { title: '服务商', dataIndex: 'provider', key: 'provider', width: 150, sorter: true },
  { title: '凭据名称', dataIndex: 'credential_name', key: 'credential_name', width: 150, ellipsis: true, sorter: true },
  { title: '描述', dataIndex: 'description', key: 'description', width: 180, ellipsis: true, sorter: true },
  { title: '记录数', dataIndex: 'record_count', key: 'record_count', width: 88, align: 'right' as const, sorter: true },
  { title: '同步状态', dataIndex: 'sync_status', key: 'sync_status', width: 105, sorter: true },
  { title: '最近同步时间', dataIndex: 'last_synced_at', key: 'last_synced_at', width: 170, sorter: true },
  { title: '错误摘要', dataIndex: 'last_sync_error', key: 'last_sync_error', width: 220, ellipsis: true, sorter: true },
  { title: '操作', key: 'action', width: canWrite && canDelete ? 350 : canWrite ? 274 : canDelete ? 203 : 132, fixed: 'right' as const },
]))

const sortableFields: domainApi.DomainSortField[] = [
  'zone_name', 'provider', 'credential_name', 'description', 'record_count',
  'sync_status', 'last_synced_at', 'last_sync_error',
]

async function loadList() {
  loading.value = true
  try {
    const data = await domainApi.listDomains({
      page: query.page,
      page_size: query.page_size,
      keyword: query.keyword || undefined,
      provider: query.provider,
      sync_status: query.sync_status,
      sort_by: query.sort_by,
      sort_order: query.sort_order,
    })
    items.value = data.items
    total.value = data.total
    selectedKeys.value = []
  } finally {
    loading.value = false
  }
}

const stats = reactive({ all: 0, synced: 0, failed: 0 })

async function loadStats() {
  const [all, synced, failed] = await Promise.all([
    domainApi.listDomains({ page: 1, page_size: 1 }),
    domainApi.listDomains({ page: 1, page_size: 1, sync_status: 'success' }),
    domainApi.listDomains({ page: 1, page_size: 1, sync_status: 'failed' }),
  ])
  stats.all = all.total
  stats.synced = synced.total
  stats.failed = failed.total
}

function refreshAll() {
  void loadList()
  void loadStats()
}

function onSearch() {
  query.page = 1
  void loadList()
}

function onTableChange(
  pagination: { current?: number; pageSize?: number },
  _: unknown,
  sorter: { field?: string; order?: 'ascend' | 'descend' | null } | { field?: string; order?: 'ascend' | 'descend' | null }[],
) {
  const current = Array.isArray(sorter) ? sorter[0] : sorter
  const sortBy = current.order && sortableFields.includes(current.field as domainApi.DomainSortField)
    ? current.field as domainApi.DomainSortField
    : undefined
  const sortOrder = current.order === 'ascend' ? 'asc' : current.order === 'descend' ? 'desc' : undefined
  const sortChanged = query.sort_by !== sortBy || query.sort_order !== sortOrder
  query.sort_by = sortBy
  query.sort_order = sortOrder
  query.page = sortChanged ? 1 : pagination.current || 1
  query.page_size = pagination.pageSize || query.page_size
  void loadList()
}

function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleString() : '—'
}

function openDetail(row: domainApi.DomainItem) {
  void router.push(`/domains/${row.id}`)
}

const syncLoadingId = ref<number | null>(null)

async function onSync(row: domainApi.DomainItem) {
  syncLoadingId.value = row.id
  try {
    await domainApi.syncDomain(row.id)
    message.success('Zone 快照已同步')
    refreshAll()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    syncLoadingId.value = null
  }
}

const descriptionVisible = ref(false)
const descriptionLoading = ref(false)
const editingDescription = ref<domainApi.DomainItem | null>(null)
const descriptionForm = reactive({ description: '' })

function openDescriptionEdit(row: domainApi.DomainItem) {
  editingDescription.value = row
  descriptionForm.description = row.description || ''
  descriptionVisible.value = true
}

async function onSaveDescription() {
  if (!editingDescription.value) return
  descriptionLoading.value = true
  try {
    await domainApi.updateDomainDescription(editingDescription.value.id, descriptionForm.description || null)
    descriptionVisible.value = false
    message.success('说明已保存')
    refreshAll()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    descriptionLoading.value = false
  }
}

async function onUnbind(row: domainApi.DomainItem) {
  try {
    await domainApi.deleteDomain(row.id)
    message.success('已解除本地绑定')
    if (items.value.length === 1 && query.page > 1) query.page -= 1
    refreshAll()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  }
}

// ---------- 批量删除（仅删除本地 Zone 台账和记录快照） ----------
const selectedKeys = ref<number[]>([])
const rowSelection = computed(() =>
  canDelete
    ? {
        fixed: true,
        selectedRowKeys: selectedKeys.value,
        onChange: (keys: Array<string | number>) => (selectedKeys.value = keys as number[]),
      }
    : undefined,
)
const batchLoading = ref(false)

async function onBatchUnbind() {
  if (!selectedKeys.value.length) return
  batchLoading.value = true
  try {
    const results = await Promise.allSettled(selectedKeys.value.map((id) => domainApi.deleteDomain(id)))
    const successCount = results.filter((result) => result.status === 'fulfilled').length
    if (successCount < results.length) message.warning(`已删除 ${successCount} 个 Zone 台账，${results.length - successCount} 个失败`)
    else message.success(`已删除 ${successCount} 个 Zone 台账`)
    if (successCount === items.value.length && query.page > 1) query.page -= 1
    refreshAll()
  } finally {
    batchLoading.value = false
  }
}

const bindVisible = ref(false)
const bindLoading = ref(false)
const discoverLoading = ref(false)
const credentialLoading = ref(false)
const credentials = ref<domainApi.ProviderCredential[]>([])
const discovered = ref<domainApi.DiscoveredZone[]>([])
const discoveredKeyword = ref('')
const selectedRemoteZoneIds = ref<string[]>([])
const bindTask = ref<domainApi.ZoneBindTask | null>(null)
let bindPollTimer: number | null = null
const bindForm = reactive({
  provider: undefined as domainApi.DomainProvider | undefined,
  credential_id: undefined as number | undefined,
  description: '',
})

const bindTaskFinished = computed(() => (
  bindTask.value?.status === 'success' ||
  bindTask.value?.status === 'partial_failed' ||
  bindTask.value?.status === 'failed'
))
const bindTaskProgress = computed(() => {
  const task = bindTask.value
  if (!task || !task.total_count) return 0
  return Math.round((task.success_count + task.skipped_count + task.failed_count) / task.total_count * 100)
})
const bindTaskStatusText: Record<domainApi.ZoneBindTaskStatus, string> = {
  queued: '等待处理',
  running: '绑定中',
  success: '已完成',
  partial_failed: '部分失败',
  failed: '绑定失败',
}
const bindItemStatusText: Record<domainApi.ZoneBindTaskItemStatus, string> = {
  pending: '等待处理',
  running: '处理中',
  success: '成功',
  skipped: '跳过',
  failed: '失败',
}
const bindItemStatusColor: Record<domainApi.ZoneBindTaskItemStatus, string> = {
  pending: 'default',
  running: 'processing',
  success: 'success',
  skipped: 'warning',
  failed: 'error',
}

/** 已发现的 Zone 在本地筛选，避免输入搜索词时重复请求 DNS 服务商。 */
const filteredDiscovered = computed(() => {
  const keyword = discoveredKeyword.value.trim().toLowerCase()
  if (!keyword) return discovered.value
  return discovered.value.filter((zone) => (
    zone.zone_name.toLowerCase().includes(keyword) || zone.remote_zone_id.toLowerCase().includes(keyword)
  ))
})

const discoveredRowSelection = computed(() => ({
  preserveSelectedRowKeys: true,
  selectedRowKeys: selectedRemoteZoneIds.value,
  onChange: (keys: Array<string | number>) => {
    selectedRemoteZoneIds.value = keys.map(String)
  },
}))

function openBind() {
  stopBindPolling()
  bindForm.provider = undefined
  bindForm.credential_id = undefined
  bindForm.description = ''
  credentials.value = []
  discovered.value = []
  discoveredKeyword.value = ''
  selectedRemoteZoneIds.value = []
  bindTask.value = null
  bindVisible.value = true
}

function closeBind() {
  stopBindPolling()
  bindVisible.value = false
}

async function onProviderChange() {
  bindForm.credential_id = undefined
  credentials.value = []
  discovered.value = []
  discoveredKeyword.value = ''
  selectedRemoteZoneIds.value = []
  bindTask.value = null
  if (!bindForm.provider) return
  credentialLoading.value = true
  try {
    const data = await domainApi.listCompatibleCredentials(bindForm.provider)
    credentials.value = data.items
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    credentialLoading.value = false
  }
}

async function onDiscover() {
  if (!bindForm.provider || !bindForm.credential_id) {
    message.warning('请先选择服务商和凭据')
    return
  }
  discoverLoading.value = true
  try {
    const data = await domainApi.discoverZones({
      provider: bindForm.provider,
      credential_id: bindForm.credential_id,
    })
    discovered.value = data.items
    discoveredKeyword.value = ''
    selectedRemoteZoneIds.value = []
    bindTask.value = null
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    discoverLoading.value = false
  }
}

async function onBind() {
  if (!bindForm.provider || !bindForm.credential_id || !selectedRemoteZoneIds.value.length) {
    message.warning('请选择至少一个已发现的 Zone')
    return
  }
  bindLoading.value = true
  try {
    const zoneNames = new Map(discovered.value.map((zone) => [zone.remote_zone_id, zone.zone_name]))
    bindTask.value = await domainApi.bindZones({
      provider: bindForm.provider,
      credential_id: bindForm.credential_id,
      selections: selectedRemoteZoneIds.value.map((remote_zone_id) => ({
        remote_zone_id,
        zone_name: zoneNames.get(remote_zone_id),
        description: bindForm.description || undefined,
      })),
    })
    void pollBindTask(bindTask.value.id)
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    bindLoading.value = false
  }
}

function stopBindPolling() {
  if (bindPollTimer !== null) {
    window.clearTimeout(bindPollTimer)
    bindPollTimer = null
  }
}

function scheduleBindTaskPoll(taskId: number, delay = 1200) {
  stopBindPolling()
  bindPollTimer = window.setTimeout(() => {
    bindPollTimer = null
    void pollBindTask(taskId)
  }, delay)
}

function notifyBindTaskFinished(task: domainApi.ZoneBindTask) {
  if (task.status === 'success') {
    message.success(`已绑定 ${task.success_count} 个 Zone`)
  } else if (task.status === 'partial_failed') {
    message.warning(`已绑定 ${task.success_count} 个 Zone，失败 ${task.failed_count} 个`)
  } else {
    message.error(`绑定失败 ${task.failed_count} 个 Zone`)
  }
}

async function pollBindTask(taskId: number) {
  if (!bindVisible.value || bindTask.value?.id !== taskId) return
  try {
    const task = await domainApi.getZoneBindTask(taskId)
    if (!bindVisible.value || bindTask.value?.id !== taskId) return
    bindTask.value = task
    if (task.status === 'success' || task.status === 'partial_failed' || task.status === 'failed') {
      notifyBindTaskFinished(task)
      refreshAll()
      return
    }
    scheduleBindTaskPoll(taskId)
  } catch {
    if (bindVisible.value && bindTask.value?.id === taskId) scheduleBindTaskPoll(taskId, 5000)
  }
}

const importVisible = ref(false)
const importLoading = ref(false)
const importFile = ref<File | null>(null)
const importResult = ref<domainApi.ZoneImportResult | null>(null)
const importColumns = [
  { title: '行号', dataIndex: 'row', key: 'row', width: 82 },
  { title: '处理结果', dataIndex: 'reason', key: 'reason' },
]

function openImport() {
  importFile.value = null
  importResult.value = null
  importVisible.value = true
}

function onBeforeUpload(file: File) {
  importFile.value = file
  importResult.value = null
  return false
}

async function onDownloadTemplate() {
  try {
    await domainApi.downloadDomainImportTemplate()
  } catch {
    message.error('模板下载失败')
  }
}

async function onSubmitImport() {
  if (!importFile.value) {
    message.warning('请选择 xlsx 文件')
    return
  }
  importLoading.value = true
  try {
    const result = await domainApi.importDomains(importFile.value)
    importResult.value = result
    if (result.failed_rows.length || result.skipped_rows.length) {
      message.warning(`成功 ${result.success_count} 条，跳过 ${result.skipped_rows.length} 条，失败 ${result.failed_rows.length} 条`)
    } else {
      message.success(`导入成功 ${result.success_count} 条`)
    }
    refreshAll()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    importLoading.value = false
  }
}

async function onExport() {
  try {
    await domainApi.exportDomains({
      keyword: query.keyword || undefined,
      provider: query.provider,
      sync_status: query.sync_status,
    })
  } catch {
    message.error('导出失败')
  }
}

onMounted(() => {
  const keyword = route.query.keyword
  if (typeof keyword === 'string' && keyword) {
    query.keyword = keyword
    void router.replace({ path: route.path })
  }
  refreshAll()
})

onBeforeUnmount(stopBindPolling)
</script>

<template>
  <div>
    <div class="op-hero op-hero--blue">
      <div class="op-hero-icon"><GlobalOutlined /></div>
      <div>
        <div class="op-hero-title">域名管理</div>
        <div class="op-hero-sub">公网 Zone 台账与 DNS 记录快照</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat"><b>{{ stats.all }}</b><span>已绑定 Zone</span></div>
        <div class="op-hero-stat"><b>{{ stats.synced }}</b><span>同步正常</span></div>
        <div class="op-hero-stat"><b>{{ stats.failed }}</b><span>同步失败</span></div>
      </div>
    </div>

    <div class="toolbar">
      <a-input
        v-model:value="query.keyword"
        class="keyword-input"
        placeholder="搜索 Zone 名称或描述"
        allow-clear
        @press-enter="onSearch"
      >
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-select
        v-model:value="query.provider"
        class="filter-select provider-select"
        placeholder="服务商"
        allow-clear
        :options="providerOptions"
        @change="onSearch"
      />
      <a-select
        v-model:value="query.sync_status"
        class="filter-select"
        placeholder="同步状态"
        allow-clear
        :options="syncOptions"
        @change="onSearch"
      />
      <div class="toolbar-actions">
        <a-popconfirm
          v-if="canDelete"
          :title="`确认删除选中的 ${selectedKeys.length} 个 Zone 台账？远端 Zone 和 DNS 记录不会被删除。`"
          :disabled="!selectedKeys.length"
          @confirm="onBatchUnbind"
        >
          <a-button danger :disabled="!selectedKeys.length" :loading="batchLoading">
            <DeleteOutlined />批量解绑{{ selectedKeys.length ? `（${selectedKeys.length}）` : '' }}
          </a-button>
        </a-popconfirm>
        <a-button @click="onExport"><DownloadOutlined />导出</a-button>
        <a-button v-if="canWrite" @click="openImport"><UploadOutlined />导入</a-button>
        <a-button v-if="canWrite" type="primary" @click="openBind"><LinkOutlined />绑定 Zone</a-button>
      </div>
    </div>

    <a-table
      :columns="columns"
      :data-source="items"
      :show-sorter-tooltip="false"
      :loading="loading"
      row-key="id"
      bordered
      :scroll="{ x: 1710 }"
      :row-selection="rowSelection"
      @resize-column="onResizeColumn"
      @change="onTableChange"
      :pagination="{
        current: query.page,
        pageSize: query.page_size,
        total,
        showSizeChanger: true,
        showQuickJumper: true,
        showTotal: (count: number) => `共 ${count} 个 Zone`,
      }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'provider'">
          <a-tag color="blue">{{ providerText[record.provider as domainApi.DomainProvider] || record.provider }}</a-tag>
        </template>
        <template v-else-if="column.key === 'credential_name'">{{ record.credential_name || '—' }}</template>
        <template v-else-if="column.key === 'description'">{{ record.description || '—' }}</template>
        <template v-else-if="column.key === 'sync_status'">
          <a-tag :color="syncText[record.sync_status as domainApi.DomainSyncStatus]?.color">
            {{ syncText[record.sync_status as domainApi.DomainSyncStatus]?.text || record.sync_status }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'last_synced_at'">{{ formatTime(record.last_synced_at) }}</template>
        <template v-else-if="column.key === 'last_sync_error'">
          <a-tooltip v-if="record.last_sync_error" :title="record.last_sync_error">
            <span class="error-summary">{{ record.last_sync_error }}</span>
          </a-tooltip>
          <template v-else>—</template>
        </template>
        <template v-else-if="column.key === 'action'">
          <a-space :size="6">
            <a-button size="small" class="op-btn-cyan" @click="openDetail(record as domainApi.DomainItem)">
              <EyeOutlined />DNS 管理
            </a-button>
            <a-button
              v-if="canWrite"
              size="small"
              class="op-btn-green"
              :loading="syncLoadingId === record.id"
              @click="onSync(record as domainApi.DomainItem)"
            >
              <CloudSyncOutlined />同步
            </a-button>
            <a-button v-if="canWrite" size="small" class="op-btn-blue" @click="openDescriptionEdit(record as domainApi.DomainItem)">
              <EditOutlined />说明
            </a-button>
            <a-popconfirm
              v-if="canDelete"
              title="确认解除本地 Zone 绑定？远端 Zone 和 DNS 记录不会被删除。"
              @confirm="onUnbind(record as domainApi.DomainItem)"
            >
              <a-button size="small" danger><DeleteOutlined />解绑</a-button>
            </a-popconfirm>
          </a-space>
        </template>
      </template>
    </a-table>

    <a-modal
      v-model:open="descriptionVisible"
      title="编辑 Zone 说明"
      :confirm-loading="descriptionLoading"
      @ok="onSaveDescription"
    >
      <a-form layout="vertical">
        <a-form-item label="Zone 名称"><a-input :value="editingDescription?.zone_name" disabled /></a-form-item>
        <a-form-item label="说明"><a-textarea v-model:value="descriptionForm.description" :rows="3" :maxlength="255" /></a-form-item>
      </a-form>
    </a-modal>

    <a-modal
      v-model:open="bindVisible"
      title="绑定公网 Zone"
      :width="820"
      :footer="null"
      :body-style="{ maxHeight: 'calc(100vh - 176px)', overflowX: 'hidden', overflowY: 'auto' }"
      @cancel="closeBind"
    >
      <template v-if="bindTask">
        <div class="bind-task-head">
          <span>已处理 {{ bindTask.success_count + bindTask.skipped_count + bindTask.failed_count }} / {{ bindTask.total_count }} 个 Zone</span>
          <a-tag :color="bindTask.status === 'success' ? 'success' : bindTask.status === 'partial_failed' ? 'warning' : bindTask.status === 'failed' ? 'error' : 'processing'">
            {{ bindTaskStatusText[bindTask.status] }}
          </a-tag>
        </div>
        <a-progress
          class="bind-task-progress"
          :percent="bindTaskProgress"
          :status="bindTask.status === 'partial_failed' || bindTask.status === 'failed' ? 'exception' : bindTaskFinished ? 'success' : 'active'"
        />
        <a-descriptions bordered size="small" :column="3" class="bind-task-summary">
          <a-descriptions-item label="成功">{{ bindTask.success_count }}</a-descriptions-item>
          <a-descriptions-item label="跳过">{{ bindTask.skipped_count }}</a-descriptions-item>
          <a-descriptions-item label="失败">{{ bindTask.failed_count }}</a-descriptions-item>
        </a-descriptions>
        <a-alert v-if="bindTask.last_error" class="bind-task-error" type="warning" show-icon :message="bindTask.last_error" />
        <a-table
          v-if="bindTask.items?.length"
          class="result-table"
          :columns="[
            { title: 'Zone 名称', dataIndex: 'zone_name', key: 'zone_name', width: 200, ellipsis: true },
            { title: '远端 Zone ID', dataIndex: 'remote_zone_id', key: 'remote_zone_id', width: 210, ellipsis: true },
            { title: '状态', key: 'status', width: 100 },
            { title: '说明', dataIndex: 'reason', key: 'reason', ellipsis: true },
          ]"
          :data-source="bindTask.items"
          row-key="remote_zone_id"
          size="small"
          :pagination="false"
          :scroll="{ y: 260 }"
        >
          <template #bodyCell="{ column, record }">
            <a-tag v-if="column.key === 'status'" :color="bindItemStatusColor[record.status as domainApi.ZoneBindTaskItemStatus]">
              {{ bindItemStatusText[record.status as domainApi.ZoneBindTaskItemStatus] }}
            </a-tag>
            <template v-else-if="column.key === 'zone_name'">{{ record.zone_name || '—' }}</template>
            <template v-else-if="column.key === 'reason'">{{ record.reason || '—' }}</template>
          </template>
        </a-table>
        <div class="modal-actions">
          <span>{{ bindTaskFinished ? '任务已完成' : '后台处理中' }}</span>
          <a-button @click="closeBind">关闭</a-button>
        </div>
      </template>
      <template v-else>
        <a-form layout="vertical" class="bind-form">
          <div class="form-row">
            <a-form-item label="服务商" required class="form-col">
              <a-select
                v-model:value="bindForm.provider"
                placeholder="选择服务商"
                :options="providerOptions"
                @change="onProviderChange"
              />
            </a-form-item>
            <a-form-item label="凭据" required class="form-col">
              <a-select
                v-model:value="bindForm.credential_id"
                placeholder="先选择服务商"
                :loading="credentialLoading"
                :disabled="!bindForm.provider"
                :options="credentials.map((item) => ({ value: item.id, label: item.description ? `${item.name} (${item.description})` : item.name }))"
              />
            </a-form-item>
            <a-form-item label=" " class="discover-action">
              <a-button :loading="discoverLoading" :disabled="!bindForm.credential_id" @click="onDiscover">
                <SearchOutlined />发现 Zone
              </a-button>
            </a-form-item>
          </div>
          <a-form-item label="绑定说明">
            <a-input v-model:value="bindForm.description" :maxlength="255" placeholder="将应用到本次选择的 Zone，可留空" />
          </a-form-item>
        </a-form>

        <a-input
          v-if="discovered.length"
          v-model:value="discoveredKeyword"
          class="discovered-search"
          allow-clear
          placeholder="搜索 Zone 名称或远端 Zone ID"
        >
          <template #prefix><SearchOutlined /></template>
        </a-input>
        <a-table
          :columns="[
            { title: 'Zone 名称', dataIndex: 'zone_name', key: 'zone_name' },
            { title: '远端 Zone ID', dataIndex: 'remote_zone_id', key: 'remote_zone_id', width: 300 },
          ]"
          :data-source="filteredDiscovered"
          :row-selection="discoveredRowSelection"
          row-key="remote_zone_id"
          size="small"
          :pagination="false"
          :scroll="{ y: 360 }"
          :locale="{ emptyText: discovered.length ? '未找到匹配的 Zone' : bindForm.credential_id ? '点击发现 Zone 获取可绑定的公网 Zone' : '请先选择服务商和凭据' }"
        />
        <div class="modal-actions">
          <span>已选择 {{ selectedRemoteZoneIds.length }} 个 Zone</span>
          <a-button type="primary" :loading="bindLoading" :disabled="!selectedRemoteZoneIds.length" @click="onBind">
            <LinkOutlined />绑定所选 Zone
          </a-button>
        </div>
      </template>
    </a-modal>

    <a-modal v-model:open="importVisible" title="导入 Zone 台账" :width="720" :footer="null">
      <div class="import-head">
        <a-button @click="onDownloadTemplate"><DownloadOutlined />下载模板</a-button>
        <span>仅导入 Zone 台账；远端记录将在绑定成功后生成快照。</span>
      </div>
      <a-upload accept=".xlsx" :max-count="1" :before-upload="onBeforeUpload">
        <a-button><UploadOutlined />选择 xlsx 文件</a-button>
      </a-upload>
      <div class="modal-actions import-actions">
        <span>{{ importFile ? `已选择：${importFile.name}` : '尚未选择文件' }}</span>
        <a-button type="primary" :loading="importLoading" @click="onSubmitImport">开始导入</a-button>
      </div>
      <div v-if="importResult" class="import-result">
        <a-alert
          :message="`成功 ${importResult.success_count} 条，跳过 ${importResult.skipped_rows.length} 条，失败 ${importResult.failed_rows.length} 条`"
          :type="importResult.failed_rows.length ? 'warning' : 'success'"
          show-icon
        />
        <div v-if="importResult.skipped_rows.length" class="result-section">
          <h4>跳过行</h4>
          <a-table :columns="importColumns" :data-source="importResult.skipped_rows" row-key="row" size="small" :pagination="false" />
        </div>
        <div v-if="importResult.failed_rows.length" class="result-section">
          <h4>失败行</h4>
          <a-table :columns="importColumns" :data-source="importResult.failed_rows" row-key="row" size="small" :pagination="false" />
        </div>
      </div>
    </a-modal>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 16px;
}
.keyword-input {
  width: 240px;
}
.filter-select {
  width: 128px;
}
.provider-select {
  width: 160px;
}
.toolbar-actions {
  display: flex;
  gap: 10px;
  margin-left: auto;
}
.error-summary {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.form-row {
  display: flex;
  gap: 16px;
}
.form-col {
  flex: 1;
}
.discover-action {
  flex: 0 0 auto;
}
.discovered-search {
  width: 100%;
  margin-bottom: 12px;
}
.bind-task-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--text-2);
  font-size: 13px;
}
.bind-task-progress {
  margin: 12px 0;
}
.bind-task-summary {
  margin-top: 12px;
}
.bind-task-error {
  margin-top: 12px;
}
.modal-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 16px;
  color: var(--text-3);
  font-size: 12px;
}
.result-table,
.import-result {
  margin-top: 16px;
}
.import-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  color: var(--text-3);
  font-size: 12px;
}
.import-actions {
  margin-top: 12px;
}
.result-section {
  margin-top: 16px;
}
.result-section h4 {
  margin: 0 0 8px;
  color: var(--text-2);
  font-size: 13px;
}
@media (max-width: 760px) {
  .toolbar-actions {
    width: 100%;
    margin-left: 0;
  }
  .form-row {
    display: block;
  }
  .discover-action {
    margin-bottom: 0;
  }
  .op-hero-extra {
    display: none;
  }
}
</style>
