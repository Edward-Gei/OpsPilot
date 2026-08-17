<script setup lang="ts">
// 应用管理：分页列表 + 创建/编辑（仅生产环境主机穿梭框全量替换关联）+ 删除/批量删除 + 详情主机清单（cmdb:read / cmdb:write）
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  AppstoreAddOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  EyeOutlined,
  PlusOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue'
import * as cmdbApi from '@/api/cmdb'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const canWrite = userStore.hasPerm('cmdb:write')
const canDelete = userStore.hasPerm('cmdb:delete')

// 部署方式彩色标签（与 M1 列表标签风格一致）
const deployText: Record<string, { text: string; color: string }> = {
  shell: { text: 'Shell', color: 'orange' },
  docker: { text: 'Docker', color: 'geekblue' },
  k8s: { text: 'K8s', color: 'purple' },
}
const deployOptions = Object.entries(deployText).map(([value, v]) => ({ label: v.text, value }))

// ---------- 列表 ----------
const loading = ref(false)
const items = ref<cmdbApi.AppItem[]>([])
const total = ref(0)
const route = useRoute()
const router = useRouter()

const query = reactive({
  page: 1,
  page_size: 20,
  keyword: '',
  deploy_type: undefined as string | undefined,
  sort_by: undefined as 'language' | 'created_at' | undefined,
  sort_order: undefined as 'asc' | 'desc' | undefined,
})

// 列宽尽量均匀；操作列固定右侧，其余列可拖拽调宽（响应式包装使 width 变更生效）
const columns = ref(makeResizable([
  { title: '应用名', dataIndex: 'name', key: 'name', width: 140, ellipsis: true },
  { title: '语言', dataIndex: 'language', key: 'language', width: 100, ellipsis: true, sorter: true },
  { title: '部署方式', key: 'deploy_type', width: 100 },
  { title: '关联主机', key: 'host_count', width: 95 },
  { title: '主机 IP', key: 'host_ips', width: 140 },
  { title: '说明', key: 'description', width: 180, ellipsis: true },
  { title: '创建时间', dataIndex: 'created_at', key: 'created', width: 155, sorter: true },
  { title: '操作', key: 'action', width: canWrite || canDelete ? 190 : 90, fixed: 'right' as const },
]))

/** 拉取应用列表（items 含关联主机数） */
async function loadList() {
  loading.value = true
  try {
    const data = await cmdbApi.listApps({
      page: query.page,
      page_size: query.page_size,
      keyword: query.keyword || undefined,
      deploy_type: query.deploy_type,
      sort_by: query.sort_by,
      sort_order: query.sort_order,
    })
    items.value = data.items
    total.value = data.total
    selectedKeys.value = []
  } finally {
    loading.value = false
  }
}

// ---------- 批量删除（多选；单个删除保护失败不影响其余） ----------
const selectedKeys = ref<number[]>([])
const rowSelection = computed(() =>
  canWrite || canDelete
    ? {
        fixed: true, // 选择框列固定左侧
        selectedRowKeys: selectedKeys.value,
        onChange: (keys: (string | number)[]) => (selectedKeys.value = keys as number[]),
      }
    : undefined,
)
const batchLoading = ref(false)

/** 批量删除：逐个调删除接口，被引用（42201 删除保护）的失败计数，原因由拦截器逐条提示 */
async function onBatchDelete() {
  if (!selectedKeys.value.length) return
  batchLoading.value = true
  try {
    const results = await Promise.allSettled(
      selectedKeys.value.map((id) => cmdbApi.deleteApp(id)),
    )
    const okCount = results.filter((r) => r.status === 'fulfilled').length
    if (okCount < results.length) message.warning(`已删除 ${okCount} 个，${results.length - okCount} 个失败（被引用等）`)
    else message.success(`已删除 ${okCount} 个应用`)
    refreshAll()
  } finally {
    batchLoading.value = false
  }
}

// ---------- 横幅统计（全局口径，不受筛选影响，page_size=1 只取 total） ----------
const stats = reactive({ all: 0, docker: 0, k8s: 0 })

async function loadStats() {
  const [all, docker, k8s] = await Promise.all([
    cmdbApi.listApps({ page: 1, page_size: 1 }),
    cmdbApi.listApps({ page: 1, page_size: 1, deploy_type: 'docker' }),
    cmdbApi.listApps({ page: 1, page_size: 1, deploy_type: 'k8s' }),
  ])
  stats.all = all.total
  stats.docker = docker.total
  stats.k8s = k8s.total
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

function onTableChange(
  pagination: { current?: number; pageSize?: number },
  _: unknown,
  sorter: { field?: string; order?: 'ascend' | 'descend' | null } | { field?: string; order?: 'ascend' | 'descend' | null }[],
) {
  const current = Array.isArray(sorter) ? sorter[0] : sorter
  const sortBy = current.order && (current.field === 'language' || current.field === 'created_at')
    ? current.field
    : undefined
  const sortOrder = current.order === 'ascend' ? 'asc' : current.order === 'descend' ? 'desc' : undefined
  const sortChanged = query.sort_by !== sortBy || query.sort_order !== sortOrder
  query.sort_by = sortBy
  query.sort_order = sortOrder
  query.page = sortChanged ? 1 : pagination.current || 1
  query.page_size = pagination.pageSize || query.page_size
  loadList()
}

async function onExport() {
  try {
    await cmdbApi.exportApps({
      keyword: query.keyword || undefined,
      deploy_type: query.deploy_type,
    })
  } catch {
    message.error('导出失败')
  }
}

// ---------- 主机穿梭框数据源（page_size 上限 100，分页拉全量） ----------
interface TransferItem {
  key: string
  title: string
  description: string
}
const allHosts = ref<TransferItem[]>([])
const hostsLoading = ref(false)

/** 分页拉取全部生产环境主机作为穿梭框数据源（应用仅可关联生产主机，上限 20 页兜底） */
async function loadAllHosts() {
  hostsLoading.value = true
  try {
    const result: TransferItem[] = []
    let page = 1
    for (;;) {
      const data = await cmdbApi.listHosts({ page, page_size: 100, environment: 'prod' })
      result.push(
        ...data.items.map((h) => ({
          key: String(h.id),
          title: `${h.hostname}（${h.ip}）`,
          description: h.ip,
        })),
      )
      if (result.length >= data.total || page >= 20) break
      page += 1
    }
    allHosts.value = result
  } finally {
    hostsLoading.value = false
  }
}

// ---------- 创建 / 编辑 ----------
const editVisible = ref(false)
const editLoading = ref(false)
const editing = ref<cmdbApi.AppItem | null>(null) // null=创建
const editForm = reactive({
  name: '',
  language: '',
  deploy_type: 'shell',
  description: '',
})
// a-transfer 的 targetKeys 为字符串，提交时转回 number[]
const targetKeys = ref<string[]>([])

function openCreate() {
  editing.value = null
  Object.assign(editForm, { name: '', language: '', deploy_type: 'shell', description: '' })
  targetKeys.value = []
  editVisible.value = true
  loadAllHosts()
}

/** 编辑：先查详情回填已关联主机（穿梭框右侧） */
async function openEdit(row: cmdbApi.AppItem) {
  editing.value = row
  Object.assign(editForm, {
    name: row.name,
    language: row.language || '',
    deploy_type: row.deploy_type,
    description: row.description || '',
  })
  editVisible.value = true
  loadAllHosts()
  const detail = await cmdbApi.getApp(row.id)
  targetKeys.value = detail.hosts.map((h) => String(h.id))
}

/** 提交创建/编辑（host_ids 全量替换关联；名称冲突 40901 由拦截器提示） */
async function onSubmitEdit() {
  if (!editForm.name) {
    message.warning('请填写应用名')
    return
  }
  editLoading.value = true
  try {
    const payload = {
      name: editForm.name,
      language: editForm.language || undefined,
      deploy_type: editForm.deploy_type,
      description: editForm.description || undefined,
      host_ids: targetKeys.value.map(Number),
    }
    if (editing.value) {
      await cmdbApi.updateApp(editing.value.id, payload)
      message.success('已保存')
    } else {
      await cmdbApi.createApp(payload)
      message.success('应用已创建')
    }
    editVisible.value = false
    refreshAll()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    editLoading.value = false
  }
}

/** 删除应用（被进行中工单引用时后端 42201 删除保护，拦截器提示原因） */
async function onDelete(row: cmdbApi.AppItem) {
  try {
    await cmdbApi.deleteApp(row.id)
    message.success('已删除')
    refreshAll()
  } catch {
    /* 42201 删除保护等提示由拦截器统一弹出 */
  }
}

// ---------- 详情抽屉（关联主机清单） ----------
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<cmdbApi.AppDetail | null>(null)

/** 打开详情：拉取应用完整信息 + 关联主机清单 */
async function openDetail(row: cmdbApi.AppItem) {
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await cmdbApi.getApp(row.id)
  } finally {
    detailLoading.value = false
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
})
</script>

<template>
  <div>
    <!-- 彩色横幅：页面标识 + 全局统计（与 M1 用户管理页同构） -->
    <div class="op-hero op-hero--amber">
      <div class="op-hero-icon"><AppstoreAddOutlined /></div>
      <div>
        <div class="op-hero-title">应用管理</div>
        <div class="op-hero-sub">应用台账维护与主机关联拓扑（穿梭框全量替换）</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat"><b>{{ stats.all }}</b><span>总应用</span></div>
        <div class="op-hero-stat"><b>{{ stats.docker }}</b><span>Docker</span></div>
        <div class="op-hero-stat"><b>{{ stats.k8s }}</b><span>K8s</span></div>
      </div>
    </div>

    <!-- 工具栏 -->
    <div class="toolbar">
      <a-input
        v-model:value="query.keyword"
        placeholder="搜索应用名"
        class="kw"
        allow-clear
        @press-enter="onSearch"
      >
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-select
        v-model:value="query.deploy_type"
        placeholder="部署方式"
        class="deploy-sel"
        allow-clear
        :options="deployOptions"
        @change="onSearch"
      />
      <!-- 右侧操作组：批量删除 + 新建整体钉右 -->
      <div class="toolbar-actions">
        <a-popconfirm
          v-if="canDelete"
          :title="`确认删除选中的 ${selectedKeys.length} 个应用？`"
          :disabled="!selectedKeys.length"
          @confirm="onBatchDelete"
        >
          <a-button danger :disabled="!selectedKeys.length" :loading="batchLoading">
            <DeleteOutlined />批量删除{{ selectedKeys.length ? `（${selectedKeys.length}）` : '' }}
          </a-button>
        </a-popconfirm>
        <a-button @click="onExport"><DownloadOutlined />导出</a-button>
        <a-button v-if="canWrite" type="primary" @click="openCreate"><PlusOutlined />新建应用</a-button>
      </div>
    </div>

    <!-- 列表：列间分割线 + 选择框/操作列固定 + 其余列可拖拽调宽 -->
    <a-table
      :columns="columns"
      :data-source="items"
      :show-sorter-tooltip="false"
      :loading="loading"
      row-key="id"
      bordered
      :scroll="{ x: 1160 }"
      :row-selection="rowSelection"
      @resize-column="onResizeColumn"
      @change="onTableChange"
      :pagination="{
        current: query.page,
        pageSize: query.page_size,
        total,
        showSizeChanger: true,
        showQuickJumper: true,
        showTotal: (t: number) => `共 ${t} 个应用`,
      }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'language'">{{ record.language || '—' }}</template>
        <template v-else-if="column.key === 'deploy_type'">
          <a-tag :color="deployText[record.deploy_type]?.color">
            {{ deployText[record.deploy_type]?.text || record.deploy_type }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'host_count'">
          <a-tag color="cyan">{{ record.host_count }} 台</a-tag>
        </template>
        <template v-else-if="column.key === 'host_ips'">
          <template v-if="record.host_ips?.length">
            <div v-for="ip in record.host_ips" :key="ip" class="ip-line">{{ ip }}</div>
          </template>
          <template v-else>—</template>
        </template>
        <template v-else-if="column.key === 'description'">{{ record.description || '—' }}</template>
        <template v-else-if="column.key === 'created'">
          {{ record.created_at ? new Date(record.created_at).toLocaleString() : '—' }}
        </template>
        <template v-else-if="column.key === 'action'">
          <a-space>
            <a-button size="small" class="op-btn-cyan" @click="openDetail(record as cmdbApi.AppItem)"><EyeOutlined />详情</a-button>
            <template v-if="canWrite || canDelete">
              <a-button size="small" class="op-btn-blue" @click="openEdit(record as cmdbApi.AppItem)"><EditOutlined />编辑</a-button>
              <a-popconfirm v-if="canDelete" title="确认删除该应用？" @confirm="onDelete(record as cmdbApi.AppItem)">
                <a-button size="small" danger><DeleteOutlined />删除</a-button>
              </a-popconfirm>
            </template>
          </a-space>
        </template>
      </template>
    </a-table>

    <!-- 创建/编辑弹窗：含主机穿梭框 -->
    <a-modal
      v-model:open="editVisible"
      :title="editing ? '编辑应用' : '新建应用'"
      :confirm-loading="editLoading"
      :width="720"
      @ok="onSubmitEdit"
    >
      <a-form layout="vertical">
        <a-form-item label="应用名" required>
          <a-input v-model:value="editForm.name" placeholder="如 订单服务" />
        </a-form-item>
        <div class="form-row">
          <a-form-item label="开发语言" class="form-col">
            <a-input v-model:value="editForm.language" placeholder="如 Java / Go / Python" />
          </a-form-item>
          <a-form-item label="部署方式" required class="form-col">
            <a-select v-model:value="editForm.deploy_type" :options="deployOptions" />
          </a-form-item>
        </div>
        <a-form-item label="说明">
          <a-textarea v-model:value="editForm.description" :rows="2" />
        </a-form-item>
        <a-form-item label="关联主机（仅可选生产环境主机，右侧为已关联，保存时全量替换）">
          <a-spin :spinning="hostsLoading">
            <a-transfer
              v-model:target-keys="targetKeys"
              :data-source="allHosts"
              :render="(item: TransferItem) => item.title"
              :titles="['可选生产主机', '已关联']"
              :list-style="{ width: '306px', height: '320px' }"
              show-search
              :filter-option="
                (input: string, item: TransferItem) => item.title.toLowerCase().includes(input.toLowerCase())
              "
            />
          </a-spin>
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 详情抽屉：应用信息 + 关联主机清单 -->
    <a-drawer v-model:open="detailVisible" :title="detail?.name || '应用详情'" :width="650">
      <a-spin :spinning="detailLoading">
        <template v-if="detail">
          <a-descriptions :column="1" bordered size="small" class="op-desc-table">
            <a-descriptions-item label="应用名">{{ detail.name }}</a-descriptions-item>
            <a-descriptions-item label="开发语言">{{ detail.language || '—' }}</a-descriptions-item>
            <a-descriptions-item label="部署方式">
              <a-tag :color="deployText[detail.deploy_type]?.color">
                {{ deployText[detail.deploy_type]?.text || detail.deploy_type }}
              </a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="说明">{{ detail.description || '—' }}</a-descriptions-item>
            <a-descriptions-item label="创建时间">
              {{ detail.created_at ? new Date(detail.created_at).toLocaleString() : '—' }}
            </a-descriptions-item>
          </a-descriptions>

          <div class="detail-hosts-title">关联主机（{{ detail.hosts.length }}）</div>
          <a-empty v-if="!detail.hosts.length" description="暂无关联主机" />
          <a-table
            v-else
            bordered
            :columns="[
              { title: '主机名', dataIndex: 'hostname' },
              { title: 'IP 地址', dataIndex: 'ip', width: 130 },
              { title: '环境', dataIndex: 'environment', width: 80 },
            ]"
            :data-source="detail.hosts"
            row-key="id"
            size="small"
            :pagination="false"
          />
        </template>
      </a-spin>
    </a-drawer>
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
.deploy-sel {
  width: 120px;
}
.toolbar-actions {
  margin-left: auto;
  display: flex;
  gap: 10px;
}
.form-row {
  display: flex;
  gap: 16px;
}
.form-col {
  flex: 1;
}
.detail-hosts-title {
  font-weight: 600;
  margin: 18px 0 10px;
}
.ip-line {
  line-height: 1.7;
  font-variant-numeric: tabular-nums;
}
</style>
