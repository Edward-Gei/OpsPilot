<script setup lang="ts">
// 基础布局：白/深色侧边栏（药丸高亮 + 里程碑徽标）+ 顶栏（搜索/主题切换/通知）+ 内容区
// M1：菜单按权限显隐、系统管理子菜单、用户下拉（个人中心/退出登录）
import { computed, onMounted, onUnmounted, ref, watch, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  AppstoreAddOutlined,
  AppstoreOutlined,
  AuditOutlined,
  BellOutlined,
  CloudServerOutlined,
  CodeOutlined,
  DownOutlined,
  FileDoneOutlined,
  KeyOutlined,
  LogoutOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SettingOutlined,
  TeamOutlined,
  ThunderboltOutlined,
  UserOutlined,
} from '@ant-design/icons-vue'
import { pollTodoEvents, todoTickets } from '@/api/ticket'
import {
  getUnreadCount,
  listNotifications,
  markAllRead,
  markRead,
  type NotificationItem,
} from '@/api/notification'
import { globalSearch, type SearchResult } from '@/api/search'
import { useThemeStore } from '@/stores/theme'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const themeStore = useThemeStore()
const userStore = useUserStore()

interface MenuItem {
  children?: MenuItem[]
  key: string
  label: string
  icon: Component
  path?: string // 可跳转路由
  perm?: string // 所需权限点（无权隐藏）
  milestone?: string // 未开放模块显示里程碑徽标
  count?: number // 待办计数角标（0 不显示）
}

// 待办审批角标：路由切换时刷新（审批/提交后进其他页能看到最新计数，FLOW-06）
const todoCount = ref(0)
let todoCountRequestSeq = 0

/** 拉取待我审批总数（page_size=1 只取 total）；无审批权限不请求 */
async function loadTodoCount() {
  if (!userStore.hasPerm('ticket:approve')) return
  const requestSeq = ++todoCountRequestSeq
  try {
    const data = await todoTickets({ page: 1, page_size: 1 })
    if (requestSeq !== todoCountRequestSeq || todoPollAbort?.signal.aborted) return
    todoCount.value = data.total
  } catch {
    /* 角标拉取失败静默，不影响布局 */
  }
}

let todoPollAbort: AbortController | null = null
let todoPolling = false
let lastTodoSeq = 0

/** 待办角标事件主通道：串行长轮询，异常后退避重试。 */
async function startTodoEventPoll() {
  if (!userStore.hasPerm('ticket:approve') || todoPolling) return
  todoPolling = true
  todoPollAbort = new AbortController()
  try {
    while (!todoPollAbort.signal.aborted) {
      try {
        const data = await pollTodoEvents(lastTodoSeq, todoPollAbort.signal)
        if (todoPollAbort.signal.aborted) return
        lastTodoSeq = data.last_seq // 服务端会在游标失效时回退，必须直接覆盖。
        if (data.events.length) {
          await loadTodoCount()
          if (todoPollAbort.signal.aborted) return
        }
      } catch {
        if (todoPollAbort.signal.aborted) return
        await new Promise((resolve) => window.setTimeout(resolve, 3000))
        if (todoPollAbort.signal.aborted) return
      }
    }
  } finally {
    todoPolling = false
  }
}

// 六大模块导航：已开放项按权限显隐，其余随各里程碑逐步开放
const menuGroups = computed(() =>
  [
    {
      title: '主导航',
      items: [
        { key: 'dashboard', label: '工作台', icon: AppstoreOutlined, path: '/' },
        // M2 已开放：资源管理拆分为主机/应用两个入口，按 cmdb:read 权限显隐
        { key: 'cmdb-hosts', label: '主机管理', icon: CloudServerOutlined, path: '/cmdb/hosts', perm: 'cmdb:read' },
        { key: 'cmdb-apps', label: '应用管理', icon: AppstoreAddOutlined, path: '/cmdb/apps', perm: 'cmdb:read' },
        // M3 已开放：作业中心拆分为模板/凭据两个入口（作业主机配置已入系统设置）
        { key: 'job-templates', label: '模板管理', icon: CodeOutlined, path: '/job/templates', perm: 'template:read' },
        { key: 'job-credentials', label: '凭据管理', icon: KeyOutlined, path: '/job/credentials', perm: 'credential:read' },
        // M4 已开放：工单中心 + 待办审批（角标显示待我审批数）
        { key: 'ticket-list', label: '工单中心', icon: FileDoneOutlined, path: '/ticket/list', perm: 'ticket:read' },
        { key: 'ticket-todo', label: '待办审批', icon: AuditOutlined, path: '/ticket/todo', perm: 'ticket:approve', count: todoCount.value },
                // M5 已开放：执行中心（执行记录 + 矩阵/实时日志）
                { key: 'execution-list', label: '执行中心', icon: ThunderboltOutlined, path: '/executions', perm: 'execution:read' },
        { key: 'audit-logs', label: '安全审计', icon: SafetyCertificateOutlined, path: '/audit', perm: 'audit:read' },
                // M6 已开放：通知中心（渠道配置 + 事件映射 + 发送记录）
                { key: 'notify', label: '通知中心', icon: BellOutlined, path: '/notify', perm: 'notify:read' },
      ] as MenuItem[],
    },
    {
      title: '系统',
      items: [
        {
          key: 'system-users',
          label: '用户管理',
          icon: TeamOutlined,
          path: '/system/users',
          perm: 'user:read',
        },
        {
          key: 'system-roles',
          label: '角色管理',
          icon: SafetyCertificateOutlined,
          path: '/system/roles',
          perm: 'role:read',
        },
        {
          key: 'system-settings',
          label: '系统设置',
          icon: SettingOutlined,
          path: '/system/settings',
          perm: 'system:config',
        },
      ] as MenuItem[],
    },
  ]
    .map((g) => ({
      ...g,
      items: g.items.filter((it) => !it.perm || userStore.hasPerm(it.perm)),
    }))
    .filter((g) => g.items.length > 0),
)

// 当前高亮菜单：跟随路由（详情页可用 meta.menuKey 指定归属菜单）
const activeKey = computed(() => (route.meta.menuKey as string) || (route.name as string) || 'dashboard')
const templateMenuOpen = ref(route.path.startsWith('/job/templates'))
// 顶栏标题：跟随路由 meta
const pageTitle = computed(() => (route.meta.title as string) || '工作台')

const me = computed(() => userStore.userInfo)
const avatarChar = computed(() =>
  (me.value?.display_name || me.value?.username || 'U').charAt(0).toUpperCase(),
)
const roleNames = computed(() => me.value?.roles.map((r) => r.name).join(' / ') || '—')

// 菜单点击：未开放模块给出里程碑提示，不跳转
function onMenuClick(item: MenuItem) {
  if (item.key === 'job-templates') {
    templateMenuOpen.value = !templateMenuOpen.value
    return
  }
  if (item.milestone) {
    message.info(`「${item.label}」将在 ${item.milestone} 里程碑开放`)
    return
  }
  if (item.path) router.push(item.path)
}

onMounted(loadTodoCount)
onMounted(() => void startTodoEventPoll())
onUnmounted(() => todoPollAbort?.abort())
watch(() => route.path, (path) => {
  loadTodoCount()
  if (path.startsWith('/job/templates')) templateMenuOpen.value = true
})

// ===== 站内通知（NOTIFY-06）：铃铛角标 30s 轮询 + 下拉面板最近 20 条 =====
const unreadCount = ref(0)
const notifOpen = ref(false)
const notifLoading = ref(false)
const notifItems = ref<NotificationItem[]>([])

// 事件标签：与通知中心六事件一致的中文短名
const EVENT_LABELS: Record<string, string> = {
  'ticket.pending_approval': '待审批',
  'ticket.approved': '审批通过',
  'ticket.rejected': '审批驳回',
  'execution.success': '执行成功',
  'execution.failed': '执行失败',
  'execution.interrupted': '执行中断',
}

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前（超 7 天显日期） */
function relativeTime(iso: string | null): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const min = Math.floor(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour} 小时前`
  const day = Math.floor(hour / 24)
  if (day <= 7) return `${day} 天前`
  return iso.slice(0, 10)
}

/** 拉取未读数（页面隐藏时跳过，与待办角标同惯例） */
async function loadUnreadCount() {
  if (document.hidden) return
  try {
    const data = await getUnreadCount()
    unreadCount.value = data.count
  } catch {
    /* 角标拉取失败静默，不影响布局 */
  }
}

/** 面板打开时拉最近 20 条（含已读，未读加粗+蓝点区分） */
async function onNotifOpenChange(open: boolean) {
  notifOpen.value = open
  if (!open) return
  notifLoading.value = true
  try {
    const data = await listNotifications({ page: 1, page_size: 20 })
    notifItems.value = data.items
  } finally {
    notifLoading.value = false
  }
}

/** 单条点击：置已读后按 ref_type 跳转（ticket → 工单中心自动开详情，execution → 执行详情） */
async function onNotifClick(item: NotificationItem) {
  if (!item.is_read) {
    try {
      await markRead(item.id)
      item.is_read = true
      unreadCount.value = Math.max(0, unreadCount.value - 1)
    } catch {
      /* 已读失败不阻断跳转 */
    }
  }
  notifOpen.value = false
  if (item.ref_type === 'execution' && item.ref_id) {
    await router.push(`/executions/${item.ref_id}`)
  } else if (item.ref_type === 'ticket' && item.ref_id) {
    await router.push({ path: '/ticket/list', query: { id: String(item.ref_id) } })
  } else {
    return
  }
  // 同组件导航（已在工单中心/另一执行详情）不会重跑 onMounted，
  // 复用统一刷新的 viewKey 机制强制重挂载响应跳转
  viewKey.value++
}

/** 全部已读：面板内条目同步置已读，角标清零 */
async function onMarkAllRead() {
  if (!unreadCount.value) return
  await markAllRead()
  notifItems.value.forEach((i) => (i.is_read = true))
  unreadCount.value = 0
  message.success('全部已读')
}

let notifTimer = 0
onMounted(() => {
  loadUnreadCount()
  notifTimer = window.setInterval(loadUnreadCount, 30000)
})
onUnmounted(() => window.clearInterval(notifTimer))

// ===== 全局搜索（SEARCH-01~04）：防抖 300ms + 分组下拉面板 =====
const searchWrap = ref<HTMLElement | null>(null)
const searchKeyword = ref('')
const searchOpen = ref(false)
const searchLoading = ref(false)
const searchResult = ref<SearchResult | null>(null)
let searchTimer = 0

// 模板类型中文短名（与模板管理页一致）
const TPL_TYPE_LABELS: Record<string, string> = {
  release: '发布',
  change: '变更',
  ops: '运维',
  other: '其他',
}

// 功能菜单本地过滤：已按权限显隐的菜单项中按名称匹配（零请求，SEARCH-02）
const menuMatches = computed(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  if (!kw) return []
  return menuGroups.value
    .flatMap((g) => g.items)
    .filter((it) => it.path && !it.milestone && it.label.toLowerCase().includes(kw))
    .slice(0, 5)
})

// 空态：功能与四段业务对象均无命中（加载中不算）
const searchEmpty = computed(() => {
  if (searchLoading.value) return false
  const r = searchResult.value
  const bizHit = r
    ? [r.hosts, r.apps, r.tickets, r.templates].some((s) => s !== null && s.total > 0)
    : false
  return menuMatches.value.length === 0 && !bizHit
})

/** 输入防抖：≥1 字符 300ms 后触发聚合搜索；清空则回到空白面板 */
function onSearchInput() {
  window.clearTimeout(searchTimer)
  const kw = searchKeyword.value.trim()
  if (!kw) {
    // 清空关键词：焦点仍在输入框，面板保持打开并回到空白态
    searchLoading.value = false
    searchResult.value = null
    return
  }
  searchOpen.value = true
  searchLoading.value = true
  searchTimer = window.setTimeout(async () => {
    try {
      const data = await globalSearch(kw)
      // 丢弃过期响应：返回时关键词已变则不覆盖
      if (kw === searchKeyword.value.trim()) searchResult.value = data
    } finally {
      searchLoading.value = false
    }
  }, 300)
}

/** 聚焦即弹出面板：无关键词时为空白态，有关键词则展示既有结果 */
function onSearchFocus() {
  searchOpen.value = true
}

/** 结果点击：统一跳列表页并带 keyword（SEARCH-04），功能条目直接跳路由；跳转后关面板清输入 */
async function onSearchGoto(path: string, withKeyword = true) {
  const kw = searchKeyword.value.trim()
  searchOpen.value = false
  searchKeyword.value = ''
  searchResult.value = null
  if (withKeyword && kw) {
    await router.push({ path, query: { keyword: kw } })
  } else {
    await router.push(path)
  }
  // 同组件导航不重跑 onMounted，复用 viewKey 强制重挂载
  viewKey.value++
}

/** 点击搜索区域外关闭面板（Esc 在输入框 keydown 处理） */
function onDocClick(e: MouseEvent) {
  if (searchWrap.value && !searchWrap.value.contains(e.target as Node)) {
    searchOpen.value = false
  }
}
onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))

// 顶栏统一刷新：viewKey 自增强制重挂载当前路由组件，各页面 onMounted 重新拉数
// （替代各模块工具栏分散的刷新按钮）
const viewKey = ref(0)
const refreshing = ref(false)
function onRefresh() {
  viewKey.value++
  loadTodoCount()
  // 短暂旋转反馈，让点击有感知
  refreshing.value = true
  window.setTimeout(() => (refreshing.value = false), 600)
}

/** 退出登录：吊销 refresh 并回登录页 */
async function onLogout() {
  await userStore.logout()
  message.success('已退出登录')
  router.push('/login')
}
</script>

<template>
  <div class="layout">
    <!-- 侧边栏 -->
    <aside class="sider">
      <div class="logo">
        <img class="logo-icon" src="/logo.svg" alt="OpsPilot" />
        <div>
          <div class="logo-name">OpsPilot</div>
          <div class="logo-sub">自动化运维平台</div>
        </div>
      </div>
      <template v-for="group in menuGroups" :key="group.title">
        <div class="menu-group">{{ group.title }}</div>
        <template v-for="item in group.items" :key="item.key">
          <div
            class="menu-item"
            :class="{ active: activeKey === item.key || (item.key === 'job-templates' && route.path.startsWith('/job/templates')), locked: !!item.milestone }"
            @click="onMenuClick(item)"
          >
            <component :is="item.icon" class="menu-icon" />
            <span>{{ item.label }}</span>
            <span v-if="item.milestone" class="badge">{{ item.milestone }}</span>
            <span v-else-if="item.count" class="badge badge-count">{{ item.count }}</span>
            <DownOutlined
              v-if="item.key === 'job-templates'"
              class="menu-caret"
              :class="{ expanded: templateMenuOpen }"
            />
          </div>
          <template v-if="item.key === 'job-templates' && templateMenuOpen">
          <div class="menu-subitems">
          <FileDoneOutlined class="menu-subicon" />
          <div
            class="menu-subitem"
            :class="{ active: activeKey === 'job-template-tickets' }"
            @click.stop="router.push('/job/templates/tickets')"
          >工单模板</div>
          <CodeOutlined class="menu-subicon" />
          <div
            class="menu-subitem"
            :class="{ active: activeKey === 'job-template-processes' }"
            @click.stop="router.push('/job/templates/processes')"
          >流程模板</div>
          </div>
          </template>
        </template>
      </template>
      <div class="sider-user">
        <a-avatar class="user-avatar" :size="34">{{ avatarChar }}</a-avatar>
        <div class="sider-user-meta">
          <b>{{ me?.display_name || me?.username || '未登录' }}</b>
          <span>{{ roleNames }}</span>
        </div>
      </div>
    </aside>

    <!-- 主区 -->
    <div class="main">
      <header class="header">
        <div class="header-left">
          <h1>{{ pageTitle }}</h1>
          <div class="header-sub">OpsPilot 自动化运维平台</div>
        </div>
        <!-- 全局搜索：顶栏居中，防抖后原位下拉分组展示（SEARCH-01） -->
        <div class="header-center">
          <div ref="searchWrap" class="search-wrap">
            <div class="search">
              <SearchOutlined class="search-icon" />
              <input
                v-model="searchKeyword"
                class="search-input"
                placeholder="搜索功能、主机、应用、工单、模板…"
                @input="onSearchInput"
                @focus="onSearchFocus"
                @keydown.esc="searchOpen = false"
              />
            </div>
            <div v-if="searchOpen" class="search-panel">
              <!-- 空白态：聚焦未输入关键词，仅展示占位区域 -->
              <div v-if="!searchKeyword.trim()" class="search-blank"></div>
              <!-- 加载态：独立占位区域居中展示，避免无内容时动画被压缩遮挡 -->
              <div v-else-if="searchLoading" class="search-loading"><a-spin /></div>
              <div v-else-if="searchEmpty" class="search-empty">未找到相关内容</div>
              <div v-else class="search-groups">
                <!-- 功能：纯前端过滤菜单，直接跳路由（不带 keyword） -->
                <div v-if="menuMatches.length" class="search-group">
                  <div class="search-group-title">功能</div>
                  <div
                    v-for="m in menuMatches"
                    :key="m.key"
                    class="search-item"
                    @click="onSearchGoto(m.path!, false)"
                  >
                    <component :is="m.icon" class="search-item-icon" />
                    <span class="search-item-main">{{ m.label }}</span>
                  </div>
                </div>
                <div v-if="searchResult?.hosts?.total" class="search-group">
                  <div class="search-group-title">主机<em>共 {{ searchResult.hosts.total }} 条</em></div>
                  <div
                    v-for="h in searchResult.hosts.items"
                    :key="h.id"
                    class="search-item"
                    @click="onSearchGoto('/cmdb/hosts')"
                  >
                    <span class="search-item-main">{{ h.hostname }}</span>
                    <span class="search-item-sub">{{ h.ip }}</span>
                  </div>
                </div>
                <div v-if="searchResult?.apps?.total" class="search-group">
                  <div class="search-group-title">应用<em>共 {{ searchResult.apps.total }} 条</em></div>
                  <div
                    v-for="a in searchResult.apps.items"
                    :key="a.id"
                    class="search-item"
                    @click="onSearchGoto('/cmdb/apps')"
                  >
                    <span class="search-item-main">{{ a.name }}</span>
                  </div>
                </div>
                <div v-if="searchResult?.tickets?.total" class="search-group">
                  <div class="search-group-title">工单<em>共 {{ searchResult.tickets.total }} 条</em></div>
                  <div
                    v-for="t in searchResult.tickets.items"
                    :key="t.id"
                    class="search-item"
                    @click="onSearchGoto('/ticket/list')"
                  >
                    <span class="search-item-main">{{ t.title }}</span>
                    <span class="search-item-sub">{{ t.ticket_no }}</span>
                  </div>
                </div>
                <div v-if="searchResult?.templates?.total" class="search-group">
                  <div class="search-group-title">模板<em>共 {{ searchResult.templates.total }} 条</em></div>
                  <div
                    v-for="t in searchResult.templates.items"
                    :key="t.id"
                    class="search-item"
                    @click="onSearchGoto('/job/templates')"
                  >
                    <span class="search-item-main">{{ t.name }}</span>
                    <span class="search-item-sub">{{ TPL_TYPE_LABELS[t.type] || t.type }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="header-right">
        <!-- 统一刷新：重挂载当前页面组件，重新拉取数据 -->
        <button class="icon-btn" title="刷新当前页面数据" @click="onRefresh">
          <ReloadOutlined :class="{ spinning: refreshing }" />
        </button>
        <!-- 主题切换：暗色显示太阳（点击切亮），浅色显示月亮 -->
        <button
          class="icon-btn"
          :title="themeStore.mode === 'dark' ? '切换为浅色主题' : '切换为暗色主题'"
          @click="themeStore.toggle()"
        >
          <svg v-if="themeStore.mode === 'dark'" viewBox="0 0 24 24" class="toggle-svg">
            <circle cx="12" cy="12" r="4.5" />
            <path
              d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.6 4.6l1.8 1.8M17.6 17.6l1.8 1.8M4.6 19.4l1.8-1.8M17.6 6.4l1.8-1.8"
            />
          </svg>
          <svg v-else viewBox="0 0 24 24" class="toggle-svg">
            <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z" />
          </svg>
        </button>
        <!-- 站内通知：未读角标 + 下拉面板最近 20 条（NOTIFY-06） -->
        <a-dropdown
          :open="notifOpen"
          trigger="click"
          placement="bottomRight"
          @openChange="onNotifOpenChange"
        >
          <button class="icon-btn" title="通知">
            <BellOutlined />
            <span v-if="unreadCount" class="notif-badge">{{ unreadCount > 99 ? '99+' : unreadCount }}</span>
          </button>
          <template #overlay>
            <div class="notif-panel">
              <div class="notif-head">
                <b>通知</b>
                <a v-if="unreadCount" class="notif-readall" @click="onMarkAllRead">全部已读</a>
              </div>
              <a-spin :spinning="notifLoading">
                <div v-if="!notifLoading && notifItems.length === 0" class="notif-empty">
                  暂无通知
                </div>
                <div v-else class="notif-list">
                  <div
                    v-for="item in notifItems"
                    :key="item.id"
                    class="notif-item"
                    :class="{ unread: !item.is_read }"
                    @click="onNotifClick(item)"
                  >
                    <span class="notif-dot" :class="{ show: !item.is_read }"></span>
                    <div class="notif-body">
                      <div class="notif-title">
                        <span class="notif-tag">{{ EVENT_LABELS[item.event] || item.event }}</span>
                        {{ item.title }}
                      </div>
                      <div class="notif-time">{{ relativeTime(item.created_at) }}</div>
                    </div>
                  </div>
                </div>
              </a-spin>
            </div>
          </template>
        </a-dropdown>
        <!-- 用户下拉：个人中心 / 访问密钥 / 退出登录 -->
        <a-dropdown placement="bottomRight">
          <a-avatar class="user-avatar clickable" :size="36">{{ avatarChar }}</a-avatar>
          <template #overlay>
            <a-menu>
              <a-menu-item key="profile" @click="router.push('/profile')">
                <UserOutlined /> 个人中心
              </a-menu-item>
              <a-menu-item key="tokens" @click="router.push('/profile/tokens')">
                <KeyOutlined /> 访问密钥
              </a-menu-item>
              <a-menu-divider />
              <a-menu-item key="logout" @click="onLogout">
                <LogoutOutlined /> 退出登录
              </a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
        </div>
      </header>
      <main class="content">
        <!-- viewKey 变化时强制重建组件实例（顶栏统一刷新） -->
        <router-view v-slot="{ Component }">
          <component :is="Component" :key="viewKey" />
        </router-view>
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  min-height: 100vh;
}

/* ===== 侧边栏 ===== */
.sider {
  width: 232px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  padding: 16px 12px;
  background: var(--bg-sider);
  border-right: 1px solid var(--border);
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}
.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 8px 18px;
}
.logo-icon {
  width: 38px;
  height: 38px;
  border-radius: 11px;
  box-shadow: 0 4px 10px rgba(37, 99, 235, 0.35);
}
.logo-name {
  font-size: 17px;
  font-weight: 700;
  color: var(--text-1);
}
.logo-sub {
  font-size: 11px;
  color: var(--text-3);
  margin-top: 4px;
}
.menu-group {
  font-size: 11px;
  color: var(--text-3);
  padding: 10px 10px 6px;
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 10px;
  color: var(--text-2);
  margin-bottom: 2px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  user-select: none;
}
.menu-item:hover {
  background: var(--bg-hover);
}
.menu-item.active {
  background: var(--primary);
  color: #fff;
  font-weight: 600;
  box-shadow: 0 4px 10px rgba(37, 99, 235, 0.3);
}
.menu-icon {
  font-size: 16px;
}
.menu-caret {
  margin-left: auto;
  font-size: 12px;
  transition: transform 0.15s ease;
}
.menu-caret.expanded {
  transform: rotate(180deg);
}
.badge {
  margin-left: auto;
  font-size: 10px;
  color: var(--text-3);
  background: var(--bg-hover);
  border-radius: 6px;
  padding: 1px 6px;
}
.menu-item.active .badge {
  background: rgba(255, 255, 255, 0.25);
  color: #fff;
}
/* 待办审批计数角标：红底白字，与里程碑灰徽标区分 */
.badge-count {
  background: var(--error);
  color: #fff;
  font-weight: 700;
}
.menu-item.active .badge-count {
  background: rgba(255, 255, 255, 0.25);
}
.menu-subitem {
  margin: 1px 0;
  padding: 8px 12px;
  border-radius: 8px;
  color: var(--text-2);
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.menu-subitems {
  display: grid;
  grid-template-columns: 20px 1fr;
  align-items: center;
  margin-left: 28px;
}
.menu-subicon {
  color: var(--text-3);
  font-size: 14px;
}
.menu-subitem.active + .menu-subicon {
  color: var(--primary);
}
.menu-subitem:hover {
  background: var(--bg-hover);
}
.menu-subitem.active {
  color: var(--primary);
  background: var(--bg-hover);
  font-weight: 600;
}
.sider-user {
  margin-top: auto;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px;
  border-radius: 12px;
  background: var(--bg-hover);
  border: 1px solid var(--border);
}
.sider-user b {
  font-size: 13px;
  display: block;
  color: var(--text-1);
}
.sider-user span {
  font-size: 11px;
  color: var(--text-3);
  margin-top: 3px;
}
.user-avatar {
  background: linear-gradient(135deg, #6366f1, #2563eb);
  color: #fff;
  font-weight: 700;
  flex-shrink: 0;
}
.user-avatar.clickable {
  cursor: pointer;
}
.sider-user-meta {
  min-width: 0;
}
.sider-user-meta span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ===== 顶栏 ===== */
.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.header {
  height: 64px;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 0 24px;
  background: var(--bg-sider);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 10;
}
.header h1 {
  font-size: 17px;
  font-weight: 700;
  margin: 0;
  color: var(--text-1);
}
.header-sub {
  font-size: 12px;
  color: var(--text-3);
  margin-top: 4px;
}
/* 三段式：左标题 / 中搜索框居中 / 右操作按钮组（SEARCH-01） */
.header-left {
  flex: 1;
  min-width: 0;
}
.header-center {
  flex-shrink: 0;
}
.header-right {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 14px;
}
.search-wrap {
  position: relative;
  width: 320px;
}
.search {
  height: 36px;
  border-radius: 10px;
  background: var(--bg-input);
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  color: var(--text-3);
  font-size: 13px;
  border: 1px solid transparent;
  transition: border-color 0.15s;
}
.search:focus-within {
  border-color: var(--primary);
}
.search-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-1);
  font-size: 13px;
}
.search-input::placeholder {
  color: var(--text-3);
}
/* 搜索下拉面板：与通知面板同风格，原位展开 */
.search-panel {
  position: absolute;
  top: 44px;
  left: 0;
  width: 100%;
  background: var(--bg-sider);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  overflow: hidden;
  z-index: 20;
}
/* 空白态与加载态占位：保证面板有可视高度，加载动画完整可见 */
.search-blank {
  height: 72px;
}
.search-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 120px;
}
.search-groups {
  max-height: 420px;
  overflow-y: auto;
  padding: 4px 0;
}
.search-group + .search-group {
  border-top: 1px solid var(--border);
  margin-top: 4px;
  padding-top: 4px;
}
.search-group-title {
  display: flex;
  align-items: center;
  font-size: 11px;
  color: var(--text-3);
  padding: 6px 14px 2px;
}
.search-group-title em {
  font-style: normal;
  margin-left: auto;
}
.search-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  cursor: pointer;
  transition: background 0.15s;
}
.search-item:hover {
  background: var(--bg-hover);
}
.search-item-icon {
  font-size: 14px;
  color: var(--text-3);
  flex-shrink: 0;
}
.search-item-main {
  font-size: 13px;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.search-item-sub {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-3);
  flex-shrink: 0;
}
.search-empty {
  padding: 28px 0;
  text-align: center;
  color: var(--text-3);
  font-size: 13px;
}
.icon-btn {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-2);
  font-size: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  cursor: pointer;
  transition: background 0.15s;
}
.icon-btn:hover {
  background: var(--bg-hover);
}
/* 刷新按钮点击后短暂旋转一周 */
.spinning {
  animation: spin-once 0.6s ease;
}
@keyframes spin-once {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
@media (prefers-reduced-motion: reduce) {
  .spinning { animation: none; }
}
.toggle-svg {
  width: 17px;
  height: 17px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
}
/* 铃铛未读角标：红底白字，99+ 封顶（0 不显示） */
.notif-badge {
  position: absolute;
  top: -6px;
  right: -6px;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  border-radius: 9px;
  background: var(--error);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  line-height: 15px;
  border: 1.5px solid var(--bg-sider);
  box-sizing: border-box;
}
/* 通知下拉面板 */
.notif-panel {
  width: 340px;
  background: var(--bg-sider);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  overflow: hidden;
}
.notif-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  color: var(--text-1);
  font-size: 14px;
}
.notif-readall {
  font-size: 12px;
  color: var(--primary);
  cursor: pointer;
}
.notif-empty {
  padding: 36px 0;
  text-align: center;
  color: var(--text-3);
  font-size: 13px;
}
.notif-list {
  max-height: 380px;
  overflow-y: auto;
}
.notif-item {
  display: flex;
  gap: 8px;
  padding: 10px 14px;
  cursor: pointer;
  transition: background 0.15s;
}
.notif-item:hover {
  background: var(--bg-hover);
}
.notif-item + .notif-item {
  border-top: 1px solid var(--border);
}
/* 未读蓝点：已读预留占位保持对齐 */
.notif-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-top: 6px;
  flex-shrink: 0;
  visibility: hidden;
  background: var(--primary);
}
.notif-dot.show {
  visibility: visible;
}
.notif-body {
  min-width: 0;
  flex: 1;
}
.notif-title {
  font-size: 13px;
  color: var(--text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.notif-item.unread .notif-title {
  color: var(--text-1);
  font-weight: 600;
}
.notif-tag {
  display: inline-block;
  font-size: 10px;
  color: var(--primary);
  background: var(--bg-hover);
  border-radius: 5px;
  padding: 1px 5px;
  margin-right: 4px;
  vertical-align: 1px;
}
.notif-time {
  font-size: 11px;
  color: var(--text-3);
  margin-top: 3px;
}
.content {
  padding: 24px;
}
</style>
