<script setup lang="ts">
// 安全审计：组合检索 + 详情抽屉 + CSV/Excel 导出（audit:read / audit:export，AUDIT-02/03）
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import dayjs, { type Dayjs } from 'dayjs'
import {
  DownOutlined,
  ExportOutlined,
  EyeOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue'
import * as auditApi from '@/api/audit'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const canExport = userStore.hasPerm('audit:export')

// 模块下拉选项（与后端埋点 module 取值对齐）
const moduleOptions = [
  { label: '认证', value: 'auth' },
  { label: '用户', value: 'user' },
  { label: '角色', value: 'role' },
  { label: '资产', value: 'cmdb' },
  { label: '凭据', value: 'credential' },
  { label: '模板', value: 'template' },
  { label: '作业管理', value: 'job' },
  { label: '域名管理', value: 'domain' },
  { label: '应用配置', value: 'config' },
  { label: '工单', value: 'ticket' },
  { label: '执行', value: 'execution' },
  { label: '通知', value: 'notify' },
  { label: '系统', value: 'system' },
  { label: '审计', value: 'audit' },
]
const moduleText: Record<string, string> = Object.fromEntries(
  moduleOptions.map((o) => [o.value, o.label]),
)
// 模块彩色标签（固定映射保证同模块颜色稳定）
const moduleColors: Record<string, string> = {
  auth: 'geekblue', user: 'blue', role: 'purple', cmdb: 'cyan',
  credential: 'green', template: 'magenta', job: 'magenta', domain: 'cyan', config: 'purple', ticket: 'volcano',
  execution: 'orange', notify: 'gold', system: 'lime', audit: 'red',
}

// ---------- 筛选与列表 ----------
const loading = ref(false)
const items = ref<auditApi.AuditLogItem[]>([])
const total = ref(0)
const rangeEnd = dayjs()
const range = ref<[string, string] | null>([
  rangeEnd.subtract(30, 'day').format('YYYY-MM-DD HH:mm:ss'),
  rangeEnd.format('YYYY-MM-DD HH:mm:ss'),
])
const rangePresets: { label: string; value: [Dayjs, Dayjs] }[] = [
  { label: '最近 7 天', value: [dayjs().subtract(7, 'day'), dayjs()] },
  { label: '最近 30 天', value: [dayjs().subtract(30, 'day'), dayjs()] },
  { label: '最近 90 天', value: [dayjs().subtract(90, 'day'), dayjs()] },
]
const moreOpen = ref(false)
const draft = reactive({ keyword: '', actor: '', action: '' })
let generation = 0
const query = reactive({
  page: 1,
  page_size: 20,
  range: [...range.value!] as string[],
  module: undefined as string | undefined,
  result: undefined as string | undefined,
  actor: '',
  action: '',
  keyword: '',
})

/** 组装组合筛选参数（列表与导出共用，保证「导出内容与筛选一致」） */
function buildFilters(): auditApi.AuditQuery {
  return {
    // 通用审计存储本地 DATETIME，保持选择器本地时间并覆盖结束秒的闭区间。
    start: query.range[0] || undefined,
    end: query.range[1] ? `${query.range[1]}.999999` : undefined,
    module: query.module,
    result: query.result,
    actor: query.actor || undefined,
    action: query.action || undefined,
    keyword: query.keyword || undefined,
  }
}

/** 拉取审计日志列表 */
async function loadList() {
  const current = ++generation
  loading.value = true
  try {
    const data = await auditApi.listAuditLogs({
      page: query.page,
      page_size: query.page_size,
      ...buildFilters(),
    })
    if (current === generation) {
      items.value = data.items
      total.value = data.total
    }
  } catch {
    /* 错误提示由拦截器统一弹出，旧请求不得影响当前筛选。 */
  } finally {
    if (current === generation) loading.value = false
  }
}

// ---------- 横幅统计（总量/今日/今日失败，不受筛选影响，page_size=1 只取 total） ----------
const stats = reactive({ all: 0, today: 0, todayFailed: 0 })

async function loadStats() {
  const todayStart = `${new Date().toISOString().slice(0, 10)} 00:00:00`
  const [all, today, failed] = await Promise.all([
    auditApi.listAuditLogs({ page: 1, page_size: 1 }),
    auditApi.listAuditLogs({ page: 1, page_size: 1, start: todayStart }),
    auditApi.listAuditLogs({ page: 1, page_size: 1, start: todayStart, result: 'failed' }),
  ])
  stats.all = all.total
  stats.today = today.total
  stats.todayFailed = failed.total
}

/** 列表 + 统计一起刷新 */
function refreshAll() {
  loadList()
  loadStats()
}

/** 筛选变化：回第一页重查 */
function onSearch() {
  query.page = 1
  loadList()
}

/** 各输入独立提交，分页、下拉和导出始终使用已提交条件。 */
function submitDraft(key: keyof typeof draft) {
  query[key] = draft[key].trim()
  onSearch()
}

function changeDraft(key: keyof typeof draft, event: Event) {
  if (event.type === 'click' && !(event.target as HTMLInputElement).value) {
    query[key] = ''
    onSearch()
  }
}

/** 仅完整选择或清空时提交时间条件，日历中的半段选择仍是草稿。 */
function changeRange(value: string[] | null) {
  if (value && (!value[0] || !value[1])) return
  query.range = value ? [...value] : []
  onSearch()
}

function onPageChange(page: number, pageSize: number) {
  query.page = page
  query.page_size = pageSize
  loadList()
}

// 操作列固定右侧，其余列可拖拽调宽
const columns = ref(makeResizable([
  { title: '时间', key: 'created', width: 165 },
  { title: '操作人', dataIndex: 'actor_name', key: 'actor_name', width: 110, ellipsis: true },
  { title: '来源 IP', dataIndex: 'source_ip', key: 'source_ip', width: 130, ellipsis: true },
  { title: '模块', key: 'module', width: 90 },
  { title: '动作', dataIndex: 'action', key: 'action', width: 170, ellipsis: true },
  { title: '对象', key: 'target', width: 220, ellipsis: true },
  { title: '结果', key: 'result', width: 90 },
  { title: '操作', key: 'op', width: 98, fixed: 'right' as const },
]))

// ---------- 详情抽屉 ----------
const detailOpen = ref(false)
const detailRow = ref<auditApi.AuditLogItem | null>(null)

/** 打开单条审计详情（含 detail JSON 原文） */
function openDetail(row: auditApi.AuditLogItem) {
  detailRow.value = row
  detailOpen.value = true
}

// ---------- 导出（按当前筛选，格式 CSV / Excel） ----------
const exporting = ref(false)

/** 导出菜单点击入口（模板内联带类型标注的箭头函数 vue-tsc 无法解析，改在 script 中声明） */
function onExportMenu(info: { key: string | number }) {
  onExport(info.key as 'csv' | 'xlsx')
}

/** 触发导出下载：与列表共用 buildFilters，后端上限 10 万行 */
async function onExport(format: 'csv' | 'xlsx') {
  if (exporting.value) return
  exporting.value = true
  try {
    await auditApi.exportAuditLogs(format, buildFilters())
    message.success(`已按当前筛选导出 ${format === 'csv' ? 'CSV' : 'Excel'}`)
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    exporting.value = false
  }
}

onMounted(refreshAll)
onBeforeUnmount(() => { generation++ })
</script>

<template>
  <div>
    <!-- 彩色横幅：页面标识 + 全局统计 -->
    <div class="op-hero op-hero--violet">
      <div class="op-hero-icon"><SafetyCertificateOutlined /></div>
      <div>
        <div class="op-hero-title">安全审计</div>
        <div class="op-hero-sub">全链路操作留痕检索、合规导出与保留期治理</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat"><b>{{ stats.all }}</b><span>总事件</span></div>
        <div class="op-hero-stat"><b>{{ stats.today }}</b><span>今日事件</span></div>
        <div class="op-hero-stat"><b>{{ stats.todayFailed }}</b><span>今日失败</span></div>
      </div>
    </div>

    <!-- 组合筛选工具栏 -->
    <div class="toolbar">
      <a-input v-model:value="draft.keyword" placeholder="搜索审计日志" aria-label="搜索审计日志" class="keyword" :maxlength="255" allow-clear
        @press-enter="submitDraft('keyword')" @change="changeDraft('keyword', $event)">
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-select
        v-model:value="query.module"
        placeholder="模块"
        class="sel"
        allow-clear
        :options="moduleOptions"
        @change="onSearch"
      />
      <a-select
        v-model:value="query.result"
        placeholder="结果"
        class="sel"
        allow-clear
        :options="[
          { label: '成功', value: 'success' },
          { label: '失败', value: 'failed' },
        ]"
        @change="onSearch"
      />
      <a-range-picker
        v-model:value="range"
        show-time
        value-format="YYYY-MM-DD HH:mm:ss"
        class="range"
        popup-class-name="security-audit-range-popup"
        :placeholder="['开始时间', '结束时间']"
        :presets="rangePresets"
        @change="changeRange"
      />
      <a-button class="audit-more-toggle" :aria-expanded="moreOpen" aria-controls="audit-advanced"
        @click="moreOpen=!moreOpen">更多筛选</a-button>
      <a-dropdown v-if="canExport" class="export-btn">
        <a-button :loading="exporting"><template #icon><ExportOutlined /></template>导出<DownOutlined v-if="!exporting" /></a-button>
        <template #overlay>
          <a-menu @click="onExportMenu">
            <a-menu-item key="csv">导出 CSV</a-menu-item>
            <a-menu-item key="xlsx">导出 Excel</a-menu-item>
          </a-menu>
        </template>
      </a-dropdown>
    </div>
    <div v-if="moreOpen" id="audit-advanced" class="toolbar audit-advanced">
      <a-input v-model:value="draft.actor" placeholder="操作人" aria-label="操作人" class="ipt" :maxlength="64" allow-clear
        @press-enter="submitDraft('actor')" @change="changeDraft('actor', $event)" />
      <a-input v-model:value="draft.action" placeholder="动作（如 login）" aria-label="动作" class="ipt" :maxlength="64" allow-clear
        @press-enter="submitDraft('action')" @change="changeDraft('action', $event)" />
    </div>

    <!-- 审计列表：列间分割线 + 操作列固定 + 可拖拽调宽 -->
    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      row-key="id"
      bordered
      :scroll="{ x: 1150 }"
      @resize-column="onResizeColumn"
      :pagination="{
        current: query.page,
        pageSize: query.page_size,
        total,
        showSizeChanger: true,
        showQuickJumper: true,
        showTotal: (t: number) => `共 ${t} 条记录`,
        onChange: onPageChange,
      }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'created'">
          {{ record.created_at ? new Date(record.created_at).toLocaleString() : '—' }}
        </template>
        <template v-else-if="column.key === 'module'">
          <a-tag :color="moduleColors[record.module] || 'default'">
            {{ moduleText[record.module] || record.module }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'target'">
          <template v-if="record.target_name || record.target_type">
            <span class="target-type" v-if="record.target_type">[{{ record.target_type }}]</span>
            {{ record.target_name || record.target_id || '—' }}
          </template>
          <template v-else>—</template>
        </template>
        <template v-else-if="column.key === 'result'">
          <a-tag :color="record.result === 'success' ? 'success' : 'error'">
            {{ record.result === 'success' ? '成功' : '失败' }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'op'">
          <a-button size="small" class="op-btn-blue" @click="openDetail(record as auditApi.AuditLogItem)">
            <EyeOutlined />
            详情
          </a-button>
        </template>
      </template>
    </a-table>

    <!-- 详情抽屉：完整字段 + detail JSON 原文 -->
    <a-drawer v-model:open="detailOpen" title="审计详情" :width="520">
      <a-descriptions v-if="detailRow" :column="1" bordered size="small" class="op-desc-table">
        <a-descriptions-item label="时间">
          {{ detailRow.created_at ? new Date(detailRow.created_at).toLocaleString() : '—' }}
        </a-descriptions-item>
        <a-descriptions-item label="操作人">
          {{ detailRow.actor_name || '—' }}<span v-if="detailRow.actor_id">（ID: {{ detailRow.actor_id }}）</span>
        </a-descriptions-item>
        <a-descriptions-item label="来源 IP">{{ detailRow.source_ip || '—' }}</a-descriptions-item>
        <a-descriptions-item label="模块">
          <a-tag :color="moduleColors[detailRow.module] || 'default'">
            {{ moduleText[detailRow.module] || detailRow.module }}
          </a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="动作">{{ detailRow.action }}</a-descriptions-item>
        <a-descriptions-item label="对象类型">{{ detailRow.target_type || '—' }}</a-descriptions-item>
        <a-descriptions-item label="对象 ID">{{ detailRow.target_id || '—' }}</a-descriptions-item>
        <a-descriptions-item label="对象名称">{{ detailRow.target_name || '—' }}</a-descriptions-item>
        <a-descriptions-item label="结果">
          <a-tag :color="detailRow.result === 'success' ? 'success' : 'error'">
            {{ detailRow.result === 'success' ? '成功' : '失败' }}
          </a-tag>
        </a-descriptions-item>
      </a-descriptions>
      <template v-if="detailRow?.detail">
        <div class="detail-label">变更明细 / 上下文</div>
        <pre class="detail-json">{{ JSON.stringify(detailRow.detail, null, 2) }}</pre>
      </template>
    </a-drawer>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}
.range {
  width: 400px;
  max-width: 100%;
  min-width: 0;
}
.sel {
  width: 140px;
}
.keyword {
  width: 220px;
}
.audit-advanced {
  padding: 14px;
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 8px;
}
.audit-advanced .ipt {
  width: 200px;
}
.export-btn {
  margin-left: auto;
}
@media (max-width: 760px) {
  .keyword { width: 100%; }
  .sel, .audit-advanced .ipt { width: calc((100% - 10px) / 2); }
  .range :deep(.ant-picker-input > input) { font-size: 12px; }
  .range :deep(.ant-picker-range-separator) { padding-inline: 4px; }
  :global(.security-audit-range-popup.ant-picker-dropdown .ant-picker-panel-container) { max-width: calc(100vw - 24px); max-height: calc(50dvh - 24px); overflow: auto; }
  :global(.security-audit-range-popup.ant-picker-dropdown .ant-picker-panel-container .ant-picker-panel-layout),
  :global(.security-audit-range-popup.ant-picker-dropdown .ant-picker-datetime-panel) { flex-direction: column; }
  :global(.security-audit-range-popup.ant-picker-dropdown .ant-picker-panel-container .ant-picker-presets) { max-width: none; }
  :global(.security-audit-range-popup.ant-picker-dropdown .ant-picker-panel-container .ant-picker-presets ul) { display: flex; flex-wrap: wrap; gap: 8px; height: auto; }
  :global(.security-audit-range-popup.ant-picker-dropdown .ant-picker-panel-container .ant-picker-presets li + li) { margin-top: 0; }
}
.target-type {
  color: var(--op-text-tertiary, #999);
  margin-right: 4px;
}
.detail-label {
  margin: 16px 0 8px;
  font-weight: 600;
}
.detail-json {
  background: var(--op-fill-quaternary, #f5f5f5);
  border-radius: 6px;
  padding: 12px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
