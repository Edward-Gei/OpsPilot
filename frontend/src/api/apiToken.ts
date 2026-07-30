// 个人访问密钥 API（04-API设计 §2.9）：opsp_ Token 可直接作为 Bearer 调用平台接口
import { request } from './http'

/** 密钥列表项（明文仅创建时返回一次，列表只含前缀） */
export interface ApiTokenItem {
  id: number
  name: string
  prefix: string
  expired: boolean
  expires_at: string | null
  last_used_at: string | null
  created_at: string | null
}

export function listTokens() {
  return request<{ items: ApiTokenItem[]; limit: number }>({
    url: '/user/tokens',
    method: 'get',
  })
}

/** 创建密钥：expiresInDays 缺省为永不过期；返回体额外携带一次性明文 token */
export function createToken(name: string, expiresInDays: number | null) {
  return request<ApiTokenItem & { token: string }>({
    url: '/user/tokens',
    method: 'post',
    data: { name, expires_in_days: expiresInDays },
  })
}

export function deleteToken(id: number) {
  return request<null>({ url: `/user/tokens/${id}`, method: 'delete' })
}
