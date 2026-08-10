<script setup lang="ts">
// 模板管理：V2 工单模板 = 工单全部规则定义（分页列表 + 启停 + 全量配置编辑器 + 详情/版本快照回看 + 删除）
// template:read / template:write 权限点控制
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  ArrowLeftOutlined,
  CodeOutlined,
  CopyOutlined,
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  HistoryOutlined,
  PlusOutlined,
  SearchOutlined,
} from '@ant-design/icons-vue'
import * as jobApi from '@/api/job'
import { listJobHosts } from '@/api/jobHost'
import { listRoleOptions } from '@/api/system'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { useUserStore } from '@/stores/user'
import TemplateEditor from './TemplateEditor.vue'
import TemplateRuleView from './TemplateRuleView.vue'
import { typeOptions, typeText } from './meta'

const userStore = useUserStore()
const canWrite = userStore.hasPerm('template:write')

// ---------- 下拉选项与名称映射（编辑器 + 规则回看共用） ----------
const jobHosts = ref<{ id: number; name: string }[]>([])
const roles = ref<{ id: number; name: string }[]>([])
const jobHostMap = computed(() => Object.fromEntries(jobHosts.value.map((h) => [h.id, h.name])) as Record<number, string>)
const roleMap = computed(() => Object.fromEntries(roles.value.map((r) => [r.id, r.name])) as Record<number, string>)

/** 拉取编辑器/回看所需的关联选项（作业主机/角色；量小一次拉全） */
async function loadOptions() {
  const [hostRes, roleRes] = await Promise.all([
    listJobHosts({ page: 1, page_size: 100, enabled: true }),
    listRoleOptions(),
  ])
  jobHosts.value = hostRes.items.map((h) => ({ id: h.id, name: h.name }))
  roles.value = roleRes.items
}

// ---------- 列表 ----------
const loading = ref(false)
const items = ref<jobApi.TemplateItem[]>([])
const total = ref(0)
const route = useRoute()
const router = useRouter()

const query = reactive({
  page: 1,
  page_size: 20,
  keyword: '',
  type: undefined as string | undefined,
  status: undefined as string | undefined,
})

// 操作列固定右侧，其余列可拖拽调宽（响应式包装使 width 变更生效）
const columns = ref(makeResizable([
  { title: '模板名', dataIndex: 'name', key: 'name', width: 170, ellipsis: true },
  { title: '类型', key: 'type', width: 90 },
  { title: '作业主机', key: 'job_host', width: 130, ellipsis: true },
  { title: '审批', key: 'approval', width: 80 },
  { title: '状态', key: 'status', width: 90 },
  { title: '当前版本', key: 'version', width: 90 },
  { title: '更新时间', key: 'updated', width: 155 },
  { title: '操作', key: 'action', width: canWrite ? 245 : 130, fixed: 'right' as const },
]))

/** 拉取模板列表（列表仅主表规则概要，步骤/节点在详情接口） */
async function loadList() {
  loading.value = true
  try {
    const data = await jobApi.listTemplates({
      page: query.page,
      page_size: query.page_size,
      keyword: query.keyword || undefined,
      type: query.type,
      status: query.status,
    })
    items.value = data.items
    total.value = data.total
    selectedKeys.value = []
  } finally {
    loading.value = false
  }
}

// ---------- 批量删除（被进行中工单引用的失败不影响其余） ----------
const selectedKeys = ref<number[]>([])
const rowSelection = computed(() =>
  canWrite
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
      selectedKeys.value.map((id) => jobApi.deleteTemplate(id)),
    )
    const okCount = results.filter((r) => r.status === 'fulfilled').length
    if (okCount < results.length) message.warning(`已删除 ${okCount} 个，${results.length - okCount} 个失败（被引用等）`)
    else message.success(`已删除 ${okCount} 个模板`)
    refreshAll()
  } finally {
    batchLoading.value = false
  }
}

// ---------- 横幅统计（全局口径，不受筛选影响，page_size=1 只取 total） ----------
const stats = reactive({ all: 0, enabled: 0, disabled: 0 })

async function loadStats() {
  const [all, en, dis] = await Promise.all([
    jobApi.listTemplates({ page: 1, page_size: 1 }),
    jobApi.listTemplates({ page: 1, page_size: 1, status: 'enabled' }),
    jobApi.listTemplates({ page: 1, page_size: 1, status: 'disabled' }),
  ])
  stats.all = all.total
  stats.enabled = en.total
  stats.disabled = dis.total
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

// ---------- 启用/禁用（TPL-03：禁用后不可提交工单，不影响已提交工单） ----------
const statusLoading = ref<number | null>(null)

async function onToggleStatus(row: jobApi.TemplateItem) {
  statusLoading.value = row.id
  try {
    const next = row.status === 'enabled' ? 'disabled' : 'enabled'
    await jobApi.setTemplateStatus(row.id, next)
    message.success(next === 'enabled' ? '已启用' : '已禁用（不再出现在工单中心）')
    refreshAll()
  } finally {
    statusLoading.value = null
  }
}

// ---------- 编辑器（新建/编辑/复制共用；保存后刷新列表） ----------
const editorOpen = ref(false)
const editingId = ref<number | null>(null) // null=新建
const copyFromId = ref<number | null>(null) // 非空=复制模式（编辑器预填源模板配置）

function openCreate() {
  editingId.value = null
  copyFromId.value = null
  editorOpen.value = true
}

function openEdit(row: jobApi.TemplateItem) {
  editingId.value = row.id
  copyFromId.value = null
  editorOpen.value = true
}

// ---------- 复制模板：居中弹窗搜索选源模板 → 编辑器预填为新建（确认保存才真正创建） ----------
const copyOpen = ref(false)
const copySourceId = ref<number | undefined>(undefined)
const copyOptions = ref<{ label: string; value: number }[]>([])
const copySearching = ref(false)
let copySearchTimer: number | null = null

/** 打开复制弹窗：清空上次选择并预加载候选项 */
function openCopy() {
  copySourceId.value = undefined
  copyOpen.value = true
  void searchCopyOptions('')
}

/** 选项远程搜索（300ms 防抖）：服务端 keyword 过滤，避免受当前列表分页限制 */
function onCopySearch(kw: string) {
  if (copySearchTimer) clearTimeout(copySearchTimer)
  copySearchTimer = window.setTimeout(() => searchCopyOptions(kw), 300)
}

async function searchCopyOptions(kw: string) {
  copySearching.value = true
  try {
    const data = await jobApi.listTemplates({ page: 1, page_size: 100, keyword: kw || undefined })
    copyOptions.value = data.items.map((t) => ({
      label: `${t.name}（${jobHostMap.value[t.job_host_id] || '—'} · v${t.current_version}）`,
      value: t.id,
    }))
  } finally {
    copySearching.value = false
  }
}

/** 确认复制：以复制模式打开编辑器（templateId=null 走新建接口，名称自动加「-副本」） */
function onCopyConfirm() {
  if (!copySourceId.value) return
  copyOpen.value = false
  editingId.value = null
  copyFromId.value = copySourceId.value
  editorOpen.value = true
}

/** 删除模板（被进行中工单引用时后端 42201 删除保护，拦截器提示原因） */
async function onDelete(row: jobApi.TemplateItem) {
  try {
    await jobApi.deleteTemplate(row.id)
    message.success('已删除')
    refreshAll()
  } catch {
    /* 42201 删除保护等提示由拦截器统一弹出 */
  }
}

// ---------- 详情抽屉（当前版本全量规则只读回看） ----------
const detailVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<jobApi.TemplateDetail | null>(null)

/** 打开详情：拉取主表规则 + 步骤 + 审批节点 */
async function openDetail(row: jobApi.TemplateItem) {
  detailVisible.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await jobApi.getTemplate(row.id)
  } finally {
    detailLoading.value = false
  }
}

// ---------- 版本历史抽屉（列表 → 点查看切到快照回看，可返回） ----------
const versionsVisible = ref(false)
const versionsLoading = ref(false)
const versionsOwner = ref<jobApi.TemplateItem | null>(null)
const versions = ref<jobApi.TemplateVersionItem[]>([])
const versionDetail = ref<jobApi.TemplateVersionDetail | null>(null) // 非空=回看态

/** 打开版本历史：拉取版本列表（倒序） */
async function openVersions(row: jobApi.TemplateItem) {
  versionsOwner.value = row
  versionDetail.value = null
  versionsVisible.value = true
  versionsLoading.value = true
  try {
    const data = await jobApi.listTemplateVersions(row.id)
    versions.value = data.items
  } finally {
    versionsLoading.value = false
  }
}

/** 回看指定历史版本快照（只读，验收点：旧版本配置仍可查看） */
async function openVersionDetail(v: jobApi.TemplateVersionItem) {
  if (!versionsOwner.value) return
  versionsLoading.value = true
  try {
    versionDetail.value = await jobApi.getTemplateVersion(versionsOwner.value.id, v.version)
  } finally {
    versionsLoading.value = false
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
  loadOptions()
})
</script>

<template>
  <div>
    <!-- 彩色横幅：页面标识 + 全局统计（与 M1 用户管理页同构） -->
    <div class="op-hero op-hero--indigo">
      <div class="op-hero-icon"><CodeOutlined /></div>
      <div>
        <div class="op-hero-title">模板管理</div>
        <div class="op-hero-sub">工单模板 = 规则定义（步骤/策略/审批/通知/权限，规则变更自动升版）</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat"><b>{{ stats.all }}</b><span>总模板</span></div>
        <div class="op-hero-stat"><b>{{ stats.enabled }}</b><span>启用</span></div>
        <div class="op-hero-stat"><b>{{ stats.disabled }}</b><span>禁用</span></div>
      </div>
    </div>

    <!-- 工具栏 -->
    <div class="toolbar">
      <a-input
        v-model:value="query.keyword"
        placeholder="搜索模板名"
        class="kw"
        allow-clear
        @press-enter="onSearch"
      >
        <template #prefix><SearchOutlined /></template>
      </a-input>
      <a-select
        v-model:value="query.type"
        placeholder="模板类型"
        class="type-sel"
        allow-clear
        :options="typeOptions"
        @change="onSearch"
      />
      <a-select
        v-model:value="query.status"
        placeholder="状态"
        class="status-sel"
        allow-clear
        :options="[
          { label: '启用', value: 'enabled' },
          { label: '禁用', value: 'disabled' },
        ]"
        @change="onSearch"
      />
      <!-- 右侧操作组：批量删除 + 新建整体钉右 -->
      <div v-if="canWrite" class="toolbar-actions">
        <a-popconfirm
          :title="`确认删除选中的 ${selectedKeys.length} 个模板？`"
          :disabled="!selectedKeys.length"
          @confirm="onBatchDelete"
        >
          <a-button danger :disabled="!selectedKeys.length" :loading="batchLoading">
            <DeleteOutlined />批量删除{{ selectedKeys.length ? `（${selectedKeys.length}）` : '' }}
          </a-button>
        </a-popconfirm>
        <a-button class="op-btn-cyan" @click="openCopy"><CopyOutlined />复制模板</a-button>
        <a-button type="primary" @click="openCreate"><PlusOutlined />新建模板</a-button>
      </div>
    </div>

    <!-- 列表：列间分割线 + 选择框/操作列固定 + 其余列可拖拽调宽 -->
    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      row-key="id"
      bordered
      :scroll="{ x: 1150 }"
      :row-selection="rowSelection"
      @resize-column="onResizeColumn"
      :pagination="{
        current: query.page,
        pageSize: query.page_size,
        total,
        showSizeChanger: true,
        showQuickJumper: true,
        showTotal: (t: number) => `共 ${t} 个模板`,
        onChange: onPageChange,
      }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'type'">
          <a-tag :color="typeText[record.type as jobApi.TemplateType]?.color">
            {{ typeText[record.type as jobApi.TemplateType]?.text || record.type }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'job_host'">{{ jobHostMap[record.job_host_id] || `#${record.job_host_id}` }}</template>
        <template v-else-if="column.key === 'approval'">
          <a-tag :color="record.approval_enabled ? 'gold' : 'default'">
            {{ record.approval_enabled ? '需审批' : '免审' }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'status'">
          <a-switch
            v-if="canWrite"
            :checked="record.status === 'enabled'"
            checked-children="启用"
            un-checked-children="禁用"
            :loading="statusLoading === record.id"
            @change="onToggleStatus(record as jobApi.TemplateItem)"
          />
          <a-tag v-else :color="record.status === 'enabled' ? 'green' : 'default'">
            {{ record.status === 'enabled' ? '启用' : '禁用' }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'version'">
          <a-tag color="cyan">v{{ record.current_version }}</a-tag>
        </template>
        <template v-else-if="column.key === 'updated'">
          {{ record.updated_at ? new Date(record.updated_at).toLocaleString() : '—' }}
        </template>
        <template v-else-if="column.key === 'action'">
          <a-space>
            <a-button size="small" class="op-btn-cyan" @click="openDetail(record as jobApi.TemplateItem)"><EyeOutlined />详情</a-button>
            <a-button size="small" class="op-btn-purple" @click="openVersions(record as jobApi.TemplateItem)"><HistoryOutlined />版本</a-button>
            <template v-if="canWrite">
              <a-button size="small" class="op-btn-blue" @click="openEdit(record as jobApi.TemplateItem)"><EditOutlined />编辑</a-button>
              <a-popconfirm title="确认删除该模板？（版本历史一并删除）" @confirm="onDelete(record as jobApi.TemplateItem)">
                <a-button size="small" danger><DeleteOutlined />删除</a-button>
              </a-popconfirm>
            </template>
          </a-space>
        </template>
      </template>
    </a-table>

    <!-- 全量配置编辑器（新建/编辑/复制共用） -->
    <TemplateEditor
      v-model:open="editorOpen"
      :template-id="editingId"
      :copy-from-id="copyFromId"
      :job-hosts="jobHosts"
      :roles="roles"
      @saved="refreshAll"
    />

    <!-- 复制模板：居中搜索选择源模板 -->
    <a-modal
      v-model:open="copyOpen"
      title="复制模板"
      :width="440"
      ok-text="下一步"
      :ok-button-props="{ disabled: !copySourceId }"
      @ok="onCopyConfirm"
    >
      <div class="copy-tip">选择要复制的源模板，将预填其全部规则配置，确认保存后才创建新模板</div>
      <a-select
        v-model:value="copySourceId"
        show-search
        placeholder="搜索模板名"
        class="copy-select"
        :filter-option="false"
        :options="copyOptions"
        :loading="copySearching"
        @search="onCopySearch"
      />
    </a-modal>

    <!-- 详情抽屉：当前版本全量规则只读回看 -->
    <a-drawer v-model:open="detailVisible" :title="detail?.name || '模板详情'" :width="760">
      <a-spin :spinning="detailLoading">
        <template v-if="detail">
          <div class="head-line">
            <a-tag :color="typeText[detail.type]?.color">{{ typeText[detail.type]?.text || detail.type }}</a-tag>
            <a-tag :color="detail.status === 'enabled' ? 'green' : 'default'">
              {{ detail.status === 'enabled' ? '启用' : '禁用' }}
            </a-tag>
            <a-tag color="cyan">当前版本 v{{ detail.current_version }}</a-tag>
          </div>
          <TemplateRuleView :rule="detail" :role-map="roleMap" :job-host-name="jobHostMap[detail.job_host_id] || ''" />
        </template>
      </a-spin>
    </a-drawer>

    <!-- 版本历史抽屉：版本列表 ⇄ 历史版本快照只读回看 -->
    <a-drawer
      v-model:open="versionsVisible"
      :title="versionDetail ? `${versionsOwner?.name} · v${versionDetail.version}` : `版本历史 · ${versionsOwner?.name || ''}`"
      :width="760"
    >
      <a-spin :spinning="versionsLoading">
        <!-- 回看态：全量配置快照只读 -->
        <template v-if="versionDetail">
          <a-button size="small" class="back-btn" @click="versionDetail = null">
            <ArrowLeftOutlined />返回版本列表
          </a-button>
          <a-descriptions :column="{ xs: 1, sm: 2 }" bordered size="small" class="version-head op-desc-table">
            <a-descriptions-item label="版本"><a-tag color="cyan">v{{ versionDetail.version }}</a-tag></a-descriptions-item>
            <a-descriptions-item label="创建时间">
              {{ versionDetail.created_at ? new Date(versionDetail.created_at).toLocaleString() : '—' }}
            </a-descriptions-item>
            <a-descriptions-item label="版本说明" :span="2">{{ versionDetail.changelog || '—' }}</a-descriptions-item>
          </a-descriptions>
          <TemplateRuleView
            :rule="versionDetail.snapshot"
            :role-map="roleMap"
            :job-host-name="jobHostMap[versionDetail.snapshot.job_host_id] || ''"
          />
        </template>

        <!-- 列表态：全部历史版本（倒序），当前版本高亮标记 -->
        <a-table
          v-else
          bordered
          size="small"
          :pagination="false"
          row-key="id"
          :columns="[
            { title: '版本', key: 'version', width: 90 },
            { title: '版本说明', key: 'changelog' },
            { title: '创建时间', key: 'created', width: 160 },
            { title: '操作', key: 'action', width: 80 },
          ]"
          :data-source="versions"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'version'">
              <a-tag :color="record.version === versionsOwner?.current_version ? 'green' : 'cyan'">
                v{{ record.version }}{{ record.version === versionsOwner?.current_version ? ' 当前' : '' }}
              </a-tag>
            </template>
            <template v-else-if="column.key === 'changelog'">{{ record.changelog || '—' }}</template>
            <template v-else-if="column.key === 'created'">
              {{ record.created_at ? new Date(record.created_at).toLocaleString() : '—' }}
            </template>
            <template v-else-if="column.key === 'action'">
              <a-button size="small" class="op-btn-cyan" @click="openVersionDetail(record as jobApi.TemplateVersionItem)">
                <EyeOutlined />查看
              </a-button>
            </template>
          </template>
        </a-table>
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
.type-sel {
  width: 130px;
}
.status-sel {
  width: 110px;
}
.toolbar-actions {
  margin-left: auto;
  display: flex;
  gap: 10px;
}
/* 复制模板弹窗 */
.copy-tip {
  font-size: 12px;
  color: var(--text-3);
  margin-bottom: 10px;
}
.copy-select {
  width: 100%;
}
.head-line {
  display: flex;
  gap: 4px;
  margin-bottom: 14px;
}
.version-head {
  margin-bottom: 14px;
}
.back-btn {
  margin-bottom: 14px;
}
</style>
