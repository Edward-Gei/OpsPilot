<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { isAxiosError } from 'axios'
import { DatabaseOutlined, SearchOutlined, PlusOutlined, DeleteOutlined, EyeOutlined, EditOutlined, ApiOutlined, CodeOutlined, DownloadOutlined, UploadOutlined, CheckCircleOutlined, ExclamationCircleOutlined, LoadingOutlined } from '@ant-design/icons-vue'
import * as db from '@/api/database'
import { useUserStore } from '@/stores/user'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { activeStatuses, deleteSelectedInstances, engineNames, engineOptions, errorCode, errorText, statusNames, useDatabasePageScroll, type DeleteOutcome } from './runtime'
import InstanceDetail from './InstanceDetail.vue'
import InstanceEditor from './InstanceEditor.vue'
import './database.css'

useDatabasePageScroll()

const user = useUserStore(),
  route = useRoute(),
  router = useRouter()
const canWrite = computed(() => user.hasPerm('database:write')),
  canImport = computed(() => canWrite.value && user.hasPerm('database:import')),
  canExport = computed(() => user.hasPerm('database:read')),
  canDelete = computed(() => user.hasPerm('database:delete')),
  canEdit = computed(() => canWrite.value || user.hasPerm('database:grant'))
const management = computed(() => ['database:read', 'database:write', 'database:delete', 'database:grant'].some(perm => user.hasPerm(perm)))
const items = ref<db.InstanceItem[]>([]),
  total = ref(0),
  loading = ref(false),
  error = ref('')
const selected = ref<number[]>([]),
  deleting = ref(false),
  outcomes = ref<DeleteOutcome[]>([]),
  feedback = ref(false),
  deleteIds = ref<number[]>([]),
  deleteStarted = ref(false)
const deleteStates = {
  deleted: { text: '已删除', color: 'success' },
  rejected: { text: '拒绝', color: 'error' },
  unknown: { text: '请求结果未知', color: 'warning' },
  active: { text: '正在删除', color: 'processing' },
  pending: { text: '等待处理', color: 'default' }
}
const deleteRows = computed(() => deleteIds.value.map((id, index) => {
  const outcome = outcomes.value[index]
  return {
    id,
    state: deleteStates[outcome?.status || (deleting.value && index === outcomes.value.length ? 'active' : 'pending')],
    reason: outcome && outcome.status !== 'deleted' ? outcome.message : ''
  }
}))
const deleteCounts = computed(() => ({
  deleted: outcomes.value.filter(item => item.status === 'deleted').length,
  rejected: outcomes.value.filter(item => item.status === 'rejected').length,
  unknown: outcomes.value.filter(item => item.status === 'unknown').length
}))
const clusterNames = ref<string[]>([]),
  detailId = ref<number | null>(null),
  editorOpen = ref(false),
  editorId = ref<number | null>(null),
  editorStep = ref(0)
const query = reactive({
  page: 1,
  page_size: 20,
  keyword: '',
  engine: undefined as db.DatabaseEngine | undefined,
  business_line: undefined as string | undefined,
  enabled: undefined as boolean | undefined,
  cluster_name: undefined as string | undefined,
  sort_by: undefined as db.InstanceQuery['sort_by'],
  sort_order: undefined as db.InstanceQuery['sort_order']
})
const keyword = ref(''),
  exact = reactive({
    instance_id: undefined as number | undefined,
    app_id: undefined as number | undefined,
    host_id: undefined as number | undefined
  })
const importVisible = ref(false), importLoading = ref(false), importFile = ref<File | null>(null),
  importUpsert = ref(false), importResult = ref<db.InstanceImportResult | null>(null),
  exporting = ref(false), templateLoading = ref(false)
/** 每行独立保留受理任务和轮询上下文，列表刷新或其他行操作不会替换它。 */
interface ProbeRun {
  name: string
  controller: AbortController
  taskId?: number
  timer?: ReturnType<typeof setTimeout>
  readFailureTimer?: ReturnType<typeof setTimeout>
  warned: boolean
}
const probeRuns = new Map<number, ProbeRun>(),
  probingIds = reactive(new Set<number>()),
  blockedProbes = reactive(new Set<number>())
let generation = 0, disposed = false
const actionWidth = computed(() => 100 + (canEdit.value ? 76 : 0) + (canWrite.value ? 76 : 0) + (user.hasPerm('database:query') ? 76 : 0))
const dataColumns = ref(makeResizable([
  {
    title: '实例名称',
    dataIndex: 'name',
    key: 'name',
    width: 170,
    ellipsis: true
  },
  {
    title: '描述',
    dataIndex: 'description',
    key: 'description',
    width: 200,
    ellipsis: true
  },
  {
    title: '引擎',
    dataIndex: 'engine',
    key: 'engine',
    width: 125
  },
  {
    title: '业务线',
    dataIndex: 'business_line',
    key: 'business_line',
    width: 120
  },
  {
    title: '集群名称',
    dataIndex: 'cluster_name',
    key: 'cluster_name',
    width: 150,
    ellipsis: true
  },
  {
    title: '集群角色',
    dataIndex: 'cluster_role',
    key: 'cluster_role',
    width: 110
  },
  {
    title: '版本',
    dataIndex: 'version',
    key: 'version',
    width: 100
  },
  {
    title: '连接地址',
    dataIndex: 'address',
    key: 'address',
    width: 230,
    ellipsis: true
  },
  {
    title: '关联应用',
    dataIndex: 'apps',
    key: 'apps',
    width: 160
  },
  { title: '承载主机', dataIndex: 'hosts', key: 'hosts', width: 170 },
  {
    title: '状态 / 检测',
    dataIndex: 'status',
    key: 'status',
    width: 145
  },
  { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 190 },
].map(column => ({ ...column, sorter: true }))))
const columns = computed(() => [
  ...dataColumns.value,
  {
    title: '操作',
    key: 'action',
    width: actionWidth.value,
    fixed: 'right' as const
  },
])
const scrollWidth = computed(() => columns.value.reduce((width, column) => width + column.width, canDelete.value ? 48 : 0))

function connectionAddress(host: string, port: number) {
  return `${host.includes(':') && !host.startsWith('[') ? `[${host}]` : host}:${port}`
}

function hasExactFilter() {
  return Boolean(exact.instance_id || exact.app_id || exact.host_id)
}

async function load() {
  const g = ++generation
  loading.value = true
  error.value = ''
  selected.value = []
  const target = { ...exact }
  try {
    const data = await db.listInstances({
      scope: management.value ? 'management' : 'query',
      ...query,
      keyword: hasExactFilter() ? undefined : query.keyword || undefined,
      ...target
    })
    if (g === generation) {
      items.value = data.items
      total.value = data.total
      clusterNames.value = data.cluster_names || []
      // 关联名称仅作回显，查询仍使用精确 ID；过期响应不能覆盖用户输入。
      if (target.instance_id) keyword.value = data.items.find(item => item.id === target.instance_id)?.name || ''
    }
  }
  catch (e) {
    if (g === generation) error.value = errorText(e)
  }
  finally {
    if (g === generation) loading.value = false
  }
}

function search() {
  query.keyword = hasExactFilter() ? '' : keyword.value
  query.page = 1
  void load()
}

function inputChanged(event: Event) {
  if (hasExactFilter()) {
    exact.instance_id = exact.app_id = exact.host_id = undefined
    void router.replace({ path: '/database/instances' })
    search()
  } else if (event.type === 'click') {
    search()
  }
}

function openImport() {
  if (!canImport.value || importLoading.value) return
  importFile.value = null
  importUpsert.value = false
  importResult.value = null
  importVisible.value = true
}

/** 上传前仅保存文件；真实解析与逐行校验由服务端统一执行。 */
function beforeImportUpload(file: File) {
  if (!canImport.value || importLoading.value) return false
  if (!file.name.toLowerCase().endsWith('.xlsx') || file.size > 10 * 1024 * 1024) {
    importFile.value = null
    importResult.value = null
    message.warning('请选择不超过 10 MiB 的 xlsx 文件')
    return false
  }
  importFile.value = file
  importResult.value = null
  return false
}

async function downloadTemplate() {
  if (!canImport.value || templateLoading.value) return
  templateLoading.value = true
  try { await db.downloadInstanceImportTemplate() }
  catch { /* 下载错误由 HTTP 拦截器统一提示。 */ }
  finally { templateLoading.value = false }
}

async function submitImport() {
  if (!canImport.value || importLoading.value || importResult.value) return
  if (!importFile.value) {
    message.warning('请选择 xlsx 文件')
    return
  }
  importLoading.value = true
  try {
    const result = await db.importInstances(importFile.value, importUpsert.value)
    if (disposed) return
    importResult.value = result
    message[result.failed_rows.length ? 'warning' : 'success'](`成功 ${result.success_count} 条，失败 ${result.failed_rows.length} 条`)
    void load()
  } catch { /* 失败后保留文件和选择，错误由 HTTP 拦截器统一提示。 */ }
  finally { importLoading.value = false }
}

/** 导出与台账使用同一组已提交筛选；分页和输入中的草稿关键词不参与导出。 */
async function exportCurrent() {
  if (!canExport.value || exporting.value) return
  exporting.value = true
  const { page: _page, page_size: _pageSize, ...filters } = query
  try {
    await db.exportInstances({ scope: 'management', ...filters, ...exact,
      keyword: hasExactFilter() ? undefined : query.keyword || undefined })
  } catch { /* 下载错误由 HTTP 拦截器统一提示。 */ }
  finally { exporting.value = false }
}

function selectRows(keys: (string | number)[]) {
  if (keys.length > 100) {
    message.warning('一次最多选择 100 个实例')
    return
  }
  selected.value = keys.map(Number)
}

/** 排序由服务端先处理再分页；改变排序时回到首页。 */
function changePage(p: {
  current?: number
  pageSize?: number
}, _filters: unknown, sorter: { field?: string; columnKey?: string; order?: string } | { field?: string; columnKey?: string; order?: string }[] = {}) {
  const current = Array.isArray(sorter) ? sorter[0] : sorter
  const sortBy = current?.order ? (current.field || current.columnKey) as db.InstanceQuery['sort_by'] : undefined
  const sortOrder = current?.order === 'ascend' ? 'asc' : current?.order === 'descend' ? 'desc' : undefined
  const sortChanged = query.sort_by !== sortBy || query.sort_order !== sortOrder
  query.sort_by = sortBy
  query.sort_order = sortOrder
  query.page = sortChanged ? 1 : p.current || 1
  query.page_size = p.pageSize || 20
  void load()
}

function deleteSelected() {
  if (!canDelete.value || !selected.value.length || feedback.value || deleting.value) return
  deleteIds.value = [...new Set(selected.value)]
  outcomes.value = []
  deleteStarted.value = false
  feedback.value = true
}

/** 确认、逐项进度与结果共用同一弹窗；处理中锁定关闭和重复提交。 */
async function confirmDelete() {
  if (!feedback.value || deleteStarted.value || deleting.value || !canDelete.value) return
  deleteStarted.value = true
  deleting.value = true
  try {
    await deleteSelectedInstances(deleteIds.value, db.deleteInstance, item => outcomes.value.push(item))
    await load()
  }
  finally {
    deleting.value = false
  }
}

function closeDelete() {
  if (!deleting.value) feedback.value = false
}

function probeActive(id: number, run: ProbeRun) {
  return !disposed && probeRuns.get(id) === run
}

function finishProbe(id: number, run: ProbeRun, level: 'success' | 'error' | 'warning', text: string, blocked = false) {
  if (!probeActive(id, run)) return
  clearTimeout(run.timer)
  clearTimeout(run.readFailureTimer)
  run.controller.abort()
  probeRuns.delete(id)
  probingIds.delete(id)
  if (blocked) blockedProbes.add(id)
  const history = run.taskId ? `请到本人历史核对任务 #${run.taskId}。` : '请到本人历史核对。'
  message[level](`${run.name}：${text}${level === 'success' ? '' : `；${history}`}`)
  void load()
}

/** 受理后仅轮询状态；临时读取失败重试 GET，不重复提交执行任务。 */
async function readProbe(id: number, run: ProbeRun) {
  if (!probeActive(id, run) || !run.taskId) return
  try {
    const task = await db.getTask(run.taskId, run.controller.signal, { silentBusinessError: true })
    if (!probeActive(id, run)) return
    if (task.task_id !== run.taskId || task.instance_id !== id || task.kind !== 'probe') {
      finishProbe(id, run, 'warning', '连接测试状态无法确认，未再次提交', true)
      return
    }
    clearTimeout(run.readFailureTimer)
    run.readFailureTimer = undefined
    // 恢复核对仍由服务端继续；当前页结束等待并锁定重投，不将其视为失败终态。
    if (task.status === 'recovering') {
      finishProbe(id, run, 'warning', '连接测试状态待确认，未再次提交', true)
      return
    }
    if (activeStatuses.includes(task.status)) {
      run.timer = setTimeout(() => void readProbe(id, run), 1500)
      return
    }
    const value = task.result_summary?.message,
      summary = typeof value === 'string' ? value : ''
    if (task.status === 'succeeded') {
      finishProbe(id, run, 'success', `数据库连接测试通过${summary ? `：${summary}` : ''}`)
    } else {
      const detail = task.error_message || summary
      finishProbe(id, run, 'error', `数据库连接测试${statusNames[task.status] || '未通过'}${detail ? `：${detail}` : ''}`)
    }
  } catch (e) {
    if (!probeActive(id, run)) return
    const status = isAxiosError(e) ? e.response?.status : undefined
    if ([40101, 40102, 40301, 40302, 40401].includes(errorCode(e) || 0) || [401, 403, 404].includes(status || 0)) {
      finishProbe(id, run, 'warning', `连接测试任务状态暂无法读取：${errorText(e)}`, true)
      return
    }
    if (!run.warned) {
      message.warning(`${run.name}：连接测试状态暂无法读取，正在继续核对，请到本人历史查看任务 #${run.taskId}。`)
      run.warned = true
    }
    // 只约束持续读取失败；读到有效状态后重新允许正常轮询，不取消服务端任务。
    run.readFailureTimer ??= setTimeout(() => {
      finishProbe(id, run, 'warning', '连接测试状态未知，持续读取失败，当前页已停止等待', true)
    }, 10000)
    run.timer = setTimeout(() => void readProbe(id, run), 3000)
  }
}

/** 提交即锁定本行；提交结果未知时保留核对提示，禁止当前页面再次投递。 */
async function testInstance(id: number) {
  if (!canWrite.value || disposed || probeRuns.has(id) || blockedProbes.has(id) || items.value.find(item => item.id === id)?.needs_configuration) return
  const run: ProbeRun = {
    name: items.value.find(item => item.id === id)?.name || `实例 #${id}`,
    controller: new AbortController(), warned: false
  }
  probeRuns.set(id, run)
  probingIds.add(id)
  try {
    const accepted = await db.submitTask({ kind: 'probe', instance_id: id }, run.controller.signal, { silentBusinessError: true })
    if (!probeActive(id, run)) return
    if (!Number.isSafeInteger(accepted.task_id) || accepted.task_id <= 0) {
      finishProbe(id, run, 'warning', '连接测试提交结果未知，未自动重试；核对后请刷新页面再测试', true)
      return
    }
    run.taskId = accepted.task_id
    await readProbe(id, run)
  } catch (e) {
    if (!probeActive(id, run)) return
    const rejectedId = (e as { data?: { task_id?: number } })?.data?.task_id,
      status = isAxiosError(e) ? e.response?.status : undefined
    if (rejectedId && Number.isSafeInteger(rejectedId) && rejectedId > 0) {
      run.taskId = rejectedId
      await readProbe(id, run)
    } else if (typeof errorCode(e) === 'number' || (status && status >= 400 && status < 500 && status !== 408)) {
      finishProbe(id, run, 'error', `数据库连接测试未执行：${errorText(e)}`)
    } else {
      finishProbe(id, run, 'warning', '连接测试提交结果未知，未自动重试；核对后请刷新页面再测试', true)
    }
  }
}

function openDetail(id: number) {
  editorOpen.value = false
  detailId.value = id
}

function followDetail(event: MouseEvent, id: number) {
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  openDetail(id)
}

function openEditor(id: number | null = null, step = 0) {
  if (!canEdit.value || (!id && !canWrite.value)) return
  detailId.value = null
  editorId.value = id
  editorStep.value = id && !canWrite.value ? 1 : step
  editorOpen.value = true
}

function closePanel() {
  detailId.value = null
  editorOpen.value = false
  // 旧直达地址在台账上承载；仅查询用户关闭后回工作台，保持原准入范围。
  if (route.path !== '/database/instances') {
    void router.replace(management.value
      ? { path: '/database/instances', query: route.query }
      : { path: '/database/workbench' })
  }
}

function saved() {
  closePanel()
  void load()
}

function applyPanelRoute() {
  const id = Number(route.params.id)
  if (route.path === '/database/instances/new') openEditor()
  else if (Number.isSafeInteger(id) && id > 0) {
    if (route.path.endsWith('/edit')) openEditor(id, Number(route.query.step) === 2 ? 1 : 0)
    else openDetail(id)
  } else {
    detailId.value = null
    editorOpen.value = false
  }
}

function applyRoute() {
  const hadExactFilter = hasExactFilter()
  for (const key of ['instance_id', 'app_id', 'host_id'] as const) {
    const value = Number(route.query[key])
    exact[key] = Number.isSafeInteger(value) && value > 0 ? value : undefined
  }
  // 主动改词时已解除 ID，随后清理路由不能抹掉新的普通搜索词。
  if (hasExactFilter() || hadExactFilter) {
    keyword.value = ''
    query.keyword = ''
  }
  query.page = 1
  void load()
}

watch(() => route.query, applyRoute)
watch(() => [route.path, route.query.step], applyPanelRoute)
onMounted(() => {
  applyRoute()
  applyPanelRoute()
})
onBeforeUnmount(() => {
  generation++
  disposed = true
  for (const run of probeRuns.values()) {
    clearTimeout(run.timer)
    clearTimeout(run.readFailureTimer)
    run.controller.abort()
  }
  probeRuns.clear()
})
</script>
<template>
  <div>
    <div class="op-hero op-hero--blue">
      <div class="op-hero-icon">
        <DatabaseOutlined />
      </div>
      <div>
        <div class="op-hero-title">数据库实例</div>
        <div class="op-hero-sub">维护实例登记信息、业务关系与独立查询授权</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat">
          <b>{{ total }}</b>
          <span>当前筛选实例</span>
        </div>
      </div>
    </div>
    <div class="instance-toolbar">
      <a-input v-model:value="keyword" style="width:220px" placeholder="搜索实例信息" allow-clear
        @press-enter="search" @change="inputChanged">
        <template #prefix>
          <SearchOutlined />
        </template>
      </a-input>
      <a-select v-model:value="query.engine" :options="engineOptions" placeholder="引擎" style="width:135px"
        allow-clear @change="query.page = 1; load()" />
      <a-select v-model:value="query.business_line" :options="[{value:'mitrade'},{value:'tradingkey'}]"
        placeholder="业务线" style="width:135px" allow-clear @change="query.page = 1; load()" />
      <a-select v-model:value="query.enabled" :options="[{value:true,label:'启用'},{value:false,label:'停用'}]"
        placeholder="登记状态" style="width:120px" allow-clear @change="query.page = 1; load()" />
      <a-select v-model:value="query.cluster_name" show-search option-filter-prop="label"
        :options="clusterNames.map(name=>({value:name,label:name}))"
        placeholder="集群名称" style="width:155px" allow-clear @change="query.page = 1; load()" />
      <div class="instance-actions">
        <a-button v-if="canDelete" danger :disabled="!selected.length || deleting" :loading="deleting"
          @click="deleteSelected">
          <template #icon><DeleteOutlined /></template>批量删除（{{ selected.length }}）
        </a-button>
        <a-button v-if="canExport" :loading="exporting" @click="exportCurrent">
          <template #icon><DownloadOutlined /></template>导出
        </a-button>
        <a-button v-if="canImport" @click="openImport"><UploadOutlined />导入</a-button>
        <a-button v-if="canWrite" type="primary" @click="openEditor()">
          <PlusOutlined />新建实例
        </a-button>
      </div>
    </div>
    <a-alert v-if="error" :message="error" type="error" show-icon>
      <template #action>
        <a-button size="small" @click="load">重试</a-button>
      </template>
    </a-alert>
    <a-table bordered :columns="columns" :data-source="items" row-key="id" :loading="loading" :show-sorter-tooltip="false"
      :scroll="{x:scrollWidth}"
      :row-selection="canDelete ? {fixed:true,selectedRowKeys:selected,onChange:selectRows,columnWidth:48} : undefined"
      :pagination="{current:query.page,pageSize:query.page_size,total,showSizeChanger:true,showTotal:(n:number)=>`共 ${n} 条`}"
      @change="changePage" @resize-column="onResizeColumn">
      <template #bodyCell="{column,record}">
        <a v-if="column.key === 'name'" :href="`/database/instances/${record.id}`"
          @click="followDetail($event,record.id)">{{record.name}}</a>
        <template v-else-if="column.key === 'engine'">{{engineNames[record.engine as db.DatabaseEngine]}}
        </template>
        <template v-else-if="column.key === 'address'">{{connectionAddress(record.host,record.port)}}</template>
        <template v-else-if="column.key === 'apps'">
          <div v-for="app in record.apps" :key="app.id">
            <a v-if="user.hasPerm('cmdb:read')" :href="`/cmdb/apps?id=${app.id}&related=database`">
              {{app.name}}</a>
            <span v-else>{{app.name}}</span>
          </div>
          <span v-if="!record.apps.length">—</span>
        </template>
        <template v-else-if="column.key === 'hosts'">
          <div v-for="host in record.hosts" :key="host.id">
            <a v-if="user.hasPerm('cmdb:read')" :href="`/cmdb/hosts?id=${host.id}&related=database`">
              {{host.hostname}}</a>
            <span v-else>{{host.hostname}}</span>
          </div>
          <span v-if="!record.hosts.length">未关联主机</span>
        </template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="record.enabled?'success':'default'">{{record.enabled?'启用':'停用'}}</a-tag>
          <a-tag v-if="record.needs_configuration" color="warning">待配置</a-tag>
          <span class="status-separator">/</span>
          <a-tag :color="record.last_probe_status==='succeeded'?'success':record.last_probe_status==='failed'?'error':'default'"
            :title="record.last_probe_message||''">{{record.last_probe_status==='succeeded'?'成功':record.last_probe_status==='failed'?'失败':'未检测'}}</a-tag>
        </template>
        <template v-else-if="column.key === 'created_at'">
          {{record.created_at ? new Date(record.created_at).toLocaleString() : '—'}}
        </template>
        <a-space v-else-if="column.key === 'action'">
          <a-button size="small" class="op-btn-cyan" @click="openDetail(record.id)"><EyeOutlined />详情</a-button>
          <a-button v-if="canEdit" size="small" class="op-btn-blue" @click="openEditor(record.id)"><EditOutlined />编辑</a-button>
          <a-button v-if="canWrite" size="small" class="op-btn-orange" :loading="probingIds.has(record.id)" :disabled="record.needs_configuration||blockedProbes.has(record.id)"
            :title="record.needs_configuration?'请先编辑实例并绑定只读凭据。':blockedProbes.has(record.id)?'连接测试结果尚待确认，请到本人历史核对后刷新页面。':undefined"
            @click="testInstance(record.id)"><template #icon><ApiOutlined /></template>测试</a-button>
          <a-button v-if="user.hasPerm('database:query') && record.enabled && !record.needs_configuration" size="small" class="op-btn-green"
            @click="router.push({path:'/database/workbench',query:{instance_id:record.id}})"><CodeOutlined />查询</a-button>
        </a-space>
        <template v-else>{{record[column.dataIndex] || '—'}}</template>
      </template>
      <template #emptyText>
        <a-empty :description="query.keyword || hasExactFilter() ? '未找到匹配实例' : '暂无数据库实例'" />
      </template>
    </a-table>
    <InstanceDetail v-if="detailId" :key="detailId" :instance-id="detailId" @close="closePanel" />
    <InstanceEditor v-if="editorOpen" :key="editorId ?? 'new'" :instance-id="editorId" :initial-step="editorStep"
      @close="closePanel" @saved="saved" @changed="load" />
    <a-modal v-if="canImport" v-model:open="importVisible" wrap-class-name="instance-import-modal" title="Excel 导入数据库实例"
      :confirm-loading="importLoading" :closable="!importLoading" :mask-closable="!importLoading" :keyboard="!importLoading"
      :cancel-button-props="{disabled:importLoading}" :ok-button-props="{disabled:!!importResult}" ok-text="开始导入" @ok="submitImport">
      <a-alert type="info" show-icon class="import-tip">
        <template #message>请先<a :aria-disabled="templateLoading" @click="downloadTemplate">下载导入模板</a>，按模板填写后上传（单次上限 5000 行、10 MiB）。</template>
        <template #description>仅导入：实例名称、引擎、连接地址、端口、环境、描述、业务线、集群名称、集群角色、版本。新增实例为停用草稿，绑定只读凭据后可启用。</template>
      </a-alert>
      <a-upload :before-upload="beforeImportUpload" :max-count="1" accept=".xlsx" :show-upload-list="false" :disabled="importLoading">
        <a-button :disabled="importLoading"><UploadOutlined />选择 xlsx 文件</a-button>
      </a-upload>
      <div v-if="importFile" class="import-file">已选择：{{importFile.name}}</div>
      <a-checkbox v-model:checked="importUpsert" class="import-upsert" :disabled="importLoading||!!importResult">
        实例名称已存在时更新（不勾选则报“实例名称已存在”）
      </a-checkbox>
      <template v-if="importResult">
        <a-divider class="import-divider" />
        <div class="import-summary">成功 {{importResult.success_count}} 条（新增 {{importResult.created_count}} 条，更新 {{importResult.updated_count}} 条），失败 {{importResult.failed_rows.length}} 条</div>
        <a-table v-if="importResult.failed_rows.length" bordered size="small" row-key="row" :pagination="false" :scroll="{y:200}"
          :columns="[{title:'行号',dataIndex:'row',width:70},{title:'失败原因',dataIndex:'reason'}]" :data-source="importResult.failed_rows" />
      </template>
    </a-modal>
    <a-modal :open="feedback" wrap-class-name="instance-delete-modal" :width="480" centered
      :title="deleteStarted ? '批量删除结果' : `删除已勾选的 ${deleteIds.length} 个实例？`"
      :closable="!deleting" :mask-closable="!deleting" :keyboard="!deleting" @cancel="closeDelete">
      <p v-if="!deleteStarted" class="delete-confirm-copy">仅删除平台台账；仅停用且无活动或恢复任务的实例可删除。</p>
      <div v-else class="delete-result">
        <div class="delete-result-summary" role="status" aria-live="polite">
          <div class="delete-result-heading">
            <LoadingOutlined v-if="deleting" class="delete-summary-active" />
            <ExclamationCircleOutlined v-else-if="deleteCounts.rejected || deleteCounts.unknown" class="delete-summary-warning" />
            <CheckCircleOutlined v-else class="delete-summary-success" />
            <span>{{deleting ? '正在删除' : deleteCounts.rejected || deleteCounts.unknown ? '处理完成' : '删除完成'}}</span>
          </div>
          <span class="delete-result-progress">{{outcomes.length}} / {{deleteIds.length}}</span>
        </div>
        <div v-if="deleteIds.length > 1 && outcomes.length" class="delete-result-counts">
          <span>已删除 {{deleteCounts.deleted}}</span>
          <span v-if="deleteCounts.rejected">拒绝 {{deleteCounts.rejected}}</span>
          <span v-if="deleteCounts.unknown">结果未知 {{deleteCounts.unknown}}</span>
        </div>
        <ul class="delete-result-list" aria-label="逐项删除结果">
          <li v-for="item in deleteRows" :key="item.id" class="delete-result-row" :data-instance-id="item.id">
            <span class="delete-result-id">实例 <b>#{{item.id}}</b></span>
            <a-tag :color="item.state.color" class="delete-result-status">{{item.state.text}}</a-tag>
            <p v-if="item.reason" class="delete-result-reason">{{item.reason}}</p>
          </li>
        </ul>
      </div>
      <template #footer>
        <a-button v-if="!deleteStarted" @click="closeDelete">取消</a-button>
        <a-button v-if="!deleteStarted || deleting" type="primary" danger :loading="deleting" @click="confirmDelete">{{deleting ? '正在删除' : '确认删除'}}</a-button>
        <a-button v-else type="primary" @click="closeDelete">关闭</a-button>
      </template>
    </a-modal>
  </div>
</template>

<style scoped>
.instance-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 16px;
}
.instance-actions {
  margin-left: auto;
  display: flex;
  gap: 10px;
}
.status-separator {
  margin-right: 6px;
  color: var(--text-2);
}
:global(.instance-delete-modal .ant-modal-header) {
  margin-bottom: 16px;
  padding-right: 28px;
}
:global(.instance-delete-modal .ant-modal-footer) {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
}
.delete-confirm-copy {
  margin: 0;
  color: var(--text-2);
  line-height: 1.7;
}
.delete-result-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.delete-result-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 500;
  color: var(--text-1);
}
.delete-summary-active { color: var(--primary); }
.delete-summary-warning { color: var(--warning); }
.delete-summary-success { color: var(--success); }
.delete-result-progress {
  color: var(--text-3);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.delete-result-counts {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 6px;
  padding-left: 22px;
  font-size: 12px;
  color: var(--text-2);
}
.delete-result-list {
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
  max-height: min(340px, 45dvh);
  overflow-y: auto;
  overscroll-behavior: contain;
}
.delete-result-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 6px 12px;
  padding: 12px 0;
  border-top: 1px solid var(--border);
}
.delete-result-row:last-child { padding-bottom: 0; }
.delete-result-id {
  min-width: 0;
  font-size: 13px;
  color: var(--text-2);
  overflow-wrap: anywhere;
}
.delete-result-id b { color: var(--text-1); font-weight: 500; font-variant-numeric: tabular-nums; }
.delete-result-status { margin: 0; font-size: 12px; }
.delete-result-reason {
  grid-column: 1 / -1;
  min-width: 0;
  margin: 0;
  color: var(--text-2);
  font-size: 12px;
  line-height: 1.65;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.import-tip { margin-bottom: 14px; }
.import-file { margin-top: 8px; color: var(--text-2); overflow-wrap: anywhere; }
.import-upsert { display: flex; margin-top: 12px; }
.import-divider { margin: 14px 0 10px; }
.import-summary { margin-bottom: 8px; }
</style>
