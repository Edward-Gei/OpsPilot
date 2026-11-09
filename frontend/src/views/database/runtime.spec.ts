import { afterEach, expect, it, vi } from 'vitest'
import { deleteSelectedInstances, createTaskSession, formatCell } from './runtime'
import type { TaskView, ResultPage } from '@/api/database'

afterEach(() => vi.useRealTimers())
it('批量删除严格串行且单项拒绝或网络未知不会回滚成功或自动重试', async () => {
  let active = 0
  const calls: number[] = []
  const outcome = await deleteSelectedInstances([1, 2, 2, 3], async id => {
    expect(active++).toBe(0)
    calls.push(id)
    await Promise.resolve()
    active--
    if (id === 2) throw { code: 40301, message: '无权限' }
    if (id === 3) throw new Error('network')
  })
  expect(calls).toEqual([1, 2, 3])
  expect(outcome.map(item => item.status)).toEqual(['deleted', 'rejected', 'unknown'])
})
it('超过100个显式ID和非法ID在任何删除请求前拒绝', async () => {
  const remove = vi.fn()
  await expect(deleteSelectedInstances(Array.from({ length: 101 }, (_, i) => i + 1), remove)).rejects.toThrow()
  await expect(deleteSelectedInstances([0, 1], remove)).rejects.toThrow()
  expect(remove).not.toHaveBeenCalled()
})
it('特殊值和HTML只产生文本且保持超安全整数与重名列之外的原值', () => {
  expect(formatCell({ $type: 'int', value: '9007199254740993' })).toBe('9007199254740993 [int]')
  expect(formatCell('<img src=x onerror=alert(1)>')).toBe('<img src=x onerror=alert(1)>')
  expect(formatCell(null)).toBe('NULL')
})
const task = (id: number): TaskView => ({
  task_id: id,
  kind: 'sql',
  status: 'succeeded',
  instance_id: id,
  instance_name: 'test',
  engine: 'mysql',
  context: {},
  submitted_text: 'SELECT 1',
  executed_statement: 'SELECT 1',
  operation_summary: 'SELECT',
  created_at: '',
  started_at: null,
  finished_at: null,
  expires_at: null,
  stop_reason: null,
  error_code: null,
  error_message: null,
  stop_confirmation: null,
  result_summary: null,
  policy_snapshot: null
})
const result: ResultPage = {
  columns: [{ ordinal: 0, name: 'id', type: 'int' }],
  rows: [[1]],
  returned_rows: 1,
  returned_bytes: 20,
  truncated: false,
  truncate_reason: null,
  expires_at: '2099-01-01T00:00:00Z',
  saved_total: 1,
  page: 1,
  page_size: 50
}
it('切换任务后旧状态和结果响应不能覆盖新任务', async () => {
  let release!: (value: TaskView) => void
  const session = createTaskSession({
    getTask: id => id === 1 ? new Promise(resolve => {
      release = resolve
    }) : Promise.resolve(task(id)),
    getResult: async () => result
  })
  const old = session.select(1, true)
  await session.select(2, true)
  release(task(1))
  await old
  expect(session.task.value?.task_id).toBe(2)
  expect(session.result.value?.rows).toEqual([[1]])
  session.dispose()
})
it('分页收到撤权错误立即清空已经展示的结果', async () => {
  let denied = false
  const session = createTaskSession({
    getTask: async () => task(1),
    getResult: async () => {
      if (denied) throw { code: 40302, message: '无权限' }
      return result
    }
  })
  await session.select(1, true)
  expect(session.result.value?.rows).toEqual([[1]])
  denied = true
  await session.page(2, 50)
  expect(session.result.value).toBeNull()
  expect(session.notice.value).toContain('无权限')
  session.dispose()
})
it('同任务分页在途保留当前页作为loading底层，成功后替换为新页', async () => {
  let finish!: (value: ResultPage) => void
  const session = createTaskSession({
    getTask: async () => task(1),
    getResult: async (_id, page) => page === 1 ? result : new Promise(resolve => { finish = resolve })
  })
  await session.select(1, true)
  const current = session.result.value
  const pending = session.page(2)
  expect(session.result.value).toBe(current)
  expect(session.resultLoading.value).toBe(true)
  finish({ ...result, page: 2, rows: [[2]] })
  await pending
  expect(session.result.value?.rows).toEqual([[2]])
  expect(session.resultLoading.value).toBe(false)
  session.dispose()
})
it.each([
  { code: 40302, message: '查询权限已撤销' },
  new Error('分页读取失败'),
])('分页失败清屏并保留错误提示，旧到期计时器不覆盖错误：$message', async error => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
  let fail!: (error: unknown) => void
  const session = createTaskSession({
    getTask: async () => task(1),
    getResult: async (_id, page) => page === 1 ? { ...result, expires_at: '2026-01-01T00:00:01Z' }
      : new Promise((_resolve, reject) => { fail = reject })
  })
  await session.select(1, true)
  const pending = session.page(2)
  expect(session.result.value?.rows).toEqual([[1]])
  fail(error)
  await pending
  expect(session.result.value).toBeNull()
  expect(session.resultLoading.value).toBe(false)
  expect(session.notice.value).toBe(error.message)
  await vi.advanceTimersByTimeAsync(1001)
  expect(session.notice.value).toBe(error.message)
  session.dispose()
})
it('分页请求不暂停结果到期，过期清屏并使迟到分页响应失效', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
  let finish!: (value: ResultPage) => void
  let signal: AbortSignal | undefined
  const session = createTaskSession({
    getTask: async () => task(1),
    getResult: async (_id, page, _size, abort) => {
      if (page === 1) return { ...result, expires_at: '2026-01-01T00:00:01Z' }
      signal = abort
      return new Promise(resolve => { finish = resolve })
    }
  })
  await session.select(1, true)
  const pending = session.page(2)
  await vi.advanceTimersByTimeAsync(1001)
  expect(session.result.value).toBeNull()
  expect(session.notice.value).toBe('结果已过期')
  expect(session.resultLoading.value).toBe(false)
  expect(signal?.aborted).toBe(true)
  finish({ ...result, page: 2, rows: [[99]] })
  await pending
  expect(session.result.value).toBeNull()
  expect(session.notice.value).toBe('结果已过期')
  session.dispose()
})
it('切换任务立即清除分页底层，在途旧页中止且迟到响应不恢复旧数据', async () => {
  let finish!: (value: ResultPage) => void
  let signal: AbortSignal | undefined
  const session = createTaskSession({
    getTask: async id => task(id),
    getResult: async (id, page, _size, abort) => {
      if (id === 1 && page === 2) {
        signal = abort
        return new Promise(resolve => { finish = resolve })
      }
      return { ...result, rows: [[id]] }
    }
  })
  await session.select(1, true)
  const pending = session.page(2)
  expect(session.result.value?.rows).toEqual([[1]])
  const selected = session.select(2, true)
  expect(session.result.value).toBeNull()
  expect(signal?.aborted).toBe(true)
  await selected
  finish({ ...result, page: 2, rows: [[99]] })
  await pending
  expect(session.task.value?.task_id).toBe(2)
  expect(session.result.value?.rows).toEqual([[2]])
  session.dispose()
})
it('分页返回已经过期的结果时清空底层页', async () => {
  const session = createTaskSession({
    getTask: async () => task(1),
    getResult: async (_id, page) => page === 1 ? result : { ...result, expires_at: '2000-01-01T00:00:00Z' }
  })
  await session.select(1, true)
  await session.page(2)
  expect(session.result.value).toBeNull()
  expect(session.notice.value).toBe('结果已过期')
  session.dispose()
})
it('取消请求返回停止中以后，旧轮询不能覆盖成运行中', async () => {
  let release!: (value: TaskView) => void
  const session = createTaskSession({
    getTask: () => new Promise(resolve => {
      release = resolve
    }),
    getResult: async () => result,
    cancelTask: async () => ({ ...task(1), status: 'stopping' })
  })
  const old = session.select(1)
  await session.cancel()
  release({ ...task(1), status: 'running' })
  await old
  expect(session.task.value?.status).toBe('stopping')
  session.dispose()
})
it('结果到期主动清空页面内容，不重新执行或重新读取', async () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
  const getResult = vi.fn(async () => ({ ...result, expires_at: '2026-01-01T00:00:01Z' }))
  const session = createTaskSession({ getTask: async () => task(1), getResult })
  await session.select(1, true)
  expect(session.result.value).not.toBeNull()
  await vi.advanceTimersByTimeAsync(1001)
  expect(session.result.value).toBeNull()
  expect(session.notice.value).toBe('结果已过期')
  expect(getResult).toHaveBeenCalledTimes(1)
  session.dispose()
})
it('提交传输未知保留待核对事实且不自动再次提交', async () => {
  vi.useFakeTimers()
  const submitTask = vi.fn(async () => {
    throw new Error('network')
  })
  const session = createTaskSession({ getTask: async () => task(1), getResult: async () => result, submitTask })
  await session.submit({ kind: 'sql', instance_id: 1, context: {}, sql: 'SELECT 1' })
  await vi.advanceTimersByTimeAsync(10000)
  expect(submitTask).toHaveBeenCalledTimes(1)
  expect(session.notice.value).toContain('提交结果未知')
  session.dispose()
})
it('离页中止在途状态请求，迟到响应不恢复任务或结果', async () => {
  let signal: AbortSignal | undefined,
    release!: (value: TaskView) => void
  const session = createTaskSession({
    getTask: (_id, abort) => {
      signal = abort
      return new Promise(resolve => {
        release = resolve
      })
    },
    getResult: async () => result
  })
  const pending = session.select(1, true)
  session.dispose()
  expect(signal?.aborted).toBe(true)
  release(task(1))
  await pending
  expect(session.task.value).toBeNull()
  expect(session.result.value).toBeNull()
})
