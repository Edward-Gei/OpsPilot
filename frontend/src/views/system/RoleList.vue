<script setup lang="ts">
// 角色管理：角色卡片列表 + 权限矩阵编辑（role:read / role:write）
// admin 角色不可编辑或删除，其他内置角色允许编辑但不可删除，由后端兜底
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { PlusOutlined, SafetyCertificateOutlined, TeamOutlined } from '@ant-design/icons-vue'
import * as sysApi from '@/api/system'
import { useUserStore } from '@/stores/user'

const userStore = useUserStore()
const canWrite = userStore.hasPerm('role:write')
const canEditRole = (role: sysApi.RoleItem) => canWrite && role.code !== 'admin'

// 角色头像渐变色（按卡片顺序循环，提升辨识度）
const avatarGrads = [
  'var(--grad-blue)',
  'var(--grad-purple)',
  'var(--grad-green)',
  'var(--grad-orange)',
  'var(--grad-red)',
]
// 权限标签按模块着色，一眼区分所属域
const moduleTagColors: Record<string, string> = {
  user: 'geekblue',
  system: 'purple',
  cmdb: 'cyan',
  job: 'orange',
  ticket: 'magenta',
  audit: 'green',
  notify: 'gold',
}

const loading = ref(false)
const roles = ref<sysApi.RoleItem[]>([])
const permissions = ref<sysApi.PermissionItem[]>([])

// 权限点按模块分组（编辑弹窗勾选用）
const moduleNames: Record<string, string> = {
  user: '用户与角色',
  system: '系统管理',
  cmdb: '资源管理',
  job: '作业中心',
  ticket: '工单中心',
  audit: '安全审计',
  notify: '通知中心',
}
const permGroups = computed(() => {
  const groups: { module: string; title: string; items: sysApi.PermissionItem[] }[] = []
  for (const p of permissions.value) {
    let g = groups.find((x) => x.module === p.module)
    if (!g) {
      g = { module: p.module, title: moduleNames[p.module] || p.module, items: [] }
      groups.push(g)
    }
    g.items.push(p)
  }
  return groups
})

/** 权限码 -> 名称/模块（角色卡片展示用） */
const permNameMap = computed(() =>
  Object.fromEntries(permissions.value.map((p) => [p.code, p.name])),
)
const permModuleMap = computed(() =>
  Object.fromEntries(permissions.value.map((p) => [p.code, p.module])),
)

async function loadAll() {
  loading.value = true
  try {
    const [roleData, permData] = await Promise.all([sysApi.listRoles(), sysApi.listPermissions()])
    roles.value = roleData.items
    permissions.value = permData.items
  } finally {
    loading.value = false
  }
}

// ---------- 创建 / 编辑 ----------
const editVisible = ref(false)
const editLoading = ref(false)
const editing = ref<sysApi.RoleItem | null>(null) // null=创建
const editForm = reactive({ code: '', name: '', description: '', permissions: [] as string[] })

function openCreate() {
  editing.value = null
  Object.assign(editForm, { code: '', name: '', description: '', permissions: [] })
  editVisible.value = true
}

function openEdit(role: sysApi.RoleItem) {
  editing.value = role
  Object.assign(editForm, {
    code: role.code,
    name: role.name,
    description: role.description || '',
    permissions: [...role.permissions],
  })
  editVisible.value = true
}

/** 提交创建/编辑（角色权限变更后成员权限即时生效） */
async function onSubmitEdit() {
  if (!editForm.code || !editForm.name) {
    message.warning('请填写角色标识与名称')
    return
  }
  editLoading.value = true
  try {
    if (editing.value) {
      await sysApi.updateRole(editing.value.id, {
        name: editForm.name,
        description: editForm.description || undefined,
        ...(editing.value.code !== 'admin' ? { permissions: editForm.permissions } : {}),
      })
      message.success('已保存')
    } else {
      await sysApi.createRole({
        code: editForm.code,
        name: editForm.name,
        description: editForm.description || undefined,
        permissions: editForm.permissions,
      })
      message.success('角色已创建')
    }
    editVisible.value = false
    loadAll()
  } catch {
    /* 错误提示由拦截器统一弹出 */
  } finally {
    editLoading.value = false
  }
}

async function onDelete(role: sysApi.RoleItem) {
  try {
    await sysApi.deleteRole(role.id)
    message.success('已删除')
    loadAll()
  } catch {
    /* 内置角色/有成员时后端 42201 拒绝，拦截器已提示 */
  }
}

onMounted(loadAll)
</script>

<template>
  <div>
    <!-- 彩色横幅：页面标识 + 角色数 -->
    <div class="op-hero op-hero--violet">
      <div class="op-hero-icon"><SafetyCertificateOutlined /></div>
      <div>
        <div class="op-hero-title">角色管理</div>
        <div class="op-hero-sub">基于 RBAC 的权限矩阵，变更对成员即时生效</div>
      </div>
      <div class="op-hero-extra">
        <div class="op-hero-stat"><b>{{ roles.length }}</b><span>角色数</span></div>
        <div class="op-hero-stat"><b>{{ permissions.length }}</b><span>权限点</span></div>
      </div>
    </div>

    <div class="toolbar">
      <div class="hint">admin 角色不可编辑；其他内置角色可编辑权限但不可删除</div>
      <a-button v-if="canWrite" type="primary" @click="openCreate">
        <PlusOutlined />新建角色
      </a-button>
    </div>

    <a-spin :spinning="loading">
      <div class="role-grid">
        <a-card v-for="(role, idx) in roles" :key="role.id" class="role-card">
          <div class="role-head">
            <div class="role-title">
              <!-- 渐变头像：按顺序循环配色 -->
              <span class="op-icon-grad" :style="{ background: avatarGrads[idx % avatarGrads.length] }">
                <SafetyCertificateOutlined />
              </span>
              <div>
                <b>{{ role.name }}</b>
                <a-tag v-if="role.is_builtin" class="builtin-tag" color="blue">内置</a-tag>
                <div class="role-code">{{ role.code }}</div>
              </div>
            </div>
            <div class="member"><TeamOutlined /> {{ role.member_count }} 人</div>
          </div>
          <div class="role-desc">{{ role.description || '暂无描述' }}</div>
          <div class="perm-tags">
            <a-tag
              v-for="code in role.permissions.slice(0, 6)"
              :key="code"
              :color="moduleTagColors[permModuleMap[code]] || 'default'"
            >
              {{ permNameMap[code] || code }}
            </a-tag>
            <a-tag v-if="role.permissions.length > 6">+{{ role.permissions.length - 6 }}</a-tag>
            <span v-if="!role.permissions.length" class="muted">无权限点</span>
          </div>
          <div v-if="canEditRole(role)" class="role-actions">
            <a-button size="small" @click="openEdit(role)">
              编辑
            </a-button>
            <a-popconfirm
              v-if="!role.is_builtin"
              title="确认删除该角色？"
              @confirm="onDelete(role)"
            >
              <a-button size="small" danger>删除</a-button>
            </a-popconfirm>
          </div>
        </a-card>
      </div>
    </a-spin>

    <!-- 创建/编辑弹窗 -->
    <a-modal
      v-model:open="editVisible"
      :title="editing ? `编辑角色：${editing.name}` : '新建角色'"
      :confirm-loading="editLoading"
      width="640px"
      @ok="onSubmitEdit"
    >
      <a-form layout="vertical">
        <div class="form-row">
          <a-form-item label="角色标识" required class="grow">
            <a-input
              v-model:value="editForm.code"
              :disabled="!!editing"
              placeholder="小写字母/下划线，如 db_admin"
            />
          </a-form-item>
          <a-form-item label="角色名称" required class="grow">
            <a-input v-model:value="editForm.name" />
          </a-form-item>
        </div>
        <a-form-item label="描述">
          <a-input v-model:value="editForm.description" />
        </a-form-item>
        <a-form-item label="权限矩阵">
          <a-alert
            v-if="editing?.is_builtin"
            type="warning"
            show-icon
            class="builtin-alert"
            message="admin 角色不可编辑；其他内置角色可修改权限矩阵。"
          />
          <a-checkbox-group v-model:value="editForm.permissions" class="perm-groups">
            <div v-for="g in permGroups" :key="g.module" class="perm-group">
              <div class="pg-title">{{ g.title }}</div>
              <a-checkbox
                v-for="p in g.items"
                :key="p.code"
                :value="p.code"
                :disabled="editing?.code === 'admin'"
              >
                {{ p.name }}
              </a-checkbox>
            </div>
          </a-checkbox-group>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.hint {
  font-size: 12px;
  color: var(--text-3);
}
.role-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}
.role-card {
  border-radius: 14px;
}
.role-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}
.role-title {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.role-head b {
  font-size: 15px;
  color: var(--text-1);
}
.builtin-tag {
  margin-left: 8px;
}
.role-code {
  font-size: 12px;
  color: var(--text-3);
  margin-top: 5px;
}
.member {
  font-size: 12px;
  color: var(--text-3);
  white-space: nowrap;
}
.role-desc {
  font-size: 13px;
  color: var(--text-2);
  margin: 10px 0;
  min-height: 20px;
}
.perm-tags {
  min-height: 26px;
}
.perm-tags :deep(.ant-tag) {
  margin-bottom: 6px;
}
.muted {
  color: var(--text-3);
  font-size: 12px;
}
.role-actions {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
  display: flex;
  gap: 8px;
}
.form-row {
  display: flex;
  gap: 12px;
}
.grow {
  flex: 1;
}
.builtin-alert {
  margin-bottom: 10px;
}
.perm-groups {
  display: block;
  max-height: 300px;
  overflow-y: auto;
}
.perm-group {
  margin-bottom: 12px;
}
.pg-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-3);
  margin-bottom: 6px;
}
.perm-group :deep(.ant-checkbox-wrapper) {
  margin-right: 16px;
  margin-bottom: 4px;
}
</style>
