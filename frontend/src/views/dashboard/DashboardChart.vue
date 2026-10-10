<script setup lang="ts">
// 工作台局部图表：统一主题、本地字体、容器尺寸与卸载清理。
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { InboxOutlined } from '@ant-design/icons-vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useThemeStore } from '@/stores/theme'

echarts.use([BarChart, LineChart, PieChart, GridComponent, TooltipComponent, CanvasRenderer])
interface ChartItem { name: string; value: number; color: string }
interface ChartSeries { name: string; color: string; values: (number | null)[] }
const props = withDefaults(defineProps<{
  kind: 'donut' | 'bar' | 'stack' | 'line'
  label: string
  items?: ChartItem[]
  categories?: string[]
  series?: ChartSeries[]
  totalLabel?: string
  loading?: boolean
  failed?: boolean
}>(), { items: () => [], categories: () => [], series: () => [] })
const theme = useThemeStore()
const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | undefined
let observer: ResizeObserver | undefined
let disposed = false
const total = computed(() => props.items.reduce((sum, item) => sum + item.value, 0))
/** 高基数时撑开图表与父卡片，少项继续使用页面原有高度。 */
const chartStyle = computed(() => {
  if (props.items.length <= 6) return undefined
  if (props.kind === 'donut') return { height: `${Math.max(190, props.items.length * 30 - 13)}px` }
  if (props.kind === 'bar') return { height: `${Math.max(190, props.items.length * 24)}px` }
  return undefined
})
const hasData = computed(() => props.kind === 'line'
  ? props.series.some(series => series.values.some(value => value !== null))
  : props.kind === 'stack' ? props.series.some(series => series.values.some(value => (value ?? 0) > 0)) : total.value > 0)
// 零值趋势仍绘制真实坐标，用文字提示解释空白周期。
const zeroTrend = computed(() => props.kind === 'line' && hasData.value && !props.series.some(series => series.values.some(value => (value ?? 0) > 0)))
const font = "'OpsPilot Varela Round', 'OpsPilot Noto Sans SC', sans-serif"
function css(name: string) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() }
function resize() { chart?.resize() }

/** 数据为空时销毁实例，避免旧图残留；图表只呈现传入的授权数据。 */
function draw() {
  if (disposed) return
  if (!el.value || !hasData.value || props.loading) { chart?.dispose(); chart = undefined; return }
  chart ??= echarts.init(el.value)
  const base = { animationDuration: 250, textStyle: { fontFamily: font }, tooltip: { trigger: props.kind === 'donut' ? 'item' : 'axis', confine: true, renderMode: 'richText', textStyle: { fontFamily: font } } }
  const axis = { axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: css('--text-3'), fontSize: 11, fontFamily: font } }
  const valueAxis = { ...axis, type: 'value', minInterval: 1, splitLine: { lineStyle: { color: css('--border'), type: 'dashed' } } }
  if (props.kind === 'donut') {
    chart.setOption({ ...base, tooltip: { ...base.tooltip, formatter: '{b}：{c}（{d}%）' }, series: [{ type: 'pie', radius: ['66%', '88%'], center: ['50%', '50%'], label: { show: false }, itemStyle: { borderColor: css('--bg-card'), borderWidth: 3 }, data: props.items.filter(item => item.value > 0).map(item => ({ name: item.name, value: item.value, itemStyle: { color: item.color } })) }] }, true)
  } else if (props.kind === 'bar') {
    chart.setOption({ ...base, grid: { left: 2, right: 24, top: 5, bottom: 4, containLabel: true }, xAxis: { ...valueAxis, show: false }, yAxis: { ...axis, type: 'category', inverse: true, data: props.items.map(item => item.name) }, series: [{ type: 'bar', barMaxWidth: 10, showBackground: true, backgroundStyle: { color: css('--bg-hover'), borderRadius: 5 }, itemStyle: { borderRadius: 5 }, label: { show: true, position: 'right', color: css('--text-2'), fontFamily: font }, data: props.items.map(item => ({ value: item.value, itemStyle: { color: item.color } })) }] }, true)
  } else {
    const line = props.kind === 'line'
    chart.setOption({ ...base, grid: { left: 4, right: 16, top: 14, bottom: 4, containLabel: true }, xAxis: line ? { ...axis, type: 'category', boundaryGap: false, data: props.categories } : valueAxis, yAxis: line ? valueAxis : { ...axis, type: 'category', inverse: true, data: props.categories }, series: props.series.map((series, index) => ({ name: series.name, type: line ? 'line' : 'bar', stack: line ? undefined : 'hosts', data: series.values, barMaxWidth: 17, smooth: line, showSymbol: props.categories.length <= 12, symbolSize: 5, itemStyle: { color: series.color }, lineStyle: { width: 2.5, type: index ? 'dashed' : 'solid' }, areaStyle: line && index === 0 ? { opacity: 0.07 } : undefined })) }, true)
  }
  chart.resize()
}
watch([() => props.items, () => props.series, () => props.categories, () => props.loading, () => theme.mode], () => nextTick(draw), { deep: true })
onMounted(() => {
  observer = new ResizeObserver(resize)
  if (el.value) observer.observe(el.value)
  window.addEventListener('resize', resize)
  draw()
  void document.fonts?.ready.then(() => { if (!disposed) { draw(); resize() } })
})
onUnmounted(() => { disposed = true; observer?.disconnect(); window.removeEventListener('resize', resize); chart?.dispose() })
</script>

<template>
  <div class="dashboard-chart" :class="{ 'donut-layout': kind === 'donut', 'chart-is-empty': !hasData && !loading }" :style="chartStyle" data-testid="dashboard-chart">
    <div class="chart-canvas-wrap" :class="{ 'donut-canvas': kind === 'donut' }">
      <div ref="el" class="chart-canvas" role="img" :aria-label="label" />
      <div v-if="loading" class="chart-overlay" role="status" aria-label="正在加载趋势"><a-spin /></div>
      <div v-else-if="!hasData || zeroTrend" class="chart-overlay chart-empty-state" :data-testid="!hasData ? 'chart-empty' : 'chart-zero-trend'">
        <div class="empty-illustration" aria-hidden="true"><InboxOutlined /><i /><i /></div>
        <strong>{{ failed ? '趋势暂时无法加载' : zeroTrend ? '本周期暂无工单或完成执行' : '暂无数据' }}</strong>
        <span>{{ failed ? '请稍后重试' : zeroTrend ? '新的业务记录将在这里汇总' : '数据就绪后，这里将展示数量与分布' }}</span>
      </div>
      <div v-else-if="kind === 'donut'" class="donut-center"><strong>{{ total.toLocaleString() }}</strong><small>{{ totalLabel }}</small></div>
    </div>
    <div v-if="kind === 'donut' && hasData" class="donut-legend">
      <div v-for="item in items" :key="item.name"><i :style="{ background: item.color }" /><span>{{ item.name }}</span><b>{{ item.value.toLocaleString() }}</b><small>{{ (item.value / total * 100).toFixed(1) }}%</small></div>
    </div>
    <ul v-if="kind === 'bar' && hasData" class="chart-accessible-values" :aria-label="label">
      <li v-for="item in items" :key="item.name">{{ item.name }}：{{ item.value }}</li>
    </ul>
  </div>
</template>

<style scoped>
.chart-empty-state{flex-direction:column;gap:8px;border-radius:12px;background:radial-gradient(ellipse at center,color-mix(in srgb,var(--panel-accent,var(--primary)) 6%,transparent),transparent 72%);pointer-events:none}
.chart-empty-state strong{font-size:12px;font-weight:500;color:var(--text-2)}
.chart-empty-state>span{font-size:11px;color:var(--text-3);text-align:center;padding:0 14px}
.empty-illustration{position:relative;display:flex;align-items:center;justify-content:center;width:62px;height:62px;margin-bottom:3px;border:1px solid color-mix(in srgb,var(--panel-accent,var(--primary)) 17%,transparent);border-radius:50%;background:color-mix(in srgb,var(--panel-accent,var(--primary)) 8%,var(--bg-card));box-shadow:0 0 0 8px color-mix(in srgb,var(--panel-accent,var(--primary)) 3%,transparent);color:color-mix(in srgb,var(--panel-accent,var(--primary)) 70%,var(--text-2));font-size:27px}
.empty-illustration i{position:absolute;right:-4px;top:9px;width:8px;height:8px;border-radius:50%;background:color-mix(in srgb,var(--panel-accent,var(--primary)) 45%,var(--bg-card))}
.empty-illustration i:last-child{right:auto;top:auto;left:-2px;bottom:8px;width:5px;height:5px}
.chart-is-empty.donut-layout{display:block}
.chart-is-empty .donut-canvas{width:100%;height:100%}
.dashboard-chart.chart-is-empty{height:160px}
.chart-accessible-values{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
.dashboard-chart{min-width:0;width:100%;height:190px}.chart-canvas-wrap{position:relative;height:100%;min-width:0;flex:1}.chart-canvas{width:100%;height:100%}.chart-overlay{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--text-3);font-size:12px}.donut-layout{display:flex;align-items:center;gap:18px}.donut-canvas{flex:0 0 145px;width:145px;height:145px}.donut-center{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;pointer-events:none}.donut-center strong{font-size:25px;color:var(--text-1)}.donut-center small{font-size:10px;color:var(--text-3);margin-top:4px}.donut-legend{flex:1;min-width:0;display:grid;gap:13px;font-size:11px}.donut-legend>div{min-height:17px;display:flex;align-items:center;gap:7px}.donut-legend i{width:7px;height:7px;border-radius:50%;flex-shrink:0}.donut-legend span{flex:1;color:var(--text-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.donut-legend b{color:var(--text-1);font-weight:500}.donut-legend small{width:39px;text-align:right;color:var(--text-3);font-size:10px}@media(max-width:1380px){.donut-layout{gap:8px}.donut-canvas{flex-basis:118px;width:118px;height:118px}.donut-center strong{font-size:22px}}@media(max-width:600px){.donut-layout{justify-content:center}.donut-canvas{flex-basis:145px;width:145px;height:145px}.donut-legend{max-width:200px}}
</style>
