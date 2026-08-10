<script setup lang="ts">
// 用户管理：分页列表 + 创建/编辑/禁用/批量禁用/重置密码 + MFA 管控（user:read / user:write / user:mfa）
import { computed, onMounted, reactive, ref } from 'vue'
import { Modal, message } from 'ant-design-vue'
import {
  CheckOutlined,
  DownOutlined,
  EditOutlined,
  KeyOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  StopOutlined,
  TeamOutlined,
} from '@ant-design/icons-vue'
import * as sysApi from '@/api/system'
import { makeResizable, onResizeColumn } from '@/utils/table'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const canWrite = userStore.hasPerm('user:write')
const canMfa = userStore.hasPerm('user:mfa') // 仅管理员：MFA 开/关/重置

// ---------- 列表 ----------
const loading = ref(false)
const items = ref<sysApi.UserItem[]>([])
const total = ref(0)
const query = reactive({ page: 1, page_size: 20, keyword: '', status: undefined as string | undefined })

const statusText: Record<string, { text: string; color: string }> = {
  active: { text: '正常', color: 'success' },
  disabled: { text: '禁用', color: 'default' },
}
// 来源/角色彩色标签，提升列表辨识度
const sourceTag: Record<string, { text: string; color: string }> = {
  local: { text: '本地', color: 'geekblue' },
  ldap: { text: 'LDAP', color: 'purple' },
  oidc: { text: 'OIDC', color: 'cyan' },
}
const roleColors = ['blue', 'purple', 'cyan', 'magenta', 'orange', 'green']

// 操作列固定右侧，其余列可拖拽调宽（响应式包装使 width 变更生效）
const columns = ref(makeResizable([
  { title: '用户名', dataIndex: 'username', key: 'username', width: 120, ellipsis: true },
  { title: '显示名', dataIndex: 'display_name', key: 'display_name', width: 120, ellipsis: true },
  { title: '角色', key: 'roles', width: 160 },
  { title: '来源', key: 'source', width: 80 },
  { title: '状态', key: 'status', width: 90 },
  { title: 'MFA', key: 'mfa', width: 90 },
  { title: '创建时间', key: 'created', width: 165 },
  { title: '最近登录', key: 'last_login', width: 165 },
  ...(canWrite || canMfa
    ? [{ title: '操作', key: 'action', width: 300, fixed: 'right' as const }]
    : []),
]))

/** 拉取用户列表 */
async function loadList() {
  loading.value = true
  try {
    const data = await sysApi.listUsers({
      page: query.page,
      page_size: query.page_size,
      keyword: query.keyword || undefined,
      status: query.status,
    })
    items.value = data.items
    total.value = data.total
    selectedKeys.value = []
  } finally {
    loading.value = false
  }
}

// ---------- 横幅统计（不受搜索过滤影响，page_size=1 只取 total） ----------
const stats = reactive({ all: 0, disabled: 0 })

async function loadStats() {
  const [all, disabled] = await Promise.all([
    sysApi.listUsers({ page: 1, page_size: 1 }),
    sysApi.listUsers({ page: 1, page_size: 1, status: 'disabled' }),
  ])
  stats.all = all.total
  stats.disabled = disabled.total
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

// ---------- 批量禁用（多选，排除自己与已禁用账号） ----------
const selectedKeys = ref<number[]>([])
const rowSelection = computed(() =>
  canWrite
    ? {
        fixed: true, // 选择框列固定左侧
        selectedRowKeys: selectedKeys.value,
        onChange: (keys: (string | number)[]) => (selectedKeys.value = keys as number[]),
        getCheckboxProps: (record: sysApi.UserItem) => ({
          // 自己与已禁用账号不参与批量禁用
          disabled: record.id === userStore.userInfo?.id || record.status === 'disabled',
        }),
      }
    : undefined,
)

const batchLoading = ref(false)

async function onBatchDisable() {
  if (!selectedKeys.value.length) return
  batchLoading.value = true
  try {
    const results = await Promise.allSettled(
      selectedKeys.value.map((id) => sysApi.updateUser(id, { status: 'disabled' })),
    )
    const okCount = results.filter((r) => r.status === 'fulfilled').length
    if (okCount < results.length) message.warning(`已禁用 ${okCount} 个，${results.length - okCount} 个失败`)
    else message.success(`已禁用 ${okCount} 个用户`)
    refreshAll()
  } finally {
    batchLoading.value = false
  }
}

// ---------- 角色选项（创建/编辑弹窗共用） ----------
const roleOptions = ref<{ label: string; value: number }[]>([])

async function loadRoles() {
  const data = await sysApi.listRoles()
  roleOptions.value = data.items.map((r) => ({ label: r.name, value: r.id }))
}

// ---------- 创建 / 编辑 ----------
const editVisible = ref(false)
const editLoading = ref(false)
const editing = ref<sysApi.UserItem | null>(null) // null=创建
const editForm = reactive({
  username: '',
  password: '',
  display_name: '',
  email: '',
  role_ids: [] as number[],
})

function openCreate() {
  editing.value = null
  Object.assign(editForm, { username: '', password: '', display_name: '', email: '', role_ids: [] })
  editVisible.value = true
}

function openEdit(row: sysApi.UserItem) {
  editing.value = row
  Object.assign(editForm, {
    username: row.username,
    password: '',
    display_name: row.display_name,
    email: row.email || '',
    role_ids: row.roles.map((r) => r.id),
  })
  editVisible.value = true
}

/** 提交创建/编辑（创建的初始密码首登强制修改） */
async function onSubmitEdit() {
  if (!editForm.username || !editForm.display_name || (!editing.value && !editForm.password)) {
    message.warning('请填写完整')
    return
  }
  editLoading.value = true
  try {
    if (editing.value) {
      await sysApi.updateUser(editing.value.id, {
        display_name: editForm.display_name,
        email: editForm.email || undefined,
        role_ids: editForm.role_ids,
      })
      message.success('已保存')
    } else {
      await sysApi.createUser({
        username: editForm.username,
        password: editForm.password,
        display_name: editForm.display_name,
        email: editForm.email || undefined,
        role_ids: editForm.role_ids,
      })
      message.success('用户已创建，首次登录须修改初始密码')
    }
    editVisible.value = false
    refreshAll()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    editLoading.value = false
  }
}

/** 启用/禁用切换 */
async function onToggleStatus(row: sysApi.UserItem) {
  const next = row.status === 'active' ? 'disabled' : 'active'
  try {
    await sysApi.updateUser(row.id, { status: next })
    message.success(next === 'disabled' ? '已禁用' : '已启用')
    refreshAll()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  }
}

// ---------- MFA 管控（仅 user:mfa 权限，默认仅管理员） ----------
const mfaActionText: Record<string, string> = { enable: '开启', disable: '关闭', reset: '重置' }

/** a-menu 点击事件参数（模板内联箭头函数不支持对象字面量类型标注，故用别名） */
type MenuClickInfo = { key: string | number }

/** MFA 下拉菜单动作：重置需二次确认（清除密钥不可逆） */
function onMfaAction(row: sysApi.UserItem, key: string | number) {
  const action = String(key) as 'enable' | 'disable' | 'reset'
  const doIt = async () => {
    try {
      await sysApi.manageUserMfa(row.id, action)
      message.success(`已${mfaActionText[action]} ${row.username} 的 MFA`)
      loadList()
    } catch {
      /* 错误提示由拦截器统一弹出 */
    }
  }
  if (action === 'reset') {
    Modal.confirm({
      title: `重置 ${row.username} 的 MFA？`,
      content: '将清除已绑定的认证器密钥，该用户需重新扫码绑定（适用于换手机/丢失认证器）。',
      okText: '确认重置',
      okType: 'danger',
      onOk: doIt,
    })
  } else {
    doIt()
  }
}

// ---------- 重置密码 ----------
const resetVisible = ref(false)
const resetLoading = ref(false)
const resetTarget = ref<sysApi.UserItem | null>(null)
const resetPassword = ref('')

function openReset(row: sysApi.UserItem) {
  resetTarget.value = row
  resetPassword.value = ''
  resetVisible.value = true
}

async function onSubmitReset() {
  if (!resetPassword.value) {
    message.warning('请输入新密码')
    return
  }
  resetLoading.value = true
  try {
    await sysApi.resetUserPassword(resetTarget.value!.id, resetPassword.value)
    message.success('已重置，该用户下次登录须修改密码')
    resetVisible.value = false
    loadList()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    resetLoading.value = false
  }
}

onMounted(() => {
  refreshAll()
  if (canWrite) loadRoles()
})
</script>

<template>
  <div>
    <!-- 彩色横幅：页面标识 + 全局统计 -->
    <div class="op-hero op-hero--blue">
      <div class="op-hero-icon"><TeamOutlined /></div>
      <div>
        <div class="op-hero-title">用户管理</div>
        <div class="op-hero-sub">账号生命周期、角色分配与 MFA 安全管控</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat"><b>{{ stats.all }}</b><span>总用户</span></div>
        <div class="op-hero-stat"><b>{{ stats.all - stats.disabled }}</b><span>正常</span></div>
        <div class="op-hero-stat"><b>{{ stats.disabled }}</b><span>禁用</span></div>
      </div>
    </div>

    <!-- 工具栏 -->
    <div class="toolbar">
      <a-input
        v-model:value="query.keyword"
        placeholder="搜索用户名 / 显示名"
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
        :options="[
          { label: '正常', value: 'active' },
          { label: '禁用', value: 'disabled' },
        ]"
        @change="onSearch"
      />
      <template v-if="canWrite">
        <a-popconfirm
          :title="`确认禁用选中的 ${selectedKeys.length} 个用户？`"
          :disabled="!selectedKeys.length"
          @confirm="onBatchDisable"
        >
          <a-button danger :disabled="!selectedKeys.length" :loading="batchLoading" class="batch-btn">
            <StopOutlined />批量禁用{{ selectedKeys.length ? `（${selectedKeys.length}）` : '' }}
          </a-button>
        </a-popconfirm>
        <a-button type="primary" @click="openCreate"><PlusOutlined />新建用户</a-button>
      </template>
    </div>

    <!-- 列表：列间分割线 + 选择框/操作列固定 + 其余列可拖拽调宽 -->
    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      :row-selection="rowSelection"
      row-key="id"
      bordered
      :scroll="{ x: 1350 }"
      @resize-column="onResizeColumn"
      :pagination="{
        current: query.page,
        pageSize: query.page_size,
        total,
        showSizeChanger: true,
        showQuickJumper: true,
        showTotal: (t: number) => `共 ${t} 个用户`,
        onChange: onPageChange,
      }"
    >
      <template #bodyCell="{ column, record, index }">
        <template v-if="column.key === 'roles'">
          <a-tag v-for="(r, i) in record.roles" :key="r.id" :color="roleColors[(index + i) % roleColors.length]">
            {{ r.name }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'source'">
          <a-tag :color="sourceTag[record.source]?.color">
            {{ sourceTag[record.source]?.text || record.source }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="statusText[record.status]?.color">
            {{ statusText[record.status]?.text || record.status }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'mfa'">
          <!-- 三态：已开启 / 已暂停（有密钥但被关闭）/ 未绑定 -->
          <a-tag v-if="record.mfa_enabled" color="success">已开启</a-tag>
          <a-tag v-else-if="record.mfa_bound" color="warning">已暂停</a-tag>
          <a-tag v-else color="default">未绑定</a-tag>
        </template>
        <template v-else-if="column.key === 'created'">
          {{ record.created_at ? new Date(record.created_at).toLocaleString() : '—' }}
        </template>
        <template v-else-if="column.key === 'last_login'">
          {{ record.last_login_at ? new Date(record.last_login_at).toLocaleString() : '—' }}
        </template>
        <template v-else-if="column.key === 'action'">
          <a-space>
            <template v-if="canWrite">
              <a-button size="small" class="op-btn-blue" @click="openEdit(record as sysApi.UserItem)"><EditOutlined />编辑</a-button>
              <a-button
                v-if="record.source === 'local'"
                size="small"
                class="op-btn-purple"
                @click="openReset(record as sysApi.UserItem)"
              >
                <KeyOutlined />
                重置密码
              </a-button>
              <a-popconfirm
                :title="record.status === 'active' ? '确认禁用该用户？' : '确认启用该用户？'"
                @confirm="onToggleStatus(record as sysApi.UserItem)"
              >
                <a-button
                  size="small"
                  :danger="record.status === 'active'"
                  :class="record.status === 'active' ? '' : 'op-btn-green'"
                >
                  <StopOutlined v-if="record.status === 'active'" /><CheckOutlined v-else />
                  {{ record.status === 'active' ? '禁用' : '启用' }}
                </a-button>
              </a-popconfirm>
            </template>
            <a-dropdown v-if="canMfa">
              <a-button size="small" class="op-btn-orange"><SafetyCertificateOutlined />MFA<DownOutlined /></a-button>
              <template #overlay>
                <a-menu @click="(info: MenuClickInfo) => onMfaAction(record as sysApi.UserItem, info.key)">
                  <a-menu-item key="enable" :disabled="record.mfa_enabled || !record.mfa_bound">
                    开启验证
                  </a-menu-item>
                  <a-menu-item key="disable" :disabled="!record.mfa_enabled">关闭验证</a-menu-item>
                  <a-menu-item key="reset" danger :disabled="!record.mfa_bound">重置绑定</a-menu-item>
                </a-menu>
              </template>
            </a-dropdown>
          </a-space>
        </template>
      </template>
    </a-table>

    <!-- 创建/编辑弹窗 -->
    <a-modal
      v-model:open="editVisible"
      :title="editing ? '编辑用户' : '新建用户'"
      :confirm-loading="editLoading"
      @ok="onSubmitEdit"
    >
      <a-form layout="vertical">
        <a-form-item label="用户名" required>
          <a-input v-model:value="editForm.username" :disabled="!!editing" />
        </a-form-item>
        <a-form-item v-if="!editing" label="初始密码" required extra="用户首次登录将被强制修改">
          <a-input-password v-model:value="editForm.password" placeholder="至少 8 位，包含字母与数字" />
        </a-form-item>
        <a-form-item label="显示名" required>
          <a-input v-model:value="editForm.display_name" />
        </a-form-item>
        <a-form-item label="邮箱">
          <a-input v-model:value="editForm.email" />
        </a-form-item>
        <a-form-item label="角色">
          <a-select
            v-model:value="editForm.role_ids"
            mode="multiple"
            placeholder="选择角色"
            :options="roleOptions"
          />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 重置密码弹窗 -->
    <a-modal
      v-model:open="resetVisible"
      :title="`重置密码：${resetTarget?.username || ''}`"
      :confirm-loading="resetLoading"
      @ok="onSubmitReset"
    >
      <a-form layout="vertical">
        <a-form-item label="新密码" required extra="重置后该用户下次登录须修改密码">
          <a-input-password v-model:value="resetPassword" placeholder="至少 8 位，包含字母与数字" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}
.kw {
  width: 260px;
}
.status-sel {
  width: 120px;
}
.batch-btn {
  margin-left: auto;
}
</style>
