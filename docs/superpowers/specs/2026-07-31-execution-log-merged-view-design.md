# 执行详情日志集中显示与可拖拽高度 设计文档

日期：2026-07-31
状态：已确认（用户批准）
范围：仅前端 `frontend/src/views/execution/ExecutionDetail.vue`，后端零改动

## 一、需求

1. 取消各执行步骤单独显示日志：不再"点步骤节点切换日志焦点"，日志框固定展示**全部步骤日志的合并流**（按步骤 1→N 顺序拼接，运行中实时追加）。
2. 日志显示框支持上下拖拽自定义高度；默认高度自适应铺满初始视口（打开页面时日志框底部贴住视口底，替代现固定 440px）。

已确认的交互决策：

- 步骤条**保留**并支持定位：点击步骤节点 → 日志框容器内滚动到该步骤日志起始位置（不再切换/清空日志）。
- 合并流中每个步骤日志开头**插入分隔标记行**（Jenkins 风格）。
- 拖拽高度**不持久化**：仅当次生效，刷新/再次进入恢复默认铺满视口。

## 二、方案选型

| 方案 | 说明 | 结论 |
| --- | --- | --- |
| A. 前端聚合 | 复用现有 `/executions/{eid}/logs?step_order&offset` 接口，前端按步骤维护独立缓冲与偏移，合并渲染 | **采用**：后端零改动，改动集中单文件 |
| B. 后端合并接口 | 服务端跨多 step 日志文件拼接 + 复合游标 | 否决：增量语义复杂，收益与 A 等同 |
| C. 执行级单文件落盘 | 引擎改为每次执行一个日志文件 | 否决：破坏按步骤日志 API、目录约定与历史数据 |

## 三、设计细节

### 3.1 数据层：按步骤分段的合并日志流

```typescript
interface LogSegment {
  step_order: number
  step_name: string
  lines: string[]
  offset: number   // 该步骤下一次拉取的行偏移
  eof: boolean     // 服务端已读到文件尾
  done: boolean    // 步骤已终态 且 eof（不再轮询）
}
const segments = ref<LogSegment[]>([])  // 按 step_order 升序
```

- **初始加载**：详情加载后，按顺序为每个非 `pending/skipped` 步骤创建段并循环拉取（每请求 limit=2000）直至 eof。
- **实时轮询**（沿用 2s 定时器）：每 tick 只对"已开跑且未 done"的段拉增量；串行执行下同一时刻实际只有 1 个活跃段，请求量与现状持平。保留在飞标志（logFetching）防堆叠。
- **步骤终态补拉**：`step_status` 事件到达且状态为终态时，对该段补拉一次收尾日志后置 `done`；`execution_status` 终态时对全部未 done 段各补拉一次（对齐现有终态补拉行为）。
- **新段发现**：轮询/事件推进中发现步骤从 pending 转为 running 时动态追加段。
- **行数上限**：合并流客户端总行数上限 20000；超限从最早段头部丢弃，并在该段顶部渲染"…较早日志已省略…"提示行（段级 truncated 标志）。
- 原"加载更多"按钮随自动拉全量取消；单请求分页语义由拉取循环内部消化。

### 3.2 渲染：单日志框 + 分隔标记

- 日志框按段顺序渲染；每段开头一条高亮分隔行：`━━━ 步骤 {n} · {step_name} ━━━`（终端风格弱化色 + 加粗，与普通日志行明显区分；分隔行不计入 20000 行上限）。
- `pending/skipped` 步骤不渲染段。
- 日志头部标题改为"执行日志（全部步骤）"；移除"未选择步骤"空态，改为"暂无日志输出"。
- 每段分隔行元素带 `ref`/`data-step` 锚点，供步骤条定位。

### 3.3 步骤条：保留状态展示 + 点击定位

- 步骤条 UI 与状态逻辑不变（状态圆点/耗时/退出码/连接线）。
- 点击节点：`logBox.scrollTop = 该段分隔行元素.offsetTop`（容器内定位，非页面滚动）；若该步骤尚无日志段（pending/skipped）则点击无操作；节点不再有"active 焦点"高亮态（无焦点概念），hover 样式保留。
- `activeStep`/`selectStep`/`activeStepInfo` 等焦点态代码删除。

### 3.4 自动跟底优化

- 仅当滚动条接近底部（距底 <40px）时，新日志到达才自动滚到底；用户上翻阅读或点击定位后不再打扰。
- 实现：logBox scroll 事件维护 `nearBottom` 标志，`scrollLogToBottom()` 前判断。

### 3.5 高度自适应 + 拖拽

- **默认高度**：`onMounted` 后测量 `logBox.getBoundingClientRect().top`，设 `height = window.innerHeight − top − 底部留白(16px)`，最小 240px；`window.resize` 时若用户未手动拖拽过则重算（`userResized` 标志）。
- **拖拽手柄**：日志框底边缘 8px 高手柄条（`cursor: ns-resize` + 居中握纹视觉），`mousedown` 后监听 `document mousemove/mouseup` 调整高度，范围 [240px, `window.innerHeight − 120px`]；拖拽置 `userResized = true`；不写 localStorage。
- 拖拽期间 `user-select: none` 防止选中文本。

### 3.6 错误处理

- 单段日志请求失败：静默跳过该段本轮拉取（与现状一致），不阻塞其他段、不弹错。
- 快速离开页面：沿用 teardown（清定时器 + pollAbort）；拉取循环检查 abort 标志提前退出。

## 四、测试与验证

1. `cd frontend; npm run build; npx vue-tsc --noEmit` 零错误。
2. 容器环境实测（多步骤模板提单执行）：
   - 合并流按步骤顺序展示且实时追加，分隔行正确；
   - 点击步骤节点定位到对应分隔行；
   - 默认高度铺满视口；拖拽调整生效、刷新后恢复默认；
   - 上翻阅读时新日志不强制拉底，回到底部恢复跟随；
   - 执行终态后日志完整（含最后一段收尾日志）。

## 五、影响面

- 仅 `ExecutionDetail.vue`（script + template + style）。
- 后端 API、日志落盘、执行引擎、其余页面均不受影响。
- docs：02-技术架构设计 §4.3"切换步骤即重置偏移重拉"表述需同步为合并流拉取语义（竣工口径最小同步）。
