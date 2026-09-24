<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { CloudSyncOutlined, DeleteOutlined, EditOutlined, EyeOutlined, FileTextOutlined, PlusOutlined, SearchOutlined, SettingOutlined } from '@ant-design/icons-vue'
import * as configApi from '@/api/applicationConfig'
import { useUserStore } from '@/stores/user'
import NewConfigDrawer from './NewConfigDrawer.vue'
import PlatformInstanceDrawer from './PlatformInstanceDrawer.vue'
import DriftCompareDrawer from './DriftCompareDrawer.vue'

const router = useRouter()
const userStore = useUserStore()
const canWrite = computed(() => userStore.hasPerm('config:write'))
const canDelete = computed(() => userStore.hasPerm('config:delete'))
const canManageInstances = computed(() => userStore.hasPerm('config:instance'))
const loading = ref(false)
const deletingId = ref<number | null>(null)
const items = ref<configApi.ConfigFileBrief[]>([])
const total = ref(0)
const query = reactive({ page: 1, page_size: 20, keyword: '' })
const newOpen = ref(false)
const instanceOpen = ref(false)
const driftFileId = ref<number | null>(null)
const syncingIds = reactive(new Set<number>())
const importTask = ref<configApi.ConfigTask | null>(null)
const syncPollTimers = new Map<number, number>()
const syncTaskIds = new Map<number, number>()
let importPollTimer: number | null = null

const columns = [
  { title: '配置文件', dataIndex: 'name', key: 'name', width: 190, ellipsis: true },
  { title: '平台实例', dataIndex: 'platform_instance_name', key: 'instance', width: 170, ellipsis: true },
  { title: '远端定位', key: 'locator', width: 270, ellipsis: true },
  { title: '格式', dataIndex: 'content_format', key: 'format', width: 115 },
  { title: '关联应用', dataIndex: 'application_count', key: 'apps', width: 100 },
  { title: '审批角色', dataIndex: 'approval_role_name', key: 'role', width: 130, ellipsis: true },
  { title: '当前版本', key: 'version', width: 105 },
  { title: '状态', key: 'status', width: 120 },
  { title: '最近同步', key: 'synced', width: 170 },
  { title: '操作', key: 'action', width: canWrite.value ? 270 : canDelete.value ? 160 : 88, fixed: 'right' as const },
]

function locatorText(file: configApi.ConfigFileBrief) {
  if (file.provider === 'apollo') return `${file.locator.app_id} / ${file.locator.cluster} / ${file.locator.namespace}`
  if (file.provider === 'nacos') return `${file.locator.namespace || 'public'} / ${file.locator.group} / ${file.locator.data_id}`
  return `${file.locator.datacenter} / ${file.locator.kv_prefix}`
}

function statusText(file: configApi.ConfigFileBrief) {
  if (file.status === 'archived') return { label: '已归档', color: 'default' }
  if (file.drift_status === 'drifted') return { label: '存在漂移', color: 'warning' }
  if (file.drift_status === 'remote_missing') return { label: '远端缺失', color: 'error' }
  if (file.drift_status === 'sync_failed') return { label: '同步失败', color: 'error' }
  if (!file.current_version_id) return { label: '待首次发布', color: 'processing' }
  return { label: '一致', color: 'success' }
}

async function load() {
  loading.value = true
  try {
    const data = await configApi.listConfigFiles({
      page: query.page, page_size: query.page_size, keyword: query.keyword || undefined,
    })
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function onSearch() { query.page = 1; void load() }
async function loadImportTasks() {
  const running = (await configApi.listConfigImportTasks()).items.find((item) => item.status === 'queued' || item.status === 'running')
  if (running && importTask.value?.id !== running.id && importPollTimer === null) {
    importTask.value = running
    void pollImportTask(running.id)
  }
}
function onTableChange(pagination: { current?: number; pageSize?: number }) {
  query.page = pagination.current || 1
  query.page_size = pagination.pageSize || 20
  void load()
}
function stopSyncPolling(fileId: number) {
  const timer = syncPollTimers.get(fileId)
  if (timer !== undefined) window.clearTimeout(timer)
  syncPollTimers.delete(fileId)
}
function stopImportPolling() { if (importPollTimer !== null) window.clearTimeout(importPollTimer); importPollTimer = null }

// 接入轮询由列表页持有，关闭进度弹窗不影响后台任务。
async function pollImportTask(id: number) {
  try {
    const result = await configApi.getConfigTask(id)
    if (importTask.value?.id !== id) return
    importTask.value = result
    if (result.status === 'queued' || result.status === 'running') {
      importPollTimer = window.setTimeout(() => void pollImportTask(id), 1200)
      return
    }
    stopImportPolling()
    await load()
    await loadImportTasks()
    if (result.status === 'success') message.success(`接入完成：成功 ${result.success_count}，跳过 ${result.skipped_count}`)
    else message.warning(result.last_error || `接入结束：成功 ${result.success_count}，跳过 ${result.skipped_count}，失败 ${result.failed_count}`)
  } catch {
    importPollTimer = window.setTimeout(() => void pollImportTask(id), 5000)
  }
}

async function pollSyncTask(id: number, fileId: number) {
  try {
    const result = await configApi.getConfigTask(id)
    if (syncTaskIds.get(fileId) !== id) return
    if (result.status === 'queued' || result.status === 'running') {
      syncPollTimers.set(fileId, window.setTimeout(() => void pollSyncTask(id, fileId), 1200))
      return
    }
    stopSyncPolling(fileId)
    syncTaskIds.delete(fileId)
    syncingIds.delete(fileId)
    await load()
    if (result.status === 'success') message.success('远端同步完成')
    else message.warning(result.last_error || '同步未完成，请查看任务状态')
    const file = items.value.find((item) => item.id === fileId)
    if (file?.drift_status === 'drifted' || file?.drift_status === 'remote_missing') driftFileId.value = file.id
  } catch {
    if (syncTaskIds.get(fileId) === id) {
      syncPollTimers.set(fileId, window.setTimeout(() => void pollSyncTask(id, fileId), 5000))
    }
  }
}

async function onSync(file: configApi.ConfigFileBrief) {
  if (syncingIds.has(file.id)) return
  syncingIds.add(file.id)
  try {
    const task = await configApi.syncConfigFile(file.id)
    if (!syncingIds.has(file.id)) return
    syncTaskIds.set(file.id, task.id)
    void pollSyncTask(task.id, file.id)
  } catch {
    /* 错误由请求层显示。 */
    syncingIds.delete(file.id)
  }
}

// 删除仅移除已归档的本地管理记录，归档状态由后端再次校验。
async function onDelete(file: configApi.ConfigFileBrief) {
  deletingId.value = file.id
  try {
    await configApi.deleteConfigFile(file.id)
    message.success('本地管理记录已删除；外部配置未改变')
    if (items.value.length === 1 && query.page > 1) query.page -= 1
    await load()
  } catch {
    /* 错误由请求层显示。 */
  } finally {
    deletingId.value = null
  }
}

function onCreated(result: { fileId?: number; task?: configApi.ConfigTask }) {
  if (result.task) {
    stopImportPolling()
    importTask.value = result.task
    void pollImportTask(result.task.id)
  }
  else void load()
  if (result.fileId) void router.push(`/application-configs/${result.fileId}/edit`)
}

onMounted(() => { void load(); void loadImportTasks() })
onBeforeUnmount(() => {
  for (const fileId of syncPollTimers.keys()) stopSyncPolling(fileId)
  syncTaskIds.clear()
  syncingIds.clear()
  stopImportPolling()
})
</script>

<template>
  <div>
    <div class="op-hero op-hero--teal">
      <div class="op-hero-icon"><FileTextOutlined /></div>
      <div><div class="op-hero-title">应用配置</div><div class="op-hero-sub">生产配置文件</div></div>
      <div class="op-hero-extra"><div class="op-hero-stat"><b>{{ total }}</b><span>配置文件</span></div></div>
    </div>
    <div class="config-toolbar">
      <a-input v-model:value="query.keyword" class="config-search" allow-clear placeholder="搜索配置文件名称" @press-enter="onSearch">
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <div class="config-toolbar-actions">
        <a-button v-if="canManageInstances" class="op-btn-cyan" @click="instanceOpen = true"><SettingOutlined />平台实例</a-button>
        <a-button v-if="canWrite" type="primary" @click="newOpen = true"><PlusOutlined />新建配置文件</a-button>
      </div>
    </div>
    <a-table :columns="columns" :data-source="items" row-key="id" bordered :loading="loading"
      :scroll="{ x: 1500 }"
      :pagination="{ current: query.page, pageSize: query.page_size, total,
        showSizeChanger: true, showTotal: (count: number) => `共 ${count} 个配置文件` }"
      @change="onTableChange">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'name'">
          <a class="file-name" @click="router.push(`/application-configs/${record.id}`)">{{ record.name }}</a>
        </template>
        <template v-else-if="column.key === 'instance'">{{ record.platform_instance_name || '—' }}</template>
        <template v-else-if="column.key === 'locator'">
          <a-tooltip :title="locatorText(record as configApi.ConfigFileBrief)">
            <span class="locator-value">{{ locatorText(record as configApi.ConfigFileBrief) }}</span>
          </a-tooltip>
        </template>
        <template v-else-if="column.key === 'format'"><a-tag>{{ record.content_format }}</a-tag></template>
        <template v-else-if="column.key === 'role'">{{ record.approval_role_name || '—' }}</template>
        <template v-else-if="column.key === 'version'">{{ record.current_version_no ? `v${record.current_version_no}` : '未发布' }}</template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="statusText(record as configApi.ConfigFileBrief).color">
            {{ statusText(record as configApi.ConfigFileBrief).label }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'synced'">
          {{ record.last_synced_at ? new Date(record.last_synced_at).toLocaleString() : '—' }}
        </template>
        <template v-else-if="column.key === 'action'">
          <a-space :size="6">
            <a-button size="small" class="op-btn-cyan" @click="router.push(`/application-configs/${record.id}`)"><EyeOutlined />查看</a-button>
            <a-button v-if="canWrite && record.status === 'active'" size="small" class="op-btn-blue" @click="router.push(`/application-configs/${record.id}/edit`)"><EditOutlined />编辑</a-button>
            <a-button v-if="canWrite && record.status === 'active'" size="small" class="op-btn-green"
              :loading="syncingIds.has(record.id)"
              @click="onSync(record as configApi.ConfigFileBrief)"><CloudSyncOutlined />同步</a-button>
            <a-popconfirm v-if="canDelete && record.status === 'archived'"
              title="仅删除 OpsPilot 中的历史和加密内容，不会删除外部配置。确认删除？"
              @confirm="onDelete(record as configApi.ConfigFileBrief)">
              <a-button size="small" danger :loading="deletingId === record.id"><DeleteOutlined />删除</a-button>
            </a-popconfirm>
          </a-space>
        </template>
      </template>
    </a-table>
    <NewConfigDrawer v-if="canWrite" v-model:open="newOpen" :import-task="importTask" @created="onCreated" />
    <PlatformInstanceDrawer v-if="canManageInstances" v-model:open="instanceOpen" />
    <DriftCompareDrawer v-if="driftFileId !== null" :key="driftFileId" :open="driftFileId !== null"
      :file-id="driftFileId" :can-write="canWrite" @update:open="(open: boolean) => { if (!open) driftFileId = null }"
      @resolved="load" />
  </div>
</template>

<style scoped>
.config-toolbar { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }
.config-search { width: min(300px, 100%); }
.config-toolbar-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-left: auto; }
.file-name { font-weight: 600; }
.locator-value { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 680px) { .config-toolbar-actions { width: 100%; margin-left: 0; } .op-hero-extra { display: none; } }
</style>
