import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import type { TaskView, ResultPage, TaskRequest, TaskSubmission, TaskStatus, DatabaseEngine } from '@/api/database'

/** 主布局由文档滚动；仅数据库页面进入或切换路径时复位，不干扰筛选与页内滚动。 */
export function useDatabasePageScroll() {
  const route = useRoute()
  const reset = () => {
    const container = document.scrollingElement || document.documentElement
    container.scrollTop = 0
  }
  onMounted(reset)
  watch(() => route.path, reset, { flush: 'post' })
}

export const engineNames: Record<DatabaseEngine, string> = {
  mysql: 'MySQL',
  postgresql: 'PostgreSQL',
  mongodb: 'MongoDB',
  clickhouse: 'ClickHouse',
  redis: 'Redis'
}
export const engineOptions = Object.entries(engineNames).map(([value, label]) => ({ value, label }))
export const statusNames: Record<TaskStatus, string> = {
  queued: '排队中',
  running: '执行中',
  stopping: '取消处理中',
  recovering: '停止待确认',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
  timed_out: '已超时',
  rejected: '未执行',
  queue_expired: '排队过期'
}
export const activeStatuses: TaskStatus[] = ['queued', 'running', 'stopping', 'recovering']
export const timeText = (value: string | null | undefined) => value ? new Date(value).toLocaleString() : '—'

export function formatCell(value: unknown): string {
  if (value === null) return 'NULL'
  if (typeof value === 'object') {
    const tag = value as {
      $type?: unknown
      value?: unknown
    }
    if (typeof tag.$type === 'string' && typeof tag.value === 'string') return `${tag.value} [${tag.$type}]`
    return JSON.stringify(value)
  }
  return String(value ?? '')
}

export function errorText(error: unknown) {
  return error instanceof Error ? error.message : String((error as {
    message?: string
  })?.message || '请求失败')
}

export function errorCode(error: unknown) {
  return (error as {
    code?: number
  })?.code
}

export interface DeleteOutcome {
  id: number
  status: 'deleted' | 'rejected' | 'unknown'
  message: string
}

/** 显式选择逐项串行删除；传输未知不推定拒绝，也不自动重放。 */
export async function deleteSelectedInstances(ids: number[], remove: (id: number) => Promise<unknown>, onItem?: (item: DeleteOutcome) => void) {
  const unique = [...new Set(ids)]
  if (!unique.length || unique.length > 100 || unique.some(id => !Number.isSafeInteger(id) || id <= 0)) throw new Error('请选择 1～100 个有效实例')
  const results: DeleteOutcome[] = []
  for (const id of unique) {
    let item: DeleteOutcome
    try {
      await remove(id)
      item = { id, status: 'deleted', message: '已删除' }
    }
    catch (error) {
      const known = typeof errorCode(error) === 'number'
      item = {
        id,
        status: known ? 'rejected' : 'unknown',
        message: known ? errorText(error) : '请求结果未知，请刷新核对；未自动重试'
      }
    }
    results.push(item)
    onItem?.(item)
  }
  return results
}

interface TaskApi {
  getTask: (id: number, signal?: AbortSignal) => Promise<TaskView>
  getResult: (id: number, page: number, page_size: number, signal?: AbortSignal) => Promise<ResultPage>
  submitTask?: (request: TaskRequest, signal?: AbortSignal) => Promise<TaskSubmission>
  cancelTask?: (id: number, signal?: AbortSignal) => Promise<TaskView>
}

/** 每次切换递增代数，旧状态/分页响应和到期计时器均不可回写新任务。 */
export function createTaskSession(api: TaskApi) {
  const task = ref<TaskView | null>(null),
    result = ref<ResultPage | null>(null),
    notice = ref('')
  const submitting = ref(false),
    cancelling = ref(false),
    resultLoading = ref(false)
  let generation = 0,
    resultGeneration = 0,
    taskId: number | null = null,
    withResult = false
  let timer: ReturnType<typeof setTimeout> | undefined,
    expiry: ReturnType<typeof setTimeout> | undefined
  let controller = new AbortController(),
    resultController = new AbortController()
  function clear() {
    generation++
    resultGeneration++
    clearTimeout(timer)
    clearTimeout(expiry)
    controller.abort()
    resultController.abort()
    controller = new AbortController()
    resultController = new AbortController()
    task.value = null
    result.value = null
    notice.value = ''
    taskId = null
    submitting.value = false
    cancelling.value = false
    resultLoading.value = false
  }
  async function page(pageNumber: number, pageSize = 50) {
    if (!taskId) return
    const id = taskId,
      g = generation,
      r = ++resultGeneration
    resultController.abort()
    resultController = new AbortController()
    // 同任务翻页保留当前页作为加载罩底层，原到期计时不中断。
    resultLoading.value = true
    try {
      const data = await api.getResult(id, pageNumber, pageSize, resultController.signal)
      if (g !== generation || r !== resultGeneration) return
      const ttl = new Date(data.expires_at).getTime() - Date.now()
      if (ttl <= 0) {
        clearTimeout(expiry)
        result.value = null
        notice.value = '结果已过期'
        return
      }
      clearTimeout(expiry)
      result.value = data
      notice.value = ''
      expiry = setTimeout(() => {
        if (g === generation) {
          resultGeneration++
          resultController.abort()
          result.value = null
          resultLoading.value = false
          notice.value = '结果已过期'
        }
      }, Math.min(ttl, 2147483647))
    } catch (error) {
      if (g !== generation || r !== resultGeneration) return
      clearTimeout(expiry)
      result.value = null
      notice.value = errorText(error)
      if ([40301, 40302, 40101, 40102].includes(errorCode(error) || 0)) {
        resultLoading.value = false
        generation++
        clearTimeout(timer)
      }
    } finally {
      if (g === generation && r === resultGeneration) resultLoading.value = false
    }
  }
  async function read(id: number, g: number) {
    try {
      const data = await api.getTask(id, controller.signal)
      if (g !== generation) return
      task.value = data
      notice.value = ''
      if (activeStatuses.includes(data.status)) timer = setTimeout(() => void read(id, g), 1500)
      else if (data.status === 'succeeded' && withResult) await page(1)
    } catch (error) {
      if (g !== generation) return
      notice.value = errorText(error)
      result.value = null
      if ([40101, 40102, 40301, 40302, 40401].includes(errorCode(error) || 0)) {
        generation++
        task.value = null
        return
      }
      timer = setTimeout(() => void read(id, g), 3000)
    }
  }
  async function select(id: number, showResult = false) {
    clear()
    taskId = id
    withResult = showResult
    await read(id, generation)
  }
  async function submit(request: TaskRequest, showResult = true) {
    if (submitting.value || !api.submitTask) return
    clear()
    const g = generation
    submitting.value = true
    withResult = showResult
    try {
      const accepted = await api.submitTask(request, controller.signal)
      if (g !== generation) return
      taskId = accepted.task_id
      await read(taskId, g)
    } catch (error) {
      if (g !== generation) return
      const rejectedId = (error as {
        data?: {
          task_id?: number
        }
      })?.data?.task_id
      if (rejectedId) {
        taskId = rejectedId
        await read(rejectedId, g)
      }
      else notice.value = typeof errorCode(error) === 'number' ? errorText(error) : '提交结果未知，请到本人历史核对；未自动重试'
    } finally {
      if (g === generation) submitting.value = false
    }
  }
  async function cancel() {
    if (!taskId || cancelling.value || !api.cancelTask) return
    // 取消响应代表较新的事实；使此前在途轮询失效，避免回写成旧的 running。
    const g = ++generation
    clearTimeout(timer)
    cancelling.value = true
    controller.abort()
    controller = new AbortController()
    try {
      const data = await api.cancelTask(taskId, controller.signal)
      if (g !== generation) return
      task.value = data
      clearTimeout(timer)
      if (activeStatuses.includes(data.status)) timer = setTimeout(() => void read(data.task_id, g), 1500)
    } catch (error) {
      if (g === generation) {
        notice.value = typeof errorCode(error) === 'number' ? errorText(error) : '取消请求结果未知，继续核对任务状态'
        timer = setTimeout(() => {
          if (taskId) void read(taskId, g)
        }, 3000)
      }
    }
    finally {
      if (g === generation) cancelling.value = false
    }
  }
  return {
    task,
    result,
    notice,
    submitting,
    cancelling,
    resultLoading,
    clear,
    dispose: clear,
    select,
    submit,
    page,
    cancel
  }
}
