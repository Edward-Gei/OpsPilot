<script setup lang="ts">
// 工单模板列表：只维护业务入口，流程步骤和参数统一从流程模板引用，避免重复配置。
import { computed, onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { CodeOutlined, CopyOutlined, DeleteOutlined, EditOutlined, EyeOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons-vue'
import * as api from '@/api/job'
import { listJobHosts } from '@/api/jobHost'
import { listRoleOptions } from '@/api/system'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { typeOptions, typeText } from './meta'
import TicketTemplateEditor from './TicketTemplateEditor.vue'

interface Row extends api.TemplateItem {
  process_template_id: number
  allow_withdraw: boolean
  notify_rules: api.NotifyRule[]
  visible_role_ids: number[]
}

const canWrite = true
const rows = ref<Row[]>([])
const processes = ref<api.ProcessTemplateItem[]>([])
const hosts = ref<{ id: number; name: string; enabled: boolean }[]>([])
const roles = ref<{ id: number; name: string }[]>([])
const loading = ref(false)
const total = ref(0)
const selectedKeys = ref<number[]>([])
const statusLoading = ref<number | null>(null)
const editing = ref<Row | null>(null)
const open = ref(false)
const copyFromId = ref<number | null>(null)
const detail = ref<(api.TemplateItem & { process_template?: { id: number; name: string; status: api.TemplateStatus } }) | null>(null)
const detailOpen = ref(false)
const query = reactive({ page: 1, page_size: 20, keyword: '', type: undefined as string | undefined, status: undefined as string | undefined })
const hostMap = computed(() => Object.fromEntries(hosts.value.map((h) => [h.id, h.name])))
const processMap = computed(() => Object.fromEntries(processes.value.map((p) => [p.id, p.name])))
const roleMap = computed(() => Object.fromEntries(roles.value.map((role) => [role.id, role.name])))
const stats = reactive({ all: 0, enabled: 0, disabled: 0 })
const columns = ref(makeResizable([
  { title: '模板名称', dataIndex: 'name', key: 'name', width: 190, ellipsis: true },
  { title: '类型', key: 'type', width: 90 },
  { title: '作业主机', key: 'host', width: 150, ellipsis: true },
  { title: '流程模板', key: 'process', width: 170, ellipsis: true },
  { title: '可见角色', key: 'visible_roles', width: 180, ellipsis: true },
  { title: '状态', key: 'status', width: 90 },
  { title: '更新时间', key: 'updated', width: 160 },
  { title: '操作', key: 'action', width: 210, fixed: 'right' as const },
]))

async function load() {
  loading.value = true
  try {
    const [data, processData, hostData, roleData] = await Promise.all([
      api.listTemplates(query),
      api.listProcessTemplates({ page: 1, page_size: 100 }),
      listJobHosts({ page: 1, page_size: 100 }),
      listRoleOptions(),
    ])
    rows.value = data.items as Row[]
    total.value = data.total
    processes.value = processData.items
    hosts.value = hostData.items.map((h) => ({ id: h.id, name: h.name, enabled: h.enabled }))
    roles.value = roleData.items
    selectedKeys.value = []
    const [all, enabled, disabled] = await Promise.all([
      api.listTemplates({ page: 1, page_size: 1 }),
      api.listTemplates({ page: 1, page_size: 1, status: 'enabled' }),
      api.listTemplates({ page: 1, page_size: 1, status: 'disabled' }),
    ])
    stats.all = all.total; stats.enabled = enabled.total; stats.disabled = disabled.total
  } finally { loading.value = false }
}
function search() { query.page = 1; load() }
function pageChange(page: number, pageSize: number) { query.page = page; query.page_size = pageSize; load() }
function startCreate() { editing.value = null; copyFromId.value = null; open.value = true }
function startEdit(row: Row) {
  copyFromId.value = null
  editing.value = row
  open.value = true
}
// 复制模板先通过搜索弹窗选择源模板，再进入编辑器确认后创建新模板。
const copyOpen = ref(false)
const copySourceId = ref<number | undefined>(undefined)
const copyOptions = ref<{ label: string; value: number }[]>([])
const copySearching = ref(false)
let copySearchTimer: number | null = null
function openCopy(): void {
  copySourceId.value = undefined
  copyOpen.value = true
  void searchCopyOptions('')
}
function onCopySearch(keyword: string): void {
  if (copySearchTimer) window.clearTimeout(copySearchTimer)
  copySearchTimer = window.setTimeout(() => void searchCopyOptions(keyword), 300)
}
async function searchCopyOptions(keyword: string): Promise<void> {
  copySearching.value = true
  try {
    const data = await api.listTemplates({ page: 1, page_size: 100, keyword: keyword || undefined })
    copyOptions.value = data.items.map((item) => ({ label: `${item.name}${item.status === 'disabled' ? '（已停用）' : ''}`, value: item.id }))
  } finally {
    copySearching.value = false
  }
}
function confirmCopy(): void {
  if (!copySourceId.value) {
    message.warning('请选择要复制的工单模板')
    return
  }
  copyOpen.value = false
  editing.value = null
  copyFromId.value = copySourceId.value
  open.value = true
}
async function remove(row: Row) { await api.deleteTemplate(row.id); message.success('工单模板已删除'); await load() }
/** 批量删除沿用单条删除接口，逐条执行并汇总被保护的失败项。 */
async function batchRemove(): Promise<void> {
  const ids = [...selectedKeys.value]
  if (!ids.length) return
  const results = await Promise.allSettled(ids.map((id) => api.deleteTemplate(id)))
  const failed = results.filter((result) => result.status === 'rejected').length
  if (failed) message.warning(`已删除 ${ids.length - failed} 个模板，${failed} 个模板删除失败（可能仍被工单使用）`)
  else message.success(`已删除 ${ids.length} 个工单模板`)
  await load()
}
function confirmBatchRemove(): void {
  if (!selectedKeys.value.length) return
  Modal.confirm({ title: '批量删除工单模板', content: `确认删除选中的 ${selectedKeys.value.length} 个模板吗？`, okText: '删除', okType: 'danger', cancelText: '取消', onOk: batchRemove })
}
async function toggle(row: Row) {
  statusLoading.value = row.id
  try { await api.setTemplateStatus(row.id, row.status === 'enabled' ? 'disabled' : 'enabled'); await load() } finally { statusLoading.value = null }
}
async function openDetail(row: Row) { detailOpen.value = true; detail.value = await api.getTemplate(row.id) }
const rowSelection = computed(() => ({ selectedRowKeys: selectedKeys.value, onChange: (keys: (string | number)[]) => (selectedKeys.value = keys as number[]) }))
onMounted(load)
</script>

<template>
  <div>
    <div class="op-hero op-hero--indigo">
      <div class="op-hero-icon"><CodeOutlined /></div>
      <div><div class="op-hero-title">工单模板</div><div class="op-hero-sub">维护工单入口并引用统一流程模板，模板变更不再重复配置步骤</div></div>
      <div class="op-hero-extra"><div class="op-hero-stat"><b>{{ stats.all }}</b><span>全部</span></div><div class="op-hero-stat"><b>{{ stats.enabled }}</b><span>启用</span></div><div class="op-hero-stat"><b>{{ stats.disabled }}</b><span>停用</span></div></div>
    </div>
    <div class="toolbar">
      <a-input v-model:value="query.keyword" class="kw" allow-clear placeholder="搜索工单模板" @press-enter="search"><template #prefix><SearchOutlined /></template></a-input>
      <a-select v-model:value="query.type" class="type-sel" allow-clear placeholder="模板类型" :options="typeOptions" @change="search" />
      <a-select v-model:value="query.status" class="status-sel" allow-clear placeholder="状态" :options="[{ label: '启用', value: 'enabled' }, { label: '停用', value: 'disabled' }]" @change="search" />
      <div class="toolbar-actions">
        <a-button danger :disabled="!selectedKeys.length" @click="confirmBatchRemove"><DeleteOutlined />批量删除{{ selectedKeys.length ? `（${selectedKeys.length}）` : '' }}</a-button>
        <a-button @click="openCopy"><CopyOutlined />复制模板</a-button>
        <a-button type="primary" @click="startCreate"><PlusOutlined />新建模板</a-button>
      </div>
    </div>
    <a-table :columns="columns" :data-source="rows" :loading="loading" bordered row-key="id" :scroll="{ x: 1060 }" :row-selection="rowSelection" @resize-column="onResizeColumn" :pagination="{ current: query.page, pageSize: query.page_size, total, showSizeChanger: true, showQuickJumper: true, showTotal: (t: number) => `共 ${t} 个模板`, onChange: pageChange }">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'type'"><a-tag :color="typeText[record.type as api.TemplateType]?.color">{{ typeText[record.type as api.TemplateType]?.text || record.type }}</a-tag></template>
        <template v-else-if="column.key === 'host'">{{ hostMap[record.job_host_id] || `#${record.job_host_id}` }}</template>
        <template v-else-if="column.key === 'process'"><a-tag color="cyan">{{ processMap[record.process_template_id] || `#${record.process_template_id}` }}</a-tag></template>
        <template v-else-if="column.key === 'visible_roles'">
          <template v-if="record.visible_role_ids?.length">
            <a-tag v-for="roleId in record.visible_role_ids" :key="roleId" color="blue">{{ roleMap[roleId] || roleId }}</a-tag>
          </template>
          <span v-else>全部角色</span>
        </template>
        <template v-else-if="column.key === 'status'"><a-switch :checked="record.status === 'enabled'" checked-children="启用" un-checked-children="停用" :loading="statusLoading === record.id" @change="toggle(record as Row)" /></template>
        <template v-else-if="column.key === 'updated'">{{ record.updated_at ? new Date(record.updated_at).toLocaleString() : '-' }}</template>
        <template v-else-if="column.key === 'action'"><a-space><a-button size="small" class="op-btn-cyan" @click="openDetail(record as Row)"><EyeOutlined />详情</a-button><a-button v-if="canWrite" size="small" class="op-btn-blue" @click="startEdit(record as Row)"><EditOutlined />编辑</a-button><a-popconfirm title="确认删除该工单模板？" @confirm="remove(record as Row)"><a-button v-if="canWrite" size="small" danger><DeleteOutlined />删除</a-button></a-popconfirm></a-space></template>
      </template>
    </a-table>

    <TicketTemplateEditor v-model:open="open" :template-id="editing?.id || null" :copy-from-id="copyFromId" @saved="load" />
    <a-modal v-model:open="copyOpen" title="复制工单模板" :confirm-loading="copySearching" @ok="confirmCopy">
      <div class="copy-tip">选择源模板后进入编辑器，确认保存后才会创建新模板。</div>
      <a-select v-model:value="copySourceId" class="copy-select" show-search :filter-option="false" :options="copyOptions" :loading="copySearching" placeholder="搜索并选择源模板" @search="onCopySearch" />
    </a-modal>
    <a-drawer v-model:open="detailOpen" :title="detail?.name || '工单模板详情'" :width="640"><a-spin :spinning="!detail"><template v-if="detail"><div class="head-line"><a-tag :color="typeText[detail.type]?.color">{{ typeText[detail.type]?.text || detail.type }}</a-tag><a-tag :color="detail.status === 'enabled' ? 'green' : 'default'">{{ detail.status === 'enabled' ? '启用' : '停用' }}</a-tag></div><a-descriptions bordered size="small" :column="1" class="op-desc-table"><a-descriptions-item label="说明">{{ detail.description || '-' }}</a-descriptions-item><a-descriptions-item label="作业主机">{{ hostMap[detail.job_host_id] || detail.job_host_id }}</a-descriptions-item><a-descriptions-item label="流程模板">{{ detail.process_template?.name || processMap[(detail as Row).process_template_id] || '-' }}</a-descriptions-item><a-descriptions-item label="允许终止">{{ (detail as Row).allow_withdraw ? '是' : '否' }}</a-descriptions-item></a-descriptions></template></a-spin></a-drawer>
  </div>
</template>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 16px; }
.kw { width: 220px; }.type-sel { width: 130px; }.status-sel { width: 110px; }.toolbar-actions { margin-left: auto; display: flex; gap: 10px; }
.copy-tip { font-size: 12px; color: var(--text-3); margin-bottom: 10px; }.copy-select { width: 100%; }
.head-line { display: flex; gap: 4px; margin-bottom: 14px; }
</style>
