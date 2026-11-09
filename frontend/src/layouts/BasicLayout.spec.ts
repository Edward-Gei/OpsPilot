// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import Antd from 'ant-design-vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import BasicLayout from './BasicLayout.vue'

const api = vi.hoisted(() => ({
  getUnreadCount: vi.fn(), listNotifications: vi.fn(), markRead: vi.fn(), markAllRead: vi.fn(),
}))
const permissions = vi.hoisted(() => new Set<string>())
vi.mock('@/api/notification', () => api)
vi.mock('@/api/applicationConfig', () => ({
  listConfigApprovalTodo: async () => ({ items: [], total: 0 }),
}))
vi.mock('@/stores/user', () => ({ useUserStore: () => ({
  userInfo: null,
  hasPerm: (permission: string) => permissions.has(permission),
  hasAnyPerm: (items: string[]) => items.some(permission => permissions.has(permission)),
}) }))
vi.mock('@/stores/theme', () => ({ useThemeStore: () => ({ mode: 'light' }) }))

const notification = (event: string, refType: string, refId: number, isRead = false) => ({
  id: 17, event, title: '配置通知', content: null as string | null, ref_type: refType, ref_id: refId,
  is_read: isRead, created_at: null,
})
let wrapper: ReturnType<typeof mount> | undefined

beforeEach(() => {
  vi.clearAllMocks()
  permissions.clear()
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(),
  }))
  api.getUnreadCount.mockResolvedValue({ count: 2 })
  api.markRead.mockResolvedValue(null)
})
afterEach(async () => {
  wrapper?.unmount()
  await flushPromises()
  vi.useRealTimers()
  document.body.innerHTML = ''
  document.body.removeAttribute('style')
  vi.unstubAllGlobals()
})

async function openNotifications(item: ReturnType<typeof notification>) {
  api.listNotifications.mockResolvedValue({ items: [item], total: 1 })
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
  ] })
  await router.push('/')
  wrapper = mount(BasicLayout, { attachTo: document.body, global: {
    plugins: [Antd, router], renderStubDefaultSlot: true,
    stubs: { ADropdown: { name: 'ADropdown', template: '<div><slot /><slot name="overlay" /></div>' }, RouterView: true },
  } })
  await flushPromises()
  wrapper.findAllComponents({ name: 'ADropdown' })[0]!.vm.$emit('openChange', true)
  await flushPromises()
  return { wrapper, router }
}

it('数据库管理紧接域名管理，保留原有四个子入口', async () => {
  for (const permission of ['cmdb:read', 'domain:read', 'config:read', 'database:read', 'database:write', 'database:query', 'database:audit']) permissions.add(permission)
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/database/instances', component: { template: '<div />' } },
  ] })
  await router.push('/database/instances')
  wrapper = mount(BasicLayout, { global: { plugins: [Antd, router], stubs: { RouterView: true } } })
  await flushPromises()
  const labels = wrapper.findAll('.menu-item').map(item => item.text())
  expect(labels.indexOf('数据库管理')).toBe(labels.indexOf('域名管理') + 1)
  expect(wrapper.findAll('.menu-subitem').map(item => item.text())).toEqual(['实例台账', 'SQL工作台', '执行历史', '执行审计'])
})

it('仅查询权限保留数据库查询与执行历史入口', async () => {
  permissions.add('database:query')
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/database/workbench', component: { template: '<div />' } },
  ] })
  await router.push('/database/workbench')
  wrapper = mount(BasicLayout, { global: { plugins: [Antd, router], stubs: { RouterView: true } } })
  await flushPromises()
  expect(wrapper.findAll('.menu-subitem').map(item => item.text())).toEqual(['SQL工作台', '执行历史'])
})

it('移动导航可通过菜单按钮打开，并通过遮罩关闭且保留原导航', async () => {
  permissions.add('database:query')
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/database/history', component: { template: '<div />' } },
  ] })
  await router.push('/database/history')
  wrapper = mount(BasicLayout, { global: { plugins: [Antd, router], stubs: { RouterView: true } } })
  await flushPromises()
  const toggle = wrapper.get('button[aria-label="导航菜单"]')
  expect(toggle.attributes('aria-expanded')).toBe('false')
  await toggle.trigger('click')
  expect(toggle.attributes('aria-expanded')).toBe('true')
  expect(wrapper.findAll('.menu-subitem').map(item => item.text())).toEqual(['SQL工作台', '执行历史'])
  await wrapper.get('button[aria-label="关闭导航遮罩"]').trigger('click')
  expect(toggle.attributes('aria-expanded')).toBe('false')
})

it('移动导航选择数据库页面后关闭，Escape也能关闭', async () => {
  permissions.add('database:query')
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/database/history', component: { template: '<div />' } },
    { path: '/database/workbench', component: { template: '<div />' } },
  ] })
  await router.push('/database/history')
  wrapper = mount(BasicLayout, { global: { plugins: [Antd, router], stubs: { RouterView: true } } })
  await flushPromises()
  const toggle = wrapper.get('button[aria-label="导航菜单"]')
  await toggle.trigger('click')
  await wrapper.findAll('.menu-subitem').find(item => item.text() === 'SQL工作台')!.trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/database/workbench')
  expect(toggle.attributes('aria-expanded')).toBe('false')
  await toggle.trigger('click')
  await wrapper.get('.layout').trigger('keydown', { key: 'Escape' })
  expect(toggle.attributes('aria-expanded')).toBe('false')
})

async function mountNavigation(path = '/database/history') {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/:pathMatch(.*)*', component: { template: '<div />' } },
  ] })
  await router.push(path)
  wrapper = mount(BasicLayout, { attachTo: document.body, global: {
    plugins: [Antd, router], stubs: { RouterView: true },
  } })
  await flushPromises()
  return { wrapper, router, toggle: wrapper.get('button[aria-label="导航菜单"]') }
}

it('移动导航使用整行按钮，展开状态可读，键盘焦点限制在菜单并在关闭后恢复', async () => {
  permissions.add('database:query')
  document.body.style.overflow = 'auto'
  const { wrapper, router, toggle } = await mountNavigation()
  ;(toggle.element as HTMLButtonElement).focus()
  await toggle.trigger('click')
  await flushPromises()
  const close = wrapper.get('button[aria-label="关闭导航"]')
  expect(document.activeElement).toBe(close.element)
  expect(wrapper.get('.main').attributes('inert')).toBeDefined()
  expect(document.body.style.overflow).toBe('hidden')
  const database = wrapper.findAll('.menu-item').find(item => item.text() === '数据库管理')!
  expect(database.element.tagName).toBe('BUTTON')
  expect(database.attributes('aria-expanded')).toBe('true')
  await database.trigger('click')
  expect(database.attributes('aria-expanded')).toBe('false')
  await database.trigger('click')
  const menuButtons = wrapper.findAll('.sider button')
  const last = menuButtons[menuButtons.length - 1]!
  ;(last.element as HTMLButtonElement).focus()
  await last.trigger('keydown', { key: 'Tab' })
  expect(document.activeElement).toBe(close.element)
  await close.trigger('keydown', { key: 'Tab', shiftKey: true })
  expect(document.activeElement).toBe(last.element)
  const workbench = wrapper.findAll('.menu-subitem').find(item => item.text() === 'SQL工作台')!
  expect(workbench.element.tagName).toBe('BUTTON')
  await workbench.get('.menu-subicon').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.path).toBe('/database/workbench')
  expect(wrapper.get('.main').attributes('inert')).toBeUndefined()
  expect(document.body.style.overflow).toBe('auto')
  expect(document.activeElement).toBe(toggle.element)
})

it.each([
  ['/job/templates/tickets', '工单模板'],
  ['/job/templates/processes', '流程模板'],
])('移动导航点击当前模板路由 %s 也关闭菜单并恢复焦点', async (path, label) => {
  permissions.add('template:read')
  const { wrapper, toggle } = await mountNavigation(path)
  await toggle.trigger('click')
  await wrapper.findAll('.menu-subitem').find(item => item.text() === label)!.trigger('click')
  await flushPromises()
  expect(toggle.attributes('aria-expanded')).toBe('false')
  expect(document.activeElement).toBe(toggle.element)
})

it('移动导航在同路径query变化后关闭', async () => {
  permissions.add('database:query')
  const { router, toggle } = await mountNavigation('/database/history?task_id=31')
  await toggle.trigger('click')
  await router.push('/database/history?task_id=30')
  await flushPromises()
  expect(toggle.attributes('aria-expanded')).toBe('false')
})

it('移动导航打开时关闭已有通知和搜索面板', async () => {
  api.listNotifications.mockResolvedValue({ items: [], total: 0 })
  const { wrapper, toggle } = await mountNavigation()
  await wrapper.get('.search-input').trigger('focus')
  wrapper.findAllComponents({ name: 'ADropdown' })[0]!.vm.$emit('openChange', true)
  await flushPromises()
  expect(wrapper.find('.search-panel').exists()).toBe(true)
  expect(wrapper.findAllComponents({ name: 'ADropdown' })[0]!.props('open')).toBe(true)
  await toggle.trigger('click')
  await flushPromises()
  expect(wrapper.find('.search-panel').exists()).toBe(false)
  expect(wrapper.findAllComponents({ name: 'ADropdown' })[0]!.props('open')).toBe(false)
})

it('移动导航在切换桌面断点或卸载时恢复滚动并移除监听', async () => {
  let mediaChange: ((event: MediaQueryListEvent) => void) | undefined
  const removeListener = vi.fn()
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: true, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: (_: string, listener: (event: MediaQueryListEvent) => void) => {
      if (query === '(max-width: 1024px)') mediaChange = listener
    },
    removeEventListener: removeListener,
  }))
  const { wrapper, toggle } = await mountNavigation()
  await toggle.trigger('click')
  expect(document.body.style.overflow).toBe('hidden')
  expect(mediaChange).toBeDefined()
  mediaChange!({ matches: false } as MediaQueryListEvent)
  await flushPromises()
  expect(toggle.attributes('aria-expanded')).toBe('false')
  expect(document.body.style.overflow).toBe('')
  await toggle.trigger('click')
  wrapper.unmount()
  expect(document.body.style.overflow).toBe('')
  expect(removeListener).toHaveBeenCalledWith('change', mediaChange)
})

it.each([
  ['/database/instances', 'database-instances', '数据库实例', undefined],
  ['/database/instances/new', 'database-instance-new', '新建数据库实例', 'database-instances'],
  ['/database/instances/23', 'database-instance-detail', '数据库实例详情', 'database-instances'],
  ['/database/instances/23/edit', 'database-instance-edit', '编辑数据库实例', 'database-instances'],
  ['/database/workbench', 'database-workbench', 'SQL工作台', undefined],
  ['/database/history', 'database-history', '执行历史', undefined],
  ['/database/audit', 'database-audit', '数据库执行审计', undefined],
])('%s面包屑归属数据库管理，不受当前可见子菜单影响', async (path, name, title, menuKey) => {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: path!, name, component: { template: '<div />' }, meta: { title, menuKey } },
  ] })
  await router.push(path!)
  wrapper = mount(BasicLayout, { attachTo: document.body, global: {
    plugins: [Antd, router], stubs: { RouterView: true },
  } })
  await flushPromises()
  expect(wrapper.get('.breadcrumb-label').text()).toBe('数据库管理')
  expect(wrapper.get('.breadcrumb-current').text()).toBe(title)
})

it('配置待审批通知进入配置审批页并同步已读和未读数', async () => {
  const { wrapper, router } = await openNotifications(notification('config.pending_approval', 'config_approval', 8))
  expect(wrapper.find('.notif-badge').text()).toBe('2')
  await wrapper.find('.notif-item').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.fullPath).toBe('/ticket/todo?tab=config')
  expect(wrapper.find('.notif-tag').text()).toBe('配置待审批')
  expect(api.markRead).toHaveBeenCalledWith(17)
  expect(wrapper.find('.notif-item').classes()).not.toContain('unread')
  expect(wrapper.find('.notif-badge').text()).toBe('1')
})

it.each([
  ['config.approved', '配置审批通过'],
  ['config.rejected', '配置审批驳回'],
  ['config.publish_success', '配置发布成功'],
  ['config.publish_failed', '配置发布失败'],
  ['config.sync_success', '配置同步成功'],
  ['config.sync_failed', '配置同步失败'],
])('配置结果 %s 跳转关联配置详情，已读通知不重复减数', async (event, label) => {
  const { wrapper, router } = await openNotifications(notification(event, 'config_file', 23, true))
  await wrapper.find('.notif-item').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.fullPath).toBe('/application-configs/23')
  expect(wrapper.find('.notif-tag').text()).toBe(label)
  expect(api.markRead).not.toHaveBeenCalled()
  expect(wrapper.find('.notif-badge').text()).toBe('2')
})

it('连续悬停通知满 1.5 秒才显示完整标题与多行正文，不标已读也不跳转', async () => {
  const title = '配置 mt-hello-world-production-service.yaml v16 发布成功，完整结果请查阅'
  const content = '配置文件 mt-hello-world-production-service.yaml v16 发布成功。\n任务 #168：<b>保留原始文本</b>'
  const { wrapper, router } = await openNotifications({
    ...notification('config.publish_success', 'config_file', 23), title, content,
  })
  vi.useFakeTimers()
  expect(document.querySelector('[role="tooltip"]')).toBeNull()
  await wrapper.find('.notif-item').trigger('mouseenter')
  await vi.advanceTimersByTimeAsync(1499)
  expect(document.querySelector('[role="tooltip"]')).toBeNull()
  await vi.advanceTimersByTimeAsync(1)
  const preview = document.querySelector('[role="tooltip"]')
  expect(preview).not.toBeNull()
  expect(preview!.textContent).toContain(title)
  expect(preview!.textContent).toContain(content)
  expect(document.querySelector('[role="tooltip"] b')).toBeNull()
  expect(api.markRead).not.toHaveBeenCalled()
  expect(wrapper.find('.notif-item').classes()).toContain('unread')
  expect(wrapper.find('.notif-badge').text()).toBe('2')
  expect(router.currentRoute.value.fullPath).toBe('/')
})

it('悬停未满 1.5 秒移开会取消预览，再次移入重新计时', async () => {
  const { wrapper } = await openNotifications(notification('config.sync_success', 'config_file', 23))
  vi.useFakeTimers()
  const item = wrapper.find('.notif-item')
  await item.trigger('mouseenter')
  await vi.advanceTimersByTimeAsync(1000)
  await item.trigger('mouseleave')
  await vi.advanceTimersByTimeAsync(2500)
  expect(document.querySelector('[role="tooltip"]')).toBeNull()
  await item.trigger('mouseenter')
  await vi.advanceTimersByTimeAsync(1499)
  expect(document.querySelector('[role="tooltip"]')).toBeNull()
  await vi.advanceTimersByTimeAsync(1)
  expect(document.querySelector('[role="tooltip"]')?.textContent).toBe('配置通知')
  expect(api.markRead).not.toHaveBeenCalled()
})

it('无正文通知悬停时仍可阅读完整标题', async () => {
  const title = '配置 mt-hello-world-production-service.yaml 等待第 16 个版本的审批'
  const { wrapper } = await openNotifications({
    ...notification('config.pending_approval', 'config_approval', 8), title,
  })
  vi.useFakeTimers()
  await wrapper.find('.notif-item').trigger('mouseenter')
  await vi.advanceTimersByTimeAsync(1500)
  expect(document.querySelector('[role="tooltip"]')?.textContent).toBe(title)
})

it('长多行通知提供视口内滚动区域，指针移入预览后保持展开', async () => {
  const content = Array.from({ length: 96 }, (_, index) => `第 ${index + 1} 行完整审批意见`).join('\n')
  const { wrapper } = await openNotifications({
    ...notification('config.rejected', 'config_file', 23), content,
  })
  vi.useFakeTimers()
  await wrapper.find('.notif-item').trigger('mouseenter')
  await vi.advanceTimersByTimeAsync(1500)
  const preview = document.querySelector<HTMLElement>('[role="tooltip"]')!
  expect(preview.textContent).toContain(content)
  expect(getComputedStyle(preview).overflowY).toBe('auto')
  expect(getComputedStyle(preview).maxHeight).toBe('calc(100vh - 48px)')
  await wrapper.find('.notif-item').trigger('mouseleave')
  const popup = preview.closest<HTMLElement>('.ant-tooltip')!
  popup.dispatchEvent(new MouseEvent('mouseenter'))
  await vi.advanceTimersByTimeAsync(150)
  expect(popup.classList.contains('ant-tooltip-hidden')).toBe(false)
  expect(api.markRead).not.toHaveBeenCalled()
})

it.each([
  ['ticket.approved', 'ticket', '/ticket/list?id=23'],
  ['execution.success', 'execution', '/executions/23'],
])('保留原有 %s 通知导航', async (event, refType, target) => {
  const { wrapper, router } = await openNotifications(notification(event, refType, 23))
  await wrapper.find('.notif-item').trigger('click')
  await flushPromises()
  expect(router.currentRoute.value.fullPath).toBe(target)
})
