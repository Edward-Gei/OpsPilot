<script setup lang="ts">
// 工单模板列表：只维护业务入口，流程步骤和参数统一从流程模板引用，避免重复配置。
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { CodeOutlined, DeleteOutlined, EditOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons-vue'
import * as api from '@/api/job'
import { listJobHosts } from '@/api/jobHost'
import { listRoleOptions } from '@/api/system'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { typeOptions, typeText } from './meta'

interface Row extends api.TemplateItem {
  process_template_id: number
  allow_withdraw: boolean
  notify_rules: api.NotifyRule[]
  visible_role_ids: number[]
}

const canWrite = true
const rows = ref<Row[]>([])
const processes = ref<api.ProcessTemplateItem[]>([])
const hosts = ref<{ id: number; name: string }[]>([])
const roles = ref<{ id: number; name: string }[]>([])
const loading = ref(false)
const total = ref(0)
const selectedKeys = ref<number[]>([])
const statusLoading = ref<number | null>(null)
const editing = ref<Row | null>(null)
const open = ref(false)
const detail = ref<(api.TemplateItem & { process_template?: { id: number; name: string; status: api.TemplateStatus } }) | null>(null)
const detailOpen = ref(false)
const query = reactive({ page: 1, page_size: 20, keyword: '', type: undefined as string | undefined, status: undefined as string | undefined })
const form = reactive<api.TicketTemplateForm>({
  name: '', type: 'daily_ops', description: '', job_host_id: undefined as unknown as number,
  process_template_id: undefined as unknown as number, allow_withdraw: true, notify_rules: [], visible_role_ids: [], status: 'enabled',
})

const hostMap = computed(() => Object.fromEntries(hosts.value.map((h) => [h.id, h.name])))
const processMap = computed(() => Object.fromEntries(processes.value.map((p) => [p.id, p.name])))
const roleOptions = computed(() => roles.value.map((r) => ({ label: r.name, value: r.id })))
const stats = reactive({ all: 0, enabled: 0, disabled: 0 })
const columns = ref(makeResizable([
  { title: '模板名称', dataIndex: 'name', key: 'name', width: 190, ellipsis: true },
  { title: '类型', key: 'type', width: 90 },
  { title: '作业主机', key: 'host', width: 150, ellipsis: true },
  { title: '流程模板', key: 'process', width: 170, ellipsis: true },
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
      listJobHosts({ page: 1, page_size: 100, enabled: true }),
      listRoleOptions(),
    ])
    rows.value = data.items as Row[]
    total.value = data.total
    processes.value = processData.items
    hosts.value = hostData.items.map((h) => ({ id: h.id, name: h.name }))
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
function resetForm() {
  Object.assign(form, { name: '', type: 'daily_ops', description: '', job_host_id: undefined, process_template_id: undefined, allow_withdraw: true, notify_rules: [], visible_role_ids: [], status: 'enabled' })
}
function startCreate() { editing.value = null; resetForm(); open.value = true }
function startEdit(row: Row) {
  editing.value = row
  Object.assign(form, { name: row.name, type: row.type, description: row.description || '', job_host_id: row.job_host_id, process_template_id: row.process_template_id, allow_withdraw: row.allow_withdraw, notify_rules: row.notify_rules || [], visible_role_ids: row.visible_role_ids || [], status: row.status })
  open.value = true
}
async function save() {
  if (!form.name || !form.job_host_id || !form.process_template_id) return message.warning('请完整填写模板名称、作业主机和流程模板')
  if (editing.value) await api.updateTemplate(editing.value.id, form)
  else await api.createTemplate(form)
  message.success('工单模板已保存'); open.value = false; load()
}
async function remove(row: Row) { await api.deleteTemplate(row.id); message.success('工单模板已删除'); load() }
async function toggle(row: Row) {
  statusLoading.value = row.id
  try { await api.setTemplateStatus(row.id, row.status === 'enabled' ? 'disabled' : 'enabled'); await load() } finally { statusLoading.value = null }
}
async function openDetail(row: Row) { detailOpen.value = true; detail.value = await api.getTemplate(row.id) }
function addNotify() { form.notify_rules.push({ event: 'ticket.pending_approval', receivers: ['creator'], channels: ['email'] }) }
function removeNotify(index: number) { form.notify_rules.splice(index, 1) }
const rowSelection = computed(() => ({ selectedRowKeys: selectedKeys.value, onChange: (keys: (string | number)[]) => (selectedKeys.value = keys as number[]) }))
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
      <div class="toolbar-actions"><a-button type="primary" @click="startCreate"><PlusOutlined />新建模板</a-button></div>
    </div>
    <a-table :columns="columns" :data-source="rows" :loading="loading" bordered row-key="id" :scroll="{ x: 1060 }" :row-selection="rowSelection" @resize-column="onResizeColumn" :pagination="{ current: query.page, pageSize: query.page_size, total, showSizeChanger: true, showQuickJumper: true, showTotal: (t: number) => `共 ${t} 个模板`, onChange: pageChange }">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'type'"><a-tag :color="typeText[record.type as api.TemplateType]?.color">{{ typeText[record.type as api.TemplateType]?.text || record.type }}</a-tag></template>
        <template v-else-if="column.key === 'host'">{{ hostMap[record.job_host_id] || `#${record.job_host_id}` }}</template>
        <template v-else-if="column.key === 'process'"><a-tag color="cyan">{{ processMap[record.process_template_id] || `#${record.process_template_id}` }}</a-tag></template>
        <template v-else-if="column.key === 'status'"><a-switch :checked="record.status === 'enabled'" checked-children="启用" un-checked-children="停用" :loading="statusLoading === record.id" @change="toggle(record as Row)" /></template>
        <template v-else-if="column.key === 'updated'">{{ record.updated_at ? new Date(record.updated_at).toLocaleString() : '-' }}</template>
        <template v-else-if="column.key === 'action'"><a-space><a-button size="small" class="op-btn-cyan" @click="openDetail(record as Row)">详情</a-button><a-button v-if="canWrite" size="small" class="op-btn-blue" @click="startEdit(record as Row)"><EditOutlined />编辑</a-button><a-popconfirm title="确认删除该工单模板？" @confirm="remove(record as Row)"><a-button v-if="canWrite" size="small" danger><DeleteOutlined />删除</a-button></a-popconfirm></a-space></template>
      </template>
    </a-table>

    <a-modal v-model:open="open" :title="editing ? '编辑工单模板' : '新建工单模板'" :width="760" @ok="save">
      <a-form layout="vertical">
        <div class="form-row"><a-form-item label="模板名称" required class="form-col"><a-input v-model:value="form.name" /></a-form-item><a-form-item label="类型" required class="form-col-sm"><a-select v-model:value="form.type" :options="typeOptions" /></a-form-item><a-form-item label="作业主机" required class="form-col"><a-select v-model:value="form.job_host_id" show-search option-filter-prop="label" :options="hosts.map((h) => ({ label: h.name, value: h.id }))" /></a-form-item></div>
        <a-form-item label="流程模板" required><a-select v-model:value="form.process_template_id" show-search option-filter-prop="label" :options="processes.map((p) => ({ label: `${p.name}${p.status === 'disabled' ? '（已停用）' : ''}`, value: p.id, disabled: p.status === 'disabled' && p.id !== editing?.process_template_id }))" /></a-form-item>
        <a-form-item label="说明"><a-textarea v-model:value="form.description" :rows="2" /></a-form-item>
        <a-form-item><a-checkbox v-model:checked="form.allow_withdraw">允许创建人终止待处理工单</a-checkbox></a-form-item>
        <a-divider orientation="left">通知与可见范围</a-divider>
        <div v-for="(rule, index) in form.notify_rules" :key="index" class="param-row"><a-select v-model:value="rule.event" class="rule-event" :options="[{ label: '待审批', value: 'ticket.pending_approval' }, { label: '审批通过', value: 'ticket.approved' }, { label: '审批驳回', value: 'ticket.rejected' }]" /><a-select v-model:value="rule.receivers" mode="multiple" class="rule-receivers" :options="[{ label: '创建人', value: 'creator' }, ...roleOptions.map((r) => ({ label: `角色：${r.label}`, value: `role:${r.value}` }))]" /><a-select v-model:value="rule.channels" mode="multiple" class="rule-channels" :options="[{ label: '站内信', value: 'inapp' }, { label: '邮件', value: 'email' }]" /><a-button size="small" danger @click="removeNotify(index)"><DeleteOutlined /></a-button></div>
        <a-button size="small" class="op-btn-green" @click="addNotify"><PlusOutlined />添加通知规则</a-button>
        <a-form-item label="可见角色" class="visible-role"><a-select v-model:value="form.visible_role_ids" mode="multiple" :options="roleOptions" placeholder="留空表示全部角色可见" /></a-form-item>
      </a-form>
    </a-modal>
    <a-drawer v-model:open="detailOpen" :title="detail?.name || '工单模板详情'" :width="640"><a-spin :spinning="!detail"><template v-if="detail"><div class="head-line"><a-tag :color="typeText[detail.type]?.color">{{ typeText[detail.type]?.text || detail.type }}</a-tag><a-tag :color="detail.status === 'enabled' ? 'green' : 'default'">{{ detail.status === 'enabled' ? '启用' : '停用' }}</a-tag></div><a-descriptions bordered size="small" :column="1"><a-descriptions-item label="说明">{{ detail.description || '-' }}</a-descriptions-item><a-descriptions-item label="作业主机">{{ hostMap[detail.job_host_id] || detail.job_host_id }}</a-descriptions-item><a-descriptions-item label="流程模板">{{ detail.process_template?.name || processMap[(detail as Row).process_template_id] || '-' }}</a-descriptions-item><a-descriptions-item label="允许终止">{{ (detail as Row).allow_withdraw ? '是' : '否' }}</a-descriptions-item></a-descriptions></template></a-spin></a-drawer>
  </div>
</template>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 16px; }
.kw { width: 220px; }.type-sel { width: 130px; }.status-sel { width: 110px; }.toolbar-actions { margin-left: auto; display: flex; gap: 10px; }
.form-row { display: flex; gap: 16px; }.form-col { flex: 1; }.form-col-sm { width: 130px; }.param-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }.rule-event { width: 140px; }.rule-receivers { flex: 1; }.rule-channels { width: 150px; }.visible-role { margin-top: 16px; }.head-line { display: flex; gap: 4px; margin-bottom: 14px; }
</style>
