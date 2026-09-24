<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import * as api from '@/api/applicationConfig'
import ConfigDiscoverPanel from './ConfigDiscoverPanel.vue'
import ConfigContentEditor from './ConfigContentEditor.vue'

const props = defineProps<{ open: boolean; importTask: api.ConfigTask | null }>()
const emit = defineEmits<{ 'update:open': [value: boolean]; created: [result: { fileId?: number; task?: api.ConfigTask }] }>()
const instances = ref<api.PlatformInstance[]>([])
const roles = ref<api.NamedOption[]>([])
const applications = ref<api.NamedOption[]>([])
const instanceId = ref<number | null>(null)
const mode = ref<'manual' | 'discover'>('manual')
const busy = ref(false)
const task = ref<api.ConfigTask | null>(null)
const selected = ref<api.DiscoveredResource[]>([])
const selectionDrafts = new Map<string, api.ConfigFileSelection>()
const manual = reactive<api.ConfigFileSelection>({
  name: '', description: '', locator: {}, content_format: 'properties', approval_role_id: 0,
  application_ids: [], initial_content: '',
})
const discovered = ref<api.ConfigFileSelection[]>([])
const nacosNamespaces = ref<api.NacosNamespace[]>([])
const nacosGroups = ref<string[]>([])
const namespaceInput = ref('')
const namespaceLoading = ref(false)
const groupLoading = ref(false)
const namespaceError = ref(false)
const groupError = ref(false)
let namespaceRequestSequence = 0
let groupRequestSequence = 0
let loadedGroupScope: string | null = null
const instance = computed(() => instances.value.find((item) => item.id === instanceId.value))
const provider = computed(() => instance.value?.provider || null)
const formats = computed(() => provider.value === 'consul'
  ? [{ label: 'Consul KV', value: 'consul_kv' }]
  : (provider.value === 'apollo' ? ['properties', 'yaml', 'json'] : ['properties', 'yaml', 'json', 'text'])
    .map((value) => ({ label: value.toUpperCase(), value })))
const locatorFields = computed(() => provider.value === 'apollo'
  ? [{ key: 'app_id', label: 'AppId' }, { key: 'cluster', label: 'Cluster' }, { key: 'namespace', label: 'Namespace' }]
  : provider.value === 'nacos'
    ? [{ key: 'namespace', label: 'Namespace' }, { key: 'group', label: 'Group' }, { key: 'data_id', label: 'DataId' }]
    : [{ key: 'datacenter', label: 'Datacenter' }, { key: 'kv_prefix', label: 'KV Prefix' }])
const namespaceOptions = computed(() => [
  { label: 'public (默认)', value: 'public' },
  ...nacosNamespaces.value.filter((item) => item.id !== '').map((item) => ({
    label: `${item.name} (${item.id})`, value: item.id,
  })),
])
const groupOptions = computed(() => nacosGroups.value.map((value) => ({ label: value, value })))
function filterNacosOption(input: string, option: { label?: string; value?: string }) {
  const keyword = input.trim().toLowerCase()
  return String(option.label || '').toLowerCase().includes(keyword) ||
    String(option.value || '').toLowerCase().includes(keyword)
}
const taskFinished = computed(() => task.value && !['queued', 'running'].includes(task.value.status))
const taskProgress = computed(() => task.value?.total_count
  ? Math.round((task.value.success_count + task.value.skipped_count + task.value.failed_count) / task.value.total_count * 100)
  : 0)
const taskStatusText: Record<api.TaskStatus, string> = {
  queued: '等待处理', running: '接入中', success: '已完成', partial_failed: '部分失败', failed: '接入失败',
}
const itemStatusText: Record<api.ConfigTaskItem['status'], string> = {
  pending: '等待处理', running: '处理中', success: '成功', skipped: '跳过', failed: '失败',
}
const itemStatusColor: Record<api.ConfigTaskItem['status'], string> = {
  pending: 'default', running: 'processing', success: 'success', skipped: 'warning', failed: 'error',
}
const taskColumns = [
  { title: '配置文件', dataIndex: 'name', key: 'name', width: 240, ellipsis: true },
  { title: '状态', key: 'status', width: 100 },
  { title: '说明', dataIndex: 'reason', key: 'reason', ellipsis: true },
]

function locatorText(locator: Record<string, string>) {
  return provider.value === 'nacos' && locator.namespace === ''
    ? `public / ${locator.group} / ${locator.data_id}` : Object.values(locator).join(' / ')
}

watch(() => props.open, async (open) => {
  if (!open) { namespaceRequestSequence += 1; groupRequestSequence += 1; return }
  if (props.importTask && ['queued', 'running'].includes(props.importTask.status)) {
    task.value = props.importTask
    return
  }
  task.value = null
  Object.assign(manual, { name: '', description: '', locator: {}, content_format: 'properties',
    approval_role_id: 0, application_ids: [], initial_content: '' })
  selectionDrafts.clear()
  selected.value = []
  discovered.value = []
  const [platforms, availableRoles, apps] = await Promise.all([
    api.listPlatformInstances(), api.listApprovalRoles(), api.listCmdbApplications(),
  ])
  instances.value = platforms.items.filter((item) => item.enabled)
  roles.value = availableRoles.items
  applications.value = apps.items
  instanceId.value = null
  mode.value = 'manual'
})
watch(() => props.importTask, (current) => {
  if (current && current.id === task.value?.id) task.value = current
})
watch(instanceId, () => {
  namespaceRequestSequence += 1
  groupRequestSequence += 1
  nacosNamespaces.value = []
  nacosGroups.value = []
  namespaceInput.value = ''
  namespaceLoading.value = false
  groupLoading.value = false
  namespaceError.value = false
  groupError.value = false
  loadedGroupScope = null
  selectionDrafts.clear()
  manual.locator = {}
  manual.content_format = provider.value === 'consul' ? 'consul_kv' : 'properties'
  manual.initial_content = provider.value === 'consul' ? '{}' : ''
  selected.value = []
  if (provider.value === 'nacos') void loadNacosNamespaces()
})
watch(selected, (resources) => {
  discovered.value.forEach((item) => selectionDrafts.set(JSON.stringify(item.locator), item))
  discovered.value = resources.map((resource) => selectionDrafts.get(JSON.stringify(resource.locator)) || ({
    name: resource.display_name, locator: resource.locator,
    content_format: provider.value === 'consul' ? 'consul_kv' : 'properties',
    approval_role_id: roles.value[0]?.id || 0, application_ids: [],
  }))
})

async function loadNacosNamespaces() {
  if (!instanceId.value || provider.value !== 'nacos') return
  const instance = instanceId.value
  const request = ++namespaceRequestSequence
  namespaceLoading.value = true
  namespaceError.value = false
  try {
    const result = await api.listNacosNamespaces(instance)
    if (request === namespaceRequestSequence && instanceId.value === instance) nacosNamespaces.value = result.items
  } catch {
    if (request === namespaceRequestSequence) namespaceError.value = true
  } finally {
    if (request === namespaceRequestSequence) namespaceLoading.value = false
  }
}

function onNacosLocatorChange(key: string, value: string) {
  const locatorValue = key === 'namespace' && value.trim().toLowerCase() === 'public' ? '' : value
  if (key === 'namespace' && manual.locator.namespace !== locatorValue) {
    groupRequestSequence += 1
    nacosGroups.value = []
    groupLoading.value = false
    groupError.value = false
    loadedGroupScope = null
    manual.locator.group = ''
  }
  if (key === 'namespace') namespaceInput.value = value
  manual.locator[key] = locatorValue
}

// Group 候选来自当前命名空间的远端资源定位器；自定义 Group 不依赖发现成功。
async function loadNacosGroups() {
  if (!instanceId.value || provider.value !== 'nacos' || !('namespace' in manual.locator)) return
  const instance = instanceId.value
  const namespace = manual.locator.namespace.trim()
  const scope = `${instance}:${namespace}`
  if (loadedGroupScope === scope || groupLoading.value) return
  const request = ++groupRequestSequence
  groupLoading.value = true
  groupError.value = false
  try {
    const result = await api.discoverConfigResources(instance, { tenant: namespace })
    if (request !== groupRequestSequence || instanceId.value !== instance || manual.locator.namespace.trim() !== namespace) return
    nacosGroups.value = [...new Set(result.items.map((item) => item.locator.group).filter(Boolean))].sort()
    loadedGroupScope = scope
  } catch {
    if (request === groupRequestSequence) groupError.value = true
  } finally {
    if (request === groupRequestSequence) groupLoading.value = false
  }
}

async function submit() {
  if (!instanceId.value) return message.warning('请选择平台实例')
  const selections = mode.value === 'manual' ? [manual] : discovered.value
  if (!selections.length || selections.some((item) => !item.name.trim() || !item.approval_role_id ||
    !item.content_format || Object.entries(item.locator).some(([key, value]) =>
      !(provider.value === 'nacos' && key === 'namespace') && !value.trim()))) {
    return message.warning('请完善名称、远端定位、格式和审批角色')
  }
  if (mode.value === 'manual' && locatorFields.value.some((field) =>
    provider.value === 'nacos' && field.key === 'namespace'
      ? !('namespace' in manual.locator) : !manual.locator[field.key]?.trim())) {
    return message.warning('请填写完整的远端定位')
  }
  busy.value = true
  try {
    if (mode.value === 'manual') {
      const file = await api.createConfigFile(instanceId.value, { ...manual, locator: { ...manual.locator } })
      emit('created', { fileId: file.id })
      emit('update:open', false)
    } else {
      task.value = await api.createConfigImportTask(instanceId.value, discovered.value)
      emit('created', { task: task.value })
    }
  } finally { busy.value = false }
}
</script>

<template>
  <a-modal :open="open" centered :width="'min(900px, calc(100vw - 32px))'" :title="task ? '配置接入进度' : '新建配置文件'"
    :body-style="{ maxHeight: 'calc(100vh - 180px)', overflowX: 'auto', overflowY: 'auto' }"
    :destroy-on-close="true" @cancel="emit('update:open', false)">
    <template v-if="task">
      <div class="task-head">
        <span>已处理 {{ task.success_count + task.skipped_count + task.failed_count }} / {{ task.total_count }} 个配置文件</span>
        <a-tag :color="task.status === 'success' ? 'success' : taskFinished ? 'error' : 'processing'">
          {{ taskStatusText[task.status] }}
        </a-tag>
      </div>
      <a-progress class="task-progress" :percent="taskProgress"
        :status="task.status === 'failed' || task.status === 'partial_failed' ? 'exception' : taskFinished ? 'success' : 'active'" />
      <a-descriptions bordered size="small" :column="{ xs: 1, sm: 3 }" class="task-summary">
        <a-descriptions-item label="成功">{{ task.success_count }}</a-descriptions-item>
        <a-descriptions-item label="跳过">{{ task.skipped_count }}</a-descriptions-item>
        <a-descriptions-item label="失败">{{ task.failed_count }}</a-descriptions-item>
      </a-descriptions>
      <a-alert v-if="task.last_error" class="task-error" type="warning" show-icon :message="task.last_error" />
      <a-table v-if="task.items?.length" class="task-items" :columns="taskColumns" :data-source="task.items"
        row-key="id" size="small" :pagination="false" :scroll="{ x: 520, y: 260 }">
        <template #bodyCell="{ column, record }">
          <a-tag v-if="column.key === 'status'" :color="itemStatusColor[record.status as api.ConfigTaskItem['status']]">
            {{ itemStatusText[record.status as api.ConfigTaskItem['status']] }}
          </a-tag>
          <template v-else-if="column.key === 'name'">{{ record.name || '—' }}</template>
          <template v-else-if="column.key === 'reason'">{{ record.reason || '—' }}</template>
        </template>
      </a-table>
    </template>
    <a-form v-else layout="vertical">
      <a-form-item label="平台实例" required>
        <a-select v-model:value="instanceId" placeholder="选择已启用的平台实例" :options="instances.map((item) => ({ label: `${item.name} (${item.provider})`, value: item.id }))" />
      </a-form-item>
      <a-radio-group v-model:value="mode" button-style="solid" class="config-mode">
        <a-radio-button value="manual">手工定位</a-radio-button>
        <a-radio-button value="discover">发现远端资源</a-radio-button>
      </a-radio-group>
      <template v-if="mode === 'manual'">
        <a-form-item label="配置文件名称" required><a-input v-model:value="manual.name" :maxlength="128" /></a-form-item>
        <div v-if="provider" class="locator-fields">
          <a-form-item v-for="field in locatorFields" :key="field.key" :label="field.label" required>
            <template v-if="provider === 'nacos' && (field.key === 'namespace' || field.key === 'group')">
              <div class="locator-autocomplete">
                <a-auto-complete :value="field.key === 'namespace' ? namespaceInput : manual.locator.group"
                  :options="field.key === 'namespace' ? namespaceOptions : groupOptions"
                  :filter-option="filterNacosOption"
                  :placeholder="field.key === 'namespace' ? '选择或输入 Namespace ID' : '选择或输入 Group'"
                  @update:value="onNacosLocatorChange(field.key, $event)"
                  @select="onNacosLocatorChange(field.key, $event)"
                  @focus="field.key === 'group' && loadNacosGroups()" />
                <a-spin v-if="field.key === 'namespace' ? namespaceLoading : groupLoading" size="small" class="locator-loading" />
              </div>
              <div v-if="field.key === 'namespace' && namespaceError" class="locator-error">
                获取命名空间失败，可手工输入 <a-button type="link" size="small" @click="loadNacosNamespaces"><ReloadOutlined />重试</a-button>
              </div>
              <div v-if="field.key === 'group' && groupError" class="locator-error">
                获取 Group 失败，可手工输入 <a-button type="link" size="small" @click="loadNacosGroups"><ReloadOutlined />重试</a-button>
              </div>
            </template>
            <a-input v-else :value="manual.locator[field.key] || ''" @update:value="manual.locator[field.key] = $event" />
          </a-form-item>
        </div>
        <a-form-item label="格式" required><a-select v-model:value="manual.content_format" :disabled="!provider" :options="formats" /></a-form-item>
        <a-form-item label="审批角色" required><a-select :value="manual.approval_role_id || undefined" placeholder="请选择审批角色" :options="roles.map((item) => ({ label: item.name, value: item.id }))" @update:value="manual.approval_role_id = $event" /></a-form-item>
        <a-form-item label="关联 CMDB 应用">
          <a-select v-model:value="manual.application_ids" mode="multiple" :options="applications.map((item) => ({ label: item.name, value: item.id }))" />
        </a-form-item>
        <a-form-item label="初始内容"><ConfigContentEditor :model-value="manual.initial_content || ''" :format="manual.content_format"
          @update:model-value="manual.initial_content = $event" /></a-form-item>
      </template>
      <template v-else>
        <ConfigDiscoverPanel :platform-instance-id="instanceId" :provider="provider" @select="selected = $event" />
        <div v-for="(item, index) in discovered" :key="JSON.stringify(item.locator)" class="discovered-item">
          <strong>{{ locatorText(item.locator) }}</strong>
          <div class="discovered-fields">
            <a-form-item label="名称" required><a-input v-model:value="item.name" :maxlength="128" /></a-form-item>
            <a-form-item label="格式" required><a-select v-model:value="item.content_format" :options="formats" /></a-form-item>
            <a-form-item label="审批角色" required><a-select v-model:value="item.approval_role_id" :options="roles.map((role) => ({ label: role.name, value: role.id }))" /></a-form-item>
          </div>
          <a-form-item label="关联 CMDB 应用">
            <a-select v-model:value="discovered[index].application_ids" mode="multiple" :options="applications.map((app) => ({ label: app.name, value: app.id }))" />
          </a-form-item>
        </div>
      </template>
    </a-form>
    <template #footer>
      <a-button v-if="task" @click="emit('update:open', false)">关闭</a-button>
      <a-space v-else><a-button @click="emit('update:open', false)">取消</a-button>
        <a-button type="primary" :disabled="!instanceId || (mode === 'discover' && !discovered.length)" :loading="busy" @click="submit">
          {{ mode === 'manual' ? '新建' : '接入选中配置' }}
        </a-button></a-space>
    </template>
  </a-modal>
</template>

<style scoped>
.config-mode { margin-bottom: 20px; }
.locator-fields, .discovered-fields { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.discovered-item { border-top: 1px solid var(--border-color, #e8e8e8); padding-top: 12px; margin-top: 14px; }
.discovered-item strong { word-break: break-all; }
.locator-autocomplete { position: relative; }
.locator-autocomplete :deep(.ant-select) { width: 100%; }
.locator-autocomplete :deep(.ant-select-selection-search-input) { padding-right: 20px; }
.locator-loading { position: absolute; right: 8px; top: 6px; pointer-events: none; }
.locator-error { color: var(--color-error, #cf1322); font-size: 12px; }
.task-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; color: var(--text-2); font-size: 13px; }
.task-progress { margin: 12px 0; }
.task-summary, .task-error, .task-items { margin-top: 12px; }
@media (max-width: 680px) { .locator-fields, .discovered-fields { grid-template-columns: 1fr; gap: 0; } }
</style>
