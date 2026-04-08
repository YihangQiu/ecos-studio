<template>
  <div class="home-view">
    <!-- 背景装饰 -->
    <div class="bg-grid"></div>

    <!-- ===== Dashboard Grid ===== -->
    <div class="dashboard-grid">

      <!-- ========== Row 1 Left: Chip Basic Info / Spec ========== -->
      <section class="section-card chip-info-area">
        <div class="section-header">
          <div class="header-icon"><i class="ri-cpu-line"></i></div>
          <h2>Chip Basic Info / Spec</h2>
          <span class="header-badge" v-if="config.pdk">{{ config.pdk }}</span>
        </div>
        <div class="chip-info-content">
          <div class="info-grid">
            <div class="info-item">
              <span class="info-label">Design</span>
              <span class="info-value highlight">{{ config.design || '--' }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">Top Module</span>
              <span class="info-value mono">{{ config.topModule || '--' }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">Die Size</span>
              <span class="info-value mono">{{ config.die?.Size.join(' x ') || '--' }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">Core Size</span>
              <span class="info-value mono">{{ config.core?.Size.join(' x ') || '--' }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">Target Frequency</span>
              <span class="info-value">{{ config.frequencyMax || '--' }} <small>MHz</small></span>
            </div>
            <div class="info-item">
              <span class="info-label">Utilization</span>
              <span class="info-value">{{ ((config.core?.utilization || 0) * 100).toFixed(0) }}%</span>
            </div>
            <div class="info-item">
              <span class="info-label">Clock</span>
              <span class="info-value">{{ config.clock || '--' }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">Layers</span>
              <span class="info-value">{{ config.bottomLayer }} - {{ config.topLayer }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- ========== Row 1 Right: 运行时监控 ========== -->
      <section class="section-card monitor-area">
        <div class="section-header">
          <div class="header-icon monitor"><i class="ri-pulse-line"></i></div>
          <h2>Runtime monitoring</h2>
        </div>
        <div class="monitor-content" v-if="monitorData">
          <div v-for="cfg in chartConfigs" :key="cfg.key" class="monitor-row">
            <span class="monitor-label">{{ cfg.label }}</span>
            <div class="monitor-chart-wrap">
              <div :ref="setChartRef(cfg.key)" class="monitor-chart"></div>
            </div>
            <span class="monitor-value">{{ getMetricMax(cfg.key) }}</span>
          </div>
        </div>
        <div v-else class="monitor-content">
          <div class="monitor-placeholder">
            <i class="ri-pulse-line"></i>
            <p>No monitor data</p>
            <span>After running the flow, the monitoring data will be displayed.</span>
          </div>
        </div>
      </section>

      <!-- ========== Row 2 Left+Center: Layout Preview ========== -->
      <section ref="layoutSectionRef" :class="['section-card layout-area', { 'is-fullscreen': isLayoutFullscreen }]">
        <div class="section-header">
          <div class="header-icon layout"><i class="ri-layout-masonry-line"></i></div>
          <h2>Layout</h2>
          <span class="header-hint">Displays the final step of the layout after the run is completed.</span>
          <div class="header-actions">
            <button class="action-btn" @click="toggleLayoutFullscreen"
              :title="isLayoutFullscreen ? 'Exit full screen' : 'full screen'">
              <i :class="isLayoutFullscreen ? 'ri-fullscreen-exit-line' : 'ri-fullscreen-line'"></i>
            </button>
          </div>
        </div>
        <div ref="layoutContentRef" class="layout-content" @wheel.prevent="onLayoutWheel" @mousedown="onLayoutMouseDown"
          @mousemove="onLayoutMouseMove" @mouseup="onLayoutMouseUp" @mouseleave="onLayoutMouseUp">
          <img v-if="layoutBlobUrl" :src="layoutBlobUrl" alt="Layout Preview" class="layout-image"
            :style="layoutImageTransform" draggable="false" />
          <div v-else class="layout-placeholder">
            <i class="ri-image-2-line"></i>
            <p>Layout Preview</p>
            <span>Waiting for layout data...</span>
          </div>
          <div v-if="isLayoutFullscreen && layoutBlobUrl" class="zoom-indicator">
            {{ Math.round(layoutScale * 100) }}%
          </div>
        </div>
      </section>

      <!-- ========== Row 2 Right: 指标分析 ========== -->
      <section class="section-card analysis-area">
        <div class="section-header">
          <div class="header-icon analysis"><i class="ri-pie-chart-line"></i></div>
          <h2>Indicator Analysis</h2>
        </div>
        <div class="analysis-content">
          <div class="charts-grid" v-if="analysisCharts.length > 0">
            <div class="chart-card" v-for="chart in analysisCharts" :key="chart.label"
              :title="chart.label"
              @click="chart.imageBlobUrl && openChartPreview(chart.imageBlobUrl, chart.label)">
              <div class="chart-visual">
                <img v-if="chart.imageBlobUrl" :src="chart.imageBlobUrl" :alt="chart.label" class="chart-image" />
                <i v-else class="ri-image-2-line"></i>
              </div>
              <span class="chart-label">{{ chart.label }}</span>
            </div>
          </div>
          <div v-else class="analysis-placeholder">
            <i class="ri-pie-chart-line"></i>
            <p>No metrics data</p>
            <span>After running the flow, the indicator analysis will be displayed.</span>
          </div>
        </div>
      </section>

      <!-- ========== Row 3 Left: Flow step log ========== -->
      <section class="section-card gds-area">
        <div class="section-header">
          <div class="header-icon gds"><i class="ri-terminal-line"></i></div>
          <h2>Flow step log</h2>
          <span v-if="flowLogStepName" class="header-badge">{{ flowLogStepName }}</span>
        </div>
        <div class="flow-log-content">
          <div v-if="flowLogError" class="flow-log-error">{{ flowLogError }}</div>
          <div v-else-if="flowLogSegments.length" ref="flowLogScrollRef" class="flow-log-scroll">
            <div
              v-for="(seg, idx) in flowLogSegments"
              :key="idx"
              class="flow-log-step"
              :class="{ failed: seg.failed, missing: seg.missing && !seg.failed }"
            >
              <div class="flow-log-step-header">
                <span class="flow-log-step-title">{{ seg.stepName }}</span>
                <span class="flow-log-step-meta">{{ seg.tool }}</span>
                <span class="flow-log-step-state" :class="{ 'is-failed': seg.failed }">{{ seg.state }}</span>
              </div>
              <pre class="flow-log-pre">{{ seg.content }}</pre>
            </div>
          </div>
          <div v-else class="flow-log-placeholder">
            <i class="ri-terminal-line"></i>
            <p>No flow step log yet</p>
            <span>Unstarted steps are hidden. Logs show up here once a step begins or finishes.</span>
          </div>
        </div>
      </section>

      <!-- ========== Row 3 Right: Checklist Table ========== -->
      <section class="section-card checklist-area">
        <div class="section-header">
          <div class="header-icon checklist"><i class="ri-checkbox-multiple-line"></i></div>
          <h2>Checklist Table</h2>
          <span class="header-count">{{ checklistCompletedCount }}/{{ checklistItems.length }}</span>
        </div>
        <div class="checklist-content">
          <!-- Table format -->
          <div class="checklist-table-wrap" v-if="checklistItems.length > 0">
            <table class="checklist-table">
              <thead>
                <tr>
                  <th>Step/Stage</th>
                  <th>Validation Type</th>
                  <th>Acceptance Criteria</th>
                  <th>Acceptance Result</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, idx) in checklistItems" :key="idx" :class="stateClass(item.state)">
                  <td>
                    <div class="table-step-name">
                      <i class="ri-checkbox-blank-circle-line table-step-icon"></i>
                      {{ item.step }}
                    </div>
                  </td>
                  <td class="table-tool">{{ item.type }}</td>
                  <td class="table-criteria">{{ item.item }}</td>
                  <td>
                    <span class="table-state-tag" :class="stateClass(item.state)">
                      <i :class="stateIcon(item.state)" class="table-state-icon"></i>
                      {{ item.state }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <!-- Empty state -->
          <div class="checklist-placeholder" v-else>
            <i class="ri-list-check-2"></i>
            <p>No checklist items</p>
            <span>After running the flow, the checklist will be displayed.</span>
          </div>
        </div>
      </section>

    </div>

    <!-- ===== 图表预览 Lightbox ===== -->
    <Teleport to="body">
      <Transition name="lightbox">
        <div v-if="chartPreview.visible" class="chart-lightbox-overlay" @click="closeChartPreview">
          <div class="chart-lightbox-content" @click.stop>
            <div class="chart-lightbox-header">
              <span class="chart-lightbox-title">{{ chartPreview.label }}</span>
              <button class="chart-lightbox-close" @click="closeChartPreview">
                <i class="ri-close-line"></i>
              </button>
            </div>
            <div class="chart-lightbox-body">
              <img :src="chartPreview.url" :alt="chartPreview.label" />
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useParameters } from '@/composables/useParameters'
import { useHomeData } from '@/composables/useHomeData'

// 注册 ECharts 组件（按需引入）
echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])

const { config } = useParameters()
const {
  monitorData,
  checklistItems,
  layoutBlobUrl,
  analysisCharts,
  flowLogSegments,
  flowLogStepName,
  flowLogError,
} = useHomeData()

const flowLogScrollRef = ref<HTMLElement | null>(null)

watch(
  flowLogSegments,
  async () => {
    await nextTick()
    const el = flowLogScrollRef.value
    if (el) {
      el.scrollTop = el.scrollHeight
    }
  },
  { deep: true }
)

// checklist 完成计数
const checklistCompletedCount = computed(() =>
  checklistItems.value.filter(c => c.state === 'Success').length
)

// ============ Layout 全屏 & 缩放平移 ============
const layoutSectionRef = ref<HTMLElement>()
const layoutContentRef = ref<HTMLElement>()
const isLayoutFullscreen = ref(false)

// 缩放 & 平移状态
const layoutScale = ref(1)
const layoutTranslateX = ref(0)
const layoutTranslateY = ref(0)
let isDragging = false
let dragStartX = 0
let dragStartY = 0
let dragStartTX = 0
let dragStartTY = 0

const layoutImageTransform = computed(() => {
  if (!isLayoutFullscreen.value) return {}
  return {
    transform: `translate(${layoutTranslateX.value}px, ${layoutTranslateY.value}px) scale(${layoutScale.value})`,
    transformOrigin: 'center center',
    cursor: isDragging ? 'grabbing' : (layoutScale.value > 1 ? 'grab' : 'default'),
  }
})

function resetLayoutTransform() {
  layoutScale.value = 1
  layoutTranslateX.value = 0
  layoutTranslateY.value = 0
}

function toggleLayoutFullscreen() {
  if (!layoutSectionRef.value) return
  isLayoutFullscreen.value = !isLayoutFullscreen.value
  if (!isLayoutFullscreen.value) {
    resetLayoutTransform()
  }
}

function onFullscreenKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && isLayoutFullscreen.value) {
    isLayoutFullscreen.value = false
    resetLayoutTransform()
  }
}

function onLayoutWheel(e: WheelEvent) {
  if (!isLayoutFullscreen.value) return

  const delta = e.deltaY > 0 ? -0.1 : 0.1
  const newScale = Math.min(Math.max(layoutScale.value + delta, 0.1), 20)

  // 以鼠标位置为中心缩放
  const container = layoutContentRef.value
  if (container) {
    const rect = container.getBoundingClientRect()
    const mouseX = e.clientX - rect.left - rect.width / 2
    const mouseY = e.clientY - rect.top - rect.height / 2

    const scaleFactor = newScale / layoutScale.value
    layoutTranslateX.value = mouseX - scaleFactor * (mouseX - layoutTranslateX.value)
    layoutTranslateY.value = mouseY - scaleFactor * (mouseY - layoutTranslateY.value)
  }

  layoutScale.value = newScale
}

function onLayoutMouseDown(e: MouseEvent) {
  if (!isLayoutFullscreen.value || layoutScale.value <= 1) return
  isDragging = true
  dragStartX = e.clientX
  dragStartY = e.clientY
  dragStartTX = layoutTranslateX.value
  dragStartTY = layoutTranslateY.value
}

function onLayoutMouseMove(e: MouseEvent) {
  if (!isDragging) return
  layoutTranslateX.value = dragStartTX + (e.clientX - dragStartX)
  layoutTranslateY.value = dragStartTY + (e.clientY - dragStartY)
}

function onLayoutMouseUp() {
  isDragging = false
}

// ============ ECharts 折线图 ============

// 4 个图表容器 ref
// 动态图表 ref & 实例管理
const chartRefs = new Map<string, HTMLDivElement>()
const chartInstances = new Map<string, echarts.ECharts>()

// ResizeObserver
let resizeObserver: ResizeObserver | null = null

/** 预置配色盘 —— 按 key 出现顺序循环取色 */
const COLOR_PALETTE = [
  '#ef4444', '#3b82f6', '#10b981', '#a855f7',
  '#f59e0b', '#06b6d4', '#ec4899', '#84cc16',
]

/** 从 monitorData 动态提取除 step 以外的所有指标 key */
const monitorKeys = computed<string[]>(() => {
  if (!monitorData.value) return []
  return Object.keys(monitorData.value).filter(k => k !== 'step')
})

/** 动态生成图表配置列表 */
const chartConfigs = computed(() => {
  return monitorKeys.value.map((key, idx) => ({
    key,
    label: key,
    color: COLOR_PALETTE[idx % COLOR_PALETTE.length],
  }))
})

/** 设置图表 DOM ref 的回调（用于 v-for 中的 :ref） */
function setChartRef(key: string) {
  return (el: any) => {
    if (el) {
      chartRefs.set(key, el as HTMLDivElement)
    } else {
      chartRefs.delete(key)
    }
  }
}

/** 将 "h:m:s" 格式的时间字符串转换为秒数 */
function parseTimeToSeconds(val: string): number {
  const parts = val.split(':').map(Number)
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2]
  if (parts.length === 2) return parts[0] * 60 + parts[1]
  return Number(val) || 0
}

/** 判断一个值数组是否全部为 "h:m:s" 格式的时间字符串 */
function isTimeFormatArray(arr: any[]): boolean {
  return arr.length > 0 && arr.every(v => typeof v === 'string' && /^\d+:\d+:\d+$/.test(v))
}

/** 获取某个维度的数值数组（自动检测字符串/数字） */
function getMetricValues(key: string): number[] {
  if (!monitorData.value) return []
  const raw = (monitorData.value as Record<string, any>)[key]
  if (!raw || !Array.isArray(raw)) return []

  // 时间格式 "h:m:s" → 秒数
  if (isTimeFormatArray(raw)) {
    return raw.map(parseTimeToSeconds)
  }
  // 字符串数字 → Number
  return raw.map((v: any) => Number(v) || 0)
}

/** 获取某个维度的最大值显示 */
function getMetricMax(key: string): string {
  const values = getMetricValues(key)
  if (values.length === 0) return '--'
  const max = Math.max(...values)
  // 整数显示为整数，小数保留 1 位
  return Number.isInteger(max) ? `${max}` : `${max.toFixed(1)}`
}

/** 获取某个维度的原始显示值 */
function getMetricDisplay(key: string, idx: number): string {
  if (!monitorData.value) return '--'
  const raw = (monitorData.value as Record<string, any>)[key]
  if (!raw || !Array.isArray(raw) || raw[idx] == null) return '--'
  const v = raw[idx]
  // 如果原始值是字符串（如 "h:m:s"），直接展示
  if (typeof v === 'string') return v
  // 数字：整数原样，小数保留 1 位
  return Number.isInteger(v) ? `${v}` : `${Number(v).toFixed(1)}`
}

/** 构建所有图表共享的 tooltip formatter（动态根据 monitorKeys 生成） */
function buildSharedTooltipFormatter(params: any): string {
  const idx = params[0]?.dataIndex ?? 0
  const steps = monitorData.value?.step || []
  const stepName = steps[idx] || `Step #${idx}`

  const configs = chartConfigs.value
  const rows = configs.map(cfg => {
    const value = getMetricDisplay(cfg.key, idx)
    return `<div style="display:flex;align-items:center;gap:6px;margin-top:3px">
       <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:${cfg.color}"></span>
       <span style="flex:1;color:#aaa">${cfg.label}</span>
       <span style="font-weight:600;color:#e5e5e5;font-family:'JetBrains Mono',monospace">${value}</span>
     </div>`
  }).join('')

  return `<div style="font-size:11px;font-weight:700;margin-bottom:4px;color:#fff;border-bottom:1px solid rgba(255,255,255,0.1);padding-bottom:4px">${stepName}</div>
          <div style="font-size:10px">${rows}</div>`
}

/** 构建单个折线图的 option */
function buildChartOption(key: string, color: string): echarts.EChartsCoreOption {
  const values = getMetricValues(key)
  const steps = monitorData.value?.step || []

  return {
    grid: {
      left: 4,
      right: 4,
      top: 6,
      bottom: 6,
      containLabel: false,
    },
    tooltip: {
      trigger: 'axis',
      appendToBody: true,
      backgroundColor: 'rgba(20, 20, 24, 0.95)',
      borderColor: 'rgba(255,255,255,0.08)',
      borderWidth: 1,
      borderRadius: 6,
      padding: [8, 10],
      extraCssText: 'pointer-events: none;',
      textStyle: {
        color: '#e5e5e5',
        fontSize: 10,
      },
      axisPointer: {
        type: 'line',
        lineStyle: {
          color: 'rgba(255,255,255,0.15)',
          type: 'dashed',
        },
      },
      formatter: buildSharedTooltipFormatter,
    },
    xAxis: {
      type: 'category',
      show: false,
      data: steps,
      boundaryGap: false,
      axisPointer: {
        show: true,
      },
    },
    yAxis: {
      type: 'value',
      show: false,
    },
    series: [
      {
        type: 'line',
        data: values,
        smooth: 0.3,
        symbol: 'circle',
        symbolSize: 6,
        showSymbol: true,
        showAllSymbol: true,
        itemStyle: {
          color,
          borderColor: '#fff',
          borderWidth: 1.5,
        },
        emphasis: {
          itemStyle: {
            color,
            borderColor: '#fff',
            borderWidth: 2,
            shadowColor: color + '80',
            shadowBlur: 8,
          },
          scale: 1.8,
        },
        lineStyle: {
          color,
          width: 2,
        },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: color + '30' },
            { offset: 1, color: color + '05' },
          ]),
        },
      },
    ],
    animation: true,
    animationDuration: 600,
  }
}

/** 获取所有已初始化的图表实例 */
function getAllChartInstances(): echarts.ECharts[] {
  return Array.from(chartInstances.values())
}

/** 图表联动分组 ID —— 同组图表的 axisPointer 自动同步 */
const CHART_GROUP = 'monitor-linked'

/** 当前鼠标所在的图表实例，用于控制仅在悬浮图表上显示 tooltip 内容 */
let activeChartInstance: echarts.ECharts | null = null

/**
 * 为图表绑定联动事件。
 * 使用 echarts.connect 实现轴指针自动同步（数据索引精确对齐），
 * 并通过动态切换 showContent 控制仅在悬浮图表上显示 tooltip。
 */
function bindChartLinkEvents(instance: echarts.ECharts) {
  // 加入联动分组，轴指针 & 高亮自动同步到同组所有图表
  instance.group = CHART_GROUP

  // 鼠标进入此图表时：仅此图表显示 tooltip 内容，其余图表隐藏内容
  instance.getZr().on('mousemove', () => {
    if (activeChartInstance === instance) return
    activeChartInstance = instance
    for (const chart of getAllChartInstances()) {
      chart.setOption(
        { tooltip: { showContent: chart === instance } },
        { lazyUpdate: true },
      )
    }
  })

  // 鼠标离开此图表
  instance.getZr().on('globalout', () => {
    if (activeChartInstance === instance) {
      activeChartInstance = null
    }
  })
}

/** 初始化或更新所有图表 */
function initOrUpdateCharts() {
  let newInstanceCreated = false

  for (const cfg of chartConfigs.value) {
    const el = chartRefs.get(cfg.key)
    if (!el) continue

    // 跳过尺寸为 0 的元素，等待 ResizeObserver 回调再初始化
    if (!el.clientWidth || !el.clientHeight) continue

    let instance = chartInstances.get(cfg.key)
    if (!instance) {
      instance = echarts.init(el, undefined, { renderer: 'canvas' })
      chartInstances.set(cfg.key, instance)
      bindChartLinkEvents(instance)
      newInstanceCreated = true
    }

    instance.setOption(buildChartOption(cfg.key, cfg.color), true)
  }

  // 有新图表加入分组时，重新注册 connect 以确保联动生效
  if (newInstanceCreated) {
    echarts.connect(CHART_GROUP)
  }
}

/** 销毁所有图表 */
function disposeCharts() {
  for (const instance of chartInstances.values()) {
    instance.dispose()
  }
  chartInstances.clear()
}

/** 所有图表 resize */
function resizeAllCharts() {
  for (const instance of chartInstances.values()) {
    instance.resize()
  }
}

/** 监听图表容器尺寸变化，处理初始化和 resize */
function setupResizeObserver() {
  resizeObserver?.disconnect()
  resizeObserver = new ResizeObserver(() => {
    // 尝试初始化尚未创建的图表（首次获得有效尺寸时）
    if (monitorData.value) {
      initOrUpdateCharts()
    }
    // 已有图表 resize
    resizeAllCharts()
  })

  // 观察所有图表容器
  for (const el of chartRefs.values()) {
    if (el) {
      resizeObserver.observe(el)
    }
  }
}

// 监听 monitorData 变化，更新图表
watch(
  () => monitorData.value,
  async () => {
    await nextTick()
    // 数据变化后可能 v-if 刚渲染出新的 DOM，重新绑定 observer
    setupResizeObserver()
    initOrUpdateCharts()
  },
  { deep: true }
)

onMounted(async () => {
  await nextTick()
  setupResizeObserver()
  if (monitorData.value) {
    initOrUpdateCharts()
  }
  document.addEventListener('keydown', onFullscreenKeydown)
})

onUnmounted(() => {
  disposeCharts()
  resizeObserver?.disconnect()
  resizeObserver = null
  document.removeEventListener('keydown', onFullscreenKeydown)
})

// ============ 指标分析 ============
// analysisCharts 数据从 useHomeData() 动态获取（基于 home.json 的 metrics 字段）

// 图表预览 Lightbox
const chartPreview = ref<{ visible: boolean; url: string; label: string }>({
  visible: false,
  url: '',
  label: '',
})

function openChartPreview(url: string, label: string) {
  chartPreview.value = { visible: true, url, label }
}

function closeChartPreview() {
  chartPreview.value.visible = false
}

// ============ 辅助函数 ============

/** 根据步骤状态返回图标类名 */
function stateIcon(state: string): string {
  switch (state) {
    case 'Success':
      return 'ri-checkbox-circle-fill'
    case 'Ongoing':
      return 'ri-loader-4-line spin'
    case 'Imcomplete':
      return 'ri-close-circle-fill'
    case 'Pending':
      return 'ri-time-line'
    case 'Unstart':
    default:
      return 'ri-checkbox-blank-circle-line'
  }
}

/** 根据步骤状态返回 CSS 类名 */
function stateClass(state: string): string {
  switch (state) {
    case 'Success':
      return 'state-success'
    case 'Ongoing':
      return 'state-ongoing'
    case 'Imcomplete':
      return 'state-failed'
    case 'Pending':
      return 'state-pending'
    case 'Unstart':
    default:
      return 'state-unstart'
  }
}

</script>

<style scoped>
/* ==================== 基础布局 ==================== */
.home-view {
  height: 100%;
  position: relative;
  overflow: hidden;
  background: var(--bg-primary);
}

.bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(var(--accent-rgb, 59, 130, 246), 0.03) 1px, transparent 1px),
    linear-gradient(90deg, rgba(var(--accent-rgb, 59, 130, 246), 0.03) 1px, transparent 1px);
  background-size: 32px 32px;
  pointer-events: none;
}

/* ==================== Dashboard Grid ==================== */
.dashboard-grid {
  position: relative;
  z-index: 1;
  height: 100%;
  display: grid;
  grid-template-columns: 1fr 1.2fr;
  grid-template-rows: auto 1fr 0.7fr;
  grid-template-areas:
    "info      monitor"
    "layout    analysis"
    "gds       checklist";
  gap: 8px;
  padding: 8px;
}

/* Grid Area Assignments */
.chip-info-area {
  grid-area: info;
}

.monitor-area {
  grid-area: monitor;
}

.layout-area {
  grid-area: layout;
}

.layout-area.is-fullscreen {
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  z-index: 9999;
  border-radius: 0;
  background: var(--bg-primary);
}

.layout-area.is-fullscreen .layout-content {
  margin: 0;
  border-radius: 0;
  overflow: hidden;
  position: relative;
}

.layout-area.is-fullscreen .layout-image {
  object-fit: contain;
  transition: transform 0.05s ease-out;
  user-select: none;
}

/* 缩放百分比指示器 */
.zoom-indicator {
  position: absolute;
  bottom: 12px;
  right: 12px;
  padding: 4px 10px;
  background: rgba(0, 0, 0, 0.6);
  color: #e5e5e5;
  font-size: 11px;
  font-weight: 600;
  font-family: 'JetBrains Mono', monospace;
  border-radius: 4px;
  pointer-events: none;
  z-index: 10;
}

.analysis-area {
  grid-area: analysis;
}

.gds-area {
  grid-area: gds;
}

.checklist-area {
  grid-area: checklist;
}

/* ==================== Section Card 通用样式 ==================== */
.section-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
  min-height: 0;
}

/* Section Header */
.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: linear-gradient(135deg, var(--bg-sidebar) 0%, var(--bg-secondary) 100%);
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.header-icon {
  width: 24px;
  height: 24px;
  background: linear-gradient(135deg, var(--accent-color) 0%, #06b6d4 100%);
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  font-size: 12px;
  flex-shrink: 0;
}

.section-header h2 {
  flex: 1;
  font-size: 11px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.header-badge {
  padding: 2px 7px;
  background: linear-gradient(135deg, var(--accent-color) 0%, #06b6d4 100%);
  color: white;
  font-size: 9px;
  font-weight: 700;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  flex-shrink: 0;
}

.header-hint {
  font-size: 9px;
  color: var(--text-secondary);
  opacity: 0.7;
  white-space: nowrap;
}

.header-count {
  padding: 2px 7px;
  background: var(--bg-primary);
  color: var(--text-secondary);
  font-size: 10px;
  font-weight: 600;
  border-radius: 4px;
  border: 1px solid var(--border-color);
  flex-shrink: 0;
}

.header-actions {
  display: flex;
  gap: 3px;
  flex-shrink: 0;
}

.action-btn {
  width: 22px;
  height: 22px;
  border: 1px solid var(--border-color);
  border-radius: 4px;
  background: var(--bg-primary);
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s ease;
  font-size: 11px;
}

.action-btn:hover {
  border-color: var(--accent-color);
  color: var(--accent-color);
}

/* ==================== Chip Basic Info ==================== */
.chip-info-content {
  flex: 1;
  padding: 10px;
  overflow: auto;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
  height: 100%;
}

.info-item {
  padding: 8px 10px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  transition: border-color 0.15s ease;
}

.info-item:hover {
  border-color: var(--accent-color);
}

.info-label {
  font-size: 9px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 3px;
}

.info-value {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-primary);
}

.info-value.highlight {
  color: var(--accent-color);
}

.info-value.mono {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
}

.info-value small {
  font-size: 9px;
  font-weight: 500;
  opacity: 0.7;
}

/* ==================== 运行时监控 ==================== */
.monitor-content {
  flex: 1;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  overflow: auto;
}

.monitor-row {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 10px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  min-height: 0;
}

.monitor-label {
  width: 100px;
  font-size: 9px;
  font-weight: 600;
  color: var(--text-secondary);
  white-space: nowrap;
  flex-shrink: 0;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.monitor-chart-wrap {
  flex: 1;
  height: 100%;
  min-height: 24px;
  min-width: 0;
}

.monitor-chart {
  width: 100%;
  height: 100%;
  min-height: 24px;
}

.monitor-value {
  min-width: 80px;
  text-align: right;
  font-size: 10px;
  font-weight: 700;
  color: var(--text-primary);
  font-family: 'JetBrains Mono', monospace;
  flex-shrink: 0;
}

.monitor-placeholder {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 20px;
  border: 2px dashed var(--border-color);
  border-radius: 8px;
  background: var(--bg-primary);
}

.monitor-placeholder i {
  font-size: 28px;
  color: var(--text-secondary);
  opacity: 0.3;
}

.monitor-placeholder p {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  margin: 0;
}

.monitor-placeholder span {
  font-size: 10px;
  color: var(--text-secondary);
  opacity: 0.6;
}

/* ==================== Layout Preview ==================== */
.layout-content {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-primary);
  margin: 8px;
  border-radius: 6px;
  overflow: hidden;
}

.layout-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  border: 2px dashed var(--border-color);
  border-radius: 8px;
  background:
    linear-gradient(90deg, rgba(var(--accent-rgb, 59, 130, 246), 0.02) 1px, transparent 1px),
    linear-gradient(rgba(var(--accent-rgb, 59, 130, 246), 0.02) 1px, transparent 1px);
  background-size: 16px 16px;
}

.layout-placeholder i {
  font-size: 36px;
  color: var(--text-secondary);
  opacity: 0.3;
}

.layout-placeholder p {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  margin: 0;
}

.layout-placeholder span {
  font-size: 10px;
  color: var(--text-secondary);
  opacity: 0.6;
}

.layout-image {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

/* ==================== 指标分析 ==================== */
.analysis-content {
  flex: 1;
  padding: 8px;
  overflow: auto;
}

.charts-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  grid-template-rows: 1fr 1fr;
  gap: 6px;
  height: 100%;
}

/* Last row span adjustment for uneven items */
.chart-card:nth-child(5) {
  grid-column: 1;
}

.chart-card:nth-child(6) {
  grid-column: 2;
}

.chart-card:nth-child(7) {
  grid-column: 3 / 5;
}

.chart-card {
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 6px;
  transition: border-color 0.15s ease;
  cursor: pointer;
  overflow: hidden;
}

.chart-card:hover {
  border-color: var(--accent-color);
}

.chart-visual {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  min-height: 0;
  font-size: 28px;
  color: var(--text-secondary);
  opacity: 0.25;
}

.chart-visual img.chart-image {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
  opacity: 1;
}

.chart-label {
  font-size: 9px;
  font-weight: 600;
  color: var(--text-secondary);
  text-align: center;
  white-space: nowrap;
  flex-shrink: 0;
}

.analysis-placeholder {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 20px;
  border: 2px dashed var(--border-color);
  border-radius: 8px;
  background: var(--bg-primary);
}

.analysis-placeholder i {
  font-size: 28px;
  color: var(--text-secondary);
  opacity: 0.3;
}

.analysis-placeholder p {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  margin: 0;
}

.analysis-placeholder span {
  font-size: 10px;
  color: var(--text-secondary);
  opacity: 0.6;
}

/* ==================== Flow step log ==================== */
.flow-log-content {
  flex: 1;
  min-height: 0;
  padding: 8px 10px 10px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.flow-log-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding-right: 2px;
}

.flow-log-step {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.flow-log-step-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 10px;
  font-size: 10px;
  font-weight: 600;
  color: var(--text-secondary);
}

.flow-log-step-title {
  color: var(--text-primary);
  font-weight: 700;
}

.flow-log-step-meta {
  font-family: 'JetBrains Mono', monospace;
  opacity: 0.85;
}

.flow-log-step-state {
  margin-left: auto;
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.flow-log-step-state.is-failed {
  color: #f87171;
  border-color: rgba(248, 113, 113, 0.45);
  background: rgba(248, 113, 113, 0.08);
}

.flow-log-pre {
  margin: 0;
  padding: 10px 12px;
  overflow: auto;
  max-height: 320px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 6px;
  font-family: 'JetBrains Mono', 'SF Mono', ui-monospace, monospace;
  font-size: 11px;
  line-height: 1.5;
  /* 浅色下用近黑色正文，避免仅用 var(--text-primary) 时被继承链/组件库压成浅灰 */
  color: #111827;
  opacity: 1;
  white-space: pre-wrap;
  word-break: break-word;
}

html.dark .flow-log-pre {
  color: #e3e3e8;
}

.flow-log-step.failed .flow-log-pre {
  color: #f87171;
  border-color: rgba(248, 113, 113, 0.35);
}

.flow-log-step.missing .flow-log-pre {
  color: var(--text-secondary);
  opacity: 0.95;
  font-style: italic;
}

.flow-log-error {
  flex: 1;
  min-height: 80px;
  padding: 12px;
  border-radius: 6px;
  border: 1px solid rgba(239, 68, 68, 0.45);
  background: rgba(239, 68, 68, 0.08);
  color: #f87171;
  font-size: 11px;
  line-height: 1.4;
  overflow: auto;
}

.flow-log-placeholder {
  flex: 1;
  min-height: 120px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 16px;
  border: 2px dashed var(--border-color);
  border-radius: 8px;
  background: var(--bg-primary);
}

.flow-log-placeholder i {
  font-size: 28px;
  color: var(--text-secondary);
  opacity: 0.35;
}

.flow-log-placeholder p {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  margin: 0;
}

.flow-log-placeholder span {
  font-size: 10px;
  color: var(--text-secondary);
  opacity: 0.65;
  text-align: center;
  max-width: 260px;
}

/* ==================== Checklist Table ==================== */
.checklist-content {
  flex: 1;
  padding: 8px;
  overflow: auto;
}

.checklist-table-wrap {
  height: 100%;
  overflow: auto;
}

.checklist-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 10px;
}

.checklist-table thead th {
  position: sticky;
  top: 0;
  background: var(--bg-sidebar);
  padding: 6px 8px;
  text-align: left;
  font-weight: 700;
  font-size: 9px;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--border-color);
  white-space: nowrap;
}

.checklist-table tbody td {
  padding: 5px 8px;
  border-bottom: 1px solid var(--border-color);
  color: var(--text-primary);
  vertical-align: middle;
}

.checklist-table tbody tr {
  transition: background 0.1s ease;
}

.checklist-table tbody tr:hover {
  background: rgba(var(--accent-rgb, 59, 130, 246), 0.04);
}

.table-step-name {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  white-space: nowrap;
}

.table-step-icon {
  font-size: 12px;
  color: var(--text-secondary);
}

.table-tool,
.table-criteria {
  color: var(--text-secondary);
  font-size: 10px;
}

.table-state-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  font-size: 9px;
  font-weight: 600;
  border-radius: 3px;
  white-space: nowrap;
}

.table-state-icon {
  font-size: 11px;
}

.table-state-tag.state-success {
  background: rgba(16, 185, 129, 0.15);
  color: #10b981;
}

.table-state-tag.state-ongoing {
  background: rgba(59, 130, 246, 0.15);
  color: #3b82f6;
}

.table-state-tag.state-failed {
  background: rgba(239, 68, 68, 0.15);
  color: #ef4444;
}

.table-state-tag.state-pending {
  background: rgba(245, 158, 11, 0.15);
  color: #f59e0b;
}

.table-state-tag.state-unstart {
  background: var(--bg-secondary);
  color: var(--text-secondary);
  opacity: 0.6;
}

/* Empty state */
.checklist-placeholder {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 20px;
  border: 2px dashed var(--border-color);
  border-radius: 8px;
  background: var(--bg-primary);
}

.checklist-placeholder i {
  font-size: 28px;
  color: var(--text-secondary);
  opacity: 0.3;
}

.checklist-placeholder p {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  margin: 0;
}

.checklist-placeholder span {
  font-size: 10px;
  color: var(--text-secondary);
  opacity: 0.6;
}

/* ==================== 通用动画 ==================== */
@keyframes spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}

.spin {
  animation: spin 1s linear infinite;
}

/* ==================== 响应式 ==================== */
@media (max-width: 1200px) {
  .dashboard-grid {
    grid-template-columns: 1fr 1fr;
  }

  .info-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .charts-grid {
    grid-template-columns: repeat(3, 1fr);
  }

  .chart-card:nth-child(7) {
    grid-column: 3;
  }
}

@media (max-width: 900px) {
  .dashboard-grid {
    grid-template-columns: 1fr;
    grid-template-rows: auto;
    grid-template-areas:
      "info"
      "monitor"
      "layout"
      "analysis"
      "gds"
      "checklist";
    overflow-y: auto;
  }

  .section-card {
    min-height: 200px;
  }
}
</style>
