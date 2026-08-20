<script setup lang="ts">
// 工单中心（V2）：选模板提交 → 审批 → 执行，全程快照留痕
// 无草稿态：提交即生效；行操作仅 详情 + 撤回（审批中且本人创建且模板允许）
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import dayjs, { type Dayjs } from 'dayjs'
import {
  EyeOutlined,
  FileDoneOutlined,
  PlusOutlined,
  RollbackOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue'
import * as ticketApi from '@/api/ticket'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { useUserStore } from '@/stores/user'
import { fmtTime, statusMeta, statusOptions } from './meta'
import TicketWizard from './TicketWizard.vue'
import TicketDetailDrawer from './TicketDetailDrawer.vue'

const userStore = useUserStore()
const route = useRoute()
const router = useRouter()
const canWrite = userStore.hasPerm('ticket:write')

/** 是否本人创建（撤回为对象级权限，管理员也不例外） */
function isMine(row: ticketApi.TicketBrief): boolean {
  return row.creator_id === userStore.userInfo?.id
}

// ---------- 列表 ----------
const loading = ref(false)
const items = ref<ticketApi.TicketBrief[]>([])
const total = ref(0)
const query = reactive({
  page: 1,
  page_size: 20,
  keyword: '',
  status: undefined as string | undefined,
  // 创建时间范围：[开始, 结束]，精确到秒（YYYY-MM-DD HH:mm:ss）
  range: [
    dayjs().subtract(30, 'day').format('YYYY-MM-DD HH:mm:ss'),
    dayjs().format('YYYY-MM-DD HH:mm:ss'),
  ] as string[],
})

// 时间筛选快捷项：最近 N 天 → 当前时刻，精确到秒
const rangePresets: { label: string; value: [Dayjs, Dayjs] }[] = [
  { label: '最近 7 天', value: [dayjs().subtract(7, 'day'), dayjs()] },
  { label: '最近 30 天', value: [dayjs().subtract(30, 'day'), dayjs()] },
  { label: '最近 90 天', value: [dayjs().subtract(90, 'day'), dayjs()] },
]

// 操作列固定右侧，其余列可拖拽调宽
const columns = ref(makeResizable([
  { title: '工单号', dataIndex: 'ticket_no', key: 'ticket_no', width: 150 },
  { title: '标题（模板名）', dataIndex: 'title', key: 'title', width: 200, ellipsis: true },
  { title: '作业主机', key: 'job_host', width: 130, ellipsis: true },
  { title: '状态', key: 'status', width: 140 },
  { title: '提交人', dataIndex: 'creator_name', key: 'creator_name', width: 110, ellipsis: true },
  { title: '提交时间', key: 'submitted', width: 155 },
  { title: '操作', key: 'action', width: 140, fixed: 'right' as const },
]))

/** 拉取工单列表（keyword 匹配 工单号/标题） */
async function loadList() {
  loading.value = true
  try {
    const data = await ticketApi.listTickets({
      page: query.page,
      page_size: query.page_size,
      keyword: query.keyword || undefined,
      status: query.status,
      start: query.range?.[0] || undefined,
      end: query.range?.[1] || undefined,
    })
    items.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

// ---------- 横幅统计（全局口径，page_size=1 只取 total） ----------
const stats = reactive({ all: 0, approving: 0, running: 0, success: 0 })

async function loadStats() {
  const [all, approving, running, success] = await Promise.all([
    ticketApi.listTickets({ page: 1, page_size: 1 }),
    ticketApi.listTickets({ page: 1, page_size: 1, status: 'approving' }),
    ticketApi.listTickets({ page: 1, page_size: 1, status: 'running' }),
    ticketApi.listTickets({ page: 1, page_size: 1, status: 'success' }),
  ])
  stats.all = all.total
  stats.approving = approving.total
  stats.running = running.total
  stats.success = success.total
}

/** 列表 + 统计一起刷新（变更操作后调用） */
function refreshAll() {
  loadList()
  loadStats()
}

function onSearch() {
  query.page = 1
  loadList()
}

function onPageChange(page: number, pageSize: number) {
  query.page = page
  query.page_size = pageSize
  loadList()
}

// ---------- 提交向导（选模板 → 填参数 → 提交） ----------
const wizardOpen = ref(false)

// ---------- 详情抽屉 ----------
const detailOpen = ref(false)
const detailId = ref<number | null>(null)

function openDetail(row: ticketApi.TicketBrief) {
  detailId.value = row.id
  detailOpen.value = true
}

// ---------- 撤回（仅创建人、审批中；模板禁止撤回时后端 42201） ----------
async function onCancel(row: ticketApi.TicketBrief) {
  try {
    await ticketApi.cancelTicket(row.id)
    message.success('工单已撤回')
    refreshAll()
  } catch {
    /* 42201 模板禁止撤回等提示由拦截器统一弹出 */
  }
}

onMounted(() => {
  // 全局搜索跳转：?keyword= 带入搜索框自动过滤，随后清掉 query 避免刷新残留（SEARCH-04）
  const qkw = route.query.keyword
  if (typeof qkw === 'string' && qkw) {
    query.keyword = qkw
    router.replace({ path: route.path })
  }
  refreshAll()
  // 站内通知跳转：?id={工单id} 自动打开详情抽屉，随后清掉 query 避免刷新重复弹出
  const qid = Number(route.query.id)
  if (qid > 0) {
    detailId.value = qid
    detailOpen.value = true
    router.replace({ path: route.path })
  }
})
</script>

<template>
  <div>
    <!-- 彩色横幅：页面标识 + 全局统计（与 M1 页面同构） -->
    <div class="op-hero op-hero--rose">
      <div class="op-hero-icon"><FileDoneOutlined /></div>
      <div>
        <div class="op-hero-title">工单中心</div>
        <div class="op-hero-sub">基于模板提交工单：填参数即可，规则由模板统一定义</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat"><b>{{ stats.all }}</b><span>总工单</span></div>
        <div class="op-hero-stat"><b>{{ stats.approving }}</b><span>审批中</span></div>
        <div class="op-hero-stat"><b>{{ stats.running }}</b><span>执行中</span></div>
        <div class="op-hero-stat"><b>{{ stats.success }}</b><span>成功</span></div>
      </div>
    </div>

    <!-- 工具栏 -->
    <div class="toolbar">
      <a-input
        v-model:value="query.keyword"
        placeholder="搜索工单号/标题"
        class="kw"
        allow-clear
        @press-enter="onSearch"
      >
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-select
        v-model:value="query.status"
        placeholder="状态"
        class="status-sel"
        allow-clear
        :options="statusOptions"
        @change="onSearch"
      />
      <a-range-picker
        v-model:value="query.range"
        show-time
        value-format="YYYY-MM-DD HH:mm:ss"
        class="range"
        :placeholder="['创建开始时间', '创建结束时间']"
        :presets="rangePresets"
        @change="onSearch"
      />
      <a-button v-if="canWrite" type="primary" class="create-btn" @click="wizardOpen = true">
        <PlusOutlined />提交工单
      </a-button>
    </div>

    <!-- 列表 -->
    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      row-key="id"
      bordered
      :scroll="{ x: 1180 }"
      @resize-column="onResizeColumn"
      :pagination="{
        current: query.page,
        pageSize: query.page_size,
        total,
        showSizeChanger: true,
        showQuickJumper: true,
        showTotal: (t: number) => `共 ${t} 个工单`,
        onChange: onPageChange,
      }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'job_host'">{{ record.job_host_name || '—' }}</template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="statusMeta[record.status as ticketApi.TicketStatus]?.color">
            {{ statusMeta[record.status as ticketApi.TicketStatus]?.text || record.status }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'submitted'">{{ fmtTime(record.submitted_at) }}</template>
        <template v-else-if="column.key === 'action'">
          <a-space>
            <a-button size="small" class="op-btn-cyan" @click="openDetail(record as ticketApi.TicketBrief)">
              <EyeOutlined />
              详情
            </a-button>
            <!-- 审批中：仅创建人可撤回（模板 allow_withdraw=false 时后端拒绝） -->
            <a-popconfirm
              v-if="record.status === 'approving' && isMine(record as ticketApi.TicketBrief)"
              title="确认撤回该工单？"
              @confirm="onCancel(record as ticketApi.TicketBrief)"
            >
              <a-button size="small" class="op-btn-orange"><RollbackOutlined />撤回</a-button>
            </a-popconfirm>
          </a-space>
        </template>
      </template>
    </a-table>

    <!-- 提交向导 + 详情抽屉 -->
    <TicketWizard v-model:open="wizardOpen" @saved="refreshAll" />
    <TicketDetailDrawer v-model:open="detailOpen" :ticket-id="detailId" />
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}
.kw {
  width: 220px;
}
.status-sel {
  width: 120px;
}
.range {
  width: 400px;
}
.create-btn {
  margin-left: auto;
}
</style>
