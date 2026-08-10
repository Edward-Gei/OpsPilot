<script setup lang="ts">
// 安全审计：组合检索 + 详情抽屉 + CSV/Excel 导出（audit:read / audit:export，AUDIT-02/03）
import { onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
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
  credential: 'green', template: 'magenta', ticket: 'volcano',
  execution: 'orange', notify: 'gold', system: 'lime', audit: 'red',
}

// ---------- 筛选与列表 ----------
const loading = ref(false)
const items = ref<auditApi.AuditLogItem[]>([])
const total = ref(0)
const query = reactive({
  page: 1,
  page_size: 20,
  range: [] as string[], // [start, end]（value-format 字符串，后端 datetime 解析）
  module: undefined as string | undefined,
  result: undefined as string | undefined,
  actor: '',
  action: '',
  keyword: '',
})

/** 组装组合筛选参数（列表与导出共用，保证「导出内容与筛选一致」） */
function buildFilters(): auditApi.AuditQuery {
  return {
    start: query.range?.[0] || undefined,
    end: query.range?.[1] || undefined,
    module: query.module,
    result: query.result,
    actor: query.actor || undefined,
    action: query.action || undefined,
    keyword: query.keyword || undefined,
  }
}

/** 拉取审计日志列表 */
async function loadList() {
  loading.value = true
  try {
    const data = await auditApi.listAuditLogs({
      page: query.page,
      page_size: query.page_size,
      ...buildFilters(),
    })
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
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

/** 重置全部筛选条件并重查 */
function onReset() {
  Object.assign(query, {
    page: 1, range: [], module: undefined, result: undefined,
    actor: '', action: '', keyword: '',
  })
  loadList()
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
  { title: '操作', key: 'op', width: 80, fixed: 'right' as const },
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
      <a-range-picker
        v-model:value="query.range"
        show-time
        value-format="YYYY-MM-DD HH:mm:ss"
        class="range"
        :placeholder="['开始时间', '结束时间']"
        @change="onSearch"
      />
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
      <a-input v-model:value="query.actor" placeholder="操作人" class="ipt" allow-clear @press-enter="onSearch" />
      <a-input v-model:value="query.action" placeholder="动作（如 login）" class="ipt" allow-clear @press-enter="onSearch" />
      <a-input v-model:value="query.keyword" placeholder="对象名称" class="ipt" allow-clear @press-enter="onSearch">
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-button type="primary" @click="onSearch"><SearchOutlined />查询</a-button>
      <a-button @click="onReset">重置</a-button>
      <a-dropdown v-if="canExport" class="export-btn">
        <a-button :loading="exporting"><ExportOutlined />导出<DownOutlined /></a-button>
        <template #overlay>
          <a-menu @click="onExportMenu">
            <a-menu-item key="csv">导出 CSV</a-menu-item>
            <a-menu-item key="xlsx">导出 Excel</a-menu-item>
          </a-menu>
        </template>
      </a-dropdown>
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
      <a-descriptions v-if="detailRow" :column="1" bordered size="small">
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
  gap: 10px;
  margin-bottom: 16px;
}
.range {
  width: 340px;
}
.sel {
  width: 110px;
}
.ipt {
  width: 150px;
}
.export-btn {
  margin-left: auto;
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
