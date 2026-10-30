// 令牌存储：独立小模块避免 http <-> store 循环依赖
// sessionStorage 持久化（关浏览器即失效，refresh 7 天有效期仅在会话内使用）
const ACCESS_KEY = 'opspilot_access_token'
const REFRESH_KEY = 'opspilot_refresh_token'
const SESSION_KEY = 'opspilot_session_id'
const REVISION_KEY = 'opspilot_token_revision'
const WRITER_KEY = 'opspilot_token_writer'
const storage = sessionStorage
const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel('opspilot-auth')
const endedSessions = new Set<string>()
const pending = new Map<string, (restored: boolean) => void>()
const tokenListeners = new Set<() => void>()
let expiryTimer: ReturnType<typeof setTimeout> | undefined

function refreshDeadlineMs(): number {
  const payload = storage.getItem(REFRESH_KEY)?.split('.')[1]
  if (!payload) return 0
  try {
    const claims = JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')))
    const seconds = claims.session_exp ?? claims.exp
    return typeof seconds === 'number' && Number.isFinite(seconds) ? seconds * 1000 : 0
  } catch {
    return 0
  }
}

function scheduleExpiry(): void {
  clearTimeout(expiryTimer)
  const deadline = refreshDeadlineMs()
  if (deadline) expiryTimer = setTimeout(() => {
    if (!expireIfNeeded()) scheduleExpiry()
  }, Math.min(Math.max(deadline - Date.now(), 0), 0x7fffffff))
}

/** 浏览器恢复焦点或路由跳转时补检后台可能被节流的到期计时器。 */
export function expireIfNeeded(): boolean {
  const deadline = refreshDeadlineMs()
  if (!deadline || Date.now() < deadline) return false
  clearTokens()
  window.dispatchEvent(new Event('opspilot:session-expired'))
  return true
}

function newId(): string {
  return Array.from(crypto.getRandomValues(new Uint32Array(4)), part => part.toString(16)).join('-')
}

const writerId = newId()

function sessionId(): string {
  if (!getAccessToken() || !getRefreshToken()) return ''
  let id = storage.getItem(SESSION_KEY)
  if (!id) {
    id = newId()
    storage.setItem(SESSION_KEY, id)
  }
  return id
}

function saveTokens(access: string, refresh: string, id: string, revision: number, writer: string): void {
  storage.setItem(ACCESS_KEY, access)
  storage.setItem(REFRESH_KEY, refresh)
  storage.setItem(SESSION_KEY, id)
  storage.setItem(REVISION_KEY, String(revision))
  storage.setItem(WRITER_KEY, writer)
  scheduleExpiry()
  for (const listener of tokenListeners) listener()
}

function clearLocalTokens(): void {
  clearTimeout(expiryTimer)
  for (const key of [ACCESS_KEY, REFRESH_KEY, SESSION_KEY, REVISION_KEY, WRITER_KEY]) storage.removeItem(key)
}

type TokenMessage = {
  type: 'request' | 'response' | 'tokens' | 'clear'
  requestId?: string
  access?: string
  refresh?: string
  sessionId?: string
  revision?: number
  writer?: string
}

if (channel) channel.onmessage = ({ data }: MessageEvent<TokenMessage>) => {
  if (!data || typeof data !== 'object') return
  if (data.type === 'request' && data.requestId) {
    if (expireIfNeeded()) return
    const id = sessionId()
    if (id && !endedSessions.has(id)) {
      channel.postMessage({ type: 'response', requestId: data.requestId, access: getAccessToken(),
        refresh: getRefreshToken(), sessionId: id, revision: Number(storage.getItem(REVISION_KEY) || 0),
        writer: storage.getItem(WRITER_KEY) || '' })
    }
    return
  }
  if (data.type === 'clear' && data.sessionId === sessionId()) {
    endedSessions.add(data.sessionId)
    clearLocalTokens()
    window.dispatchEvent(new Event('opspilot:session-expired'))
    return
  }
  if (!data.access || !data.refresh || !data.sessionId || typeof data.revision !== 'number' || typeof data.writer !== 'string') return
  if (endedSessions.has(data.sessionId)) return
  if (data.type === 'response' && data.requestId && pending.has(data.requestId)) {
    if (!getAccessToken()) saveTokens(data.access, data.refresh, data.sessionId, data.revision, data.writer)
    pending.get(data.requestId)?.(Boolean(getAccessToken()))
    pending.delete(data.requestId)
  } else if (data.type === 'tokens' && data.sessionId === sessionId()) {
    const currentRevision = Number(storage.getItem(REVISION_KEY) || 0)
    const currentWriter = storage.getItem(WRITER_KEY) || ''
    if (data.revision > currentRevision || (data.revision === currentRevision && data.writer > currentWriter)) {
      saveTokens(data.access, data.refresh, data.sessionId, data.revision, data.writer)
    }
  }
}

export function getAccessToken(): string {
  return storage.getItem(ACCESS_KEY) || ''
}

export function getRefreshToken(): string {
  return storage.getItem(REFRESH_KEY) || ''
}

export function getSessionId(): string {
  return sessionId()
}

/** 登录创建新共享会话，刷新旋转沿用当前会话。 */
export function setTokens(access: string, refresh: string, newSession = false): void {
  if (newSession) clearTokens()
  const id = sessionId() || newId()
  const revision = Number(storage.getItem(REVISION_KEY) || 0) + 1
  saveTokens(access, refresh, id, revision, writerId)
  channel?.postMessage({ type: 'tokens', access, refresh, sessionId: id, revision, writer: writerId })
}

/** 清空令牌（登出 / 会话失效） */
export function clearTokens(broadcast = true): void {
  const id = sessionId()
  if (id && broadcast) endedSessions.add(id)
  clearLocalTokens()
  if (id && broadcast) channel?.postMessage({ type: 'clear', sessionId: id })
}

/** 等待同一会话的其他窗口完成一次令牌旋转。 */
export function waitForTokenChange(id: string, refresh: string, timeoutMs = 250): Promise<boolean> {
  if (sessionId() !== id || getRefreshToken() !== refresh) return Promise.resolve(getRefreshToken() !== refresh && sessionId() === id)
  return new Promise(resolve => {
    const done = () => {
      clearTimeout(timer)
      tokenListeners.delete(check)
      resolve(sessionId() === id && !!getAccessToken() && getRefreshToken() !== refresh)
    }
    const check = () => {
      if (sessionId() !== id || getRefreshToken() !== refresh) done()
    }
    const timer = setTimeout(done, timeoutMs)
    tokenListeners.add(check)
  })
}

/** 新窗口仅向当前仍打开的同源窗口请求会话，超时后保持未登录。 */
export function restoreTokensFromPeer(timeoutMs = 500): Promise<boolean> {
  if (getAccessToken()) return Promise.resolve(true)
  if (!channel) return Promise.resolve(false)
  const requestId = newId()
  return new Promise(resolve => {
    const timer = setTimeout(() => {
      pending.delete(requestId)
      resolve(false)
    }, timeoutMs)
    pending.set(requestId, restored => {
      clearTimeout(timer)
      resolve(restored)
    })
    channel.postMessage({ type: 'request', requestId })
  })
}

scheduleExpiry()
window.addEventListener('focus', expireIfNeeded)
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') expireIfNeeded()
})
