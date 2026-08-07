<script setup lang="ts">
// 流程模板列表：集中维护参数、步骤、执行策略和步骤前审批，供多个工单模板一对一引用。
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { CodeOutlined, DeleteOutlined, EditOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons-vue'
import * as api from '@/api/job'
import { makeResizable, onResizeColumn } from '@/utils/table'
import ProcessTemplateEditor from './ProcessTemplateEditor.vue'

const rows = ref<api.ProcessTemplateItem[]>([])
const loading = ref(false)
const total = ref(0)
const open = ref(false)
const editingId = ref<number | null>(null)
const query = reactive({ page: 1, page_size: 20, keyword: '', status: undefined as string | undefined })
const selectedKeys = ref<number[]>([])
const statusLoading = ref<number | null>(null)
const stats = reactive({ all: 0, enabled: 0, disabled: 0 })
const columns = computed(() => makeResizable([
  { title: '流程名称', dataIndex: 'name', key: 'name', width: 220, ellipsis: true },
  { title: '步骤数', key: 'steps', width: 90 },
  { title: '参数数', key: 'params', width: 90 },
  { title: '引用工单模板', key: 'refs', width: 120 },
  { title: '状态', key: 'status', width: 90 },
  { title: '更新时间', key: 'updated', width: 160 },
  { title: '操作', key: 'action', width: 235, fixed: 'right' as const },
]))
async function load() {
  loading.value = true
  try {
    const data = await api.listProcessTemplates(query)
    rows.value = data.items; total.value = data.total; selectedKeys.value = []
    const [all, enabled, disabled] = await Promise.all([
      api.listProcessTemplates({ page: 1, page_size: 1 }),
      api.listProcessTemplates({ page: 1, page_size: 1, status: 'enabled' }),
      api.listProcessTemplates({ page: 1, page_size: 1, status: 'disabled' }),
    ])
    stats.all = all.total; stats.enabled = enabled.total; stats.disabled = disabled.total
  } finally { loading.value = false }
}
function search() { query.page = 1; load() }
function pageChange(page: number, pageSize: number) { query.page = page; query.page_size = pageSize; load() }
function edit(id: number | null) { editingId.value = id; open.value = true }
async function toggle(row: api.ProcessTemplateItem) { statusLoading.value = row.id; try { await api.setProcessTemplateStatus(row.id, row.status === 'enabled' ? 'disabled' : 'enabled'); await load() } finally { statusLoading.value = null } }
async function remove(row: api.ProcessTemplateItem) { await api.deleteProcessTemplate(row.id); message.success('流程模板已删除'); load() }
const rowSelection = computed(() => ({ selectedRowKeys: selectedKeys.value, onChange: (keys: (string | number)[]) => (selectedKeys.value = keys as number[]) }))
onMounted(load)
</script>

<template>
  <div>
    <div class="op-hero op-hero--indigo"><div class="op-hero-icon"><CodeOutlined /></div><div><div class="op-hero-title">流程模板</div><div class="op-hero-sub">集中维护参数、步骤编排、执行策略和步骤前审批，供工单模板引用</div></div><div class="op-hero-extra"><div class="op-hero-stat"><b>{{ stats.all }}</b><span>全部</span></div><div class="op-hero-stat"><b>{{ stats.enabled }}</b><span>启用</span></div><div class="op-hero-stat"><b>{{ stats.disabled }}</b><span>停用</span></div></div></div>
    <div class="toolbar"><a-input v-model:value="query.keyword" class="kw" allow-clear placeholder="搜索流程模板" @press-enter="search"><template #prefix><SearchOutlined /></template></a-input><a-select v-model:value="query.status" class="status-sel" allow-clear placeholder="状态" :options="[{ label: '启用', value: 'enabled' }, { label: '停用', value: 'disabled' }]" @change="search" /><div class="toolbar-actions"><a-button type="primary" @click="edit(null)"><PlusOutlined />新建流程模板</a-button></div></div>
    <a-table :columns="columns" :data-source="rows" :loading="loading" bordered row-key="id" :scroll="{ x: 1000 }" :row-selection="rowSelection" @resize-column="onResizeColumn" :pagination="{ current: query.page, pageSize: query.page_size, total, showSizeChanger: true, showQuickJumper: true, showTotal: (t: number) => `共 ${t} 个模板`, onChange: pageChange }">
      <template #bodyCell="{ column, record }"><template v-if="column.key === 'steps'">{{ record.steps_count ?? 0 }}</template><template v-else-if="column.key === 'params'">{{ record.params_schema?.length || 0 }}</template><template v-else-if="column.key === 'refs'"><a-tag :color="record.ticket_template_refs ? 'blue' : 'default'">{{ record.ticket_template_refs || 0 }}</a-tag></template><template v-else-if="column.key === 'status'"><a-switch :checked="record.status === 'enabled'" checked-children="启用" un-checked-children="停用" :loading="statusLoading === record.id" @change="toggle(record as api.ProcessTemplateItem)" /></template><template v-else-if="column.key === 'updated'">{{ record.updated_at ? new Date(record.updated_at).toLocaleString() : '-' }}</template><template v-else-if="column.key === 'action'"><a-space><a-button size="small" class="op-btn-blue" @click="edit(record.id)"><EditOutlined />编辑</a-button><a-popconfirm title="流程模板被工单模板引用时不能删除，确认继续？" @confirm="remove(record as api.ProcessTemplateItem)"><a-button size="small" danger><DeleteOutlined />删除</a-button></a-popconfirm></a-space></template></template>
    </a-table>
    <ProcessTemplateEditor v-model:open="open" :process-id="editingId" @saved="load" />
  </div>
</template>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 16px; }.kw { width: 240px; }.status-sel { width: 110px; }.toolbar-actions { margin-left: auto; }
/* 统一主操作文案，避免列表页出现重复的“流程”前缀。 */
.toolbar-actions :deep(.ant-btn) { font-size: 0; }
.toolbar-actions :deep(.ant-btn .anticon) { font-size: 14px; }
.toolbar-actions :deep(.ant-btn::after) { content: '新建模板'; font-size: 14px; }
</style>
