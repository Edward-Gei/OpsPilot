// 站内通知 API（04-API设计 §10.1，NOTIFY-06）：登录即可访问，仅本人数据
import { request } from './http'
import type { PageResult } from './system'

/** 站内信条目：ref_type/ref_id 供点击跳转（ticket → 工单中心，execution → 执行详情） */
export interface NotificationItem {
  id: number
  event: string
  title: string
  content: string | null
  ref_type: 'ticket' | 'execution' | null
  ref_id: number | null
  is_read: boolean
  created_at: string | null
}

/** 我的站内信：按创建时间倒序分页，only_unread=true 只看未读 */
export function listNotifications(params: {
  page?: number
  page_size?: number
  only_unread?: boolean
}) {
  return request<PageResult<NotificationItem>>({ url: '/notifications', method: 'get', params })
}

/** 未读数（铃铛角标 30 秒轮询，轻量接口） */
export function getUnreadCount() {
  return request<{ count: number }>({ url: '/notifications/unread-count', method: 'get' })
}

/** 单条已读：非本人/不存在 40401，重复调用幂等 */
export function markRead(id: number) {
  return request<null>({ url: `/notifications/${id}/read`, method: 'put' })
}

/** 全部已读：返回本次置已读条数 */
export function markAllRead() {
  return request<{ count: number }>({ url: '/notifications/read-all', method: 'put' })
}
