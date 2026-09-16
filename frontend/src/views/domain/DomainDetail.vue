<script setup lang="ts">
// Zone 详情：本地快照浏览与带前后对比确认的 DNS 记录集变更。
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  ArrowLeftOutlined,
  CloudSyncOutlined,
  DeleteOutlined,
  EditOutlined,
  GlobalOutlined,
  PlusOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue'
import * as domainApi from '@/api/domain'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const canWrite = userStore.hasPerm('domain:write')
const canDelete = userStore.hasPerm('domain:delete')
const zoneId = computed(() => Number(route.params.id))

const providerText: Record<domainApi.DomainProvider, string> = {
  aws_route53: 'AWS Route 53',
  tencent_dnspod: '腾讯云 DNSPod',
  google_cloud_dns: 'Google Cloud DNS',
}
const syncText: Record<domainApi.DomainSyncStatus, { text: string; color: string }> = {
  success: { text: '已同步', color: 'success' },
  failed: { text: '同步失败', color: 'error' },
  syncing: { text: '同步中', color: 'processing' },
}
const writableTypeOptions = ['A', 'AAAA', 'CNAME', 'MX', 'TXT', 'CAA', 'SRV'].map((value) => ({ value, label: value }))
const recordTypeOptions = [...writableTypeOptions, { value: 'SOA', label: 'SOA' }, { value: 'NS', label: 'NS' }]
const readOnlyOptions = [
  { value: true, label: '只读' },
  { value: false, label: '可编辑' },
]

const detailLoading = ref(false)
const recordLoading = ref(false)
const zone = ref<domainApi.DomainItem | null>(null)
const records = ref<domainApi.DnsRecordSet[]>([])
const recordTotal = ref(0)
const recordQuery = reactive({
  page: 1,
  page_size: 10,
  keyword: '',
  record_type: undefined as string | undefined,
  read_only: undefined as boolean | undefined,
})

const recordColumns = ref(makeResizable([
  { title: '记录名称', dataIndex: 'record_name', key: 'record_name', width: 220, ellipsis: true },
  { title: '类型', dataIndex: 'record_type', key: 'record_type', width: 88 },
  { title: 'TTL', dataIndex: 'ttl', key: 'ttl', width: 82, align: 'right' as const },
  { title: '记录值', key: 'values', width: 330 },
  { title: '状态', key: 'read_only', width: 105 },
  { title: '只读原因', key: 'read_only_reason', width: 200, ellipsis: true },
  { title: '操作', key: 'action', width: canDelete ? 152 : canWrite ? 82 : 72, fixed: 'right' as const },
]))

async function loadZone() {
  detailLoading.value = true
  try {
    zone.value = await domainApi.getDomain(zoneId.value)
  } finally {
    detailLoading.value = false
  }
}

async function loadRecords() {
  recordLoading.value = true
  try {
    const data = await domainApi.listRecords(zoneId.value, {
      page: recordQuery.page,
      page_size: recordQuery.page_size,
      keyword: recordQuery.keyword || undefined,
      record_type: recordQuery.record_type,
      read_only: recordQuery.read_only,
    })
    records.value = data.items
    recordTotal.value = data.total
  } finally {
    recordLoading.value = false
  }
}

function refreshAll() {
  void loadZone()
  void loadRecords()
}

function onRecordSearch() {
  recordQuery.page = 1
  void loadRecords()
}

function onRecordPageChange(page: number, pageSize: number) {
  recordQuery.page = page
  recordQuery.page_size = pageSize
  void loadRecords()
}

function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleString() : '—'
}

function backToList() {
  void router.push('/domains')
}

const syncLoading = ref(false)

async function onSync() {
  syncLoading.value = true
  try {
    zone.value = await domainApi.syncDomain(zoneId.value)
    message.success('Zone 快照已同步')
    await loadRecords()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    syncLoading.value = false
  }
}

const recordEditorVisible = ref(false)
const editingRecord = ref<domainApi.DnsRecordSet | null>(null)
const recordForm = reactive({
  owner_name: '',
  record_type: 'A',
  ttl: 300,
  values_text: '',
})

function openCreateRecord() {
  pendingChange.value = null
  confirmVisible.value = false
  editingRecord.value = null
  Object.assign(recordForm, { owner_name: '', record_type: 'A', ttl: 300, values_text: '' })
  recordEditorVisible.value = true
}

function openEditRecord(record: domainApi.DnsRecordSet) {
  pendingChange.value = null
  confirmVisible.value = false
  editingRecord.value = record
  Object.assign(recordForm, {
    owner_name: record.record_name,
    record_type: record.record_type,
    ttl: record.ttl || 300,
    values_text: record.values.join('\n'),
  })
  recordEditorVisible.value = true
}

function splitValues(value: string): string[] {
  return value.split(/\r?\n/).filter((line) => line.trim().length > 0)
}

interface RecordPreview {
  record_name: string
  record_type: string
  ttl: number | null
  values: string[]
}

interface PendingChange {
  kind: 'create' | 'update' | 'delete'
  before: RecordPreview | null
  after: RecordPreview | null
  record: domainApi.DnsRecordSet | null
}

const pendingChange = ref<PendingChange | null>(null)
const confirmVisible = ref(false)
const confirmLoading = ref(false)

/** 编辑和变更确认复用同一弹窗，关闭时一并清理待提交快照。 */
function closeRecordModal() {
  recordEditorVisible.value = false
  confirmVisible.value = false
  pendingChange.value = null
}

function onRecordModalOpenChange(open: boolean) {
  if (!open) closeRecordModal()
}

function returnToRecordEditor() {
  confirmVisible.value = false
  pendingChange.value = null
  recordEditorVisible.value = true
}

function toPreview(record: domainApi.DnsRecordSet): RecordPreview {
  return {
    record_name: record.record_name,
    record_type: record.record_type,
    ttl: record.ttl,
    values: record.values,
  }
}

function openRecordConfirm(kind: 'create' | 'update' | 'delete', record: domainApi.DnsRecordSet | null = null) {
  const values = kind === 'delete' ? [] : splitValues(recordForm.values_text)
  pendingChange.value = {
    kind,
    record,
    before: record ? toPreview(record) : null,
    after: kind === 'delete'
      ? null
      : {
          record_name: record?.record_name || recordForm.owner_name,
          record_type: record?.record_type || recordForm.record_type,
          ttl: recordForm.ttl,
          values,
        },
  }
  confirmVisible.value = true
}

function onPrepareRecordChange() {
  if (!editingRecord.value && (!recordForm.owner_name || !recordForm.record_type)) {
    message.warning('请填写记录名称和类型')
    return
  }
  if (recordForm.ttl < 300 || recordForm.ttl > 86400) {
    message.warning('TTL 必须在 300 到 86400 秒之间')
    return
  }
  if (!splitValues(recordForm.values_text).length) {
    message.warning('请至少填写一条记录值')
    return
  }
  openRecordConfirm(editingRecord.value ? 'update' : 'create', editingRecord.value)
}

function openDeleteConfirm(record: domainApi.DnsRecordSet) {
  openRecordConfirm('delete', record)
}

const confirmTitle = computed(() => {
  if (pendingChange.value?.kind === 'create') return '确认新增记录集'
  if (pendingChange.value?.kind === 'update') return '确认修改记录集'
  return '确认删除记录集'
})

async function onConfirmRecordChange() {
  const pending = pendingChange.value
  if (!pending) return
  confirmLoading.value = true
  try {
    if (pending.kind === 'create' && pending.after) {
      await domainApi.createRecord(zoneId.value, {
        owner_name: pending.after.record_name,
        record_type: pending.after.record_type,
        ttl: pending.after.ttl || 300,
        values: pending.after.values,
      })
      message.success('记录集已新增')
    } else if (pending.kind === 'update' && pending.after && pending.record) {
      await domainApi.updateRecord(zoneId.value, pending.record.id, {
        ttl: pending.after.ttl || 300,
        values: pending.after.values,
      })
      message.success('记录集已更新')
    } else if (pending.kind === 'delete' && pending.record) {
      await domainApi.deleteRecord(zoneId.value, pending.record.id)
      message.success('记录集已删除')
    }
    closeRecordModal()
    if (records.value.length === 1 && recordQuery.page > 1) recordQuery.page -= 1
    refreshAll()
  } catch {
    /* 40901 等错误由拦截器提示；确认和编辑表单保持打开供操作者同步后比较。 */
  } finally {
    confirmLoading.value = false
  }
}

watch(zoneId, () => {
  recordQuery.page = 1
  refreshAll()
})

onMounted(refreshAll)
</script>

<template>
  <div>
    <div class="op-hero op-hero--indigo detail-hero">
      <div class="op-hero-icon"><GlobalOutlined /></div>
      <div>
        <div class="op-hero-title">{{ zone?.zone_name || 'Zone 详情' }}</div>
        <div class="op-hero-sub">{{ zone ? providerText[zone.provider] : '正在加载 Zone 快照' }}</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat"><b>{{ zone?.record_count ?? 0 }}</b><span>记录集</span></div>
        <div class="op-hero-stat"><b>{{ zone ? syncText[zone.sync_status].text : '—' }}</b><span>快照状态</span></div>
      </div>
    </div>

    <div class="detail-actions">
      <a-button @click="backToList"><ArrowLeftOutlined />返回 Zone 台账</a-button>
      <a-button v-if="canWrite" :loading="syncLoading" @click="onSync"><CloudSyncOutlined />手动同步</a-button>
      <a-button v-if="canWrite" type="primary" @click="openCreateRecord"><PlusOutlined />新增记录集</a-button>
    </div>

    <a-spin :spinning="detailLoading">
      <a-descriptions v-if="zone" class="zone-summary" bordered :column="{ xs: 1, sm: 2, lg: 3 }" size="small">
        <a-descriptions-item label="Zone 名称">{{ zone.zone_name }}</a-descriptions-item>
        <a-descriptions-item label="服务商">{{ providerText[zone.provider] }}</a-descriptions-item>
        <a-descriptions-item label="凭据名称">{{ zone.credential_name || '—' }}</a-descriptions-item>
        <a-descriptions-item label="远端 Zone ID">{{ zone.remote_zone_id }}</a-descriptions-item>
        <a-descriptions-item label="记录集数量">{{ zone.record_count }}</a-descriptions-item>
        <a-descriptions-item label="最近同步时间">{{ formatTime(zone.last_synced_at) }}</a-descriptions-item>
        <a-descriptions-item label="描述" :span="3">{{ zone.description || '—' }}</a-descriptions-item>
      </a-descriptions>
      <a-alert
        v-if="zone?.last_sync_error"
        class="sync-error"
        type="warning"
        show-icon
        :message="`最近同步失败：${zone.last_sync_error}`"
      />
    </a-spin>

    <div class="record-toolbar">
      <a-input
        v-model:value="recordQuery.keyword"
        class="keyword-input"
        placeholder="搜索记录名称"
        allow-clear
        @press-enter="onRecordSearch"
      >
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-select
        v-model:value="recordQuery.record_type"
        class="filter-select"
        placeholder="记录类型"
        allow-clear
        :options="recordTypeOptions"
        @change="onRecordSearch"
      />
      <a-select
        v-model:value="recordQuery.read_only"
        class="filter-select"
        placeholder="可编辑状态"
        allow-clear
        :options="readOnlyOptions"
        @change="onRecordSearch"
      />
    </div>

    <a-table
      :columns="recordColumns"
      :data-source="records"
      :loading="recordLoading"
      row-key="id"
      bordered
      :scroll="{ x: 1210 }"
      @resize-column="onResizeColumn"
      :pagination="{
        current: recordQuery.page,
        pageSize: recordQuery.page_size,
        total: recordTotal,
        showSizeChanger: true,
        showQuickJumper: true,
        showTotal: (count: number) => `共 ${count} 个记录集`,
        onChange: onRecordPageChange,
      }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'ttl'">{{ record.ttl ?? '—' }}</template>
        <template v-else-if="column.key === 'values'">
          <a-tooltip :title="record.values.join('\n')">
            <span class="record-values">{{ record.values.join('\n') }}</span>
          </a-tooltip>
        </template>
        <template v-else-if="column.key === 'read_only'">
          <a-tag :color="record.read_only ? 'default' : 'success'">{{ record.read_only ? '只读' : '可编辑' }}</a-tag>
        </template>
        <template v-else-if="column.key === 'read_only_reason'">{{ record.read_only_reason || '—' }}</template>
        <template v-else-if="column.key === 'action'">
          <a-space v-if="!record.read_only && (canWrite || canDelete)" :size="6">
            <a-button v-if="canWrite" size="small" class="op-btn-blue" @click="openEditRecord(record as domainApi.DnsRecordSet)">
              <EditOutlined />编辑
            </a-button>
            <a-button v-if="canDelete" size="small" danger @click="openDeleteConfirm(record as domainApi.DnsRecordSet)">
              <DeleteOutlined />删除
            </a-button>
          </a-space>
          <template v-else>—</template>
        </template>
      </template>
    </a-table>

    <a-modal
      :open="recordEditorVisible || confirmVisible"
      :title="confirmVisible ? confirmTitle : editingRecord ? '修改记录集' : '新增记录集'"
      :width="620"
      @update:open="onRecordModalOpenChange"
    >
      <div v-if="confirmVisible" class="confirm-grid">
        <div v-if="pendingChange?.before" class="preview-block">
          <h4>变更前</h4>
          <div><b>名称：</b>{{ pendingChange.before.record_name }}</div>
          <div><b>类型：</b>{{ pendingChange.before.record_type }}</div>
          <div><b>TTL：</b>{{ pendingChange.before.ttl ?? '—' }}</div>
          <pre>{{ pendingChange.before.values.join('\n') }}</pre>
        </div>
        <div v-if="pendingChange?.after" class="preview-block">
          <h4>变更后</h4>
          <div><b>名称：</b>{{ pendingChange.after.record_name }}</div>
          <div><b>类型：</b>{{ pendingChange.after.record_type }}</div>
          <div><b>TTL：</b>{{ pendingChange.after.ttl ?? '—' }}</div>
          <pre>{{ pendingChange.after.values.join('\n') }}</pre>
        </div>
        <div v-if="pendingChange?.kind === 'delete'" class="delete-note">确认后将从远端 DNS 与本地快照删除该记录集。</div>
      </div>
      <a-form v-else layout="vertical">
        <div class="form-row">
          <a-form-item label="记录名称" required class="form-col">
            <a-input v-model:value="recordForm.owner_name" :disabled="Boolean(editingRecord)" placeholder="@、www 或完整记录名称" />
          </a-form-item>
          <a-form-item label="记录类型" required class="type-field">
            <a-select v-model:value="recordForm.record_type" :disabled="Boolean(editingRecord)" :options="writableTypeOptions" />
          </a-form-item>
          <a-form-item label="TTL（秒）" required class="ttl-field">
            <a-input-number v-model:value="recordForm.ttl" :min="300" :max="86400" :precision="0" />
          </a-form-item>
        </div>
        <a-form-item label="记录值" required>
          <a-textarea
            v-model:value="recordForm.values_text"
            :rows="7"
            placeholder="每行一条记录值；TXT 行会按原始内容提交"
            class="values-input"
          />
        </a-form-item>
      </a-form>
      <template #footer>
        <template v-if="confirmVisible">
          <a-button v-if="pendingChange?.kind !== 'delete'" @click="returnToRecordEditor">返回修改</a-button>
          <a-button v-else @click="closeRecordModal">取消</a-button>
          <a-button type="primary" :loading="confirmLoading" @click="onConfirmRecordChange">确认提交</a-button>
        </template>
        <template v-else>
          <a-button @click="closeRecordModal">取消</a-button>
          <a-button type="primary" @click="onPrepareRecordChange">继续</a-button>
        </template>
      </template>
    </a-modal>
  </div>
</template>

<style scoped>
.detail-actions,
.record-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 16px;
}
.detail-actions {
  justify-content: flex-end;
}
.detail-actions > :first-child {
  margin-right: auto;
}
.zone-summary {
  margin-bottom: 16px;
}
.sync-error {
  margin: 0 0 16px;
}
.keyword-input {
  width: 240px;
}
.filter-select {
  width: 128px;
}
.record-values {
  display: -webkit-box;
  overflow: hidden;
  max-width: 300px;
  color: var(--text-2);
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-line;
  word-break: break-all;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.form-row {
  display: flex;
  gap: 16px;
}
.form-col {
  flex: 1;
}
.type-field {
  width: 110px;
}
.ttl-field {
  width: 126px;
}
.values-input,
.preview-block pre {
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
}
.confirm-grid {
  display: grid;
  gap: 12px;
}
.preview-block {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  color: var(--text-2);
  font-size: 13px;
}
.preview-block h4 {
  margin: 0 0 8px;
  color: var(--text-1);
}
.preview-block div + div {
  margin-top: 4px;
}
.preview-block pre {
  max-height: 160px;
  margin: 10px 0 0;
  overflow: auto;
  padding: 8px;
  border-radius: 6px;
  background: var(--bg-hover);
  white-space: pre-wrap;
  word-break: break-all;
}
.delete-note {
  color: var(--error);
  font-size: 13px;
}
@media (max-width: 760px) {
  .detail-actions {
    justify-content: flex-start;
  }
  .detail-actions > :first-child {
    width: 100%;
    margin-right: 0;
  }
  .form-row {
    display: block;
  }
  .type-field,
  .ttl-field {
    width: auto;
  }
  .op-hero-extra {
    display: none;
  }
}
</style>
