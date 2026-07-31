# 执行详情日志集中显示与可拖拽高度 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 执行详情页日志改为全部步骤合并流集中显示（分隔行区分步骤、点击步骤条定位），日志框默认铺满视口且底缘可拖拽调高。

**Architecture:** 纯前端改造（方案 A）：后端按步骤的 `/executions/{eid}/logs?step_order&offset` 接口不变，前端为每个步骤维护独立日志段（缓冲+偏移），按步骤顺序渲染为单一合并流。实时链路沿用"事件长轮询 + 2s 日志增量轮询"，每 tick 仅对未完成段拉增量。

**Tech Stack:** Vue 3 `<script setup>` + TypeScript + ant-design-vue 4.x；改动集中于 `frontend/src/views/execution/ExecutionDetail.vue` 单文件。

**Spec:** `docs/superpowers/specs/2026-07-31-execution-log-merged-view-design.md`

**前置说明（工程师须知）：**
- 本项目前端无单元测试框架，验证闭环 = `npm run build` + `npx vue-tsc --noEmit` 零错误 + 容器部署实测（既有惯例）。
- PowerShell 环境：命令分隔用 `;`，不可用 `&&`。
- 当前文件 `ExecutionDetail.vue` 共 566 行：script（L1-262）/ template（L264-366）/ style（L368-565）。行号基于 commit 6f1d6db。

---

### Task 1: 合并日志流（script + template + style 一次改到可编译）

**Files:**
- Modify: `frontend/src/views/execution/ExecutionDetail.vue`

- [ ] **Step 1: script 区——删除步骤焦点态与旧日志状态，替换为分段合并流**

删除以下代码块（L53-137 整段，含注释）：
- `// ---------- 流水线步骤节点（点击切换日志焦点） ----------` 至 `fmtDuration` 之前的 `activeStep`/`activeStepInfo`/`selectStep`（保留 `fmtDuration`，它被步骤条模板引用）
- `// ---------- 日志面板（历史 REST + 定时增量拉取追加） ----------` 整段：`logLines`/`logEof`/`logOffset`/`LOG_CAP`/`logReqSeq`/`logFetching`/`fetchHistoryLogs`/`loadMoreLogs`/`appendLogLines`/`scrollLogToBottom`

在 `fmtDuration` 函数之后插入新代码：

```typescript
// ---------- 合并日志流（全部步骤集中显示，按步骤分段管理缓冲与偏移） ----------
// 设计：后端日志按步骤落盘（/logs?step_order&offset），前端为每步骤维护一个
// LogSegment，按 step_order 顺序渲染为单一合并流；串行执行下同一时刻只有
// 1 个活跃段在增长，轮询请求量与旧"单步骤焦点"模式持平
interface LogSegment {
  step_order: number
  step_name: string
  lines: string[]
  offset: number // 该步骤下一次拉取的行偏移
  eof: boolean // 服务端已读到当前文件尾
  done: boolean // 步骤已终态且 eof：不再拉取
  truncated: boolean // 触发行数上限后段头部已丢弃较早日志
}
const segments = ref<LogSegment[]>([])
const logBox = ref<HTMLElement | null>(null)
// 客户端合并流总行数上限（超出从最早段头部丢弃，防止长任务撑爆内存）
const LOG_CAP = 20000
// 拉取在飞标志：轮询 tick 遇上一轮未完成时跳过，避免慢网下请求堆叠
let logFetching = false
// 离开页面中断拉取循环标志
let abortFetch = false
// 步骤终态集合（与后端步骤状态机一致）
const STEP_FINAL = ['success', 'failed', 'timeout', 'terminated']

/** pending/skipped 步骤不产生日志文件，不建段 */
function hasLog(status: string): boolean {
  return !['pending', 'skipped'].includes(status)
}

/** 为步骤补建日志段（幂等；按 step_order 保序，支持运行中动态发现新步骤） */
function ensureSegment(step: { step_order: number; step_name: string }): LogSegment {
  let seg = segments.value.find((s) => s.step_order === step.step_order)
  if (!seg) {
    seg = {
      step_order: step.step_order, step_name: step.step_name,
      lines: [], offset: 0, eof: false, done: false, truncated: false,
    }
    segments.value.push(seg)
    segments.value.sort((a, b) => a.step_order - b.step_order)
  }
  return seg
}

/** 单段增量拉取直至 eof（每请求 2000 行；失败静默跳过，下轮重试） */
async function fetchSegment(seg: LogSegment): Promise<void> {
  try {
    while (!abortFetch) {
      const data = await execApi.getExecutionLogs(executionId, {
        step_order: seg.step_order, offset: seg.offset, limit: 2000,
      })
      if (data.lines.length) {
        seg.lines.push(...data.lines)
        trimToCapacity()
        scrollLogToBottom()
      }
      seg.offset = data.next_offset
      seg.eof = data.eof
      if (data.eof) break
    }
  } catch {
    /* 日志文件未生成等场景静默（该段本轮跳过） */
  }
}

/** 合并流行数上限裁剪：从最早段头部丢弃并标记 truncated */
function trimToCapacity() {
  let overflow = segments.value.reduce((n, s) => n + s.lines.length, 0) - LOG_CAP
  for (const seg of segments.value) {
    if (overflow <= 0) break
    const drop = Math.min(overflow, seg.lines.length)
    if (drop > 0) {
      seg.lines.splice(0, drop)
      seg.truncated = true
      overflow -= drop
    }
  }
}

/** 拉取一轮：按步骤顺序对所有"已开跑且未 done"的段拉增量并推进 done 标记 */
async function fetchAllSegments(): Promise<void> {
  if (logFetching || !detail.value) return
  logFetching = true
  try {
    for (const step of detail.value.steps) {
      if (abortFetch) return
      if (!hasLog(step.status)) continue
      const seg = ensureSegment(step)
      if (seg.done) continue
      await fetchSegment(seg)
      // 步骤已终态且拉到文件尾：该段完成，后续轮询不再触碰
      if (seg.eof && STEP_FINAL.includes(step.status)) seg.done = true
    }
  } finally {
    logFetching = false
  }
}

// ---------- 底部跟随与步骤定位 ----------
// 仅当用户停留在底部附近(<40px)时新日志才自动滚底；上翻阅读/定位后不打扰
let nearBottom = true

function onLogScroll() {
  const el = logBox.value
  if (!el) return
  nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

function scrollLogToBottom() {
  if (!nearBottom) return
  requestAnimationFrame(() => {
    if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
  })
}

/** 点击步骤节点：日志框容器内定位到该步骤分隔行（尚无日志段则无操作） */
function locateStep(order: number) {
  const box = logBox.value
  const el = box?.querySelector<HTMLElement>(`[data-step="${order}"]`)
  if (!box || !el) return
  nearBottom = false // 定位阅读期间暂停自动跟底
  box.scrollTop = el.offsetTop
}
```

- [ ] **Step 2: script 区——实时链路改用 fetchAllSegments**

以下 4 处逐一替换：

1. `loadDetail` 中删除 `if (activeStep.value === 0 && d.steps.length) selectStep(d.steps[0].step_order)`，替换为 `void fetchAllSegments()`。
2. `applyEvent` 的 `execution_status` 分支：`void fetchHistoryLogs(false)` → `void fetchAllSegments()`（注释不变）。
3. `applyEvent` 的 `step_status` 分支：在字段更新后追加终态补拉：

```typescript
      // 步骤终态：补拉该段收尾日志（fetchAllSegments 内部会推进 done 标记）
      if (STEP_FINAL.includes(step.status)) void fetchAllSegments()
```

4. `startEventPoll` 的 `data.finished` 分支：`void fetchHistoryLogs(false)` → `void fetchAllSegments()`；`tickLogPoll` 整体替换为：

```typescript
/** 日志轮询 tick：对未完成段拉增量（在飞/已终态则跳过） */
function tickLogPoll() {
  if (logFetching || isFinished.value) return
  void fetchAllSegments()
}
```

`teardownRealtime` 不动（它在执行终态时也会被调用，若在其中置 `abortFetch` 会把终态补拉截断）；`abortFetch` 仅在页面卸载时置位：`onBeforeUnmount(teardownRealtime)` 改为：

```typescript
onBeforeUnmount(() => {
  // 离开页面：中断拉取循环（终态 teardown 不置此标志，保证终态补拉能跑完）
  abortFetch = true
  teardownRealtime()
})
```

- [ ] **Step 3: template 区——步骤条改定位、日志框改合并流**

步骤条节点（原 L319-323）：`@click="selectStep(s.step_order)"` → `@click="locateStep(s.step_order)"`；class 绑定删除 active 项：`:class="[\`is-${s.status}\`, { active: s.step_order === activeStep }]"` → `:class="\`is-${s.status}\`"`。

日志面板整体（原 L350-364）替换为：

```vue
    <!-- 日志显示屏：全部步骤合并流（分隔行区分步骤），历史 REST + 定时增量轮询 -->
    <div class="log-panel">
      <div class="log-head">
        <span>执行日志（全部步骤）</span>
      </div>
      <div
        ref="logBox"
        class="log-body"
        :style="{ height: logHeight + 'px' }"
        @scroll="onLogScroll"
      >
        <template v-for="seg in segments" :key="seg.step_order">
          <div class="log-sep" :data-step="seg.step_order">
            ━━━ 步骤 {{ seg.step_order }} · {{ seg.step_name }} ━━━
          </div>
          <div v-if="seg.truncated" class="log-trunc">…较早日志已省略…</div>
          <div v-for="(line, i) in seg.lines" :key="i" class="log-line">{{ line }}</div>
        </template>
        <div v-if="!segments.length" class="log-empty">暂无日志输出</div>
      </div>
    </div>
```

注意：`logHeight` 在 Task 2 定义；为让 Task 1 独立可编译，本 Task 先在 script 区临时加 `const logHeight = ref(440)`（Task 2 会扩展它，不会冲突）。

- [ ] **Step 4: style 区——删除 active 样式、新增分隔行样式**

1. 删除 `.step-node.active { ... }` 规则（原 L423-426）。
2. `.log-body` 规则：删除 `height: 440px;` 行，追加 `position: relative;`（供分隔行 `offsetTop` 容器内定位）。
3. `.log-empty` 的 `padding-top: 180px` 改为 `padding-top: 40px`（合并流下空态不再垂直居中于固定高框）。
4. 在 `.log-line` 规则后新增：

```css
/* 步骤分隔行：Jenkins 风格高亮标记，与普通日志行明显区分 */
.log-sep {
  color: #7dd3fc;
  font-weight: 700;
  margin: 8px 0 2px;
  user-select: none;
}
.log-sep:first-child {
  margin-top: 0;
}
/* 行数上限裁剪提示 */
.log-trunc {
  color: #64748b;
  font-style: italic;
}
```

- [ ] **Step 5: 类型与构建验证**

Run: `cd d:\SourceCode\qoder\frontend; npx vue-tsc --noEmit; npm run build`
Expected: vue-tsc 零输出；build 成功（chunk >500kB 警告为既有现象）。若报 `activeStep`/`logLines` 等未定义引用，说明 Step 1-3 有残留，逐一清理。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/execution/ExecutionDetail.vue
git commit -m "feat(ui): 执行日志改为全部步骤合并流集中显示"
```

---

### Task 2: 日志框默认铺满视口 + 底缘拖拽调高

**Files:**
- Modify: `frontend/src/views/execution/ExecutionDetail.vue`

- [ ] **Step 1: script 区——高度状态与拖拽逻辑**

将 Task 1 临时加的 `const logHeight = ref(440)` 替换为完整实现（插在 `locateStep` 之后）：

```typescript
// ---------- 日志框高度：默认铺满视口 + 底缘拖拽自定义（不持久化，刷新恢复默认） ----------
const logHeight = ref(440)
const MIN_LOG_HEIGHT = 240
// 用户拖拽过则窗口 resize 不再重算默认高度
let userResized = false

/** 默认高度 = 视口高 − 日志框顶部位置 − 留白(手柄+边框+页面底距) */
function fitLogHeight() {
  if (userResized || !logBox.value) return
  const top = logBox.value.getBoundingClientRect().top
  logHeight.value = Math.max(MIN_LOG_HEIGHT, Math.floor(window.innerHeight - top - 28))
}

/** 底缘手柄拖拽：mousedown 后跟踪全局 mousemove 调整高度，mouseup 收尾 */
function onResizeStart(e: MouseEvent) {
  e.preventDefault()
  userResized = true
  const startY = e.clientY
  const startH = logHeight.value
  const maxH = window.innerHeight - 120
  document.body.style.userSelect = 'none' // 拖拽期间禁止选中文本
  const onMove = (ev: MouseEvent) => {
    logHeight.value = Math.min(maxH, Math.max(MIN_LOG_HEIGHT, startH + ev.clientY - startY))
  }
  const onUp = () => {
    document.body.style.userSelect = ''
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}
```

`onMounted` 替换为：

```typescript
onMounted(async () => {
  await loadDetail()
  // 详情渲染完成后再测量日志框顶部位置计算默认高度
  requestAnimationFrame(fitLogHeight)
  window.addEventListener('resize', fitLogHeight)
  if (!isFinished.value) startRealtime()
})
```

`onBeforeUnmount` 替换为（保留 Task 1 的 `abortFetch = true`）：

```typescript
onBeforeUnmount(() => {
  // 离开页面：中断拉取循环（终态 teardown 不置此标志，保证终态补拉能跑完）
  abortFetch = true
  teardownRealtime()
  window.removeEventListener('resize', fitLogHeight)
})
```

- [ ] **Step 2: template 区——log-body 之后加拖拽手柄**

在 `</div>`（log-body 闭合）与 `</div>`（log-panel 闭合）之间插入：

```vue
      <div class="log-resize" title="拖拽调整日志区高度" @mousedown="onResizeStart">
        <span class="grip" />
      </div>
```

- [ ] **Step 3: style 区——手柄样式**

在 `.log-trunc` 规则后新增：

```css
/* 底缘拖拽手柄：ns-resize 光标 + 居中握纹 */
.log-resize {
  height: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: ns-resize;
  background: var(--bg-1, #fff);
  border-top: 1px solid var(--border-1, #e5e7eb);
}
.log-resize:hover .grip {
  background: var(--primary, #6366f1);
}
.log-resize .grip {
  width: 36px;
  height: 3px;
  border-radius: 2px;
  background: var(--border, #d1d5db);
  transition: background-color 0.2s;
}
```

- [ ] **Step 4: 类型与构建验证**

Run: `cd d:\SourceCode\qoder\frontend; npx vue-tsc --noEmit; npm run build`
Expected: 零错误、build 成功。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/execution/ExecutionDetail.vue
git commit -m "feat(ui): 执行日志框默认铺满视口并支持底缘拖拽调高"
```

---

### Task 3: 容器实测 + docs 同步 + 收尾

**Files:**
- Modify: `docs/02-技术架构设计.md`（§4.3 实时日志描述）

- [ ] **Step 1: 重建前端容器**

```powershell
cd d:\SourceCode\qoder\deploy
docker compose build nginx
docker compose up -d nginx
```

Expected: 镜像构建成功、容器重启正常。

- [ ] **Step 2: 页面实测（多步骤模板执行）**

准备：复用 `deploy/loadtest/sshd` 镜像起冒烟目标机（若无存量作业主机可用）：

```powershell
docker run -d --name smoke-sshd --network opspilot_default -e IP_COUNT=0 -e ROOT_PASSWORD=Smoke123! opspilot-loadtest-sshd-matrix
docker inspect smoke-sshd --format "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"
```

浏览器（admin 登录 http://localhost）操作与断言：
1. 创建 3 步骤免审模板（每步 `echo step-N; sleep 3`，作业主机指向冒烟机）→ 提单执行 → 进入执行详情页。
2. 日志框展示 3 个 `━━━ 步骤 N · … ━━━` 分隔行，各段日志按顺序实时追加。
3. 页面初始加载时日志框底部贴住视口底（非 440px 固定高）。
4. 拖拽底缘手柄可调高度（范围 240px ~ 视口高−120px）；刷新页面恢复默认铺满。
5. 执行中上翻滚动条：新日志不强制拉底；点击步骤 1 节点：日志框定位到步骤 1 分隔行。
6. 执行终态后日志完整（最后一步的收尾输出可见）。

清理：`docker rm -f smoke-sshd`。

- [ ] **Step 3: docs 同步（竣工口径）**

`docs/02-技术架构设计.md` §4.3 中原行：

```
- **实时日志**：前端每 2s `GET /executions/{eid}/logs?offset=` 按行偏移增量拉取追加（Jenkins 式尾随）；切换步骤即重置偏移重拉，无订阅状态。
```

替换为：

```
- **实时日志**：前端每 2s 对"已开跑且未完成"的步骤按 `GET /executions/{eid}/logs?step_order&offset=` 行偏移增量拉取（Jenkins 式尾随）；单日志框按步骤顺序合并展示全部步骤日志（分隔行区分步骤），点击步骤节点在日志框内定位到该步骤起始，无订阅状态。
```

- [ ] **Step 4: 全量回归 + Commit**

Run: `cd d:\SourceCode\qoder\frontend; npm run build; npx vue-tsc --noEmit`
Expected: 通过（后端无改动无需回归）。

```bash
git add docs/02-技术架构设计.md
git commit -m "docs: 执行日志合并流展示 文档同步"
```

---

## Self-Review 结论

- 规格覆盖：spec §3.1→Task 1 Step 1-2，§3.2→Task 1 Step 3-4，§3.3→Task 1 Step 3（locateStep），§3.4→Task 1 Step 1（nearBottom），§3.5→Task 2，§3.6→fetchSegment catch + abortFetch，§四→各 Task 验证步骤 + Task 3 实测，§五 docs→Task 3 Step 3 ✅
- 占位符扫描：无 TBD/TODO；所有代码块完整可落 ✅
- 类型一致：`LogSegment`/`segments`/`fetchAllSegments`/`locateStep`/`logHeight`/`onResizeStart` 前后任务命名一致；Task 1 临时 `logHeight = ref(440)` 与 Task 2 完整实现显式衔接 ✅
