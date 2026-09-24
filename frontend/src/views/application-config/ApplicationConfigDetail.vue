<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { ArrowLeftOutlined, CloudSyncOutlined, DeleteOutlined, EditOutlined, EyeOutlined, FileTextOutlined, InboxOutlined, ReloadOutlined, SaveOutlined, SendOutlined, WarningOutlined } from '@ant-design/icons-vue'
import * as api from '@/api/applicationConfig'
import { useUserStore } from '@/stores/user'
import ConfigContentEditor from './ConfigContentEditor.vue'
import DriftCompareDrawer from './DriftCompareDrawer.vue'

const route = useRoute()
const router = useRouter()
const user = useUserStore()
const fileId = computed(() => Number(route.params.id))
const canWrite = computed(() => user.hasPerm('config:write'))
const canEdit = computed(() => route.name === 'application-config-edit' && canWrite.value)
const canDelete = computed(() => user.hasPerm('config:delete'))
const canReadSecret = computed(() => user.hasPerm('secret:read'))
const file = ref<api.ConfigFileBrief | null>(null)
const versions = ref<api.ConfigVersion[]>([])
const publishTasks = ref<api.ConfigTask[]>([])
const roles = ref<api.NamedOption[]>([])
const applications = ref<api.NamedOption[]>([])
const activeTab = ref('content')
const selectedVersion = ref<number | null>(null)
const versionContent = ref('')
const draftLoaded = ref(false)
const draftContent = ref('')
const draftDirty = ref(false)
const driftOpen = ref(false)
const task = ref<api.ConfigTask | null>(null)
const syncStarting = ref(false)
const busy = ref(false)
const previewOpen = ref(false)
const previewVersion = ref<api.ConfigVersion | null>(null)
const previewContent = ref('')
const viewingVersionId = ref<number | null>(null)
const metadataOpen = ref(false)
const metadata = ref({ name: '', description: '', approval_role_id: 0, application_ids: [] as number[] })
let timer: number | null = null
let refreshTimer: number | null = null

function stopPolling() {
  if (timer !== null) window.clearTimeout(timer)
  if (refreshTimer !== null) window.clearTimeout(refreshTimer)
  timer = null
  refreshTimer = null
}
function statusText(status: string) {
  return ({ pending_approval: '待审批', approved: '已批准', publishing: '发布中', published: '正式版本',
    rejected: '已驳回', invalidated: '已失效' } as Record<string, string>)[status] || status
}
function publishTaskFor(versionId: number) {
  return publishTasks.value.find((item) => item.kind === 'publish' && item.config_version_id === versionId)
}
function versionStatus(version: api.ConfigVersion) {
  const task = publishTaskFor(version.id)
  if (version.status === 'approved' && task?.status === 'failed') return '发布失败'
  if (version.status === 'approved' && (task?.status === 'queued' || task?.status === 'running')) return '发布中'
  return statusText(version.status)
}
async function load() {
  if (refreshTimer !== null) window.clearTimeout(refreshTimer)
  const [detail, history, apps, tasks] = await Promise.all([
    api.getConfigFile(fileId.value), api.listVersions(fileId.value),
    api.listCmdbApplications(), api.listConfigFileTasks(fileId.value),
  ])
  file.value = detail
  versions.value = history.items
  applications.value = apps.items
  publishTasks.value = tasks.items
  if (detail.current_version_id && selectedVersion.value !== detail.current_version_id) await showVersion(detail.current_version_id)
  if (tasks.items.some((item) => item.kind === 'publish' && ['queued', 'running'].includes(item.status))) {
    refreshTimer = window.setTimeout(() => void load(), 2000)
  }
}
async function showVersion(id: number) {
  versionContent.value = (await api.getVersionContent(fileId.value, id)).content
  selectedVersion.value = id
  activeTab.value = 'content'
}
async function viewVersion(version: api.ConfigVersion) {
  // 独立预览不切换页签，也不覆盖当前正文。
  viewingVersionId.value = version.id
  try {
    previewContent.value = (await api.getVersionContent(fileId.value, version.id)).content
    previewVersion.value = version
    previewOpen.value = true
  } finally { viewingVersionId.value = null }
}
async function loadDraft() {
  if (!canEdit.value || !file.value || file.value.status !== 'active') return
  const draft = await api.getDraft(fileId.value)
  // TEXT 在缺少 secret:read 时只能完整替换，不将旧正文载入输入框。
  draftContent.value = file.value.content_format === 'text' && !canReadSecret.value ? '' : draft.content
  draftLoaded.value = true
  draftDirty.value = false
}
async function saveDraft() {
  busy.value = true
  try { await api.saveDraft(fileId.value, draftContent.value); draftDirty.value = false; message.success('草稿已保存') }
  finally { busy.value = false }
}
async function submit() {
  busy.value = true
  try {
    if (draftDirty.value) await api.saveDraft(fileId.value, draftContent.value)
    await api.submitCandidate(fileId.value)
    message.success('已提交审批')
    draftLoaded.value = false
    draftDirty.value = false
    await load()
    activeTab.value = 'versions'
  } finally { busy.value = false }
}
async function discard() {
  await api.discardDraft(fileId.value)
  draftLoaded.value = false
  draftDirty.value = false
  message.success('草稿已丢弃')
}
async function poll(id: number, kind: 'sync' | 'publish') {
  try {
    const result = await api.getConfigTask(id)
    task.value = result
    if (result.status === 'queued' || result.status === 'running') {
      timer = window.setTimeout(() => void poll(id, kind), 1200)
      return
    }
    await load()
    if (kind === 'sync' && (file.value?.drift_status === 'drifted' || file.value?.drift_status === 'remote_missing')) driftOpen.value = true
    if (result.status === 'success') message.success(kind === 'sync' ? '同步完成' : '发布完成')
    else message.warning(result.last_error || (kind === 'sync' ? '同步失败' : '发布失败'))
  } catch { timer = window.setTimeout(() => void poll(id, kind), 5000) }
}
async function start(kind: 'sync' | 'publish', versionId?: number) {
  stopPolling()
  syncStarting.value = kind === 'sync'
  try {
    task.value = kind === 'sync' ? await api.syncConfigFile(fileId.value) : await api.publishVersion(fileId.value, versionId!)
    void poll(task.value.id, kind)
  } finally { syncStarting.value = false }
}
async function openMetadata() {
  if (!file.value) return
  const [roleOptions, appOptions] = await Promise.all([api.listApprovalRoles(), api.listCmdbApplications()])
  roles.value = roleOptions.items
  applications.value = appOptions.items
  metadata.value = { name: file.value.name, description: file.value.description || '',
    approval_role_id: file.value.approval_role_id, application_ids: [...file.value.application_ids] }
  metadataOpen.value = true
}
async function saveMetadata() {
  await api.updateConfigFile(fileId.value, metadata.value)
  metadataOpen.value = false
  message.success('配置文件信息已更新')
  await load()
}
async function archive() { await api.archiveConfigFile(fileId.value); message.success('已归档；外部配置未改变'); await load() }
async function remove() {
  await api.deleteConfigFile(fileId.value)
  message.success('本地管理记录已删除；外部配置未改变')
  void router.push('/application-configs')
}
onMounted(load)
onBeforeUnmount(stopPolling)
watch(fileId, () => {
  draftLoaded.value = false
  selectedVersion.value = null
  previewOpen.value = false
  previewVersion.value = null
  previewContent.value = ''
  void load()
})
watch(canEdit, (editing) => { if (!editing) { draftLoaded.value = false; metadataOpen.value = false } })
</script>

<template>
  <div v-if="file">
    <div class="op-hero op-hero--teal">
      <div class="op-hero-icon"><FileTextOutlined /></div>
      <div class="config-title"><div class="op-hero-title">{{ file.name }}</div>
        <div class="op-hero-sub">{{ file.platform_instance_name }} · {{ file.content_format.toUpperCase() }}</div></div>
      <div class="op-hero-extra"><a-tag v-if="file.drift_status !== 'clean'" color="warning">{{ file.drift_status === 'remote_missing' ? '远端缺失' : '存在漂移' }}</a-tag></div>
    </div>
    <div class="detail-actions">
      <a-button class="op-btn-cyan" @click="router.push('/application-configs')"><ArrowLeftOutlined />返回列表</a-button>
      <a-button v-if="canEdit && file.status === 'active'" class="op-btn-green"
        :loading="syncStarting || (task?.kind === 'sync' && ['queued', 'running'].includes(task.status))"
        :disabled="task?.status === 'queued' || task?.status === 'running'" @click="start('sync')"><CloudSyncOutlined />手动同步</a-button>
      <a-button v-if="file.drift_status === 'drifted' || file.drift_status === 'remote_missing'" class="op-btn-orange" @click="driftOpen = true"><WarningOutlined />查看漂移</a-button>
      <a-button v-if="canEdit" class="op-btn-blue" @click="openMetadata"><EditOutlined />编辑信息</a-button>
      <a-popconfirm v-if="canEdit && canDelete && file.status === 'active'" title="归档后不可编辑或同步，外部内容不变。确认归档？" @confirm="archive"><a-button class="op-btn-purple"><InboxOutlined />归档</a-button></a-popconfirm>
      <a-popconfirm v-if="canEdit && canDelete && file.status === 'archived'" title="仅删除 OpsPilot 中的历史和加密内容，不会删除外部配置。确认删除？" @confirm="remove"><a-button danger><DeleteOutlined />删除本地记录</a-button></a-popconfirm>
    </div>
    <a-descriptions bordered size="small" :column="{ xs: 1, sm: 2, lg: 3 }" class="detail-summary op-desc-table">
      <a-descriptions-item label="远端定位">{{ file.provider === 'nacos' && file.locator.namespace === ''
        ? `public / ${file.locator.group} / ${file.locator.data_id}` : Object.values(file.locator).join(' / ') }}</a-descriptions-item>
      <a-descriptions-item label="审批角色">{{ file.approval_role_name || '—' }}</a-descriptions-item>
      <a-descriptions-item label="状态">{{ file.status === 'active' ? '使用中' : '已归档' }}</a-descriptions-item>
      <a-descriptions-item label="最近同步">{{ file.last_synced_at ? new Date(file.last_synced_at).toLocaleString() : '—' }}</a-descriptions-item>
      <a-descriptions-item label="关联应用">
        <a-space v-if="file.application_ids.length" wrap>
          <a-tag v-for="id in file.application_ids" :key="id" class="related-app-tag">{{ applications.find((item) => item.id === id)?.name || `应用 #${id}` }}</a-tag>
        </a-space>
        <span v-else>暂无关联应用</span>
      </a-descriptions-item>
      <a-descriptions-item label="当前版本">{{ versions.find((item) => item.id === file?.current_version_id)?.version_no ?? '未发布' }}</a-descriptions-item>
    </a-descriptions>
    <a-tabs v-model:active-key="activeTab">
      <a-tab-pane key="content" :tab="canEdit ? '内容与草稿' : '配置内容'">
        <div class="content-head"><strong>{{ selectedVersion ? `版本 v${versions.find((item) => item.id === selectedVersion)?.version_no}` : '尚无正式版本' }}</strong>
          <a-button v-if="canEdit && file.status === 'active' && !draftLoaded" class="op-btn-blue" @click="loadDraft"><EditOutlined />编辑草稿</a-button></div>
        <ConfigContentEditor v-if="selectedVersion && !draftLoaded" :model-value="versionContent" :format="file.content_format" readonly :masked="!canReadSecret" :can-read-secret="canReadSecret" />
        <a-empty v-else-if="!draftLoaded" description="尚无正式版本" />
        <template v-if="draftLoaded">
          <ConfigContentEditor :model-value="draftContent" :format="file.content_format" :masked="!canReadSecret" :can-read-secret="canReadSecret"
            @update:model-value="draftContent = $event; draftDirty = true" />
          <a-space class="draft-actions"><a-button class="op-btn-green" :loading="busy" @click="saveDraft"><SaveOutlined />保存草稿</a-button>
            <a-button type="primary" :loading="busy" @click="submit"><SendOutlined />提交审批</a-button>
            <a-popconfirm title="确认丢弃草稿？" @confirm="discard"><a-button class="op-btn-orange"><DeleteOutlined />丢弃草稿</a-button></a-popconfirm></a-space>
        </template>
      </a-tab-pane>
      <a-tab-pane key="versions" tab="版本记录">
        <a-table :data-source="versions" row-key="id" size="small" :scroll="{ x: 830 }" :columns="[
          { title: '版本', dataIndex: 'version_no', key: 'number', width: 80 },
          { title: '状态', key: 'status', width: 120 }, { title: '来源', key: 'source', width: 130 },
          { title: '审批角色 ID', dataIndex: 'approval_role_id', key: 'role', width: 120 },
          { title: '提交人 ID', dataIndex: 'submitted_by', key: 'submitter', width: 110 },
          { title: '操作', key: 'actions', width: canEdit ? 190 : 80 },
        ]">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'status'"><a-tooltip :title="publishTaskFor(record.id)?.last_error || undefined"><a-tag>{{ versionStatus(record as api.ConfigVersion) }}</a-tag></a-tooltip></template>
            <template v-else-if="column.key === 'source'">{{ record.source === 'external_import' ? '外部导入' : 'OpsPilot 发布' }}</template>
            <template v-else-if="column.key === 'actions'"><a-space size="small">
              <a-button size="small" class="op-btn-cyan" :loading="viewingVersionId === record.id" @click="viewVersion(record as api.ConfigVersion)"><EyeOutlined />查看</a-button>
              <a-button v-if="canEdit && file?.status === 'active' && file.drift_status === 'clean' && record.status === 'approved' && publishTaskFor(record.id)?.status === 'failed'" size="small" class="op-btn-orange" @click="start('publish', record.id)"><ReloadOutlined />重试发布</a-button>
            </a-space></template>
          </template>
        </a-table>
      </a-tab-pane>
    </a-tabs>
    <a-modal v-model:open="metadataOpen" title="编辑配置文件" @ok="saveMetadata">
      <a-form layout="vertical"><a-form-item label="名称"><a-input v-model:value="metadata.name" /></a-form-item>
        <a-form-item label="说明"><a-textarea v-model:value="metadata.description" :rows="2" /></a-form-item>
        <a-form-item label="审批角色"><a-select v-model:value="metadata.approval_role_id" :options="roles.map((item) => ({ label: item.name, value: item.id }))" /></a-form-item>
        <a-form-item label="关联 CMDB 应用"><a-select v-model:value="metadata.application_ids" mode="multiple" :options="applications.map((item) => ({ label: item.name, value: item.id }))" /></a-form-item></a-form>
    </a-modal>
    <a-modal v-model:open="previewOpen" centered :width="'min(900px, calc(100vw - 32px))'"
      :title="`版本 v${previewVersion?.version_no ?? ''} 内容`"
      :body-style="{ maxHeight: 'calc(100vh - 180px)', overflowY: 'auto' }">
      <ConfigContentEditor v-if="previewVersion" :model-value="previewContent" :format="file.content_format"
        readonly :masked="!canReadSecret" :can-read-secret="canReadSecret" />
      <template #footer><a-button @click="previewOpen = false">关闭</a-button></template>
    </a-modal>
    <DriftCompareDrawer v-model:open="driftOpen" :file-id="fileId" :can-write="canEdit" @resolved="load" />
  </div>
</template>

<style scoped>
.config-title { min-width: 0; overflow-wrap: anywhere; }
.detail-actions { display: flex; gap: 8px; flex-wrap: wrap; margin: 14px 0; }
.detail-summary { margin: 16px 0; }
.related-app-tag { max-width: 100%; white-space: normal; overflow-wrap: anywhere; margin-inline-end: 0; }
.content-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.draft-actions { margin-top: 12px; }
@media (max-width: 680px) {
  .op-hero-extra { display: none; }
  .detail-summary { overflow-x: auto; }
  .detail-summary :deep(.ant-descriptions-view) { min-width: 320px; }
}
</style>
