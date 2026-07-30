// 执行中心共享元数据：执行/主机状态展示映射（列表/详情/矩阵共用）
import type { ExecutionStatus, HostExecStatus } from '@/api/execution'

/** 执行实例状态展示（03 §5.2：queued/running/paused + 四终态） */
export const execStatusMeta: Record<ExecutionStatus, { text: string; color: string }> = {
  queued: { text: '排队中', color: 'default' },
  running: { text: '执行中', color: 'processing' },
  paused: { text: '已暂停', color: 'gold' },
  success: { text: '成功', color: 'success' },
  failed: { text: '失败', color: 'error' },
  terminated: { text: '已中止', color: 'warning' },
  interrupted: { text: '已中断', color: 'warning' },
}

export const execStatusOptions = Object.entries(execStatusMeta).map(([value, v]) => ({
  label: v.text,
  value,
}))

/** 主机粒度状态展示（矩阵单元格/主机明细行） */
export const hostStatusMeta: Record<HostExecStatus, { text: string; color: string }> = {
  pending: { text: '待执行', color: 'default' },
  running: { text: '执行中', color: 'processing' },
  success: { text: '成功', color: 'success' },
  failed: { text: '失败', color: 'error' },
  timeout: { text: '超时', color: 'error' },
  skipped: { text: '已跳过', color: 'default' },
  terminated: { text: '已终止', color: 'warning' },
}

/** 中断起因展示（工单 interrupt_reason，03 §5.1） */
export const interruptReasonText: Record<string, string> = {
  user_abort: '用户中止',
  system_crash: '系统崩溃恢复',
  worker_lost: '消息投递超限',
}

/** 触发方式展示 */
export const triggeredByText: Record<string, string> = {
  auto_approve: '审批通过',
  no_approval: '免审提交',
}
