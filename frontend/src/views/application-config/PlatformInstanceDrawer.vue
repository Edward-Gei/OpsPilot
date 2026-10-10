<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { DeleteOutlined, EditOutlined, PlusOutlined, SyncOutlined } from '@ant-design/icons-vue'
import * as api from '@/api/applicationConfig'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
const instances = ref<api.PlatformInstance[]>([])
const credentials = ref<api.CredentialOption[]>([])
const editing = ref<api.PlatformInstance | null>(null)
const formOpen = ref(false)
const busy = ref(false)
const form = reactive({
  name: '', provider: 'apollo' as api.ConfigProvider, base_url: '', credential_id: undefined as number | undefined,
  description: '', compatibility_version: '', enabled: true,
})

async function load() { instances.value = (await api.listPlatformInstances()).items }
async function loadCredentials() {
  credentials.value = (await api.listCompatibleCredentials(form.provider)).items
}
watch(() => props.open, (open) => { if (open) void load() })
function onProviderChange() { form.credential_id = undefined; void loadCredentials() }

function openForm(instance?: api.PlatformInstance) {
  editing.value = instance || null
  Object.assign(form, instance ? {
    name: instance.name, provider: instance.provider, base_url: instance.base_url,
    credential_id: instance.credential_id, description: instance.description || '',
    compatibility_version: instance.compatibility_version || '', enabled: instance.enabled,
  } : { name: '', provider: 'apollo', base_url: '', credential_id: undefined,
    description: '', compatibility_version: '', enabled: true })
  void loadCredentials()
  formOpen.value = true
}

async function save() {
  if (!form.name.trim() || !form.base_url.trim() || !form.credential_id) {
    return message.warning('请填写名称、Base URL 和凭据')
  }
  busy.value = true
  try {
    if (editing.value) {
      await api.updatePlatformInstance(editing.value.id, {
        name: form.name, provider: form.provider, base_url: form.base_url,
        credential_id: form.credential_id, description: form.description,
        compatibility_version: form.compatibility_version, enabled: form.enabled,
      })
    } else {
      await api.createPlatformInstance({
        name: form.name, provider: form.provider, base_url: form.base_url,
        credential_id: form.credential_id, description: form.description,
        compatibility_version: form.compatibility_version,
      })
    }
    message.success('平台实例已保存')
    formOpen.value = false
    await load()
  } finally { busy.value = false }
}

async function probe(instance: api.PlatformInstance) {
  try {
    const result = await api.probePlatformInstance(instance.id)
    message[result.last_probe_result === 'success' ? 'success' : 'warning'](
      result.last_probe_result === 'success' ? '连接正常' : '连接未通过',
    )
    await load()
  } catch { await load() }
}

async function remove(instance: api.PlatformInstance) {
  await api.deletePlatformInstance(instance.id)
  message.success('平台实例已删除')
  await load()
}
</script>

<template>
  <a-drawer :open="open" title="平台实例" :width="760" @close="emit('update:open', false)">
    <div class="instance-actions"><a-button type="primary" @click="openForm()"><PlusOutlined />新建实例</a-button></div>
    <a-table :columns="[
      { title: '名称', dataIndex: 'name', key: 'name' },
      { title: '平台', dataIndex: 'provider', key: 'provider', width: 90 },
      { title: '状态', key: 'status', width: 90 },
      { title: '操作', key: 'actions', width: 150 },
    ]" :data-source="instances" row-key="id" size="small" :scroll="{ x: 620 }">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'status'"><a-tag :color="record.enabled ? 'success' : 'default'">{{ record.enabled ? '已启用' : '已停用' }}</a-tag></template>
        <template v-if="column.key === 'actions'">
          <a-space :size="4">
            <a-tooltip title="编辑"><a-button size="small" aria-label="编辑平台实例" @click="openForm(record as api.PlatformInstance)"><EditOutlined /></a-button></a-tooltip>
            <a-tooltip title="测试连接"><a-button size="small" aria-label="测试连接" @click="probe(record as api.PlatformInstance)"><SyncOutlined /></a-button></a-tooltip>
            <a-popconfirm v-if="!record.enabled" title="确认删除未被引用的停用实例？" @confirm="remove(record as api.PlatformInstance)">
              <a-button size="small" danger aria-label="删除平台实例"><DeleteOutlined /></a-button>
            </a-popconfirm>
          </a-space>
        </template>
      </template>
    </a-table>
    <a-modal v-model:open="formOpen" :title="editing ? '编辑平台实例' : '新建平台实例'" :confirm-loading="busy" @ok="save">
      <a-form layout="vertical">
        <a-form-item label="名称" required><a-input v-model:value="form.name" :maxlength="64" /></a-form-item>
        <a-form-item label="平台" required>
          <a-select v-model:value="form.provider" :disabled="!!editing?.first_bound_at" :options="[
            { label: 'Apollo', value: 'apollo' }, { label: 'Nacos', value: 'nacos' }, { label: 'Consul', value: 'consul' },
          ]" @change="onProviderChange" />
        </a-form-item>
        <a-form-item label="Base URL" required><a-input v-model:value="form.base_url" :disabled="!!editing?.first_bound_at" /></a-form-item>
        <a-form-item label="凭据" required>
          <a-select v-model:value="form.credential_id" placeholder="请选择凭据"
            :options="credentials.map((item) => ({ label: `${item.name} (${item.auth_type})`, value: item.id }))" />
        </a-form-item>
        <a-form-item label="兼容版本"><a-input v-model:value="form.compatibility_version" :maxlength="64" /></a-form-item>
        <a-form-item label="说明"><a-input v-model:value="form.description" :maxlength="255" /></a-form-item>
        <a-form-item v-if="editing" label="启用"><a-switch v-model:checked="form.enabled" /></a-form-item>
      </a-form>
    </a-modal>
  </a-drawer>
</template>

<style scoped>
.instance-actions { display: flex; justify-content: flex-end; margin-bottom: 12px; }
</style>
