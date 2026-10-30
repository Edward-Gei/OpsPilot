import { message } from 'ant-design-vue'

const handledErrors = new WeakSet<object>()
const RECOVERY_KEY = 'opspilot_route_load_recovery'
let recoveryGeneration = 0

/** 同页重新认证也会替代旧流程，使其尚未返回的恢复检查失效。 */
export function invalidateRouteRecovery(): void {
  recoveryGeneration += 1
}

/** 只信任当前站点唯一的 Vite 主入口，不把 query 或外站地址写入恢复标记。 */
function moduleEntry(page: Document): string | null {
  const entries = Array.from(page.querySelectorAll<HTMLScriptElement>('script[type="module"][src]'))
    .map(script => new URL(script.getAttribute('src')!, window.location.origin))
    .filter(url => url.origin === window.location.origin && !url.search && !url.hash &&
      /^\/assets\/index-[\w-]+\.js$/.test(url.pathname))
  return entries.length === 1 ? entries[0]!.pathname : null
}

/** 恢复仅限站内页面，避免错误目标变成外跳或访问静态资源、API。 */
function pageTarget(target: string): URL | null {
  if (!/^\/(?!\/)/.test(target) || /[\s\\]/.test(target)) return null
  const url = new URL(target, window.location.origin)
  if (url.origin !== window.location.origin || /^\/(?:api|assets)(?:\/|$)/.test(url.pathname) || url.pathname === '/index.html') return null
  return url
}

/** 全局路由处理器已接管提示，登录流程不再重复报告同一异常。 */
export function isHandledRouteError(error: unknown): boolean {
  return error instanceof Error && handledErrors.has(error)
}

/** 确认入口换版才替换导航一次；其余异常统一提示，避免静默停留或刷新循环。 */
export async function handleRouteError(error: unknown, target: string, isCurrentNavigation: () => boolean = () => true): Promise<void> {
  if (error instanceof Error) handledErrors.add(error)
  const generation = recoveryGeneration
  const isCurrent = () => generation === recoveryGeneration && isCurrentNavigation()
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    if (!isCurrent()) return
    const routeLoadFailed = error instanceof Error && /^(?:Failed to fetch dynamically imported module:|error loading dynamically imported module:|Importing a module script failed\.?$|Unable to preload CSS for )/i.test(error.message)
    const current = routeLoadFailed ? moduleEntry(document) : null
    const destination = pageTarget(target)
    if (current && destination) {
      const controller = new AbortController()
      timer = setTimeout(() => controller.abort(), 5000)
      const response = await fetch('/index.html', { cache: 'no-store', signal: controller.signal })
      if (!isCurrent()) return
      if (response.ok) {
        const content = await response.text()
        if (!isCurrent()) return
        const latest = moduleEntry(new DOMParser().parseFromString(content, 'text/html'))
        if (latest && latest !== current) {
          const versions = `${current}->${latest}`
          if (sessionStorage.getItem(RECOVERY_KEY) !== versions && isCurrent()) {
            sessionStorage.setItem(RECOVERY_KEY, versions)
            window.location.replace(destination.href)
            return
          }
        }
      }
    }
  } catch {
    // 网络、解析和存储异常均回落到可见提示，不扩大恢复范围。
  } finally {
    clearTimeout(timer)
  }
  if (isCurrent()) message.error('页面加载失败，请稍后重试')
}
