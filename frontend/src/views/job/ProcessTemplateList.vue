<script setup lang="ts">
// 流程模板列表：集中维护步骤、执行策略和步骤前审批，供多个工单模板复用。
import { computed, onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { CodeOutlined, CopyOutlined, DeleteOutlined, EditOutlined, EyeOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons-vue'
import * as api from '@/api/job'
import { makeResizable, onResizeColumn } from '@/utils/table'
import ProcessTemplateEditor from './ProcessTemplateEditor.vue'
import { useUserStore } from '@/stores/user'
import { listRoleOptions } from '@/api/system'

const userStore = useUserStore()
const canWrite = userStore.hasPerm('template:write')
const canDelete = userStore.hasPerm('template:delete')

const rows = ref<api.ProcessTemplateItem[]>([])
const loading = ref(false)
const total = ref(0)
const open = ref(false)
const editingId = ref<number | null>(null)
const copyFromId = ref<number | null>(null)
const roles = ref<{ id: number; name: string }[]>([])
const detail = ref<(api.ProcessTemplateItem & { steps: api.ProcessStep[] }) | null>(null)
const detailOpen = ref(false)
const query = reactive({ page: 1, page_size: 20, keyword: '', status: undefined as string | undefined })
const selectedKeys = ref<number[]>([])
const statusLoading = ref<number | null>(null)
const stats = reactive({ all: 0, enabled: 0, disabled: 0 })
const roleMap = computed(() => Object.fromEntries(roles.value.map((role) => [role.id, role.name])))
/** 将执行策略压缩为列表摘要，保留关键配置且避免表格单元格过度占用空间。 */
function strategySummary(strategy: api.ProcessTemplateItem['exec_strategy']): string {
  const timeout = strategy?.timeout ?? '-'
  const flag = (value: boolean | undefined, enabled: string, disabled: string) => value === undefined ? '-' : value ? enabled : disabled
  const failFast = flag(strategy?.fail_fast, '失败即停', '失败继续')
  const killOnStop = flag(strategy?.kill_on_stop, '终止停止', '终止继续')
  return `${timeout}秒 / ${failFast} / ${killOnStop}`
}
const columns = computed(() => makeResizable([
  { title: '流程名称', dataIndex: 'name', key: 'name', width: 220, ellipsis: true },
  { title: '步骤数', key: 'steps', width: 90 },
  { title: '引用工单模板', key: 'refs', width: 120 },
  { title: '执行策略', key: 'strategy', width: 220, ellipsis: true },
  { title: '状态', key: 'status', width: 90 },
  { title: '更新时间', key: 'updated', width: 160 },
  { title: '操作', key: 'action', width: 235, fixed: 'right' as const },
]))
async function load() {
  loading.value = true
  try {
    const [data, roleData] = await Promise.all([api.listProcessTemplates(query), listRoleOptions()])
    rows.value = data.items; total.value = data.total; selectedKeys.value = []; roles.value = roleData.items
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
function edit(id: number | null) { editingId.value = id; copyFromId.value = null; open.value = true }
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
    const data = await api.listProcessTemplates({ page: 1, page_size: 100, keyword: keyword || undefined })
    copyOptions.value = data.items.map((item) => ({ label: `${item.name}${item.status === 'disabled' ? '（已停用）' : ''}`, value: item.id }))
  } finally {
    copySearching.value = false
  }
}
function confirmCopy(): void {
  if (!copySourceId.value) {
    message.warning('请选择要复制的流程模板')
    return
  }
  copyOpen.value = false
  editingId.value = null
  copyFromId.value = copySourceId.value
  open.value = true
}
async function toggle(row: api.ProcessTemplateItem) { statusLoading.value = row.id; try { await api.setProcessTemplateStatus(row.id, row.status === 'enabled' ? 'disabled' : 'enabled'); await load() } finally { statusLoading.value = null } }
async function remove(row: api.ProcessTemplateItem) { await api.deleteProcessTemplate(row.id); message.success('流程模板已删除'); await load() }
async function openDetail(row: api.ProcessTemplateItem) { detail.value = null; detailOpen.value = true; detail.value = await api.getProcessTemplate(row.id) }
/** 批量删除逐条调用现有接口，保留流程模板引用保护和明确的失败汇总。 */
async function batchRemove(): Promise<void> {
  const ids = [...selectedKeys.value]
  if (!ids.length) return
  const results = await Promise.allSettled(ids.map((id) => api.deleteProcessTemplate(id)))
  const failed = results.filter((result) => result.status === 'rejected').length
  if (failed) message.warning(`已删除 ${ids.length - failed} 个模板，${failed} 个模板删除失败（可能仍被工单模板引用）`)
  else message.success(`已删除 ${ids.length} 个流程模板`)
  await load()
}
function confirmBatchRemove(): void {
  if (!selectedKeys.value.length) return
  Modal.confirm({ title: '批量删除流程模板', content: `确认删除选中的 ${selectedKeys.value.length} 个模板吗？`, okText: '删除', okType: 'danger', cancelText: '取消', onOk: batchRemove })
}
const rowSelection = computed(() => ({ selectedRowKeys: selectedKeys.value, onChange: (keys: (string | number)[]) => (selectedKeys.value = keys as number[]) }))
onMounted(load)
</script>

<template>
  <div>
    <div class="op-hero op-hero--indigo"><div class="op-hero-icon"><CodeOutlined /></div><div><div class="op-hero-title">流程模板</div><div class="op-hero-sub">集中维护步骤编排、执行策略和步骤前审批，供多个工单模板复用</div></div><div class="op-hero-extra"><div class="op-hero-stat"><b>{{ stats.all }}</b><span>全部</span></div><div class="op-hero-stat"><b>{{ stats.enabled }}</b><span>启用</span></div><div class="op-hero-stat"><b>{{ stats.disabled }}</b><span>停用</span></div></div></div>
    <div class="toolbar"><a-input v-model:value="query.keyword" class="kw" allow-clear placeholder="搜索流程模板" @press-enter="search"><template #prefix><SearchOutlined /></template></a-input><a-select v-model:value="query.status" class="status-sel" allow-clear placeholder="状态" :options="[{ label: '启用', value: 'enabled' }, { label: '停用', value: 'disabled' }]" @change="search" /><div class="toolbar-actions"><a-button v-if="canDelete" danger :disabled="!selectedKeys.length" @click="confirmBatchRemove"><DeleteOutlined />批量删除{{ selectedKeys.length ? `（${selectedKeys.length}）` : '' }}</a-button><a-button v-if="canWrite" @click="openCopy"><CopyOutlined />复制模板</a-button><a-button v-if="canWrite" type="primary" @click="edit(null)"><PlusOutlined />新建模板</a-button></div></div>
    <a-table :columns="columns" :data-source="rows" :loading="loading" bordered row-key="id" :scroll="{ x: 1000 }" :row-selection="rowSelection" @resize-column="onResizeColumn" :pagination="{ current: query.page, pageSize: query.page_size, total, showSizeChanger: true, showQuickJumper: true, showTotal: (t: number) => `共 ${t} 个模板`, onChange: pageChange }">
      <template #bodyCell="{ column, record }"><template v-if="column.key === 'steps'">{{ record.steps_count ?? 0 }}</template><template v-else-if="column.key === 'refs'"><a-tag :color="record.ticket_template_refs ? 'blue' : 'default'">{{ record.ticket_template_refs || 0 }}</a-tag></template><template v-else-if="column.key === 'strategy'">{{ strategySummary(record.exec_strategy) }}</template><template v-else-if="column.key === 'status'"><a-switch :checked="record.status === 'enabled'" checked-children="启用" un-checked-children="停用" :loading="statusLoading === record.id" @change="toggle(record as api.ProcessTemplateItem)" /></template><template v-else-if="column.key === 'updated'">{{ record.updated_at ? new Date(record.updated_at).toLocaleString() : '-' }}</template><template v-else-if="column.key === 'action'"><a-space><a-button size="small" class="op-btn-cyan" @click="openDetail(record as api.ProcessTemplateItem)"><EyeOutlined />详情</a-button><a-button v-if="canWrite" size="small" class="op-btn-blue" @click="edit(record.id)"><EditOutlined />编辑</a-button><a-popconfirm v-if="canDelete" title="流程模板被工单模板引用时不能删除，确认继续？" @confirm="remove(record as api.ProcessTemplateItem)"><a-button size="small" danger><DeleteOutlined />删除</a-button></a-popconfirm></a-space></template></template>
    </a-table>
    <ProcessTemplateEditor v-model:open="open" :process-id="editingId" :copy-from-id="copyFromId" @saved="load" />
    <a-modal v-model:open="copyOpen" title="复制流程模板" :confirm-loading="copySearching" @ok="confirmCopy">
      <div class="copy-tip">选择源模板后进入编辑器，确认保存后才会创建新模板。</div>
      <a-select v-model:value="copySourceId" class="copy-select" show-search :filter-option="false" :options="copyOptions" :loading="copySearching" placeholder="搜索并选择源模板" @search="onCopySearch" />
    </a-modal>
    <a-drawer v-model:open="detailOpen" :title="detail?.name || '流程模板详情'" :width="720">
      <a-spin :spinning="!detail">
        <template v-if="detail">
          <div class="head-line"><a-tag :color="detail.status === 'enabled' ? 'green' : 'default'">{{ detail.status === 'enabled' ? '启用' : '停用' }}</a-tag></div>
          <a-descriptions bordered size="small" :column="1" class="op-desc-table">
            <a-descriptions-item label="模板 ID">{{ detail.id }}</a-descriptions-item>
            <a-descriptions-item label="模板名称">{{ detail.name }}</a-descriptions-item>
            <a-descriptions-item label="说明">{{ detail.description || '-' }}</a-descriptions-item>
            <a-descriptions-item label="步骤数量">{{ detail.steps?.length || 0 }}</a-descriptions-item>
            <a-descriptions-item label="创建时间">{{ detail.created_at ? new Date(detail.created_at).toLocaleString() : '-' }}</a-descriptions-item>
            <a-descriptions-item label="更新时间">{{ detail.updated_at ? new Date(detail.updated_at).toLocaleString() : '-' }}</a-descriptions-item>
          </a-descriptions>
          <a-descriptions bordered size="small" :column="1" class="op-desc-table detail-section">
            <a-descriptions-item label="默认超时">{{ detail.exec_strategy?.timeout ?? '-' }}{{ detail.exec_strategy?.timeout !== undefined ? ' 秒' : '' }}</a-descriptions-item>
            <a-descriptions-item label="失败策略">{{ detail.exec_strategy?.fail_fast === undefined ? '-' : detail.exec_strategy.fail_fast ? '失败即停' : '失败继续' }}</a-descriptions-item>
            <a-descriptions-item label="停止策略">{{ detail.exec_strategy?.kill_on_stop === undefined ? '-' : detail.exec_strategy.kill_on_stop ? '停止时结束进程' : '停止时继续进程' }}</a-descriptions-item>
          </a-descriptions>
          <a-collapse class="detail-collapse" :bordered="false">
            <a-collapse-panel v-for="(step, index) in detail.steps" :key="index" :header="`步骤 ${index + 1}：${step.name || '未命名步骤'}`">
              <a-descriptions bordered size="small" :column="1" class="op-desc-table"><a-descriptions-item label="步骤名称">{{ step.name || '-' }}</a-descriptions-item><a-descriptions-item label="脚本类型">{{ step.script_type === 'playbook' ? 'Playbook' : step.script_type === 'shell' ? 'Shell' : step.script_type }}</a-descriptions-item><a-descriptions-item label="步骤超时">{{ step.timeout }} 秒</a-descriptions-item><a-descriptions-item label="审批角色">{{ step.approval_role_id ? roleMap[step.approval_role_id] || step.approval_role_id : '无需审批' }}</a-descriptions-item><a-descriptions-item label="脚本内容"><pre class="detail-code">{{ step.content || '-' }}</pre></a-descriptions-item></a-descriptions>
            </a-collapse-panel>
            <a-empty v-if="!detail.steps?.length" description="未配置步骤" />
          </a-collapse>
        </template>
      </a-spin>
    </a-drawer>
  </div>
</template>

<style scoped>
.toolbar { display: flex; gap: 10px; margin-bottom: 16px; }.kw { width: 240px; }.status-sel { width: 110px; }.toolbar-actions { margin-left: auto; display: flex; gap: 10px; }.copy-tip { font-size: 12px; color: var(--text-3); margin-bottom: 10px; }.copy-select { width: 100%; }.head-line { display: flex; gap: 4px; margin-bottom: 14px; }.detail-section { margin-top: 14px; }.detail-collapse { margin-top: 14px; }.detail-code { max-height: 320px; overflow: auto; margin: 0; padding: 10px; background: var(--bg-soft); white-space: pre-wrap; word-break: break-word; }
</style>
