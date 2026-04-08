<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue'
import type { Editor } from '@/applications/editor'
import { SelectPlugin } from '@/applications/editor/plugins'

interface Props {
  editor?: Editor | null
  /** 是否显示「从布局 JSON 生成瓦片」工具（Tauri 开发模式等） */
  showTileGenerate?: boolean
  /** 正在生成瓦片时禁用按钮 */
  tileGenBusy?: boolean
  /** 矢量版图模式：在 Select 工具上附加与画布选中区一致的快捷键说明 */
  layoutTileShortcutsHint?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  showTileGenerate: false,
  tileGenBusy: false,
  layoutTileShortcutsHint: false,
})

const emit = defineEmits<{
  toolChange: [toolId: string]
  generateTiles: []
}>()

const activeTool = ref('hand')
const isRulerEnabled = ref(true)
/** 与 Editor 缩放一致：100% = scale 1；极小 scale 避免 round 成 0 */
const zoomPercentLabel = ref('100')
let unlistenTransform: (() => void) | null = null

function formatZoomPercentLabel(scale: number): string {
  const pct = scale * 100
  if (!Number.isFinite(pct) || pct <= 0) return '0'
  if (pct < 0.01) return '<0.01'
  if (pct < 1) return pct.toFixed(2).replace(/\.?0+$/, '')
  return String(Math.round(pct))
}

const tools = [
  { id: 'hand', icon: 'ri-hand', tooltip: 'Pan (H)', shortcut: 'h' },
  { id: 'select', icon: 'ri-cursor-fill', tooltip: 'Select (S)', shortcut: 's' },
  { id: 'measure', icon: 'ri-ruler-2-line', tooltip: 'Measure (M)', shortcut: 'm' },
  { id: 'highlight', icon: 'ri-focus-3-line', tooltip: 'Highlight', shortcut: '' },
  { id: 'layers', icon: 'ri-stack-line', tooltip: 'Layers', shortcut: '' },
]

function toolTitle(tool: (typeof tools)[number]): string {
  if (tool.id === 'select' && props.layoutTileShortcutsHint) {
    return `${tool.tooltip} · 版图 Esc / R / C / Del / Ctrl+Z / F`
  }
  return tool.tooltip
}

const setActiveTool = (toolId: string) => {
  const prev = activeTool.value
  activeTool.value = toolId

  const editor = props.editor
  if (!editor) return

  // Deactivate previous tool plugins
  if (prev === 'select') {
    const selectPlugin = editor.getPlugin<SelectPlugin>('select')
    selectPlugin?.deactivate()
  }
  // if (prev === 'measure') {
  //   const measurePlugin = editor.getPlugin<MeasurePlugin>('measure')
  //   measurePlugin?.deactivate()
  // }

  // Activate new tool
  if (toolId === 'select') {
    const selectPlugin = editor.getPlugin<SelectPlugin>('select')
    selectPlugin?.activate()
  }
  else if (toolId === 'hand') {
    const viewport = editor.view
    if (viewport) viewport.plugins.resume('drag')
  }
  // else if (toolId === 'measure') {
  //   const measurePlugin = editor.getPlugin<MeasurePlugin>('measure')
  //   measurePlugin?.activate()
  // } 

  emit('toolChange', toolId)
}

const toggleRuler = () => {
  isRulerEnabled.value = !isRulerEnabled.value
  props.editor?.setPluginEnabled('ruler', isRulerEnabled.value)
}

const handleZoomIn = () => {
  props.editor?.zoomIn()
}

const handleZoomOut = () => {
  props.editor?.zoomOut()
}

const handleFitToWorld = () => {
  props.editor?.fitToWorld()
}

const handleKeyDown = (e: KeyboardEvent) => {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return

  for (const tool of tools) {
    if (tool.shortcut && e.key.toLowerCase() === tool.shortcut) {
      e.preventDefault()
      setActiveTool(tool.id)
      return
    }
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeyDown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeyDown)
  if (unlistenTransform) {
    unlistenTransform()
  }
})

watch(() => props.editor, (editor) => {
  if (editor) {
    editor.setPluginEnabled('ruler', isRulerEnabled.value)
    zoomPercentLabel.value = formatZoomPercentLabel(editor.getScale())

    if (unlistenTransform) {
      unlistenTransform()
    }

    unlistenTransform = editor.onTransformChange((t) => {
      zoomPercentLabel.value = formatZoomPercentLabel(t.scale)
    })
  }
}, { immediate: true })
</script>

<template>
  <div class="h-10 bg-(--bg-secondary) border-b border-(--border-color) flex items-center gap-2 px-4 shrink-0">
    <!-- 工具按钮组 -->
    <div class="flex items-center gap-1">
      <button v-for="tool in tools" :key="tool.id" @click="setActiveTool(tool.id)" :class="{
        'bg-(--accent-color) text-white': activeTool === tool.id,
        'text-(--text-secondary) hover:text-(--text-primary) hover:bg-(--bg-hover)': activeTool !== tool.id
      }" class="w-9 h-9 flex items-center justify-center rounded transition-all" :title="toolTitle(tool)">
        <i :class="tool.icon" class="text-base"></i>
      </button>

      <div v-if="showTileGenerate" class="w-px h-6 bg-(--border-color) mx-0.5" />

      <button
        v-if="showTileGenerate"
        type="button"
        :disabled="tileGenBusy"
        @click="emit('generateTiles')"
        :class="[
          tileGenBusy
            ? 'opacity-50 cursor-wait text-(--text-secondary)'
            : 'text-(--text-secondary) hover:text-(--text-primary) hover:bg-(--bg-hover)',
          'w-9 h-9 flex items-center justify-center rounded transition-all shrink-0',
        ]"
        title="Rendering layout"
      >
        <i class="ri-grid-fill text-base" :class="{ 'animate-pulse': tileGenBusy }"></i>
      </button>
    </div>

    <div class="w-px h-6 bg-(--border-color)"></div>

    <!-- 右侧：缩放控制等 -->
    <div class="flex-1 flex items-center justify-end gap-3">
      <!-- 标尺开关 -->
      <button @click="toggleRuler" :class="[
        isRulerEnabled ? 'text-(--accent-color) bg-(--accent-color)/20 border-(--accent-color)/50 shadow-sm shadow-(--accent-color)/20' : 'text-(--text-secondary) border-(--border-color) hover:text-(--text-primary) hover:bg-(--bg-hover) hover:border-(--border-color)',
        'h-8 px-2 flex items-center gap-1.5 rounded border transition-all'
      ]" title="Show/Hide Ruler">
        <i class="ri-ruler-line text-base"></i>
      </button>

      <div class="w-px h-6 bg-(--border-color)"></div>

      <div class="flex items-center gap-2 px-3 py-1.5 bg-(--bg-primary) rounded border border-(--border-color)">
        <button @click="handleZoomOut" class="text-(--text-secondary) hover:text-(--text-primary) transition-colors"
          title="Zoom Out">
          <i class="ri-subtract-line text-sm"></i>
        </button>
        <span class="text-[13px] text-(--text-primary) font-medium min-w-[52px] text-center tabular-nums">{{ zoomPercentLabel }}%</span>
        <button @click="handleZoomIn" class="text-(--text-secondary) hover:text-(--text-primary) transition-colors"
          title="Zoom In">
          <i class="ri-add-line text-sm"></i>
        </button>
      </div>
      <button @click="handleFitToWorld"
        class="w-8 h-8 flex items-center justify-center rounded text-(--text-secondary) hover:text-(--text-primary) hover:bg-(--bg-hover) transition-colors"
        title="Fit to Canvas">
        <i class="ri-fullscreen-fill text-base"></i>
      </button>
    </div>
  </div>
</template>
