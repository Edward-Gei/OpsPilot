<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { DatabaseOutlined, FieldStringOutlined, FolderOutlined, PlayCircleOutlined, ReloadOutlined, StopOutlined, TableOutlined } from '@ant-design/icons-vue'
import * as db from '@/api/database'
import CodeEditor from '@/components/CodeEditor.vue'
import { useUserStore } from '@/stores/user'
import { activeStatuses, createTaskSession, engineNames, errorText, formatCell, statusNames, timeText, useDatabasePageScroll } from './runtime'
import ResultTable from './ResultTable.vue'
import TaskStatus from './TaskStatus.vue'
import './database.css'

useDatabasePageScroll()

const route = useRoute(),
  user = useUserStore()
const instances = ref<db.InstanceItem[]>([]),
  instanceId = ref<number>(),
  instance = ref<db.InstanceDetail | null>(null),
  policy = ref<db.ClientPolicy | null>(null)
const loading = ref(false),
  initializing = ref(true),
  error = ref(''),
  sql = ref('SELECT 1;'),
  database = ref(''),
  schema = ref(''),
  objectName = ref(''),
  dbOptions = ref<string[]>([]),
  schemaOptions = ref<string[]>([]),
  objects = ref<string[]>([])
const activeTab = ref('result'),
  objectFilter = ref(''),
  browserOpen = ref(false),
  expandedKeys = ref<string[]>([]),
  objectsLoaded = ref(false),
  objectsTruncated = ref(false)
const session = createTaskSession(db),
  metadata = createTaskSession(db),
  metadataOperation = ref<db.MetadataOperation>('databases')
// 标记发起命令，避免同一任务让多个入口一起显示加载动画。
type QueryAction = 'execute' | 'scan' | 'scan-next' | 'members-next' | 'cancel'
type MetadataAction = 'databases' | 'structure' | 'object' | 'cancel'
const queryAction = ref<QueryAction | null>(null),
  metadataAction = ref<MetadataAction | null>(null)
const mongo = reactive({
  type: 'find' as db.MongoOperation['type'],
  filter: '{}',
  projection: '',
  sort: '{}',
  limit: 100,
  field: ''
})
const redis = reactive({
  type: 'scan' as db.RedisOperation['type'],
  database: 0,
  pattern: '*',
  key: '',
  cursor: '',
  offset: 0,
  count: 100,
  length: 1024
})
let generation = 0,
  disposed = false
const busy = computed(() => session.submitting.value || !!session.task.value && activeStatuses.includes(session.task.value.status))
const canQuery = computed(() => user.hasPerm('database:query'))
const metadataBusy = computed(() => metadata.submitting.value || metadata.resultLoading.value || !!metadata.task.value && activeStatuses.includes(metadata.task.value.status))
const sqlEngine = computed(() => instance.value && ['mysql', 'postgresql', 'clickhouse'].includes(instance.value.engine))
const structureResult = computed(() => objectName.value && ['columns', 'fields'].includes(metadataOperation.value) ? metadata.result.value : null)
const queryStatus = computed(() => session.notice.value || (session.submitting.value ? '正在提交' : session.task.value ? statusNames[session.task.value.status] : '尚未执行'))
const queryEmpty = computed(() => session.resultLoading.value ? '正在读取保存结果'
  : session.notice.value || (busy.value ? '正在执行查询，请等待结果'
    : session.task.value?.error_message || (session.task.value ? '当前没有可查看的保存结果' : '执行查询后查看结果')))
const sqlSize = computed(() => new TextEncoder().encode(sql.value).length)
const metadataNames: Record<db.MetadataOperation, string> = {
  databases: '可见数据库', schemas: 'Schema', tables: '表列表', columns: '字段结构', collections: '集合列表', fields: '采样字段'
}

interface BrowserNode {
  key: string
  title: string
  kind: 'database' | 'schema' | 'group' | 'object' | 'field'
  name: string
  detail?: string
  children?: BrowserNode[]
  isLeaf?: boolean
}
const nodeKey = (kind: string, name: string) => `${kind}:${name}`
// 仅将当前元数据结果映射为节点；未读取对象不补造字段、索引或约束。
const fieldNodes = computed<BrowserNode[]>(() => {
  const data = structureResult.value
  if (!data) return []
  const nameIndex = data.columns.findIndex(column => column.name === 'name'),
    typeIndex = data.columns.findIndex(column => column.name === 'type')
  return data.rows.flatMap((row, index) => typeof row[nameIndex] === 'string' ? [{
    key: nodeKey('field', `${objectName.value}:${index}`), title: row[nameIndex] as string,
    kind: 'field' as const, name: row[nameIndex] as string,
    detail: typeIndex >= 0 ? formatCell(row[typeIndex]) : '', isLeaf: true
  }] : [])
})
const browserTree = computed<BrowserNode[]>(() => {
  const filter = objectFilter.value.trim().toLocaleLowerCase()
  const tableNodes: BrowserNode[] = objects.value.flatMap(name => {
    const fields = name === objectName.value ? fieldNodes.value : []
    const match = !filter || name.toLocaleLowerCase().includes(filter)
    const children = match ? fields : fields.filter(field => field.name.toLocaleLowerCase().includes(filter))
    return match || children.length ? [{ key: nodeKey('object', name), title: name, name,
      kind: 'object' as const, children, isLeaf: !children.length }] : []
  })
  const group: BrowserNode = { key: 'objects', title: instance.value?.engine === 'mongodb' ? '集合' : '表', name: '', kind: 'group', children: tableNodes }
  const schemas = schemaOptions.value
  return dbOptions.value.map(name => ({
    key: nodeKey('database', name), title: name, name, kind: 'database' as const,
    children: name !== database.value ? [] : instance.value?.engine === 'postgresql'
      ? schemas.map(value => ({ key: nodeKey('schema', value), title: value, name: value, kind: 'schema' as const, children: value === schema.value ? [group] : [] }))
      : [group]
  }))
})
watch([database, schema, objectName], () => {
  expandedKeys.value = [nodeKey('database', database.value), nodeKey('schema', schema.value), 'objects', nodeKey('object', objectName.value)]
})
const nextCursor = computed(() => {
  const result = session.result.value
  if (!result) return null
  const index = result.columns.findIndex(column => column.name === 'next_cursor')
  return index < 0 ? null : String(result.rows[0]?.[index] ?? '0')
})
const canCursor = computed(() => ['scan', 'hash_page', 'set_page'].includes(redis.type))

function clearResults() {
  session.clear()
  metadata.clear()
  queryAction.value = null
  metadataAction.value = null
  error.value = ''
}

function contextChanged() {
  clearResults()
  objects.value = []
  objectName.value = ''
  objectFilter.value = ''
  objectsLoaded.value = false
  objectsTruncated.value = false
  redis.cursor = ''
  redis.offset = 0
}

function databaseChanged() {
  schemaOptions.value = []
  schema.value = instance.value?.engine === 'postgresql' ? instance.value.connection_options.default_schema || 'public' : ''
  contextChanged()
}

function schemaChanged() {
  contextChanged()
}

async function loadContext(action: MetadataAction | null = null) {
  if (!database.value || !instance.value || instance.value.engine === 'redis') return
  await browse(instance.value.engine === 'postgresql' ? 'schemas' : instance.value.engine === 'mongodb' ? 'collections' : 'tables', action)
}

function selectBrowser(_keys: (string | number)[], info: { node: { dataRef?: BrowserNode } }) {
  const node = info.node.dataRef
  if (!node) return
  if (node.kind === 'object') chooseObject(node.name)
  else if (node.kind === 'database') {
    database.value = node.name
    databaseChanged()
    void loadContext()
  } else if (node.kind === 'schema') {
    schema.value = node.name
    schemaChanged()
    void browse('tables')
  }
}

function refreshStructure() {
  if (metadataBusy.value) return
  if (objectName.value) void browse(instance.value?.engine === 'mongodb' ? 'fields' : 'columns', 'structure')
  else if (database.value) void loadContext('structure')
  else void browse('databases', 'structure')
}

// 刷新库列表不抢占当前结构任务，避免改写它的操作上下文。
function refreshDatabases() {
  if (metadataBusy.value) return
  void browse('databases', 'databases')
}

async function chooseInstance(restoredContext?: Record<string, unknown>) {
  if (disposed || !canQuery.value) return
  const g = ++generation
  clearResults()
  instance.value = null
  dbOptions.value = []
  schemaOptions.value = []
  objects.value = []
  objectName.value = ''
  database.value = ''
  schema.value = ''
  objectFilter.value = ''
  objectsLoaded.value = false
  objectsTruncated.value = false
  if (!instanceId.value) return
  loading.value = true
  try {
    const data = await db.getInstance(instanceId.value)
    if (g !== generation || disposed) return
    if (!data.enabled || data.needs_configuration || !data.credential) {
      error.value = '该实例停用或待配置，暂不可查询。'
      return
    }
    instance.value = data
    // 历史上下文先于元数据任务生效，避免恢复时清空尚未完成的可见库链路。
    database.value = typeof restoredContext?.database === 'string' ? restoredContext.database : data.connection_options.default_database || ''
    schema.value = typeof restoredContext?.schema === 'string' ? restoredContext.schema : data.connection_options.default_schema || (data.engine === 'postgresql' ? 'public' : '')
    redis.database = data.connection_options.database ?? 0
    redis.cursor = ''
    redis.offset = 0
    if (data.engine !== 'redis') await browse('databases')
  } catch (e) {
    if (g === generation && !disposed) error.value = errorText(e)
  } finally {
    if (g === generation) loading.value = false
  }
}

function currentContext(): db.QueryContext {
  return {
    ...(database.value ? { database: database.value } : {}),
    ...(instance.value?.engine === 'postgresql' && schema.value ? { schema: schema.value } : {})
  }
}

async function browse(operation: db.MetadataOperation, action: MetadataAction | null = null) {
  if (!instanceId.value || !instance.value || !canQuery.value || disposed) return
  metadataAction.value = action
  metadataOperation.value = operation
  if (['tables', 'collections'].includes(operation)) {
    objects.value = []
    objectsLoaded.value = false
    objectName.value = ''
  }
  const context = currentContext()
  if (['columns', 'fields'].includes(operation)) {
    if (instance.value?.engine === 'mongodb') {
      context.collection = objectName.value
    } else {
      context.table = objectName.value
    }
  }
  await metadata.submit({ kind: 'metadata', instance_id: instanceId.value, context, operation }, true)
}

watch([metadata.result, metadata.submitting], ([result, submitting]) => {
  // 提交收尾后再串行读取下一层，避免被任务会话的防重入保护吞掉。
  if (!result || submitting) return
  const index = result.columns.findIndex(column => column.name === 'name')
  const names = result.rows.map(row => row[index < 0 ? 0 : index]).filter((value): value is string => typeof value === 'string')
  const operation = metadataOperation.value
  const target = operation === 'databases' ? dbOptions : operation === 'schemas' ? schemaOptions
    : ['tables', 'collections'].includes(operation) ? objects : null
  if (!target) return
  target.value = [...new Set([...(result.page > 1 ? target.value : []), ...names])]
  if (target === objects) {
    objectsLoaded.value = true
    objectsTruncated.value = result.truncated
  }
  // 对象列表只遍历本次已保存的结果页，不追加数据库查询或推断全库总数。
  if (result.page * result.page_size < result.saved_total) {
    void metadata.page(result.page + 1, result.page_size)
    return
  }
  const nextAction = metadataAction.value === 'structure' ? 'structure' : null
  if (operation === 'databases' && database.value) void loadContext(nextAction)
  else if (operation === 'schemas') {
    if (!schemaOptions.value.includes(schema.value)) schema.value = schemaOptions.value[0] || ''
    if (schema.value) void browse('tables', nextAction)
  }
})

function chooseObject(name: string) {
  session.clear()
  queryAction.value = null
  metadata.clear()
  objectName.value = name
  void browse(instance.value?.engine === 'mongodb' ? 'fields' : 'columns', 'object')
}

function prepareTableQuery() {
  if (!instance.value || !objectName.value) return
  const quote = instance.value.engine === 'postgresql' ? '"' : '`'
  const escaped = (value: string) => quote + value.split(quote).join(quote + quote) + quote
  sql.value = `SELECT * FROM ${instance.value.engine === 'postgresql' && schema.value ? `${escaped(schema.value)}.` : ''}${escaped(objectName.value)};`
}

function parseObject(text: string, label: string): Record<string, unknown> {
  const value = JSON.parse(text || '{}')
  if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(`${label}必须是 JSON 对象`)
  return value
}

function buildRequest(): db.TaskRequest {
  const id = instanceId.value!
  if (!instance.value) throw new Error('请选择可查询实例')
  if (sqlEngine.value) return { kind: 'sql', instance_id: id, context: currentContext(), sql: sql.value }
  if (instance.value.engine === 'mongodb') {
    if (!database.value || !objectName.value) throw new Error('请选择 Database 和 Collection')
    const filter = parseObject(mongo.filter, '筛选条件')
    let operation: db.MongoOperation
    if (mongo.type === 'count_documents') operation = { type: 'count_documents', filter }
    else if (mongo.type === 'distinct') {
      if (!mongo.field.trim()) throw new Error('请填写 distinct 字段')
      operation = { type: 'distinct', field: mongo.field, filter, limit: mongo.limit }
    }
    else {
      const sortObject = parseObject(mongo.sort, '排序')
      const sort = Object.entries(sortObject).map(([field, direction]) => {
        if (direction !== 1 && direction !== -1) throw new Error('排序方向仅允许 1 或 -1')
        return { field, direction: direction as 1 | -1 }
      })
      operation = {
        type: 'find',
        filter,
        projection: mongo.projection.trim() ? parseObject(mongo.projection, '返回字段') : null,
        sort,
        limit: mongo.limit
      }
    }
    return {
      kind: 'mongo_read',
      instance_id: id,
      context: { database: database.value, collection: objectName.value },
      operation
    }
  }
  let operation: db.RedisOperation
  if (redis.type === 'scan') {
    operation = {
      type: 'scan',
      pattern: redis.pattern,
      count: redis.count,
      ...(redis.cursor ? { cursor: redis.cursor } : {})
    }
  } else {
    if (!redis.key) throw new Error('请填写 Key')
    if (redis.type === 'string_preview') {
      operation = {
        type: 'string_preview',
        key: redis.key,
        offset: redis.offset,
        length: redis.length
      }
    } else if (['type', 'ttl', 'length'].includes(redis.type)) {
      operation = { type: redis.type as 'type' | 'ttl' | 'length', key: redis.key }
    } else if (['hash_page', 'set_page'].includes(redis.type)) {
      operation = {
        type: redis.type as 'hash_page' | 'set_page',
        key: redis.key,
        count: redis.count,
        ...(redis.cursor ? { cursor: redis.cursor } : {})
      }
    } else {
      operation = {
        type: redis.type as 'list_page' | 'zset_page',
        key: redis.key,
        offset: redis.offset,
        count: redis.count
      }
    }
  }
  return {
    kind: 'redis_read',
    instance_id: id,
    context: { database: redis.database },
    operation
  }
}

async function execute(action: QueryAction | null = 'execute') {
  if (busy.value || !canQuery.value || disposed) return
  error.value = ''
  activeTab.value = 'result'
  try {
    const request = buildRequest()
    queryAction.value = action
    await session.submit(request, true)
  } catch (e) {
    error.value = errorText(e)
  }
}

// 取消接管当前任务的加载提示；请求结果未知时继续等待任务事实。
async function cancelQuery() {
  if (session.cancelling.value || !session.task.value || !activeStatuses.includes(session.task.value.status) || ['stopping', 'recovering'].includes(session.task.value.status)) return
  queryAction.value = 'cancel'
  await session.cancel()
}

async function cancelMetadata() {
  if (metadata.cancelling.value || !metadata.task.value || !activeStatuses.includes(metadata.task.value.status) || ['stopping', 'recovering'].includes(metadata.task.value.status)) return
  metadataAction.value = 'cancel'
  await metadata.cancel()
}

function continueScan() {
  if (busy.value || !nextCursor.value || nextCursor.value === '0') return
  redis.cursor = nextCursor.value
  void execute('scan-next')
}

function pickKey(row: unknown[]) {
  const index = session.result.value?.columns.findIndex(column => column.name === 'key') ?? -1
  const key = row[index]
  if (typeof key !== 'string') return
  redis.key = key
  redis.type = 'type'
  redis.cursor = ''
  redis.offset = 0
  void execute(null)
}

function nextMembers() {
  const count = session.result.value?.saved_total || 0
  if (busy.value || !count) return
  redis.offset += count
  void execute('members-next')
}

watch(session.result, result => {
  if (!result || instance.value?.engine !== 'redis' || redis.type !== 'type') return
  const index = result.columns.findIndex(column => column.name === 'type')
  const type = result.rows[0]?.[index]
  if (typeof type !== 'string') return
  const operation: Record<string, db.RedisOperation['type']> = {
    string: 'string_preview',
    hash: 'hash_page',
    list: 'list_page',
    set: 'set_page',
    zset: 'zset_page'
  }
  if (operation[type]) redis.type = operation[type]
})

async function initialize() {
  if (!canQuery.value) {
    initializing.value = false
    return
  }
  loading.value = true
  try {
    const all: db.InstanceItem[] = []
    for (let page = 1; ; page++) {
      const data = await db.listInstances({ scope: 'query', page, page_size: 100 })
      if (disposed) return
      all.push(...data.items)
      if (!data.items.length || all.length >= data.total) break
    }
    instances.value = all.filter(item => item.enabled && !item.needs_configuration)
    const limits = await db.clientPolicy()
    if (disposed) return
    policy.value = limits
    const requested = Number(route.query.instance_id)
    if (instances.value.some(item => item.id === requested)) {
      instanceId.value = requested
      await chooseInstance()
    }
    if (route.query.task_id) {
      const original = await db.getTask(Number(route.query.task_id))
      if (disposed) return
      if (instances.value.some(item => item.id === original.instance_id)) {
        instanceId.value = original.instance_id
        await chooseInstance(original.context)
        if (disposed) return
        if (original.kind === 'sql') sql.value = original.submitted_text || ''
      }
    }
  } catch (e) {
    if (!disposed) error.value = errorText(e)
  } finally {
    if (!disposed) {
      loading.value = false
      initializing.value = false
    }
  }
}

watch(() => user.userInfo, () => {
  if (!user.hasPerm('database:query')) {
    generation++
    clearResults()
    instances.value = []
    instance.value = null
    instanceId.value = undefined
  }
})
onMounted(initialize)
onBeforeUnmount(() => {
  disposed = true
  generation++
  session.dispose()
  metadata.dispose()
})
</script>
<template>
  <div class="db-workspace">
    <header class="op-hero op-hero--blue db-workspace-heading">
      <div class="op-hero-icon"><DatabaseOutlined /></div>
      <h1 class="op-hero-title">SQL工作台</h1>
      <span v-if="instance" class="op-hero-extra" :title="'最近连接检测：'+timeText(instance.last_probe_at)">
        {{ instance.last_probe_status==='succeeded'?'最近测试通过':instance.last_probe_status==='failed'?'最近测试失败':'尚未测试连接' }}
      </span>
    </header>
    <a-alert v-if="error" :message="error" type="error" show-icon />
    <a-alert v-if="!canQuery" message="暂无数据库查询权限，请联系授权管理员" type="info" show-icon />
    <section v-else class="db-workspace-shell" aria-label="数据库查询工作区">
      <div class="db-workspace-connection">
        <label class="db-workspace-selector db-workspace-instance">
          <DatabaseOutlined /><span>实例</span>
          <a-select v-model:value="instanceId" :options="instances.map(item=>({value:item.id,label:item.name+' / '+engineNames[item.engine]}))"
            placeholder="选择可查询实例" show-search option-filter-prop="label" :loading="loading" :disabled="initializing" @change="chooseInstance()" />
        </label>
        <label v-if="instance&&instance.engine!=='redis'" class="db-workspace-selector">
          <span>数据库</span>
          <a-auto-complete v-model:value="database" :options="dbOptions.map(value=>({value}))" placeholder="Database"
            aria-label="数据库" @change="databaseChanged" @select="loadContext()" @keydown.enter="loadContext()" />
        </label>
        <label v-if="instance?.engine==='postgresql'" class="db-workspace-selector db-workspace-schema">
          <span>Schema</span>
          <a-auto-complete v-model:value="schema" :options="schemaOptions.map(value=>({value}))" placeholder="Schema"
            aria-label="Schema" @change="schemaChanged" @select="loadContext()" @keydown.enter="loadContext()" />
        </label>
        <label v-if="instance?.engine==='redis'" class="db-workspace-selector db-workspace-schema">
          <span>逻辑 DB</span>
          <a-input-number v-model:value="redis.database" :min="0" :max="15" aria-label="Redis 逻辑 DB" @change="contextChanged" />
        </label>
        <div class="db-workspace-connection-meta">
          <span v-if="instance">账号：{{instance.credential?.login_user||'仅口令'}}</span>
          <a-tag color="green">只读</a-tag>
          <span v-if="policy">执行上限 {{policy.execution_timeout_seconds}} 秒</span>
        </div>
      </div>
      <div v-if="!instance" class="db-workspace-empty">
        <a-skeleton v-if="loading" active :paragraph="{rows:5}" />
        <a-empty v-else :description="!instances.length?'暂无可查询实例，请联系授权管理员':'选择实例，开始浏览结构与只读查询'" />
      </div>
      <div v-else class="db-workspace-body">
        <aside class="db-workspace-browser" aria-label="对象浏览器">
          <div class="db-workspace-pane-title">
            <h2>{{instance.engine==='redis'?'Key 分页发现':'对象浏览器'}}</h2>
            <a-button class="db-workspace-browser-toggle" type="text" size="small" :aria-expanded="browserOpen" @click="browserOpen=!browserOpen">
              {{browserOpen?'收起':'展开'}}
            </a-button>
            <a-button v-if="instance.engine!=='redis'" type="text" size="small" title="刷新可见库" aria-label="刷新可见库"
              :loading="metadataBusy&&(metadataAction==='object'||metadataAction==='databases'&&metadataOperation==='databases')" :disabled="metadataBusy" @click="refreshDatabases"><template #icon><ReloadOutlined /></template></a-button>
          </div>
          <div class="db-workspace-browser-body" :class="{'is-collapsed':!browserOpen}">
            <template v-if="instance.engine!=='redis'">
              <a-input v-model:value="objectFilter" :placeholder="instance.engine==='mongodb'?'搜索集合或字段':'搜索表或字段'" allow-clear aria-label="搜索已加载对象" />
              <div class="db-workspace-tree">
                <a-tree v-if="browserTree.length" v-model:expandedKeys="expandedKeys" :tree-data="browserTree" block-node
                  :selected-keys="objectName?[nodeKey('object',objectName)]:[]" @select="selectBrowser">
                  <template #title="node">
                    <span class="db-workspace-tree-title" :title="node.title+(node.detail?' · '+node.detail:'')">
                      <DatabaseOutlined v-if="node.kind==='database'" />
                      <FolderOutlined v-else-if="node.kind==='schema'||node.kind==='group'" />
                      <FieldStringOutlined v-else-if="node.kind==='field'" />
                      <TableOutlined v-else />
                      <span>{{node.title}}</span><small v-if="node.detail">{{node.detail}}</small>
                    </span>
                  </template>
                </a-tree>
                <a-empty v-else :image="undefined" :description="metadataBusy?'正在读取可见数据库':'没有已加载对象，请选择数据库并刷新结构'" />
                <p v-if="objectsLoaded&&!objects.length" class="db-muted">当前上下文没有返回可见对象。</p>
              </div>
              <div class="db-workspace-browser-footer">
                <span v-if="metadataBusy">正在读取{{metadataNames[metadataOperation]}}</span>
                <span v-else>已加载 {{objects.length}} 个{{instance.engine==='mongodb'?'集合':'表'}}</span>
                <span v-if="objectsTruncated">对象列表已达到保存上限，可能还有更多。</span>
                <a v-if="metadata.notice.value||metadata.task.value?.error_message" @click="activeTab='execution'">读取有提示，查看执行信息</a>
              </div>
            </template>
            <a-form v-else layout="vertical" class="db-workspace-key-discovery">
              <a-form-item label="Key 模式"><a-input v-model:value="redis.pattern" @change="redis.cursor=''" /></a-form-item>
              <a-button block :loading="busy&&queryAction==='scan'" :disabled="busy" @click="redis.type='scan';redis.cursor='';execute('scan')">扫描 Key</a-button>
              <a-form-item label="本次游标"><a-textarea v-model:value="redis.cursor" :rows="2" :maxlength="2048" /></a-form-item>
              <a-button v-if="canCursor" block :loading="busy&&queryAction==='scan-next'" :disabled="busy||!nextCursor||nextCursor==='0'" @click="continueScan">继续扫描下一页</a-button>
              <p v-if="nextCursor==='0'" class="db-muted">扫描完成</p>
              <p class="db-muted">本页未命中可继续扫描；页数不代表 Key 总数。</p>
            </a-form>
          </div>
        </aside>
        <main class="db-workspace-main">
          <section class="db-workspace-editor">
            <div class="db-workspace-editor-toolbar">
              <h2>{{sqlEngine?'SQL 编辑器':instance.engine==='mongodb'?'MongoDB 查询':'Redis 查询'}}</h2>
              <div class="db-workspace-editor-actions">
                <a-button v-if="sqlEngine&&objectName" size="small" @click="prepareTableQuery">生成 SELECT</a-button>
                <a-button v-if="instance.engine!=='redis'" size="small" :loading="metadataBusy&&metadataAction==='structure'" :disabled="metadataBusy" @click="refreshStructure"><template #icon><ReloadOutlined /></template>刷新结构</a-button>
                <a-button v-if="metadataBusy" size="small" :loading="metadataAction==='cancel'&&(metadataBusy||metadata.cancelling.value)"
                  :disabled="!metadata.task.value||!activeStatuses.includes(metadata.task.value.status)||['stopping','recovering'].includes(metadata.task.value.status)" @click="cancelMetadata">取消结构读取</a-button>
                <a-button v-if="instance.engine==='redis'&&['list_page','zset_page'].includes(redis.type)" size="small"
                  :loading="busy&&queryAction==='members-next'" :disabled="busy||!session.result.value?.saved_total" @click="nextMembers">下一页成员</a-button>
                <a-button size="small" :disabled="!session.task.value||!activeStatuses.includes(session.task.value.status)||['stopping','recovering'].includes(session.task.value.status)"
                  :loading="queryAction==='cancel'&&(busy||session.cancelling.value)" @click="cancelQuery"><template #icon><StopOutlined /></template>取消查询</a-button>
                <a-button type="primary" size="small" :loading="busy&&queryAction==='execute'" :disabled="busy||loading" @click="execute()">
                  <template #icon><PlayCircleOutlined /></template>执行{{sqlEngine?'查询':'只读操作'}}
                </a-button>
              </div>
            </div>
            <template v-if="sqlEngine">
              <div class="db-workspace-editor-resizer">
                <CodeEditor v-model="sql" lang="sql" height="100%" placeholder="输入完整 SELECT SQL" />
              </div>
              <div class="db-workspace-editor-footnote">
                <span>{{sql.split('\n').length}} 行 · {{sqlSize}} / 65536 UTF-8 字节</span>
              </div>
            </template>
            <a-form v-else-if="instance.engine==='mongodb'" layout="vertical" class="db-workspace-native-form">
              <div class="db-workspace-fields">
                <a-form-item label="只读操作">
                  <a-select v-model:value="mongo.type" :options="[{value:'find',label:'find'},{value:'count_documents',label:'countDocuments'},{value:'distinct',label:'受限 distinct'}]" @change="session.clear" />
                </a-form-item>
                <a-form-item label="Collection">
                  <a-auto-complete v-model:value="objectName" :options="objects.map(value=>({value}))" @change="session.clear();metadata.clear()" />
                </a-form-item>
              </div>
              <a-form-item label="筛选条件（JSON）"><CodeEditor v-model="mongo.filter" lang="json" height="120px" /></a-form-item>
              <template v-if="mongo.type==='find'">
                <a-form-item label="返回字段（JSON，可空）"><CodeEditor v-model="mongo.projection" lang="json" height="90px" /></a-form-item>
                <a-form-item label="排序（JSON，方向为1或-1）"><a-input v-model:value="mongo.sort" /></a-form-item>
              </template>
              <div class="db-workspace-fields">
                <a-form-item v-if="mongo.type==='distinct'" label="distinct 字段"><a-input v-model:value="mongo.field" /></a-form-item>
                <a-form-item v-if="mongo.type!=='count_documents'" label="请求条数"><a-input-number v-model:value="mongo.limit" :min="1" :max="10000" /></a-form-item>
              </div>
            </a-form>
            <a-form v-else layout="vertical" class="db-workspace-native-form">
              <a-form-item label="受控只读操作">
                <a-select v-model:value="redis.type"
                  :options="[{value:'scan',label:'扫描 Key'},{value:'type',label:'类型'},{value:'ttl',label:'TTL'},{value:'length',label:'长度'},{value:'string_preview',label:'字符串受限预览'},{value:'hash_page',label:'Hash 分页'},{value:'list_page',label:'List 分页'},{value:'set_page',label:'Set 分页'},{value:'zset_page',label:'SortedSet 分页'}]"
                  @change="redis.cursor='';redis.offset=0;session.clear()" />
              </a-form-item>
              <a-form-item v-if="redis.type!=='scan'" label="Key"><a-input v-model:value="redis.key" :maxlength="1024" @change="redis.cursor='';redis.offset=0;session.clear()" /></a-form-item>
              <div class="db-workspace-fields">
                <a-form-item v-if="['string_preview','list_page','zset_page'].includes(redis.type)" label="起始偏移"><a-input-number v-model:value="redis.offset" :min="0" /></a-form-item>
                <a-form-item v-if="redis.type==='string_preview'" label="预览字节数"><a-input-number v-model:value="redis.length" :min="1" :max="65536" /></a-form-item>
                <a-form-item v-if="['scan','hash_page','list_page','set_page','zset_page'].includes(redis.type)" label="本次请求条数"><a-input-number v-model:value="redis.count" :min="1" :max="1000" /></a-form-item>
              </div>
              <p class="db-muted">仅提供有界读取；不开放任意命令、脚本或全量集合读取。</p>
            </a-form>
          </section>
          <section class="db-workspace-output">
            <a-tabs v-model:activeKey="activeTab" :animated="false" :destroy-inactive-tab-pane="true" class="db-workspace-tabs">
              <template #rightExtra>
                <span class="db-workspace-query-status" aria-live="polite" :title="queryStatus">
                  <span :class="['db-workspace-status-dot',{'is-success':session.task.value?.status==='succeeded'&&!session.notice.value,'is-error':session.task.value?.status==='failed'}]"></span>
                  {{queryStatus}}<span v-if="session.task.value"> · 任务 #{{session.task.value.task_id}}</span>
                </span>
              </template>
              <a-tab-pane key="result" tab="查询结果">
                <div class="db-workspace-result">
                  <p class="db-workspace-mobile-status" aria-live="polite">{{queryStatus}}<span v-if="session.task.value"> · 任务 #{{session.task.value.task_id}}</span></p>
                  <a-alert v-if="session.notice.value||session.task.value?.error_message" :message="session.notice.value||session.task.value?.error_message" :type="session.task.value?.status==='failed'?'error':'warning'" show-icon />
                  <ResultTable v-if="session.result.value" :result="session.result.value" :loading="session.resultLoading.value"
                    :select-key="instance.engine==='redis'&&redis.type==='scan'" @page="session.page" @pick="pickKey" />
                  <a-empty v-else :description="queryEmpty" />
                </div>
              </a-tab-pane>
              <a-tab-pane key="structure" tab="表结构">
                <div class="db-workspace-structure">
                  <template v-if="instance.engine!=='redis'">
                    <div v-if="objectName" class="db-workspace-structure-heading"><b>{{objectName}}</b></div>
                    <a-alert v-if="metadata.notice.value" :message="metadata.notice.value" type="warning" show-icon />
                    <ResultTable v-if="structureResult" :result="structureResult" :loading="metadata.resultLoading.value" @page="metadata.page" />
                    <a-empty v-else :description="!objectName?'在对象浏览器选择表或集合，查看字段结构':metadataBusy?'正在读取字段结构':metadata.notice.value||metadata.task.value?.error_message||'没有可查看的字段结果，请刷新结构'" />
                  </template>
                  <a-empty v-else description="Redis 不提供表结构，请通过 Key 类型、TTL 和受控读取查看数据" />
                </div>
              </a-tab-pane>
              <a-tab-pane key="execution" tab="执行信息">
                <div class="db-workspace-execution">
                  <section><h3>查询任务</h3><TaskStatus :task="session.task.value" :notice="session.notice.value" :show-stop-evidence="false" />
                    <p v-if="!session.task.value&&!session.notice.value" class="db-muted">尚未提交查询任务。</p>
                  </section>
                  <section v-if="instance.engine!=='redis'"><h3>对象浏览 · {{metadataNames[metadataOperation]}}</h3><TaskStatus :task="metadata.task.value" :notice="metadata.notice.value" :show-stop-evidence="false" />
                    <p v-if="!metadata.task.value&&!metadata.notice.value" class="db-muted">选择数据库或刷新结构后查看元数据任务。</p>
                  </section>
                </div>
              </a-tab-pane>
            </a-tabs>
            <div v-if="policy" class="db-workspace-output-footer">每次最多 {{policy.max_result_rows}} 条 / {{(policy.max_result_bytes/1048576).toLocaleString(undefined,{maximumFractionDigits:2})}} MiB · 完成后保留 {{policy.result_ttl_minutes}} 分钟</div>
          </section>
        </main>
      </div>
    </section>
  </div>
</template>
<style scoped>
.db-workspace { display:flex; flex-direction:column; gap:16px; min-width:0; height:calc(100vh - 112px); min-height:0; overflow:hidden; }
.db-workspace-heading { margin-bottom:0; flex-shrink:0; }
.db-workspace-heading h1 { margin:0; }
.db-workspace-heading .op-hero-extra { font-size:12px; }
.db-workspace-shell { display:flex; flex:1; flex-direction:column; background:var(--bg-card, var(--bg-elevated,#fff)); border:1px solid var(--border); border-radius:8px; overflow:hidden; min-width:0; min-height:0; }
.db-workspace-connection { display:flex; flex-shrink:0; align-items:center; flex-wrap:wrap; gap:10px 14px; padding:12px 16px; border-bottom:1px solid var(--border); }
.db-workspace-selector { display:flex; align-items:center; gap:8px; min-width:0; flex:0 1 235px; padding:3px 8px; border:1px solid var(--border); border-radius:6px; color:var(--text-3); font-size:12px; }
.db-workspace-selector>span { flex-shrink:0; }
.db-workspace-selector :deep(.ant-select) { flex:1; min-width:0; }
.db-workspace-selector :deep(.ant-select-selector) { border:0!important; box-shadow:none!important; padding:0!important; background:transparent!important; }
.db-workspace-selector :deep(.ant-select-selection-search) { inset-inline-start:0!important; }
.db-workspace-selector:focus-within { border-color:var(--primary); box-shadow:0 0 0 2px var(--primary-soft); }
.db-workspace-instance { flex-basis:370px; }
.db-workspace-schema { flex-basis:180px; }
.db-workspace-schema :deep(.ant-input-number) { flex:1; width:80px; }
.db-workspace-connection-meta { display:flex; align-items:center; flex-wrap:wrap; gap:8px; margin-left:auto; color:var(--text-3); font-size:12px; }
.db-workspace-connection-meta :deep(.ant-tag) { margin:0; }
.db-workspace-empty { flex:1; min-height:0; display:grid; align-content:center; padding:32px; overflow:auto; }
.db-workspace-body { display:grid; flex:1; grid-template-columns:264px minmax(0,1fr); grid-template-rows:minmax(0,1fr); min-height:0; overflow:hidden; }
.db-workspace-browser { display:flex; flex-direction:column; min-width:0; min-height:0; overflow:hidden; border-right:1px solid var(--border); }
.db-workspace-pane-title,.db-workspace-editor-toolbar { min-height:48px; flex-shrink:0; display:flex; align-items:center; gap:8px; padding:8px 16px; border-bottom:1px solid var(--border); }
.db-workspace-pane-title h2,.db-workspace-editor-toolbar h2 { flex-shrink:0; margin:0; color:var(--text-1); font-size:13px; line-height:24px; font-weight:600; }
.db-workspace-pane-title>button:last-child { margin-left:auto; }
.db-workspace-browser-toggle { display:none; }
.db-workspace-browser-body { display:flex; flex:1; flex-direction:column; gap:14px; min-height:0; padding:12px; overflow:auto; }
.db-workspace-browser-body>:not(.db-workspace-tree) { flex-shrink:0; }
.db-workspace-tree { flex:1; min-height:0; overflow:auto; }
.db-workspace-tree :deep(.ant-tree) { font-size:12px; background:transparent; }
.db-workspace-tree :deep(.ant-tree-indent-unit) { width:14px; }
.db-workspace-tree :deep(.ant-tree-switcher) { width:16px; }
.db-workspace-tree :deep(.ant-tree-node-content-wrapper) { min-width:0; padding:3px 4px; }
.db-workspace-tree-title { display:flex; align-items:center; gap:7px; min-width:0; }
.db-workspace-tree-title>span:nth-child(2) { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.db-workspace-tree-title>.anticon { color:var(--text-3); flex-shrink:0; }
.db-workspace-tree-title small { margin-left:auto; color:var(--text-3); font-size:11px; white-space:nowrap; }
.db-workspace-browser-footer { display:flex; flex-direction:column; gap:6px; padding-top:12px; border-top:1px solid var(--border); font-size:11px; line-height:1.6; color:var(--text-3); }
.db-workspace-key-discovery :deep(.ant-form-item) { margin-bottom:14px; }
.db-workspace-key-discovery>button { margin-bottom:16px; }
.db-workspace-main { min-width:0; min-height:0; display:flex; flex-direction:column; overflow:hidden; }
.db-workspace-editor { flex:none; max-height:calc(100% - 150px); overflow:auto; border-bottom:1px solid var(--border); min-width:0; }
.db-workspace-editor-resizer { height:260px; min-height:120px; max-height:min(520px,calc(100vh - 480px)); resize:vertical; overflow:hidden; }
.db-workspace-editor-resizer :deep(.code-editor) { height:100%; border:0; border-radius:0; }
.db-workspace-editor-resizer :deep(.cm-editor) { height:100%; }
.db-workspace-editor-resizer :deep(.cm-scroller) { overflow:auto; }
.db-workspace-editor-toolbar { flex-wrap:wrap; }
.db-workspace-editor-actions { display:flex; align-items:center; justify-content:flex-end; flex-wrap:wrap; gap:6px; margin-left:auto; }
.db-workspace-editor-actions .db-muted { margin-right:6px; }
.db-workspace-editor :deep(.cm-editor) { border:0; border-radius:0; }
.db-workspace-editor :deep(.cm-gutters) { border-right:1px solid var(--border); background:var(--bg-hover); color:var(--text-3); }
.db-workspace-editor-footnote { display:flex; flex-wrap:wrap; justify-content:space-between; gap:4px 12px; padding:6px 14px; border-top:1px solid var(--border); color:var(--text-3); background:var(--bg-hover); font-size:11px; line-height:1.6; }
.db-workspace-native-form { padding:16px 20px 4px; }
.db-workspace-native-form :deep(.ant-form-item) { margin-bottom:14px; }
.db-workspace-fields { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.db-workspace-output { display:flex; flex-direction:column; flex:1; min-width:0; min-height:0; overflow:hidden; }
.db-workspace-tabs { flex:1; min-width:0; min-height:0; }
.db-workspace-tabs :deep(.ant-tabs-nav) { flex-shrink:0; margin:0; padding:0 16px; }
.db-workspace-tabs :deep(.ant-tabs-content-holder) { min-height:0; overflow:hidden; }
.db-workspace-tabs :deep(.ant-tabs-content),.db-workspace-tabs :deep(.ant-tabs-tabpane) { height:100%; }
.db-workspace-tabs :deep(.ant-tabs-tabpane) { overflow:hidden; }
.db-workspace-tabs :deep(.ant-tabs-tab) { padding:12px 0; font-size:13px; }
.db-workspace-tabs :deep(.ant-tabs-tab+.ant-tabs-tab) { margin-left:24px; }
.db-workspace-tabs :deep(.ant-tabs-extra-content) { min-width:0; max-width:50%; }
.db-workspace-query-status { display:flex; align-items:center; gap:6px; color:var(--text-3); font-size:11px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.db-workspace-status-dot { width:6px; height:6px; background:var(--text-3); border-radius:50%; flex-shrink:0; }
.db-workspace-status-dot.is-success { background:var(--success,#22a06b); }
.db-workspace-status-dot.is-error { background:var(--error,#e34d59); }
.db-workspace-result,.db-workspace-structure,.db-workspace-execution { box-sizing:border-box; height:100%; padding:12px 16px; min-height:0; min-width:0; overflow:auto; }
.db-workspace-result>.ant-empty,.db-workspace-structure>.ant-empty { margin:48px 0; }
.db-workspace-result>.ant-alert,.db-workspace-structure>.ant-alert { margin-bottom:12px; }
.db-workspace-mobile-status { display:none; }
.db-workspace-structure-heading { display:flex; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:12px; }
.db-workspace-execution h3 { font-size:13px; margin:0 0 10px; }
.db-workspace-execution>section+section { margin-top:20px; padding-top:16px; border-top:1px solid var(--border); }
.db-workspace-output-footer { flex-shrink:0; padding:9px 16px; border-top:1px solid var(--border); color:var(--text-3); font-size:11px; line-height:1.6; }
@media(max-width:1100px) {
  .db-workspace-body { grid-template-columns:225px minmax(0,1fr); }
  .db-workspace-connection-meta { margin-left:0; }
  .db-workspace-tabs :deep(.ant-tabs-extra-content) { max-width:36%; }
}
@media(max-width:800px) {
  .db-workspace { height:auto; min-height:0; overflow:visible; }
  .db-workspace-shell { flex:none; }
  .db-workspace-body { display:flex; flex:none; flex-direction:column; min-height:0; overflow:visible; }
  .db-workspace-connection { padding:12px; gap:8px; }
  .db-workspace-selector { flex:1 1 190px; }
  .db-workspace-instance { flex-basis:100%; }
  .db-workspace-browser { border-right:0; border-bottom:1px solid var(--border); }
  .db-workspace-browser-toggle { display:inline-flex; margin-left:auto; }
  .db-workspace-pane-title>button:last-child { margin-left:0; }
  .db-workspace-browser-body.is-collapsed { display:none; }
  .db-workspace-browser-body { max-height:260px; overflow:auto; }
  .db-workspace-tree { min-height:80px; max-height:180px; flex-shrink:0; }
  .db-workspace-main { overflow:visible; }
  .db-workspace-editor { max-height:none; overflow:visible; }
  .db-workspace-editor-resizer { max-height:520px; }
  .db-workspace-editor-toolbar { padding:10px 12px; }
  .db-workspace-editor-actions { justify-content:flex-start; margin-left:0; width:100%; }
  .db-workspace-editor-actions :deep(.ant-btn) { min-height:34px; }
  .db-workspace-tabs :deep(.ant-tabs-extra-content) { display:none; }
  .db-workspace-mobile-status { display:block; margin:0 0 12px; color:var(--text-3); font-size:12px; overflow-wrap:anywhere; }
  .db-workspace-fields { grid-template-columns:1fr; gap:0; }
  .db-workspace-native-form { padding:14px 12px 0; }
  .db-workspace-output { flex:none; overflow:visible; }
  .db-workspace-tabs :deep(.ant-tabs-content-holder),.db-workspace-tabs :deep(.ant-tabs-tabpane) { overflow:visible; }
  .db-workspace-tabs :deep(.ant-tabs-content),.db-workspace-tabs :deep(.ant-tabs-tabpane) { height:auto; }
  .db-workspace-result,.db-workspace-structure,.db-workspace-execution { height:auto; min-height:240px; padding:12px; overflow:visible; }
}
</style>
