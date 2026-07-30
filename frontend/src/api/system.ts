// 用户 / 角色 / 系统配置 API（04-API设计 §3）
import { request } from './http'

export interface RoleBrief {
  id: number
  code: string
  name: string
}

export interface UserItem {
  id: number
  username: string
  display_name: string
  email: string | null
  source: string
  status: string
  mfa_enabled: boolean
  mfa_bound: boolean
  must_change_password: boolean
  locked_until: string | null
  last_login_at: string | null
  created_at: string | null
  roles: RoleBrief[]
}

export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface RoleItem {
  id: number
  code: string
  name: string
  description: string | null
  is_builtin: boolean
  member_count: number
  permissions: string[]
}

export interface PermissionItem {
  id: number
  code: string
  name: string
  module: string
}

// ---------- 用户管理 ----------

export function listUsers(params: {
  page?: number
  page_size?: number
  keyword?: string
  status?: string
  source?: string
}) {
  return request<PageResult<UserItem>>({ url: '/users', method: 'get', params })
}

export function createUser(data: {
  username: string
  password: string
  display_name: string
  email?: string
  role_ids?: number[]
}) {
  return request<{ id: number }>({ url: '/users', method: 'post', data })
}

export function updateUser(
  id: number,
  data: { display_name?: string; email?: string; status?: string; role_ids?: number[] },
) {
  return request<null>({ url: `/users/${id}`, method: 'put', data })
}

/** 管理员重置密码：目标用户下次登录强制改密 */
export function resetUserPassword(id: number, newPassword: string) {
  return request<null>({ url: `/users/${id}/password`, method: 'put', data: { new_password: newPassword } })
}

/** 管理员管理用户 MFA（仅 user:mfa 权限）：enable/disable/reset */
export function manageUserMfa(id: number, action: 'enable' | 'disable' | 'reset') {
  return request<null>({ url: `/users/${id}/mfa`, method: 'put', data: { action } })
}

// ---------- 角色管理 ----------

export function listRoles() {
  return request<{ items: RoleItem[] }>({ url: '/roles', method: 'get' })
}

/** 轻量角色选项（template:read 即可）：模板编辑器审批节点/可见范围/收件人下拉用 */
export function listRoleOptions() {
  return request<{ items: { id: number; name: string }[] }>({ url: '/roles/options', method: 'get' })
}

export function listPermissions() {
  return request<{ items: PermissionItem[] }>({ url: '/roles/permissions', method: 'get' })
}

export function createRole(data: {
  code: string
  name: string
  description?: string
  permissions: string[]
}) {
  return request<{ id: number }>({ url: '/roles', method: 'post', data })
}

export function updateRole(
  id: number,
  data: { name?: string; description?: string; permissions?: string[] },
) {
  return request<null>({ url: `/roles/${id}`, method: 'put', data })
}

export function deleteRole(id: number) {
  return request<null>({ url: `/roles/${id}`, method: 'delete' })
}

// ---------- 系统配置（M1 仅接口，供后续设置页使用） ----------

export function getConfigs() {
  return request<Record<string, unknown>>({ url: '/system/configs', method: 'get' })
}

export function updateConfigs(configs: Record<string, unknown>) {
  return request<null>({ url: '/system/configs', method: 'put', data: { configs } })
}

// ---------- Ansible 作业主机（M5，系统配置键 ansible.job_host） ----------

/** 作业主机配置：Ansible 控制节点，null = 未配置（playbook 步骤将无法执行） */
export interface JobHostConfig {
  ip: string
  port: number
  credential_id: number | null
  workdir: string
}

/** 连通性测试：SSH 建连 + ansible --version；传当前表单值，保存前可预测（04-API §11） */
export function testJobHost(data: { ip: string; port: number; credential_id: number }) {
  return request<{ success: boolean; message: string }>({
    url: '/system/ansible-job-host/test',
    method: 'post',
    data,
  })
}
