// 工单模板共享元数据：模板类型和通知收件人选项。
import type { TemplateType } from '@/api/job'

/** 模板类型彩色标签（执行范式改造后收敛为 3 种） */
export const typeText: Record<TemplateType, { text: string; color: string }> = {
  release: { text: '发布', color: 'geekblue' },
  daily_ops: { text: '日常运维', color: 'green' },
  other: { text: '其他', color: 'purple' },
}
export const typeOptions = Object.entries(typeText).map(([value, v]) => ({ label: v.text, value }))

/** 通知收件人选项：creator / approver_role / role:<id>（TPL-05）。 */
export function receiverOptions(roles: { id: number; name: string }[]) {
  return [
    { label: '工单提交人', value: 'creator' },
    { label: '当前步骤审批角色', value: 'approver_role' },
    ...roles.map((r) => ({ label: `角色：${r.name}`, value: `role:${r.id}` })),
  ]
}
