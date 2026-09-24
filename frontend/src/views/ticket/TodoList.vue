<script setup lang="ts">
// 待办审批：工单与配置候选按当前角色分别展示；配置预览和裁决不依赖工单权限。
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { AuditOutlined, EyeOutlined } from '@ant-design/icons-vue'
import * as ticketApi from '@/api/ticket'
import * as configApi from '@/api/applicationConfig'
import { useUserStore } from '@/stores/user'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { fmtTime } from './meta'
import TicketDetailDrawer from './TicketDetailDrawer.vue'
import ConfigContentEditor from '@/views/application-config/ConfigContentEditor.vue'

const user = useUserStore()
const canApproveTicket = computed(() => user.hasPerm('ticket:approve'))
const activeTab = ref(canApproveTicket.value ? 'ticket' : 'config')

// ---------- 列表 ----------
const loading = ref(false)
const items = ref<ticketApi.TicketBrief[]>([])
const total = ref(0)
const query = reactive({ page: 1, page_size: 20 })
const configLoading = ref(false)
const configItems = ref<configApi.ConfigApprovalTodo[]>([])
const configTotal = ref(0)
const configQuery = reactive({ page: 1, page_size: 20 })
const configOpen = ref(false)
const configContent = ref<configApi.ConfigApprovalContent | null>(null)
const selectedConfig = ref<configApi.ConfigApprovalTodo | null>(null)
const decisionBusy = ref(false)
const rejectOpen = ref(false)
const rejectReason = ref('')

const columns = ref(makeResizable([
  { title: '工单号', dataIndex: 'ticket_no', key: 'ticket_no', width: 150 },
  { title: '标题（模板名）', dataIndex: 'title', key: 'title', width: 220, ellipsis: true },
  { title: '作业主机', key: 'job_host', width: 150, ellipsis: true },
  { title: '提交人', dataIndex: 'creator_name', key: 'creator_name', width: 110, ellipsis: true },
  { title: '提交时间', key: 'submitted', width: 155 },
  { title: '操作', key: 'action', width: 98, fixed: 'right' as const },
]))
const configColumns = [
  { title: '配置文件', dataIndex: 'file_name', key: 'file_name', width: 240, ellipsis: true },
  { title: '候选版本', key: 'version', width: 120 },
  { title: '审批角色', dataIndex: 'approval_role_name', key: 'role', width: 160 },
  { title: '提交人', dataIndex: 'submitter_name', key: 'submitter', width: 140 },
  { title: '提交时间', key: 'submitted', width: 175 },
  { title: '操作', key: 'action', width: 110, fixed: 'right' as const },
]

/** 拉取待我审批列表（后端按角色与当前步骤过滤） */
async function loadList() {
  if (!canApproveTicket.value) return
  loading.value = true
  try {
    const data = await ticketApi.todoTickets({ page: query.page, page_size: query.page_size })
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

async function loadConfigs() {
  configLoading.value = true
  try {
    const data = await configApi.listConfigApprovalTodo(configQuery)
    configItems.value = data.items
    configTotal.value = data.total
  } finally { configLoading.value = false }
}

function onConfigPage(page: number, pageSize: number) {
  configQuery.page = page
  configQuery.page_size = pageSize
  void loadConfigs()
}

function onPageChange(page: number, pageSize: number) {
  query.page = page
  query.page_size = pageSize
  loadList()
}

// ---------- 审批抽屉 ----------
const detailOpen = ref(false)
const detailId = ref<number | null>(null)

function openApprove(row: ticketApi.TicketBrief) {
  detailId.value = row.id
  detailOpen.value = true
}

function onTicketChanged() {
  void loadList()
  window.dispatchEvent(new Event('opspilot:todo-changed'))
}

async function openConfig(row: configApi.ConfigApprovalTodo) {
  selectedConfig.value = row
  configContent.value = null
  configOpen.value = true
  try { configContent.value = await configApi.getConfigApprovalContent(row.version_id) }
  catch { configOpen.value = false }
}

async function decideConfig(approved: boolean) {
  if (!selectedConfig.value) return
  decisionBusy.value = true
  try {
    const { file_id, version_id } = selectedConfig.value
    if (approved) await configApi.approveCandidate(file_id, version_id)
    else await configApi.rejectCandidate(file_id, version_id, rejectReason.value || undefined)
    message.success(approved ? '审批已通过，已自动发起发布' : '已驳回')
    configOpen.value = false
    rejectOpen.value = false
    rejectReason.value = ''
    await loadConfigs()
    window.dispatchEvent(new Event('opspilot:todo-changed'))
  } finally { decisionBusy.value = false }
}

onMounted(() => { if (canApproveTicket.value) void loadList(); void loadConfigs() })
</script>

<template>
  <div>
    <!-- 彩色横幅（与 M1 页面同构） -->
    <div class="op-hero op-hero--amber">
      <div class="op-hero-icon"><AuditOutlined /></div>
      <div>
        <div class="op-hero-title">待办审批</div>
        <div class="op-hero-sub">待您处理的工单与配置文件</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat"><b>{{ total + configTotal }}</b><span>待处理</span></div>
      </div>
    </div>

    <a-tabs v-model:active-key="activeTab">
      <a-tab-pane key="ticket" :disabled="!canApproveTicket" :tab="`工单审批 (${total})`">
    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      row-key="id"
      bordered
      :scroll="{ x: 980 }"
      @resize-column="onResizeColumn"
      :pagination="{
        current: query.page,
        pageSize: query.page_size,
        total,
        showSizeChanger: true,
        showQuickJumper: true,
        showTotal: (t: number) => `共 ${t} 个待办`,
        onChange: onPageChange,
      }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'job_host'">{{ record.job_host_name || '—' }}</template>
        <template v-else-if="column.key === 'submitted'">{{ fmtTime(record.submitted_at) }}</template>
        <template v-else-if="column.key === 'action'">
          <a-button size="small" class="op-btn-green" @click="openApprove(record as ticketApi.TicketBrief)">
            <AuditOutlined />
            审批
          </a-button>
        </template>
      </template>
    </a-table>
      </a-tab-pane>
      <a-tab-pane key="config" :tab="`配置审批 (${configTotal})`">
        <a-table :columns="configColumns" :data-source="configItems" :loading="configLoading" row-key="version_id" bordered
          :scroll="{ x: 950 }"
          :pagination="{ current: configQuery.page, pageSize: configQuery.page_size, total: configTotal,
            showSizeChanger: true, showQuickJumper: true, showTotal: (t: number) => `共 ${t} 个待办`, onChange: onConfigPage }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'version'">v{{ record.version_no }}</template>
            <template v-else-if="column.key === 'submitted'">{{ fmtTime(record.submitted_at) }}</template>
            <template v-else-if="column.key === 'action'">
              <a-button size="small" class="op-btn-cyan" @click="openConfig(record as configApi.ConfigApprovalTodo)"><EyeOutlined />查看</a-button>
            </template>
          </template>
        </a-table>
      </a-tab-pane>
    </a-tabs>

    <!-- 审批抽屉：详情 + 通过/驳回操作区 -->
    <TicketDetailDrawer v-if="canApproveTicket" v-model:open="detailOpen" :ticket-id="detailId" show-approve
      @changed="onTicketChanged" />
    <a-drawer v-model:open="configOpen" :width="'min(760px, 100vw)'" :title="`配置审批 · ${selectedConfig?.file_name || ''}`">
      <template v-if="selectedConfig">
        <a-descriptions bordered size="small" :column="1" class="config-approval-summary">
          <a-descriptions-item label="候选版本">v{{ selectedConfig.version_no }}</a-descriptions-item>
          <a-descriptions-item label="审批角色">{{ selectedConfig.approval_role_name }}</a-descriptions-item>
          <a-descriptions-item label="提交人">{{ selectedConfig.submitter_name || '—' }}</a-descriptions-item>
          <a-descriptions-item label="提交时间">{{ fmtTime(selectedConfig.submitted_at) }}</a-descriptions-item>
        </a-descriptions>
        <a-spin :spinning="!configContent">
          <ConfigContentEditor v-if="configContent" :format="configContent.content_format" :model-value="configContent.content"
            readonly :masked="!user.hasPerm('secret:read')" :can-read-secret="user.hasPerm('secret:read')" />
        </a-spin>
      </template>
      <template #footer>
        <a-space>
          <a-button @click="configOpen = false">关闭</a-button>
          <a-button :disabled="!configContent || decisionBusy" @click="rejectOpen = true">驳回</a-button>
          <a-popconfirm title="确认批准该候选版本？" @confirm="decideConfig(true)">
            <a-button type="primary" :loading="decisionBusy" :disabled="!configContent">批准</a-button>
          </a-popconfirm>
        </a-space>
      </template>
    </a-drawer>
    <a-modal v-model:open="rejectOpen" title="驳回配置版本" :confirm-loading="decisionBusy"
      @ok="decideConfig(false)" @cancel="rejectReason = ''">
      <a-textarea v-model:value="rejectReason" :rows="3" placeholder="驳回原因（选填）" />
    </a-modal>
  </div>
</template>

<style scoped>
.config-approval-summary { margin-bottom: 16px; }
</style>
