// 作业主机 API（执行范式改造：脚本统一在固定作业主机上执行，04-API §5）
// 登录认证随关联凭据（凭据管理）；主机侧只选凭据不填密文
import { request } from './http'
import type { PageResult } from './system'

export interface JobHost {
  id: number
  name: string
  ip: string
  ssh_port: number
  credential_id: number | null
  credential_name: string | null
  workdir: string
  enabled: boolean
  last_check_at: string | null
  last_check_ok: boolean | null
  last_check_msg: string | null
  created_at: string | null
  updated_at: string | null
}

export interface JobHostCreate {
  name: string
  ip: string
  ssh_port?: number
  credential_id: number
  workdir?: string
}

/** 编辑表单：credential_id 传值即切换关联凭据 */
export interface JobHostUpdate {
  name?: string
  ip?: string
  ssh_port?: number
  credential_id?: number
  workdir?: string
}

export function listJobHosts(params: {
  page?: number
  page_size?: number
  keyword?: string
  enabled?: boolean
} = {}) {
  return request<PageResult<JobHost>>({ url: '/job-hosts', method: 'get', params })
}

export function createJobHost(data: JobHostCreate) {
  return request<{ id: number }>({ url: '/job-hosts', method: 'post', data })
}

export function getJobHost(id: number) {
  return request<JobHost>({ url: `/job-hosts/${id}`, method: 'get' })
}

export function updateJobHost(id: number, data: JobHostUpdate) {
  return request<JobHost>({ url: `/job-hosts/${id}`, method: 'put', data })
}

/** 删除作业主机：被工单模板引用时后端 42201 删除保护 */
export function deleteJobHost(id: number) {
  return request<null>({ url: `/job-hosts/${id}`, method: 'delete' })
}

/** 连通性测试：SSH 建连执行 echo ok，结果落 last_check_* 字段 */
export function testJobHost(id: number) {
  return request<{ ok: boolean; message: string; checked_at: string }>({
    url: `/job-hosts/${id}/test`,
    method: 'post',
  })
}

/** 启用/禁用：禁用后不可被新模板选用 */
export function setJobHostStatus(id: number, enabled: boolean) {
  return request<JobHost>({
    url: `/job-hosts/${id}/status`,
    method: 'put',
    data: { enabled },
  })
}
