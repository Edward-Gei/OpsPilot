// 工单模板共享元数据：类型/审批方式/通知事件与渠道的展示映射（列表/编辑器/详情回看共用）
import type { ApproveMode, TemplateType } from '@/api/job'

/** 模板类型彩色标签（V2：类型描述工单业务属性） */
export const typeText: Record<TemplateType, { text: string; color: string }> = {
  release: { text: '发布', color: 'geekblue' },
  change: { text: '变更', color: 'orange' },
  ops: { text: '日常运维', color: 'green' },
  other: { text: '其他', color: 'purple' },
}
export const typeOptions = Object.entries(typeText).map(([value, v]) => ({ label: v.text, value }))

export const scriptTypeOptions = [
  { label: 'Shell', value: 'shell' },
  { label: 'Playbook', value: 'playbook' },
]

/** 步骤脚本类型 → 编辑器高亮语言 */
export function editorLang(scriptType: string): 'shell' | 'yaml' {
  return scriptType === 'playbook' ? 'yaml' : 'shell'
}

/** 节点内审批方式：V1 仅 any（或签）生效，其余仅存配置（FLOW-01） */
export const approveModeText: Record<ApproveMode, string> = {
  any: '或签',
  all: '会签（预留）',
  seq: '依次（预留）',
}
export const approveModeOptions = Object.entries(approveModeText).map(([value, label]) => ({ label, value }))

/** 通知触发事件（PRD NOTIFY-02） */
export const eventText: Record<string, string> = {
  'ticket.pending_approval': '工单待审批',
  'ticket.approved': '审批通过',
  'ticket.rejected': '审批驳回',
  'execution.success': '执行成功',
  'execution.failed': '执行失败',
}
export const eventOptions = Object.entries(eventText).map(([value, label]) => ({ label, value }))

/** 通知渠道：V1 落地前三个，后三个仅预留（PRD NOTIFY-01） */
export const channelText: Record<string, string> = {
  email: '邮件',
  webhook: 'Webhook',
  teams: 'Teams',
  dingtalk: '钉钉（预留）',
  feishu: '飞书（预留）',
  wecom: '企业微信（预留）',
}
export const channelOptions = Object.entries(channelText).map(([value, label]) => ({ label, value }))

/** 通知收件人选项：creator / approver_role / role:<id>（TPL-05） */
export function receiverOptions(roles: { id: number; name: string }[]) {
  return [
    { label: '工单提交人', value: 'creator' },
    { label: '当前节点审批角色', value: 'approver_role' },
    ...roles.map((r) => ({ label: `角色：${r.name}`, value: `role:${r.id}` })),
  ]
}

/** 收件人 token 转展示文案（roleMap: id→角色名） */
export function receiverLabel(receiver: string, roleMap: Record<number, string>): string {
  if (receiver === 'creator') return '工单提交人'
  if (receiver === 'approver_role') return '当前节点审批角色'
  if (receiver.startsWith('role:')) {
    const id = Number(receiver.slice(5))
    return `角色：${roleMap[id] || id}`
  }
  return receiver
}
