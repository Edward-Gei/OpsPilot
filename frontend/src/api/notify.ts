// 通知中心 API（04-API设计 §10，权限 notify:config）
import { request } from './http'
import type { PageResult } from './system'

/** 渠道配置项：secret 为掩码回显（****** = 已配置，null = 未配置） */
export interface NotifyChannelItem {
  type: string
  implemented: boolean
  enabled: boolean
  config: Record<string, unknown>
  secret: string | null
  updated_at: string | null
}

/** 事件-渠道映射（六事件全量返回，含空映射） */
export interface EventMappingItem {
  event: string
  channels: string[]
}

/** 发送记录：含重试调度字段（退避 1m/5m/15m，最多 3 次） */
export interface NotifyRecordItem {
  id: number
  event: string
  channel_type: string
  receiver: string | null
  title: string
  content: string | null
  ref_type: string | null
  ref_id: number | null
  status: 'pending' | 'success' | 'failed'
  retry_count: number
  next_retry_at: string | null
  error: string | null
  sent_at: string | null
  created_at: string | null
}

export function listChannels() {
  return request<{ items: NotifyChannelItem[] }>({ url: '/notify/channels', method: 'get' })
}

/** 保存渠道配置：secret 原样提交 ****** 不会覆盖真实密钥，空串 = 清空 */
export function updateChannel(
  type: string,
  data: { enabled: boolean; config: Record<string, unknown>; secret?: string },
) {
  return request<null>({ url: `/notify/channels/${type}`, method: 'put', data })
}

/** 发送测试消息：传当前表单值（保存前预测）；Email 渠道 receiver 直接给邮箱地址 */
export function testChannel(
  type: string,
  data: { config: Record<string, unknown>; secret?: string; receiver?: string },
) {
  return request<{ success: boolean; message: string }>({
    url: `/notify/channels/${type}/test`,
    method: 'post',
    data,
  })
}

export function listEventMappings() {
  return request<{ items: EventMappingItem[] }>({ url: '/notify/events', method: 'get' })
}

/** 全量提交映射：未提交的事件视为清空 */
export function updateEventMappings(mappings: Record<string, string[]>) {
  return request<null>({ url: '/notify/events', method: 'put', data: { mappings } })
}

export function listRecords(params: {
  page?: number
  page_size?: number
  event?: string
  channel?: string
  status?: string
  start?: string
  end?: string
}) {
  return request<PageResult<NotifyRecordItem>>({ url: '/notify/records', method: 'get', params })
}
