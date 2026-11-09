<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { isAxiosError } from 'axios'
import * as db from '@/api/database'
import * as cmdb from '@/api/cmdb'
import { useUserStore } from '@/stores/user'
import { engineOptions, errorText } from './runtime'
import './database.css'

const props = defineProps<{ instanceId?: number | null; initialStep?: number }>()
const emit = defineEmits<{ close: []; saved: [id: number]; changed: [] }>()
const user = useUserStore(),
  id = computed(() => props.instanceId || null)
const canWrite = computed(() => user.hasPerm('database:write')),
  canGrant = computed(() => (Boolean(id.value) || canWrite.value) && user.hasPerm('database:grant')),
  canRelate = computed(() => canWrite.value && user.hasPerm('cmdb:read'))
const firstStep = () => id.value && !canWrite.value && canGrant.value ? 1 : props.initialStep === 1 ? 1 : 0
const step = ref(firstStep()),
  loading = ref(false),
  saving = ref(false),
  ready = ref(false),
  error = ref(''),
  formRef = ref(),
  persistedId = ref<number | null>(null),
  instanceSaved = ref(false),
  creationUnknown = ref(false),
  finished = ref(false)
const emptyForm = (): db.InstanceWrite => ({
  name: '',
  description: null,
  engine: 'mysql',
  environment: 'prod',
  enabled: true,
  host: '',
  port: 3306,
  credential_id: null,
  business_line: null,
  cluster_name: null,
  cluster_role: null,
  version: null,
  connection_options: { tls: false, tls_verify: true, default_database: '' },
  app_ids: [],
  host_ids: []
})
const form = reactive<db.InstanceWrite>(emptyForm())
const credentials = ref<db.CredentialSummary[]>([]),
  apps = ref<{
    label: string
    value: number
  }[]>([]),
  hosts = ref<{
    label: string
    value: number
  }[]>([])
const assignedRoles = ref<db.Relation[]>([]),
  availableRoles = ref<db.Relation[]>([]),
  selectedRoles = ref<number[]>([]),
  roleLoading = ref(false),
  roleReady = ref(false),
  roleError = ref('')
const roleOptions = computed(() => [
  ...availableRoles.value,
  ...assignedRoles.value.filter(role => !availableRoles.value.some(option => option.id === role.id))
].map(role => ({ label: role.name, value: role.id })))
let generation = 0,
  credentialGeneration = 0,
  roleGeneration = 0,
  initialPayload = ''

/** 比较与提交使用同一份规范化数据；无关系维护权限时始终省略关系字段。 */
function buildPayload(): db.InstanceWrite {
  const payload = {
    ...form,
    credential_id: form.credential_id || null,
    connection_options: Object.fromEntries(Object.entries(form.connection_options)
      .filter(([, value]) => value !== '' && value !== undefined)
      .sort(([left], [right]) => left.localeCompare(right))) as unknown as db.ConnectionOptions
  }
  if (canRelate.value) {
    payload.app_ids = [...(form.app_ids || [])].sort((left, right) => left - right)
    payload.host_ids = [...(form.host_ids || [])].sort((left, right) => left - right)
  } else {
    delete payload.app_ids
    delete payload.host_ids
  }
  for (const key of ['cluster_name', 'cluster_role', 'version', 'description'] as const) payload[key] = payload[key]?.trim() || null
  return payload
}

async function loadCredentials() {
  if (!canWrite.value) return
  const g = ++credentialGeneration
  try {
    const data = await db.compatibleCredentials(form.engine)
    if (g === credentialGeneration) credentials.value = data.items
  } catch (e) {
    if (g === credentialGeneration) error.value = errorText(e)
  }
}

function changeEngine() {
  if (!canWrite.value) return
  form.port = { mysql: 3306, postgresql: 5432, mongodb: 27017, clickhouse: 8123, redis: 6379 }[form.engine]
  form.credential_id = null
  form.connection_options = form.engine === 'redis' ? { tls: false, tls_verify: true, database: 0, auth_mode: 'password_only' } : {
    tls: false,
    tls_verify: true,
    default_database: '',
    ...(form.engine === 'mongodb' ? { auth_database: 'admin' } : {})
  }
  void loadCredentials()
}

/** 保留成功取得的角色摘要；当前授权与候选均成功后，才允许提交授权。 */
async function loadRoles() {
  if (!canGrant.value || saving.value) return
  const g = generation, r = ++roleGeneration, targetId = persistedId.value || id.value
  roleLoading.value = true
  roleReady.value = false
  roleError.value = ''
  const [assigned, available] = await Promise.allSettled([
    targetId ? db.getInstanceRoles(targetId) : Promise.resolve({ items: [] as db.Relation[] }), db.roleOptions()
  ])
  if (g !== generation || r !== roleGeneration) return
  if (assigned.status === 'fulfilled') {
    assignedRoles.value = assigned.value.items
    selectedRoles.value = assigned.value.items.map(role => role.id)
  }
  if (available.status === 'fulfilled') availableRoles.value = available.value.items
  roleReady.value = assigned.status === 'fulfilled' && available.status === 'fulfilled'
  roleError.value = [assigned, available]
    .filter(result => result.status === 'rejected')
    .map(result => errorText(result.reason)).join('；')
  roleLoading.value = false
}

function sameRoles(left: number[], right: number[]) {
  return left.length === right.length && left.every(value => right.includes(value))
}

async function loadRelations(g: number) {
  if (!canRelate.value) return
  const appRows: typeof apps.value = [],
    hostRows: typeof hosts.value = []
  for (let page = 1; ; page++) {
    const data = await cmdb.listApps({ page, page_size: 100 })
    if (g !== generation) return
    appRows.push(...data.items.map(item => ({ label: item.name, value: item.id })))
    if (!data.items.length || appRows.length >= data.total) break
  }
  for (let page = 1; ; page++) {
    const data = await cmdb.listHosts({ page, page_size: 100 })
    if (g !== generation) return
    hostRows.push(...data.items.map(item => ({ label: `${item.hostname} (${item.ip})`, value: item.id })))
    if (!data.items.length || hostRows.length >= data.total) break
  }
  apps.value = appRows
  hosts.value = hostRows
}

async function load() {
  const g = ++generation
  loading.value = true
  ready.value = false
  saving.value = false
  persistedId.value = null
  instanceSaved.value = false
  creationUnknown.value = false
  finished.value = false
  initialPayload = ''
  error.value = ''
  roleGeneration++
  roleLoading.value = false
  roleReady.value = false
  roleError.value = ''
  assignedRoles.value = []
  availableRoles.value = []
  selectedRoles.value = []
  Object.assign(form, emptyForm())
  apps.value = []
  hosts.value = []
  credentials.value = []
  try {
    if (id.value) {
      const data = await db.getInstance(id.value)
      if (g !== generation) return
      for (const key of [
        'name',
        'description',
        'engine',
        'environment',
        'enabled',
        'host',
        'port',
        'credential_id',
        'business_line',
        'cluster_name',
        'cluster_role',
        'version',
      ] as const) Object.assign(form, { [key]: data[key] })
      // 独立编辑副本，取消或关闭弹窗不会修改已加载的连接与关系数据。
      form.connection_options = { ...data.connection_options }
      form.app_ids = [...data.app_ids]
      form.host_ids = [...data.host_ids]
      apps.value = data.apps.map(item => ({ label: item.name, value: item.id }))
      hosts.value = data.hosts.map(item => ({ label: item.hostname, value: item.id }))
      credentials.value = data.credential ? [data.credential] : []
    }
    initialPayload = JSON.stringify(buildPayload())
    if (canGrant.value) void loadRoles()
    await Promise.all([loadCredentials(), loadRelations(g)])
    if (g === generation) {
      ready.value = true
    }
  } catch (e) {
    if (g === generation) error.value = errorText(e)
  } finally {
    if (g === generation) loading.value = false
  }
}

/** 草稿可先登记；启用校验直接读取当前值，不依赖表单规则的下一次渲染。 */
function connectionReady() {
  if (!form.enabled) return true
  if (!form.credential_id) {
    message.warning('启用实例前请选择兼容的只读凭据')
    return false
  }
  if (form.engine === 'postgresql' && !form.connection_options.default_database?.trim()) {
    message.warning('PostgreSQL 连接 Database 必填')
    return false
  }
  return true
}

async function next() {
  if (!ready.value || loading.value || saving.value) return
  if (instanceSaved.value || creationUnknown.value) {
    step.value = 1
    return
  }
  if (!canWrite.value) {
    if (canGrant.value) step.value = 1
    return
  }
  try {
    await formRef.value?.validate()
    if (!connectionReady()) return
    step.value = 1
  } catch {
  }
}

/** 一个保存入口顺序提交；部分成功后只重试授权，保留新建 ID 并校验角色基线。 */
async function save() {
  if ((!canWrite.value && !canGrant.value) || saving.value || finished.value || creationUnknown.value || !ready.value || loading.value
    || (canGrant.value && (!roleReady.value || roleLoading.value))) return
  saving.value = true
  error.value = ''
  roleError.value = ''
  const g = generation, roleIds = [...selectedRoles.value]
  let target = persistedId.value || id.value, savingRoles = false
  try {
    const payload = buildPayload()
    if (canWrite.value && !instanceSaved.value && (!target || JSON.stringify(payload) !== initialPayload)) {
      try {
        await formRef.value?.validate()
      } catch {
        step.value = 0
        return
      }
      if (g !== generation) return
      if (!connectionReady()) {
        step.value = 0
        return
      }
      if (target) await db.updateInstance(target, payload)
      else {
        try {
          target = (await db.createInstance(payload)).id
        } catch (e) {
          // 已发出但未收到响应时不能判定创建失败，暂停本弹窗重试并刷新台账供人工核对。
          if (g === generation && isAxiosError(e) && !e.response && e.request) {
            creationUnknown.value = true
            error.value = `创建结果未知，已暂停再次创建。请关闭弹窗，在台账中按实例名称“${payload.name}”核对；如已存在，请编辑该实例继续配置查询授权。`
            emit('changed')
            return
          }
          throw e
        }
      }
      if (g !== generation) return
      persistedId.value = target
      instanceSaved.value = true
    }
    if (!target) return
    if (canGrant.value && !sameRoles(roleIds, assignedRoles.value.map(role => role.id))) {
      savingRoles = true
      const current = (await db.getInstanceRoles(target)).items.map(role => role.id)
      if (g !== generation) return
      // 请求结果不明时先确认是否已生效；他人已修改的集合必须重新加载确认。
      if (!sameRoles(current, roleIds)) {
        if (!sameRoles(current, assignedRoles.value.map(role => role.id))) {
          roleReady.value = false
          throw new Error('查询角色已被其他管理员修改，请重新加载后确认。')
        }
        await db.updateInstanceRoles(target, roleIds)
      }
    }
    if (g !== generation) return
    finished.value = true
    emit('saved', target)
    message.success('保存成功')
  } catch (e) {
    if (g === generation) {
      if (savingRoles) {
        roleError.value = errorText(e)
        if (instanceSaved.value) emit('changed')
      } else error.value = errorText(e)
    }
  } finally {
    if (g === generation) saving.value = false
  }
}

watch(id, () => {
  step.value = firstStep()
  void load()
})
onMounted(load)
onBeforeUnmount(() => {
  generation++
  credentialGeneration++
  roleGeneration++
})
</script>
<template>
  <a-modal :open="true" centered :title="id||persistedId?'编辑数据库实例':'新建数据库实例'" :width="960"
    :mask-closable="!saving" :closable="!saving" @cancel="!saving && emit('close')">
    <div class="instance-editor-body">
      <a-steps :current="step" size="small" :items="[{title:'登记信息与连接'},{title:'业务关联与授权'}]" />
      <a-alert v-if="error" :message="error" type="error" show-icon />
      <a-alert v-if="!form.credential_id" type="info" show-icon message="未绑定只读凭据，可保存为停用草稿；绑定凭据后才能启用、测试或查询。" />
      <a-alert v-if="instanceSaved&&!finished" type="info" show-icon message="实例已保存，查询授权尚未保存；请确认后重试。" />
      <a-spin :spinning="loading">
        <a-form ref="formRef" :model="form" layout="vertical" :disabled="!canWrite||saving||instanceSaved||creationUnknown">
          <div v-show="step===0" class="instance-fields">
            <a-form-item label="实例名称" name="name" :rules="[{required:true,whitespace:true,message:'请填写实例名称'}]">
              <a-input v-model:value="form.name" :maxlength="128" />
            </a-form-item>
            <a-form-item label="数据库引擎" name="engine" required>
              <a-select v-model:value="form.engine" :options="engineOptions" @change="changeEngine" />
            </a-form-item>
            <a-form-item label="业务线">
              <a-select v-model:value="form.business_line" :options="[{value:'mitrade'},{value:'tradingkey'}]" allow-clear />
            </a-form-item>
            <a-form-item label="环境" required>
              <a-select v-model:value="form.environment" :options="[{value:'demo',label:'演示'},{value:'stage',label:'预发'},{value:'prod',label:'生产'}]" />
            </a-form-item>
            <a-form-item label="集群名称（可选）">
              <a-input v-model:value="form.cluster_name" :maxlength="128" />
            </a-form-item>
            <a-form-item label="集群角色（可选）">
              <a-input v-model:value="form.cluster_role" :maxlength="64" />
            </a-form-item>
            <a-form-item label="登记版本（可选）">
              <a-input v-model:value="form.version" :maxlength="128" />
            </a-form-item>
            <a-form-item label="只读凭据引用" name="credential_id" :rules="[{required:form.enabled,type:'number',min:1,message:'启用实例前请选择兼容的只读凭据'}]">
              <a-select v-model:value="form.credential_id" :options="credentials.map(item=>({value:item.id,label:item.name+'（'+(item.login_user||'仅口令')+'）'}))"
                show-search option-filter-prop="label" allow-clear />
            </a-form-item>
            <a-form-item label="统一连接地址" name="host" :rules="[{required:true,whitespace:true,message:'填写主机名或IP，不包含URI或密码'}]">
              <a-input v-model:value="form.host" :maxlength="255" />
            </a-form-item>
            <a-form-item label="端口" name="port" :rules="[{required:true,type:'number',min:1,max:65535}]">
              <a-input-number v-model:value="form.port" :min="1" :max="65535" style="width:100%" />
            </a-form-item>
            <template v-if="form.engine!=='redis'">
              <a-form-item :class="{'instance-wide':form.engine==='mysql'||form.engine==='clickhouse'}"
                :label="form.engine==='postgresql'?'连接 Database（启用时必填）':'默认 Database'" :required="form.enabled&&form.engine==='postgresql'">
                <a-input v-model:value="form.connection_options.default_database" />
              </a-form-item>
              <a-form-item v-if="form.engine==='postgresql'" label="默认 Schema">
                <a-input v-model:value="form.connection_options.default_schema" />
              </a-form-item>
              <a-form-item v-if="form.engine==='mongodb'" label="认证 Database">
                <a-input v-model:value="form.connection_options.auth_database" />
              </a-form-item>
            </template>
            <template v-else>
              <a-form-item label="逻辑 DB（由目标能力决定）">
                <a-input-number v-model:value="form.connection_options.database" :min="0" :max="15" style="width:100%" />
              </a-form-item>
              <a-form-item label="认证方式">
                <a-select v-model:value="form.connection_options.auth_mode" :options="[{value:'password_only',label:'仅口令'},{value:'acl',label:'ACL 账号'}]" />
              </a-form-item>
            </template>
            <div class="instance-switches instance-wide">
              <a-form-item label="登记状态">
                <a-switch v-model:checked="form.enabled" checked-children="启用" un-checked-children="停用" />
              </a-form-item>
              <a-form-item label="TLS">
                <a-switch v-model:checked="form.connection_options.tls" checked-children="开启" un-checked-children="关闭" />
              </a-form-item>
              <a-form-item v-if="form.connection_options.tls" label="验证服务器证书及主机名">
                <a-switch v-model:checked="form.connection_options.tls_verify" />
              </a-form-item>
            </div>
            <a-form-item label="描述" class="instance-wide">
              <a-textarea v-model:value="form.description" :maxlength="255" :rows="3" />
            </a-form-item>
          </div>
          <div v-if="step===1" class="instance-relations">
            <a-form-item label="关联应用">
              <a-select v-model:value="form.app_ids" mode="multiple" :options="apps" show-search
                option-filter-prop="label" :disabled="!canRelate" allow-clear />
            </a-form-item>
            <a-form-item label="承载主机">
              <a-select v-model:value="form.host_ids" mode="multiple" :options="hosts" show-search
                option-filter-prop="label" :disabled="!canRelate" allow-clear />
            </a-form-item>
            <a-alert v-if="!canRelate" type="info" message="关系仅只读回显；保存不覆盖已有关系。" />
          </div>
        </a-form>
        <div v-if="step===1&&canGrant" class="instance-role-settings">
          <a-alert v-if="roleError" :message="roleError" type="error" show-icon>
            <template #action>
              <a-button v-if="!roleReady" size="small" :loading="roleLoading" @click="loadRoles">重试</a-button>
            </template>
          </a-alert>
          <a-form layout="vertical">
            <a-form-item label="查询角色">
              <a-select v-model:value="selectedRoles" mode="multiple" show-search option-filter-prop="label"
                :options="roleOptions" :loading="roleLoading" :disabled="!roleReady||roleLoading||saving||creationUnknown"
                placeholder="选择查询角色" allow-clear />
            </a-form-item>
          </a-form>
        </div>
      </a-spin>
    </div>
    <template #footer>
      <a-button :disabled="saving" @click="emit('close')">取消</a-button>
      <a-button v-if="step===1" :disabled="saving" @click="step=0">上一步</a-button>
      <a-button v-if="step===0" type="primary" :disabled="loading||!ready||saving" @click="next">下一步：业务关联与授权</a-button>
      <a-button v-if="step===1&&(canWrite||canGrant)" type="primary" :loading="saving"
        :disabled="loading||!ready||finished||creationUnknown||(canGrant&&(!roleReady||roleLoading))" @click="save">保存</a-button>
    </template>
  </a-modal>
</template>

<style scoped>
.instance-editor-body {
  max-height: 70vh;
  overflow: auto;
}
.instance-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 24px;
  margin-top: 24px;
}
.instance-editor-body .ant-alert {
  margin-top: 16px;
}
.instance-switches {
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 24px;
}
.instance-wide {
  grid-column: 1 / -1;
}
.instance-relations {
  margin-top: 24px;
}
.instance-role-settings {
  margin-top: 24px;
}
@media (max-width: 680px) {
  .instance-fields {
    grid-template-columns: 1fr;
  }
}
</style>
