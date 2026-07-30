// 工单中心共享元数据：状态标签与时间格式化（列表/待办/详情三处共用）
import type { TicketStatus } from '@/api/ticket'

/** V2 九态状态机的展示映射（03-数据库设计 §5.1；M5 新增 queued/paused） */
export const statusMeta: Record<TicketStatus, { text: string; color: string }> = {
  approving: { text: '审批中', color: 'gold' },
  queued: { text: '排队中', color: 'default' },
  running: { text: '执行中', color: 'processing' },
  paused: { text: '已暂停', color: 'orange' },
  success: { text: '成功', color: 'success' },
  failed: { text: '失败', color: 'error' },
  rejected: { text: '已驳回', color: 'magenta' },
  cancelled: { text: '已撤回', color: 'default' },
  interrupted: { text: '已中断', color: 'warning' },
}

export const statusOptions = Object.entries(statusMeta).map(([value, v]) => ({
  label: v.text,
  value,
}))

/** ISO 时间转本地显示（空值出 —） */
export function fmtTime(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : '—'
}
