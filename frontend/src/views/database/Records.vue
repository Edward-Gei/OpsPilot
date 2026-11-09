<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { Grid } from 'ant-design-vue'
import dayjs, { type Dayjs } from 'dayjs'
import { useRoute, useRouter } from 'vue-router'
import { AuditOutlined, DatabaseOutlined, EditOutlined, HistoryOutlined, InfoCircleOutlined, SearchOutlined } from '@ant-design/icons-vue'
import * as db from '@/api/database'
import { useUserStore } from '@/stores/user'
import { activeStatuses, createTaskSession, engineNames, engineOptions, errorCode, errorText, statusNames, timeText, useDatabasePageScroll } from './runtime'
import ResultTable from './ResultTable.vue'
import './database.css'

useDatabasePageScroll()

const props = defineProps<{
  audit?: boolean
}>(),
  user = useUserStore(),
  route = useRoute(),
  router = useRouter()
const items = ref<(db.TaskView | db.AuditView)[]>([]),
  total = ref(0),
  loading = ref(false),
  error = ref(''),
  selected = ref<db.AuditView | null>(null)
const history = createTaskSession(db),
  drawerOpen = ref(false),
  detailLoading = ref(false),
  detailTab = ref('overview'),
  now = ref(Date.now()),
  historyKeyword = ref(''),
  submittedHistoryKeyword = ref(''),
  historyRange = ref<[string, string] | null>(props.audit ? null : defaultHistoryRange()),
  auditRange = ref<[string, string] | null>(props.audit ? defaultHistoryRange() : null),
  auditKeyword = ref(''),
  auditMoreOpen = ref(false),
  auditDetailId = ref<number | null>(null),
  auditDetailError = ref('')
// 精确条件只提交按下回车的字段，其他输入草稿不随下拉筛选或分页带入请求。
const auditDraft = reactive({ instance_id: undefined as number | null | undefined, actor_id: undefined as number | null | undefined, source_ip: '', database: '' })
let auditInitialized = !!props.audit
// 本人历史独立保存筛选，避免与共用组件的审计页面互相带入条件。
const historyFilters = reactive({
  page: 1,
  page_size: 20,
  engine: undefined as db.DatabaseEngine | undefined,
  kind: undefined as string | undefined,
  status: undefined as db.TaskStatus | undefined
})
const query = reactive({
  page: 1,
  page_size: 20,
  instance_id: undefined as number | undefined,
  engine: undefined as db.DatabaseEngine | undefined,
  kind: undefined as string | undefined,
  status: undefined as db.TaskStatus | undefined,
  actor_id: undefined as number | undefined,
  source_ip: '',
  database: '',
  keyword: ''
})
let generation = 0,
  detailGeneration = 0
let clock: ReturnType<typeof setInterval> | undefined
const title = computed(() => props.audit ? '数据库执行审计' : '数据库执行历史')
const kindNames: Record<string, string> = { sql: 'SQL 查询', mongo_read: 'MongoDB 查询', redis_read: 'Redis 读取', metadata: '结构浏览', probe: '连接测试' }
// 窄屏时间信息独占一行，桌面仍保持两列；上下文跨度同步避免挤压。
const historyScreens = Grid.useBreakpoint()
const historyMetadataColumns = computed(() => historyScreens.value.md ? 2 : 1)
const kindOptions = Object.entries(kindNames).map(([value, label]) => ({ value, label }))
const rangePresets: { label: string; value: [Dayjs, Dayjs] }[] = [
  { label: '最近 7 天', value: [dayjs().subtract(7, 'day'), dayjs()] },
  { label: '最近 30 天', value: [dayjs().subtract(30, 'day'), dayjs()] },
  { label: '最近 90 天', value: [dayjs().subtract(90, 'day'), dayjs()] },
]
const historyColumns = [
  { title: '操作编号', key: 'id', width: 100 },
  { title: '实例 / 上下文', key: 'target', width: 230 },
  { title: '操作摘要', key: 'operation', width: 240 },
  { title: '提交时间', key: 'created', width: 180 },
  { title: '状态', key: 'status', width: 130 },
  { title: '执行耗时', key: 'duration', width: 105 },
  { title: '结果', key: 'result', width: 220 },
  { title: '操作', key: 'action', width: 100, fixed: 'right' as const }
]
const displayItems = computed(() => items.value.map(item => history.task.value?.task_id === item.task_id ? history.task.value : item))
const canReadResult = computed(() => {
  const task = history.task.value
  const expires = task && resultExpiry(task)
  return !!task && task.status === 'succeeded' && task.kind !== 'probe' && user.hasPerm('database:query') && (!expires || expires > now.value)
})
const canCancel = computed(() => !!history.task.value && ['queued', 'running'].includes(history.task.value.status))
const processSteps = computed(() => {
  const task = history.task.value
  if (!task) return []
  const steps = [{ label: '提交请求', time: task.created_at }]
  if (task.started_at) steps.push({ label: '开始执行', time: task.started_at })
  if (task.finished_at) steps.push({ label: task.status === 'succeeded' ? '执行完成' : statusNames[task.status], time: task.finished_at })
  return steps
})

function contextText(task: db.TaskView | db.AuditView) {
  return Object.entries(task.context || {}).filter(([, value]) => ['string', 'number'].includes(typeof value))
    .map(([key, value]) => task.engine === 'redis' && key === 'database' ? `DB ${value}` : String(value)).join(' / ') || '—'
}
function summaryNumber(task: db.TaskView, key: string) {
  const value = task.result_summary?.[key]
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}
function bytesText(value: number | null) {
  if (value === null) return '—'
  if (value < 1024) return `${value} B`
  return value < 1048576 ? `${(value / 1024).toFixed(1)} KiB` : `${(value / 1048576).toFixed(2)} MiB`
}
function resultExpiry(task: db.TaskView) {
  const value = task.expires_at || task.result_summary?.expires_at
  const time = typeof value === 'string' ? new Date(value).getTime() : NaN
  return Number.isFinite(time) ? time : null
}
function resultInfo(task: db.TaskView) {
  if (task.kind === 'probe') return { title: '不产生结果集', detail: typeof task.result_summary?.message === 'string' ? task.result_summary.message : kindNames.probe }
  if (task.status !== 'succeeded') return { title: activeStatuses.includes(task.status) ? '结果暂不可用' : '无可查看结果', detail: task.status === 'rejected' ? '未执行' : statusNames[task.status] }
  const expires = resultExpiry(task)
  if (expires && expires <= now.value) return { title: '结果已过期', detail: '操作详情继续保留' }
  const count = summaryNumber(task, 'returned_rows')
  return { title: count === null ? '结果摘要暂不可用' : `${count.toLocaleString('en-US')} 条 · ${bytesText(summaryNumber(task, 'returned_bytes'))}`, detail: `${task.result_summary?.truncated === true ? '已截断 · ' : ''}${expires ? `有效至 ${timeText(new Date(expires).toISOString())}` : '按需查看结果'}` }
}
function durationText(task: db.TaskView) {
  if (!task.started_at || (!task.finished_at && !activeStatuses.includes(task.status))) return '—'
  const elapsed = new Date(task.finished_at || now.value).getTime() - new Date(task.started_at).getTime()
  return Number.isFinite(elapsed) && elapsed >= 0 ? `${(elapsed / 1000).toFixed(3)} s` : '—'
}
function statusColor(status: db.TaskStatus) {
  return status === 'succeeded' ? 'success' : ['queued', 'running'].includes(status) ? 'processing' : ['stopping', 'recovering', 'timed_out'].includes(status) ? 'warning' : status === 'failed' ? 'error' : 'default'
}
const auditColumns = [
  { title: '操作编号', key: 'id', width: 100 },
  { title: '操作者 / 来源 IP', key: 'actor', width: 190 },
  { title: '实例 / 上下文', key: 'target', width: 230 },
  { title: '操作摘要', key: 'operation', width: 240 },
  { title: '提交时间', key: 'created', width: 180 },
  { title: '状态', key: 'status', width: 130 },
  { title: '操作', key: 'action', width: 100, fixed: 'right' as const }
]

async function load() {
  const g = ++generation
  loading.value = true
  error.value = ''
  try {
    const current = props.audit ? query : historyFilters
    const range = props.audit ? auditRange.value : historyRange.value
    const filters = {
      page: current.page,
      page_size: current.page_size,
      instance_id: props.audit ? query.instance_id : undefined,
      engine: current.engine,
      kind: current.kind,
      status: current.status,
      started_from: range?.[0] ? dayjs(range[0]).toISOString() : undefined,
      // 输入精确到秒，结束参数向后一秒覆盖该秒内提交的操作。
      started_to: range?.[1] ? dayjs(range[1]).add(1, 'second').toISOString() : undefined
    }
    const data = props.audit ? await db.listAudit({
      ...filters,
      actor_id: query.actor_id,
      source_ip: query.source_ip || undefined,
      database: query.database || undefined,
      keyword: query.keyword || undefined
    }) : await db.listTasks({ ...filters, keyword: submittedHistoryKeyword.value || undefined })
    if (g === generation) {
      items.value = data.items
      total.value = data.total
    }
  } catch (e) {
    if (g === generation) {
      if ([40101, 40102, 40301, 40302].includes(errorCode(e) || 0)) clearRecords()
      error.value = errorText(e)
    }
  } finally {
    if (g === generation) loading.value = false
  }
}

async function choose(id: number) {
  if (!Number.isSafeInteger(id) || id <= 0) return
  if (props.audit && !user.hasPerm('database:audit')) { clearRecords(); error.value = '当前无权读取此页面记录'; return }
  detailTab.value = 'overview'
  selected.value = null
  auditDetailError.value = ''
  history.clear()
  const g = ++detailGeneration
  drawerOpen.value = true
  detailLoading.value = true
  if (props.audit) {
    auditDetailId.value = id
    try {
      const data = await db.getAudit(id)
      if (g === detailGeneration) selected.value = data
    } catch (e) {
      if (g === detailGeneration) {
        if ([40101, 40102, 40301, 40302].includes(errorCode(e) || 0)) { clearRecords(); error.value = errorText(e) }
        else auditDetailError.value = errorText(e)
      }
    } finally {
      if (g === detailGeneration) detailLoading.value = false
    }
  } else {
    try { await history.select(id, false) }
    finally { if (g === detailGeneration) detailLoading.value = false }
  }
}

async function viewResult() {
  if (props.audit || !drawerOpen.value || !canReadResult.value || history.resultLoading.value) return
  detailTab.value = 'result'
  await history.page(1)
}
function changeDetailTab(key: string | number) {
  detailTab.value = String(key)
  if (key === 'result' && !history.result.value) void viewResult()
}
/** 关闭即销毁会话，终止轮询、在途结果和到期计时器，避免敏感结果回写。 */
function closeDetails() {
  detailGeneration++
  drawerOpen.value = false
  detailLoading.value = false
  detailTab.value = 'overview'
  selected.value = null
  auditDetailId.value = null
  auditDetailError.value = ''
  history.clear()
}
/** 权限边界变化时使列表、详情和在途响应一并失效，避免旧范围的数据留屏。 */
function clearRecords() {
  generation++
  selected.value = null
  closeDetails()
  items.value = []
  total.value = 0
  loading.value = false
}
function defaultHistoryRange(): [string, string] {
  const end = dayjs()
  return [end.subtract(30, 'day').format('YYYY-MM-DD HH:mm:ss'), end.format('YYYY-MM-DD HH:mm:ss')]
}
function resetHistoryFilters() {
  Object.assign(historyFilters, { page: 1, page_size: 20, engine: undefined, kind: undefined, status: undefined })
  historyKeyword.value = ''
  submittedHistoryKeyword.value = ''
  historyRange.value = defaultHistoryRange()
}
function searchHistory() {
  historyFilters.page = 1
  void load()
}
function submitHistoryKeyword() {
  submittedHistoryKeyword.value = historyKeyword.value.trim()
  searchHistory()
}
/** 清空图标以 click change 上报；普通输入（包括删空）仍等待回车。 */
function changeHistoryKeyword(event: Event) {
  if (event.type === 'click' && !(event.target as HTMLInputElement).value) {
    submittedHistoryKeyword.value = ''
    searchHistory()
  }
}
function searchAudit() {
  query.page = 1
  void load()
}
function submitAuditKeyword() {
  query.keyword = auditKeyword.value.trim()
  searchAudit()
}
function changeAuditKeyword(event: Event) {
  if (event.type === 'click' && !(event.target as HTMLInputElement).value) {
    query.keyword = ''
    searchAudit()
  }
}
function submitAuditId(key: 'instance_id' | 'actor_id') {
  query[key] = auditDraft[key] ?? undefined
  searchAudit()
}
function clearAuditId(key: 'instance_id' | 'actor_id', value: number | string | null) {
  if (value === null || value === '') { query[key] = undefined; searchAudit() }
}
function submitAuditText(key: 'source_ip' | 'database') {
  query[key] = auditDraft[key].trim()
  searchAudit()
}
function clearAuditText(key: 'source_ip' | 'database', event: Event) {
  if (!(event.target as HTMLInputElement).value && query[key]) { query[key] = ''; searchAudit() }
}

function changePage(p: {
  current?: number
  pageSize?: number
}) {
  const current = props.audit ? query : historyFilters
  current.page = p.current || 1
  current.page_size = p.pageSize || 20
  void load()
}

// 两个路由共用此组件；切换时清除本人任务/结果和旧列表，重新按目标页面权限读取。
watch(() => props.audit, () => {
  clearRecords()
  if (props.audit) {
    query.page = 1
    if (!auditInitialized) { auditRange.value = defaultHistoryRange(); auditInitialized = true }
    auditKeyword.value = query.keyword
    Object.assign(auditDraft, { instance_id: query.instance_id, actor_id: query.actor_id, source_ip: query.source_ip, database: query.database })
  }
  else resetHistoryFilters()
  void load()
})
onMounted(() => {
  void load()
  clock = setInterval(() => { now.value = Date.now() }, 1000)
})
watch(() => [route.path, route.query.task_id] as const, ([path, value], previous) => {
  if (previous && path !== previous[0]) { closeDetails(); return }
  const id = Number(value)
  if (Number.isSafeInteger(id) && id > 0) void choose(id)
  else closeDetails()
}, { immediate: true })
watch(() => [user.hasPerm('database:query'), user.hasPerm('database:write'), user.hasPerm('database:audit')], (current, previous) => {
  const changed = props.audit ? current[2] !== previous[2] : current[0] !== previous[0] || current[1] !== previous[1]
  if (!changed) return
  clearRecords()
  if (props.audit) query.page = 1
  else historyFilters.page = 1
  if (props.audit ? current[2] : current[0] || current[1]) void load()
  else error.value = '当前无权读取此页面记录'
})
onBeforeUnmount(() => {
  generation++
  detailGeneration++
  history.dispose()
  clearInterval(clock)
})
</script>
<template>
  <div class="db-page records-page" :class="{'history-page':!audit,'audit-page':audit}">
    <div class="op-hero op-hero--blue">
      <div class="op-hero-icon">
        <AuditOutlined v-if="audit" />
        <HistoryOutlined v-else />
      </div>
      <div>
        <div class="op-hero-title">{{title}}</div>
        <div class="op-hero-sub">{{audit?'查看全员操作与过程事实，不包含查询结果集':'仅查看本人操作，历史与结果分别保留'}}</div>
      </div>
      <div v-if="!audit" class="history-hero-actions">
        <a-button v-if="user.hasPerm('database:query')" ghost @click="router.push('/database/workbench')">
          <DatabaseOutlined /> 打开查询工作台
        </a-button>
      </div>
    </div>
    <div v-if="audit" class="history-toolbar audit-toolbar">
      <a-input v-model:value="auditKeyword" class="history-toolbar-keyword" placeholder="操作者 / 实例关键词" aria-label="操作者 / 实例关键词" :maxlength="255" allow-clear @press-enter="submitAuditKeyword" @change="changeAuditKeyword">
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-select v-model:value="query.engine" class="history-toolbar-select" :options="engineOptions" placeholder="数据库引擎" allow-clear aria-label="数据库引擎" @change="searchAudit" />
      <a-select v-model:value="query.kind" class="history-toolbar-select" :options="kindOptions" placeholder="操作类型" allow-clear aria-label="操作类型" @change="searchAudit" />
      <a-select v-model:value="query.status" class="history-toolbar-select" :options="Object.entries(statusNames).map(([value,label])=>({value,label}))" placeholder="执行状态" allow-clear aria-label="执行状态" @change="searchAudit" />
      <a-range-picker v-model:value="auditRange" show-time value-format="YYYY-MM-DD HH:mm:ss" class="history-toolbar-range"
        popup-class-name="history-range-popup audit-range-popup" :placeholder="['提交开始时间','提交结束时间']" :presets="rangePresets" @change="searchAudit" />
      <a-button class="audit-more-toggle" :aria-expanded="auditMoreOpen" aria-controls="audit-advanced" @click="auditMoreOpen=!auditMoreOpen">更多筛选</a-button>
    </div>
    <div v-else class="history-toolbar">
      <a-input v-model:value="historyKeyword" class="history-toolbar-keyword" placeholder="搜索执行历史" aria-label="搜索执行历史" :maxlength="255" allow-clear @press-enter="submitHistoryKeyword" @change="changeHistoryKeyword">
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-select v-model:value="historyFilters.engine" class="history-toolbar-select" :options="engineOptions" placeholder="数据库引擎" allow-clear aria-label="数据库引擎" @change="searchHistory" />
      <a-select v-model:value="historyFilters.kind" class="history-toolbar-select" :options="kindOptions" placeholder="操作类型" allow-clear aria-label="操作类型" @change="searchHistory" />
      <a-select v-model:value="historyFilters.status" class="history-toolbar-select" :options="Object.entries(statusNames).map(([value,label])=>({value,label}))" placeholder="执行状态" allow-clear aria-label="执行状态" @change="searchHistory" />
      <a-range-picker v-model:value="historyRange" show-time value-format="YYYY-MM-DD HH:mm:ss" class="history-toolbar-range"
        popup-class-name="history-range-popup" :placeholder="['提交开始时间','提交结束时间']" :presets="rangePresets" @change="searchHistory" />
    </div>
    <div v-if="audit&&auditMoreOpen" id="audit-advanced" class="history-toolbar audit-advanced">
      <a-input-number v-model:value="auditDraft.instance_id" :min="1" placeholder="实例 ID" aria-label="实例 ID" @press-enter="submitAuditId('instance_id')" @change="(value:number|string|null)=>clearAuditId('instance_id',value)" />
      <a-input-number v-model:value="auditDraft.actor_id" :min="1" placeholder="操作人 ID" aria-label="操作人 ID" @press-enter="submitAuditId('actor_id')" @change="(value:number|string|null)=>clearAuditId('actor_id',value)" />
      <a-input v-model:value="auditDraft.source_ip" placeholder="来源 IP" aria-label="来源 IP" allow-clear @press-enter="submitAuditText('source_ip')" @change="(event:Event)=>clearAuditText('source_ip',event)" />
      <a-input v-model:value="auditDraft.database" placeholder="Database 上下文" aria-label="Database 上下文" allow-clear @press-enter="submitAuditText('database')" @change="(event:Event)=>clearAuditText('database',event)" />
    </div>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <template v-if="audit">
        <a-table class="history-list audit-list" :columns="auditColumns" :data-source="items" row-key="task_id" bordered :loading="loading"
          :scroll="{x:1170}"
          :pagination="{current:query.page,pageSize:query.page_size,total,showSizeChanger:true,showTotal:(count:number)=>`共 ${count} 条记录`}"
          :row-class-name="(record:db.AuditView)=>drawerOpen&&selected?.task_id===record.task_id?'history-row-selected':''"
          :custom-row="(record:db.TaskView|db.AuditView)=>({onClick:()=>choose(record.task_id),style:{cursor:'pointer'}})"
          @change="changePage">
          <template #bodyCell="{column,record}">
            <a v-if="column.key==='id'" class="history-task-id" @click.stop="choose(record.task_id)">#{{record.task_id}}</a>
            <template v-else-if="column.key==='actor'">
              <div class="history-primary" :title="record.actor_name">{{record.actor_name||'—'}}</div>
              <div class="history-secondary">{{record.source_ip||'—'}}</div>
            </template>
            <template v-else-if="column.key==='target'">
              <div class="history-primary" :title="record.instance_name">{{record.instance_name}}</div>
              <div class="history-secondary"><span class="history-engine">{{engineNames[record.engine as db.DatabaseEngine]}}</span><span :title="contextText(record)">{{contextText(record)}}</span></div>
            </template>
            <template v-else-if="column.key==='operation'">
              <div class="history-primary" :title="record.operation_summary">{{record.operation_summary}}</div>
              <div class="history-secondary">{{kindNames[record.kind]||record.kind}}</div>
            </template>
            <span v-else-if="column.key==='created'" class="history-time">{{timeText(record.created_at)}}</span>
            <a-tag v-else-if="column.key==='status'" :color="statusColor(record.status)">{{statusNames[record.status as db.TaskStatus]}}</a-tag>
            <a v-else-if="column.key==='action'" class="history-detail-link audit-detail-link" @click.stop="choose(record.task_id)">查看详情</a>
          </template>
          <template #emptyText><a-empty description="没有匹配的审计记录" /></template>
        </a-table>
      <a-drawer :open="drawerOpen" width="min(780px, 100vw)" :get-container="false" :root-style="{position:'fixed'}"
        root-class-name="history-drawer audit-drawer" destroy-on-close @close="closeDetails">
        <template #title>
          <div class="history-detail-eyebrow">审计详情<span v-if="auditDetailId"> · #{{auditDetailId}}</span></div>
          <template v-if="selected">
            <div class="history-detail-title"><span>{{selected.operation_summary}}</span><a-tag :color="statusColor(selected.status)">{{statusNames[selected.status]}}</a-tag></div>
            <div class="history-detail-target"><DatabaseOutlined /> {{selected.instance_name}}<span>/</span>{{contextText(selected)}}<span>·</span>{{engineNames[selected.engine]}}</div>
          </template>
        </template>
        <a-spin v-if="detailLoading" class="history-detail-loading audit-detail-loading" tip="正在加载审计详情" />
        <div v-else-if="auditDetailError" class="audit-detail-error">
          <a-alert :message="auditDetailError" type="error" show-icon />
          <a-button class="audit-detail-retry" @click="auditDetailId&&choose(auditDetailId)">重试</a-button>
        </div>
        <template v-else-if="selected">
          <div v-if="selected.error_code||selected.error_message" class="history-feedback">
            <a-alert :message="[selected.error_code,selected.error_message].filter(Boolean).join(' · ')" type="error" show-icon />
          </div>
          <a-tabs v-model:active-key="detailTab" destroy-inactive-tab-pane>
            <a-tab-pane key="overview" tab="概览">
              <h3 class="history-section-title">操作信息</h3>
              <a-descriptions :column="historyMetadataColumns" size="small" class="history-metadata">
                <a-descriptions-item label="操作者">{{selected.actor_name}}（{{selected.actor_id}}）</a-descriptions-item>
                <a-descriptions-item label="来源 IP">{{selected.source_ip||'—'}}</a-descriptions-item>
                <a-descriptions-item label="操作类型">{{kindNames[selected.kind]||selected.kind}}</a-descriptions-item>
                <a-descriptions-item label="数据库引擎">{{engineNames[selected.engine]}}</a-descriptions-item>
                <a-descriptions-item label="上下文" :span="historyMetadataColumns">{{contextText(selected)}}</a-descriptions-item>
                <a-descriptions-item label="提交时间">{{timeText(selected.created_at)}}</a-descriptions-item>
                <a-descriptions-item label="结束时间">{{timeText(selected.finished_at)}}</a-descriptions-item>
              </a-descriptions>
              <h3 class="history-section-title">原始提交</h3>
              <div class="history-statement"><div>{{kindNames[selected.kind]||selected.kind}}</div><pre class="db-record-text">{{selected.submitted_text||'无原始文本'}}</pre></div>
              <h3 class="history-section-title">实际执行</h3><pre class="db-record-text">{{selected.executed_statement||'无'}}</pre>
              <h3 class="history-section-title">结果摘要</h3><pre class="db-record-text">{{JSON.stringify(selected.result_summary,null,2)||'无'}}</pre>
            </a-tab-pane>
            <a-tab-pane key="events" tab="过程事件">
              <a-timeline v-if="selected.events?.length" class="history-process audit-events">
                <a-timeline-item v-for="event in selected.events" :key="event.id">
                  <b>{{event.event_type}}</b><time>{{timeText(event.created_at)}}</time>
                  <p v-if="event.error_code||event.error_message">{{[event.error_code,event.error_message].filter(Boolean).join(' · ')}}</p>
                  <template v-if="event.validation"><h4 class="history-section-title">校验事实</h4><pre class="db-record-text">{{JSON.stringify(event.validation,null,2)}}</pre></template>
                  <template v-if="event.stop_confirmation"><h4 class="history-section-title">停止确认</h4><pre class="db-record-text">{{JSON.stringify(event.stop_confirmation,null,2)}}</pre></template>
                  <template v-if="event.result_summary"><h4 class="history-section-title">结果摘要</h4><pre class="db-record-text">{{JSON.stringify(event.result_summary,null,2)}}</pre></template>
                </a-timeline-item>
              </a-timeline>
              <a-empty v-else description="暂无过程事件" />
            </a-tab-pane>
            <a-tab-pane key="snapshots" tab="安全快照">
              <h3 class="history-section-title">目标快照</h3><pre class="db-record-text">{{JSON.stringify(selected.target_snapshot,null,2)||'无'}}</pre>
              <h3 class="history-section-title">账号安全快照</h3><pre class="db-record-text">{{JSON.stringify(selected.credential_snapshot,null,2)||'无'}}</pre>
            </a-tab-pane>
          </a-tabs>
        </template>
        <template #footer><div class="history-detail-footer"><a-button @click="closeDetails">关闭</a-button></div></template>
      </a-drawer>
    </template>
    <template v-else>
        <a-table class="history-list" :columns="historyColumns" :data-source="displayItems" row-key="task_id" bordered :loading="loading"
          :scroll="{x:1305}"
          :pagination="{current:historyFilters.page,pageSize:historyFilters.page_size,total,showSizeChanger:true,showTotal:(count:number)=>`共 ${count} 条记录`}"
          :row-class-name="(record:db.TaskView)=>drawerOpen&&history.task.value?.task_id===record.task_id?'history-row-selected':''"
          :custom-row="(record:db.TaskView)=>({onClick:()=>choose(record.task_id),style:{cursor:'pointer'}})"
          @change="changePage">
          <template #bodyCell="{column,record}">
            <a v-if="column.key==='id'" class="history-task-id" @click.stop="choose(record.task_id)">#{{record.task_id}}</a>
            <template v-else-if="column.key==='target'">
              <div class="history-primary" :title="record.instance_name">{{record.instance_name}}</div>
              <div class="history-secondary"><span class="history-engine">{{engineNames[record.engine as db.DatabaseEngine]}}</span><span :title="contextText(record)">{{contextText(record)}}</span></div>
            </template>
            <template v-else-if="column.key==='operation'">
              <div class="history-primary" :title="record.operation_summary">{{record.operation_summary}}</div>
              <div class="history-secondary">{{kindNames[record.kind]||record.kind}}</div>
            </template>
            <template v-else-if="column.key==='created'"><span class="history-time">{{timeText(record.created_at)}}</span></template>
            <a-tag v-else-if="column.key==='status'" :color="statusColor(record.status)">{{statusNames[record.status as db.TaskStatus]}}</a-tag>
            <span v-else-if="column.key==='duration'" class="history-duration">{{durationText(record)}}</span>
            <template v-else-if="column.key==='result'">
              <div class="history-result-title" :class="{'history-result-expired':resultInfo(record).title==='结果已过期'}">{{resultInfo(record).title}}</div>
              <div class="history-secondary" :title="resultInfo(record).detail">{{resultInfo(record).detail}}</div>
            </template>
            <a v-else-if="column.key==='action'" class="history-detail-link" @click.stop="choose(record.task_id)">查看详情</a>
          </template>
          <template #emptyText><a-empty description="暂无匹配的本人操作记录" /></template>
        </a-table>
      <p class="history-bottom-note"><InfoCircleOutlined /> 历史与查询结果分别保留；结果过期后仍可查看保留期内的操作详情。</p>
      <a-drawer :open="drawerOpen" width="min(780px, 100vw)" :get-container="false" :root-style="{position:'fixed'}"
        root-class-name="history-drawer" destroy-on-close @close="closeDetails">
        <template #title>
          <div class="history-detail-eyebrow">操作详情<span v-if="history.task.value"> · #{{history.task.value.task_id}}</span></div>
          <template v-if="history.task.value">
            <div class="history-detail-title"><span>{{history.task.value.operation_summary}}</span><a-tag :color="statusColor(history.task.value.status)">{{statusNames[history.task.value.status]}}</a-tag></div>
            <div class="history-detail-target"><DatabaseOutlined /> {{history.task.value.instance_name}}<span>/</span>{{contextText(history.task.value)}}<span>·</span>{{engineNames[history.task.value.engine]}}</div>
          </template>
        </template>
        <a-spin v-if="detailLoading" class="history-detail-loading" tip="正在加载操作详情" />
        <a-alert v-if="history.notice.value" :message="history.notice.value" type="warning" show-icon />
        <template v-if="history.task.value">
          <div class="history-detail-stats">
            <div><span>执行耗时</span><strong>{{durationText(history.task.value)}}</strong></div>
            <div><span>结果记录数</span><strong>{{summaryNumber(history.task.value,'returned_rows')?.toLocaleString('en-US')??'—'}}</strong></div>
            <div><span>结果大小</span><strong>{{bytesText(summaryNumber(history.task.value,'returned_bytes'))}}</strong></div>
            <div><span>提交时间</span><strong class="history-stat-time">{{timeText(history.task.value.created_at)}}</strong></div>
          </div>
          <div v-if="['stopping','recovering'].includes(history.task.value.status)||history.task.value.error_message" class="history-feedback" aria-live="polite">
            <a-alert v-if="['stopping','recovering'].includes(history.task.value.status)" message="已申请停止，目标停止状态尚未确认；不会自动重新执行。" type="warning" show-icon />
            <a-alert v-if="history.task.value.error_message" :message="history.task.value.error_message" type="error" show-icon />
          </div>
          <a-tabs :active-key="detailTab" destroy-inactive-tab-pane @change="changeDetailTab">
            <a-tab-pane key="overview" tab="概览">
              <h3 class="history-section-title">执行信息</h3>
              <a-descriptions :column="historyMetadataColumns" size="small" class="history-metadata">
                <a-descriptions-item label="操作类型">{{kindNames[history.task.value.kind]||history.task.value.kind}}</a-descriptions-item>
                <a-descriptions-item label="数据库引擎">{{engineNames[history.task.value.engine]}}</a-descriptions-item>
                <a-descriptions-item label="上下文" :span="historyMetadataColumns">{{contextText(history.task.value)}}</a-descriptions-item>
                <a-descriptions-item label="开始时间">{{timeText(history.task.value.started_at)}}</a-descriptions-item>
                <a-descriptions-item label="结束时间">{{timeText(history.task.value.finished_at)}}</a-descriptions-item>
              </a-descriptions>
              <h3 class="history-section-title">提交语句</h3>
              <div class="history-statement"><div>{{kindNames[history.task.value.kind]||history.task.value.kind}}</div><pre class="db-record-text">{{history.task.value.submitted_text||'无原始文本'}}</pre></div>
              <template v-if="history.task.value.executed_statement&&history.task.value.executed_statement!==history.task.value.submitted_text">
                <h3 class="history-section-title">实际执行</h3><pre class="db-record-text">{{history.task.value.executed_statement}}</pre>
              </template>
              <h3 class="history-section-title">结果摘要</h3>
              <div class="history-result-notice"><InfoCircleOutlined /><div><strong>{{resultInfo(history.task.value).title}}</strong><span>{{resultInfo(history.task.value).detail}}</span></div>
                <a-button v-if="canReadResult" :loading="history.resultLoading.value" @click="viewResult">查看结果</a-button>
              </div>
            </a-tab-pane>
            <a-tab-pane v-if="user.hasPerm('database:query')&&history.task.value.kind!=='probe'" key="result" tab="结果">
              <ResultTable v-if="detailTab==='result'&&history.result.value&&canReadResult" :result="history.result.value" :loading="history.resultLoading.value" @page="history.page" />
              <a-spin v-else-if="history.resultLoading.value" class="history-detail-loading" tip="正在加载查询结果" />
              <a-empty v-else :description="history.notice.value||(canReadResult?'结果尚未加载':resultInfo(history.task.value).title)">
                <a-button v-if="canReadResult&&!history.result.value" @click="viewResult">查看结果</a-button>
              </a-empty>
              <details class="history-result-statement"><summary>提交语句</summary><pre class="db-record-text">{{history.task.value.submitted_text||'无原始文本'}}</pre></details>
            </a-tab-pane>
            <a-tab-pane key="process" tab="过程">
              <h3 class="history-section-title">执行过程</h3>
              <a-timeline class="history-process">
                <a-timeline-item v-for="step in processSteps" :key="step.label"><b>{{step.label}}</b><time>{{timeText(step.time)}}</time></a-timeline-item>
                <a-timeline-item v-if="!history.task.value.finished_at" color="gray"><b>{{statusNames[history.task.value.status]}}</b><p>尚无结束时间</p></a-timeline-item>
              </a-timeline>
            </a-tab-pane>
          </a-tabs>
        </template>
        <template #footer>
          <div class="history-detail-footer">
            <span v-if="history.task.value?.kind==='sql'&&user.hasPerm('database:query')"><InfoCircleOutlined /> 仅填入编辑器，不会自动执行</span>
            <a-space wrap>
              <a-button v-if="canCancel" :loading="history.cancelling.value" @click="history.cancel">申请取消</a-button>
              <a-button @click="closeDetails">关闭</a-button>
              <a-button v-if="history.task.value?.kind==='sql'&&user.hasPerm('database:query')" type="primary" @click="router.push({path:'/database/workbench',query:{task_id:history.task.value.task_id}})"><EditOutlined /> 带入工作台编辑</a-button>
            </a-space>
          </div>
        </template>
      </a-drawer>
    </template>
  </div>
</template>
<style scoped>
.records-page {
  min-width: 0;
  gap: 16px;
}
.records-page .op-hero {
  min-height: 82px;
  padding: 18px 24px;
  margin-bottom: 0;
}
.records-page .op-hero-sub { font-size: 12px; }
.audit-advanced { padding: 14px; background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; }
.audit-advanced :deep(.ant-input-number) { width: 140px; }
.audit-advanced :deep(.ant-input-affix-wrapper) { width: 200px; }
.audit-detail-error { display: grid; gap: 16px; padding-top: 24px; }
.audit-detail-retry { justify-self: start; }
.audit-events .history-section-title { margin-top: 16px; margin-bottom: 8px; }
.history-hero-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 18px;
  position: relative;
  z-index: 1;
}
.history-hero-actions :deep(.ant-btn) { font-size: 12px; background: #ffffff14; color: #fff; border-color: #ffffff4d; }
.history-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}
.history-toolbar-select { width: 140px; }
.history-toolbar-keyword { width: 220px; }
.history-toolbar-range { width: 400px; max-width: 100%; min-width: 0; }
.history-list { min-width: 0; }
.history-list :deep(.history-row-selected > td) { background: var(--primary-soft); }
.history-primary, .history-secondary { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
.history-primary { color: var(--text-1); line-height: 1.55; }
.history-secondary { display: flex; gap: 8px; color: var(--text-3); font-size: 12px; line-height: 1.6; }
.history-secondary > span { overflow: hidden; text-overflow: ellipsis; }
.history-secondary .history-engine { flex-shrink: 0; color: color-mix(in srgb, var(--primary) 40%, var(--text-2)); }
.history-task-id, .history-detail-link { color: color-mix(in srgb, var(--primary) 60%, var(--text-2)); }
.history-detail-link { white-space: nowrap; }
.history-time, .history-duration { color: var(--text-2); font-variant-numeric: tabular-nums; white-space: nowrap; }
.history-result-title { color: var(--text-2); }
.history-result-expired { color: var(--text-3); }
.history-bottom-note { display: flex; align-items: baseline; gap: 6px; color: var(--text-3); font-size: 11px; margin: 0; }
.records-page :deep(.history-drawer .ant-drawer-header) { padding: 22px 24px 18px; }
.records-page :deep(.history-drawer .ant-drawer-header-title) { align-items: flex-start; flex-direction: row-reverse; gap: 12px; }
.records-page :deep(.history-drawer .ant-drawer-close) { margin: -2px -6px 0 0; }
.records-page :deep(.history-drawer .ant-drawer-body) { padding: 0 24px 24px; overflow-x: hidden; }
.records-page :deep(.history-drawer .ant-drawer-footer) { padding: 16px 24px; }
.history-detail-eyebrow { font-size: 13px; color: var(--text-2); font-weight: 500; }
.history-detail-eyebrow span { color: var(--text-3); }
.history-detail-title { display: flex; align-items: center; gap: 14px; margin-top: 12px; }
.history-detail-title > span { font-size: 20px; line-height: 1.5; overflow-wrap: anywhere; min-width: 0; }
.history-detail-title :deep(.ant-tag) { flex-shrink: 0; font-size: 11px; font-weight: 400; }
.history-detail-target { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; color: var(--text-2); font-size: 12px; font-weight: 400; overflow-wrap: anywhere; }
.history-detail-target > span { color: var(--text-3); }
.history-detail-stats { display: grid; grid-template-columns: 1fr 1fr 1fr 1.35fr; padding: 20px 0; border-bottom: 1px solid var(--border); }
.history-detail-stats > div { display: flex; flex-direction: column; gap: 8px; padding-left: 18px; border-left: 1px solid var(--border); min-width: 0; }
.history-detail-stats > div:first-child { padding-left: 0; border: 0; }
.history-detail-stats span { color: var(--text-3); font-size: 11px; }
.history-detail-stats strong { color: var(--text-1); font-size: 20px; font-weight: 500; overflow-wrap: anywhere; }
.history-detail-stats .history-stat-time { font-size: 13px; line-height: 30px; }
.history-feedback { display: grid; gap: 10px; margin-top: 16px; }
.history-result-statement summary { cursor: pointer; }
.history-detail-loading { display: block; margin: 48px auto; }
.records-page :deep(.history-drawer .ant-tabs-nav) { margin-bottom: 20px; }
.records-page :deep(.history-drawer .ant-tabs-tab) { padding-top: 16px; font-size: 13px; }
.history-section-title { font-size: 13px; font-weight: 600; color: var(--text-1); margin: 0 0 14px; }
.history-metadata { margin-bottom: 24px; }
.history-metadata :deep(.ant-descriptions-item-label) { color: var(--text-3); font-size: 11px; }
.history-metadata :deep(.ant-descriptions-item-content) { font-size: 12px; color: var(--text-2); overflow-wrap: anywhere; }
.history-statement { overflow: hidden; border: 1px solid var(--border); border-radius: 9px; margin-bottom: 22px; }
.history-statement > div { padding: 9px 14px; font-size: 11px; background: var(--bg-hover); color: var(--text-2); border-bottom: 1px solid var(--border); }
.history-statement .db-record-text { background: color-mix(in srgb, var(--primary) 3%, var(--bg-card)); color: var(--text-2); border-radius: 0; margin: 0; padding: 15px; }
.history-result-notice { display: flex; align-items: center; gap: 12px; border: 1px solid color-mix(in srgb, var(--primary) 12%, var(--border)); background: color-mix(in srgb, var(--primary) 4%, var(--bg-card)); padding: 13px 15px; border-radius: 9px; font-size: 14px; color: var(--primary); }
.history-result-notice > div { flex: 1; min-width: 0; }
.history-result-notice strong, .history-result-notice span { display: block; }
.history-result-notice strong { color: var(--text-2); font-size: 12px; font-weight: 500; }
.history-result-notice span { font-size: 11px; color: var(--text-3); margin-top: 3px; overflow-wrap: anywhere; }
.history-result-notice :deep(.ant-btn) { font-size: 12px; flex-shrink: 0; }
.history-process { padding-top: 8px; }
.history-process :deep(.ant-timeline-item-content) { font-size: 12px; min-height: 54px; color: var(--text-2); }
.history-process b { font-weight: 500; }
.history-process time { float: right; color: var(--text-3); font-size: 11px; }
.history-process p { color: var(--text-3); margin-top: 6px; }
.history-result-statement { margin-top: 20px; color: var(--text-2); font-size: 12px; }
.history-detail-footer { display: flex; justify-content: flex-end; align-items: center; flex-wrap: wrap; gap: 12px; }
.history-detail-footer > span { margin-right: auto; color: var(--text-3); font-size: 10px; }
.history-detail-footer :deep(.ant-btn) { font-size: 12px; }
@media (max-width: 760px) {
  .records-page .op-hero { flex-wrap: wrap; padding: 16px; }
  .history-hero-actions { margin-left: 0; flex-wrap: wrap; gap: 12px; }
  .history-toolbar-select { width: calc((100% - 10px) / 2); }
  .history-toolbar-keyword { width: 100%; }
  /* 完整日期时间含秒，收紧手机分隔留白以免两个输入的末尾被裁切。 */
  .history-toolbar-range :deep(.ant-picker-input > input) { font-size: 12px; }
  .history-toolbar-range :deep(.ant-picker-range-separator) { padding-inline: 4px; }
  /* 手机弹层纵向滚动并限制半屏高度，给输入框上下翻转预留空间。 */
  :global(.history-range-popup.ant-picker-dropdown .ant-picker-panel-container) { max-width: calc(100vw - 24px); max-height: calc(50dvh - 24px); overflow: auto; }
  :global(.history-range-popup.ant-picker-dropdown .ant-picker-panel-container .ant-picker-panel-layout),
  :global(.history-range-popup.ant-picker-dropdown .ant-picker-datetime-panel) { flex-direction: column; }
  :global(.history-range-popup.ant-picker-dropdown .ant-picker-panel-container .ant-picker-presets) { max-width: none; }
  :global(.history-range-popup.ant-picker-dropdown .ant-picker-panel-container .ant-picker-presets ul) { display: flex; flex-wrap: wrap; gap: 8px; height: auto; }
  :global(.history-range-popup.ant-picker-dropdown .ant-picker-panel-container .ant-picker-presets li + li) { margin-top: 0; }
  .records-page :deep(.history-drawer .ant-drawer-header) { padding: 18px 16px; }
  .records-page :deep(.history-drawer .ant-drawer-body) { padding: 0 16px 20px; }
  .records-page :deep(.history-drawer .ant-drawer-footer) { padding: 12px 16px; }
  .audit-advanced :deep(.ant-input-number), .audit-advanced :deep(.ant-input-affix-wrapper) { width: calc((100% - 10px) / 2); }
  .history-detail-title > span { font-size: 17px; }
  .history-detail-stats { grid-template-columns: 1fr 1fr; row-gap: 18px; }
  .history-detail-stats > div:nth-child(3) { padding-left: 0; border: 0; }
  .history-detail-stats strong { font-size: 18px; }
  .history-process time { display: block; float: none; margin-top: 4px; }
}
.db-record-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-height: 320px;
  overflow: auto;
  padding: 12px;
  background: var(--bg-hover);
  border-radius: 8px;
  font: 13px/1.7 Consolas, monospace
}
</style>
