<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons-vue'
import * as configApi from '@/api/applicationConfig'

const props = defineProps<{ platformInstanceId: number | null; provider: configApi.ConfigProvider | null }>()
const emit = defineEmits<{ select: [resources: configApi.DiscoveredResource[]] }>()
const loading = ref(false)
const discovered = ref<configApi.DiscoveredResource[]>([])
const selectedKeys = ref<string[]>([])
const query = ref('')
const secondaryScope = ref('')
const keyword = ref('')
const namespaceId = ref<string | undefined>()
const namespaces = ref<configApi.NacosNamespace[]>([])
const namespaceLoading = ref(false)
const namespaceError = ref(false)
let requestSequence = 0
let namespaceRequestSequence = 0

function resourceKey(resource: configApi.DiscoveredResource) { return JSON.stringify(resource.locator) }
function locatorText(locator: Record<string, string>) {
  return props.provider === 'nacos' && locator.namespace === ''
    ? `public / ${locator.group} / ${locator.data_id}` : Object.values(locator).join(' / ')
}
const filtered = computed(() => discovered.value.filter((resource) =>
  resource.display_name.toLowerCase().includes(keyword.value.trim().toLowerCase())))

function clearDiscovery() {
  requestSequence += 1
  loading.value = false
  discovered.value = []; selectedKeys.value = []; emit('select', [])
}

async function loadNamespaces() {
  if (!props.platformInstanceId || props.provider !== 'nacos') return
  const instanceId = props.platformInstanceId
  const requestId = ++namespaceRequestSequence
  namespaceLoading.value = true
  namespaceError.value = false
  try {
    const result = await configApi.listNacosNamespaces(instanceId)
    if (requestId !== namespaceRequestSequence || props.platformInstanceId !== instanceId) return
    namespaces.value = result.items
  } catch {
    if (requestId === namespaceRequestSequence) namespaceError.value = true
  } finally {
    if (requestId === namespaceRequestSequence) namespaceLoading.value = false
  }
}

watch(() => [props.platformInstanceId, props.provider], () => {
  namespaceRequestSequence += 1
  namespaces.value = []
  namespaceId.value = undefined
  namespaceError.value = false
  namespaceLoading.value = false
  query.value = ''
  secondaryScope.value = ''
  clearDiscovery()
  void loadNamespaces()
}, { immediate: true })

watch(namespaceId, clearDiscovery)

async function discover() {
  if (!props.platformInstanceId || !props.provider ||
    (props.provider === 'nacos' ? namespaceId.value === undefined : !query.value.trim())) return
  loading.value = true
  const requestId = ++requestSequence
  const instanceId = props.platformInstanceId
  try {
    const value = props.provider === 'nacos' ? namespaceId.value || '' : query.value.trim()
    const scope: Record<string, string> = props.provider === 'apollo'
      ? { app_id: value, cluster: secondaryScope.value.trim() || 'default' }
      : props.provider === 'nacos' ? { tenant: value }
        : { datacenter: secondaryScope.value.trim(), kv_prefix: value }
    if (props.provider === 'consul' && !scope.datacenter) return
    const result = await configApi.discoverConfigResources(instanceId, scope)
    if (requestId !== requestSequence || props.platformInstanceId !== instanceId) return
    const existing = new Set(discovered.value.map(resourceKey))
    discovered.value = [...discovered.value, ...result.items.filter((item) => !existing.has(resourceKey(item)))]
  } finally { if (requestId === requestSequence) loading.value = false }
}

function onSelectionChange(keys: Array<string | number>) {
  selectedKeys.value = keys.map(String)
  emit('select', discovered.value.filter((item) => selectedKeys.value.includes(resourceKey(item))))
}
</script>

<template>
  <div>
    <a-form-item v-if="provider === 'nacos'" label="命名空间" required>
      <div class="namespace-controls">
        <a-select v-model:value="namespaceId" class="namespace-select" show-search option-filter-prop="label"
          :options="namespaces.map((item) => ({ label: item.id ? `${item.name} (${item.id})` : `${item.name} (默认)`, value: item.id }))"
          :loading="namespaceLoading" :disabled="!platformInstanceId || namespaceLoading"
          :placeholder="namespaceLoading ? '正在获取命名空间' : '选择远端命名空间'" />
        <a-button type="primary" :loading="loading" :disabled="namespaceId === undefined || namespaceLoading" @click="discover">
          <SearchOutlined />发现
        </a-button>
      </div>
      <div v-if="namespaceError" class="namespace-error">
        获取命名空间失败，请检查平台授权
        <a-button type="link" size="small" @click="loadNamespaces"><ReloadOutlined />重试</a-button>
      </div>
    </a-form-item>
    <a-form-item v-else :label="provider === 'apollo' ? 'AppId' : 'KV 前缀'" required>
      <a-input-search v-model:value="query" :disabled="!platformInstanceId" :loading="loading"
        placeholder="输入发现范围" enter-button="发现" @search="discover" />
    </a-form-item>
    <a-form-item v-if="provider === 'apollo'" label="Cluster">
      <a-input v-model:value="secondaryScope" placeholder="default" />
    </a-form-item>
    <a-form-item v-if="provider === 'consul'" label="Datacenter" required>
      <a-input v-model:value="secondaryScope" placeholder="输入数据中心" />
    </a-form-item>
    <a-input v-if="discovered.length" v-model:value="keyword" allow-clear placeholder="筛选发现结果" class="discovery-filter">
      <template #prefix><SearchOutlined /></template>
    </a-input>
    <a-table :columns="[
      { title: '远端配置', dataIndex: 'display_name', key: 'name' },
      { title: '定位', key: 'locator', width: 300 },
    ]" :data-source="filtered" :row-key="resourceKey"
      :row-selection="{ selectedRowKeys: selectedKeys, onChange: onSelectionChange }"
      :pagination="false" size="small" :scroll="{ x: 640, y: 260 }"
      :locale="{ emptyText: discovered.length ? '没有匹配结果' : '输入范围后发现远端配置' }">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'locator'">{{ locatorText(record.locator) }}</template>
      </template>
    </a-table>
    <div class="discovery-count">已选择 {{ selectedKeys.length }} 项</div>
  </div>
</template>

<style scoped>
.namespace-controls { display: flex; gap: 8px; }
.namespace-select { flex: 1; min-width: 0; }
.namespace-error { color: var(--color-error, #cf1322); font-size: 12px; margin-top: 6px; }
.discovery-filter { margin-bottom: 12px; }
.discovery-count { color: var(--text-2); font-size: 12px; margin-top: 8px; }
</style>
