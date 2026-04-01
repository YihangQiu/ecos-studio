<script setup lang="ts">
import { shallowRef, markRaw, watch, ref, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { EditorContainer, type Editor } from '@/applications/editor'
import {
  LayoutDataStore,
  LayoutRenderer,
  LayerStyleManager,
  SpatialIndex,
  InteractionManager,
} from '@/applications/editor/layout'
import {
  SelectPlugin,
  MeasurePlugin,
  LayerManagerPlugin,
  HighlightPlugin,
} from '@/applications/editor/plugins'
import type { RawHeaderJSON, RawDataJSON } from '@/applications/editor/layout'
import DrawingToolbar from './DrawingToolbar.vue'
import { useWorkspace } from '@/composables/useWorkspace'
import { useEDA } from '@/composables/useEDA'
import { useLayoutState } from '@/composables/useLayoutState'
import { getInfoApi } from '@/api/flow'
import { CMDEnum, InfoEnum, StepEnum, ResponseEnum } from '@/api/type'

const route = useRoute()
const { currentProject, sseMessages, stepRefreshCounter } = useWorkspace()
const { getResourceUrl } = useEDA()
const layoutState = useLayoutState()

const editor = shallowRef<Editor | null>(null)

/** 鼠标在画布上时的 EDA/显示坐标（屏幕 → 世界 → display，与标尺一致） */
const cursorEda = ref<{ x: number; y: number } | null>(null)

let detachCanvasPointerListeners: (() => void) | null = null

function formatCursorCoord(n: number): string {
  if (!Number.isFinite(n)) return '—'
  return Math.round(n).toLocaleString()
}

function attachCanvasPointerTracking(ed: Editor): void {
  detachCanvasPointerListeners?.()
  const canvas = ed.application?.canvas as HTMLCanvasElement | undefined
  const vp = ed.view
  if (!canvas || !vp) return

  const onMove = (e: PointerEvent): void => {
    const world = vp.toWorld(e.offsetX, e.offsetY)
    const d = ed.worldToDisplay(world.x, world.y)
    cursorEda.value = { x: d.x, y: d.y }
  }
  const onLeave = (): void => {
    cursorEda.value = null
  }

  canvas.addEventListener('pointermove', onMove)
  canvas.addEventListener('pointerleave', onLeave)

  detachCanvasPointerListeners = () => {
    canvas.removeEventListener('pointermove', onMove)
    canvas.removeEventListener('pointerleave', onLeave)
    detachCanvasPointerListeners = null
  }
}

watch(
  () => editor.value,
  (ed) => {
    detachCanvasPointerListeners?.()
    cursorEda.value = null
    if (ed) attachCanvasPointerTracking(ed)
  },
  { immediate: true }
)

onUnmounted(() => {
  detachCanvasPointerListeners?.()
})

// Layout modules (not reactive — managed imperatively)
let dataStore: LayoutDataStore | null = null
let renderer: LayoutRenderer | null = null
let styleManager: LayerStyleManager | null = null
let spatialIndex: SpatialIndex | null = null
let interactionManager: InteractionManager | null = null
let styleStateUnlisten: (() => void) | null = null

const stepEnumValues = Object.values(StepEnum)

function getStepEnumFromPath(path: string): StepEnum | undefined {
  return stepEnumValues.find(step => step.toLowerCase() === path.toLowerCase())
}

const onEditorReady = (editorInstance: Editor) => {
  editor.value = editorInstance

  const layerMgrPlugin = editorInstance.getPlugin<LayerManagerPlugin>('layerManager')
  if (layerMgrPlugin) {
    layoutState.layerManager.value = markRaw(layerMgrPlugin)
  }

  const pathParts = route.path.split('/')
  const stage = pathParts[pathParts.length - 1] || 'home'
  handleStageChange(stage)
}

function cleanupLayout(): void {
  if (styleStateUnlisten) {
    styleStateUnlisten()
    styleStateUnlisten = null
  }

  interactionManager?.destroy()
  renderer?.destroy()
  spatialIndex?.clear()
  dataStore?.clear()
  styleManager?.clear()

  interactionManager = null
  renderer = null
  spatialIndex = null
  dataStore = null
  styleManager = null

  layoutState.selectedGroups.value = []
  layoutState.dataStore.value = null
  layoutState.renderMode.value = 'image'
}

async function loadLayoutData(headerJson: RawHeaderJSON, dataJson: RawDataJSON): Promise<void> {
  const ed = editor.value
  if (!ed?.view) return

  cleanupLayout()

  layoutState.loadingState.value = 'loading'
  layoutState.loadingMessage.value = 'Parsing header...'

  try {
    const t0 = performance.now()

    // 1. Parse data
    dataStore = markRaw(new LayoutDataStore())
    dataStore.loadHeader(headerJson)
    layoutState.dataStore.value = dataStore

    layoutState.loadingMessage.value = 'Parsing layout data...'
    dataStore.loadData(dataJson)

    // 2. Build style manager
    styleManager = markRaw(new LayerStyleManager())
    styleManager.buildFromLayerDefs(dataStore.header!.layerList)
    styleManager.applySnapshot(layoutState.layerStyleSnapshot.value)
    styleStateUnlisten = styleManager.onChange(() => {
      if (styleManager) {
        layoutState.layerStyleSnapshot.value = styleManager.serialize()
      }
    })

    // 3. Build spatial index
    layoutState.loadingMessage.value = 'Building spatial index...'
    spatialIndex = markRaw(new SpatialIndex())
    const allBoxes = Array.from({ length: dataStore.totalGroups }, (_, i) => dataStore!.groups[i].children).flat()
    spatialIndex.buildFromBoxes(allBoxes)

    // 4. Render
    layoutState.loadingMessage.value = 'Rendering layout...'
    renderer = markRaw(new LayoutRenderer())
    renderer.init(ed.view, dataStore, styleManager)

    // 5. Interaction manager
    interactionManager = markRaw(new InteractionManager())
    interactionManager.init(ed.view, dataStore, renderer, spatialIndex)

    interactionManager.onSelectionChange((e) => {
      layoutState.selectedGroups.value = e.selectedGroups
    })

    // 6. Configure plugins
    const selectPlugin = ed.getPlugin<SelectPlugin>('select')
    if (selectPlugin) {
      selectPlugin.configure(interactionManager, renderer)
    }
    const highlightPlugin = ed.getPlugin<HighlightPlugin>('highlight')
    if (highlightPlugin) {
      highlightPlugin.configure(dataStore, renderer)
    }
    const measurePlugin = ed.getPlugin<MeasurePlugin>('measure')
    if (measurePlugin) {
      measurePlugin.setDbuPerMicron(dataStore.dbuPerMicron)
    }
    const layerMgrPlugin = ed.getPlugin<LayerManagerPlugin>('layerManager')
    if (layerMgrPlugin) {
      layerMgrPlugin.configure(dataStore, renderer, styleManager)
      layoutState.layerManager.value = markRaw(layerMgrPlugin)
    }

    // 7. 世界范围 + 缩放适配 die，左下角对齐标尺原点 (X=0、Y 显示 0)，不要用 moveCenter 居中（会抵消 align）
    const dieArea = dataStore.dieArea
    if (dieArea && dieArea.width > 0) {
      ed.setWorldBounds(dieArea.width, dieArea.height)

      const vp = ed.view!
      const padding = 40
      const sw = ed.size.width - padding * 2
      const sh = ed.size.height - padding * 2
      const scale = Math.min(sw / dieArea.width, sh / dieArea.height)
      vp.scale.set(scale)
      ed.alignViewportToRulerOrigin()
    }

    const elapsed = performance.now() - t0
    console.log(`Layout loaded in ${elapsed.toFixed(0)}ms: ${dataStore.totalGroups} groups, ${dataStore.totalBoxes} boxes`)

    layoutState.renderMode.value = 'layout'
    layoutState.loadingState.value = 'ready'
    layoutState.loadingMessage.value = ''
  } catch (err) {
    console.error('Failed to load layout data:', err)
    layoutState.loadingState.value = 'error'
    layoutState.loadingMessage.value = String(err)
    cleanupLayout()
  }
}

const handleStageChange = async (stage: string) => {
  if (!editor.value || !stage) return

  const stepEnum = getStepEnumFromPath(stage)
  if (!stepEnum) {
    editor.value.clearBackground()
    cleanupLayout()
    return
  }

  try {
    // Try to load structured layout JSON first
    const layoutResponse = await getInfoApi({
      cmd: CMDEnum.get_info,
      data: { step: stepEnum, id: InfoEnum.layout }
    })

    if (layoutResponse.response === ResponseEnum.success && layoutResponse.data?.info) {
      const info = layoutResponse.data.info

      // Check if structured JSON data is available
      if (info.header_json && info.data_json) {
        const projectPath = currentProject.value?.path || ''

        const [headerUrl, dataUrl] = await Promise.all([
          getResourceUrl(info.header_json, projectPath),
          getResourceUrl(info.data_json, projectPath),
        ])

        const [headerResp, dataResp] = await Promise.all([
          fetch(headerUrl),
          fetch(dataUrl),
        ])

        const headerJson: RawHeaderJSON = await headerResp.json()
        const dataJson: RawDataJSON = await dataResp.json()

        await loadLayoutData(headerJson, dataJson)
        return
      }
      // 暂时先使用 固定的数据 
      // const headerJson: RawHeaderJSON = await import('../../assets/05_layout_json_top_layout_json-header.json') as unknown as RawHeaderJSON
      // const dataJson: RawDataJSON = await import('../../assets/05_layout_json_top_layout_json-0.json') as unknown as RawDataJSON
      // await loadLayoutData(headerJson, dataJson)
      // return
      // Fallback to image mode
      const imagePath = info.image
      if (imagePath) {
        cleanupLayout()
        const imageUrl = await getResourceUrl(imagePath, currentProject.value?.path || '')
        await editor.value?.setBackgroundImage(imageUrl)
        layoutState.renderMode.value = 'image'
        return
      }
    }

    editor.value?.clearBackground()
    cleanupLayout()
  } catch (error) {
    console.error('Failed to load stage results:', error)
    editor.value?.clearBackground()
    cleanupLayout()
  }
}

watch(() => route.path, (newPath) => {
  const pathParts = newPath.split('/')
  const stage = pathParts[pathParts.length - 1] || 'home'
  handleStageChange(stage)
})

// SSE 通知驱动：subflow/step 通知到达时刷新当前 step 的版图
watch(
  () => sseMessages.value.length,
  async (newLen, oldLen) => {
    if (newLen <= (oldLen ?? 0)) return
    const latest = sseMessages.value[newLen - 1]
    if (!latest || latest.cmd !== 'notify') return

    const notifyId = latest.data?.id as string | undefined
    const sseStep = latest.data?.step as string | undefined
    if (notifyId !== 'subflow' && notifyId !== 'step') return

    const pathParts = route.path.split('/')
    const currentStage = pathParts[pathParts.length - 1] || ''
    if (sseStep && currentStage.toLowerCase() === sseStep.toLowerCase()) {
      await handleStageChange(currentStage)
    }
  }
)

// runFlow 完成后的手动刷新信号（兜底：SSE 通知未就绪时使用）
watch(stepRefreshCounter, () => {
  const pathParts = route.path.split('/')
  const stage = pathParts[pathParts.length - 1] || 'home'
  handleStageChange(stage)
})
</script>

<template>
  <div class="flex flex-col h-full overflow-hidden">
    <DrawingToolbar :editor="editor" />

    <div class="relative flex-1 overflow-hidden">
      <EditorContainer @ready="onEditorReady" />

      <!-- Loading overlay -->
      <div
        v-if="layoutState.loadingState.value === 'loading'"
        class="absolute inset-0 flex items-center justify-center bg-black/40 z-10"
      >
        <div class="flex flex-col items-center gap-2 text-white/80 text-sm">
          <div class="w-6 h-6 border-2 border-white/30 border-t-white/80 rounded-full animate-spin"></div>
          <span>{{ layoutState.loadingMessage.value || 'Loading...' }}</span>
        </div>
      </div>

      <!-- Error state -->
      <div
        v-if="layoutState.loadingState.value === 'error'"
        class="absolute bottom-4 left-4 px-3 py-2 bg-red-900/80 text-red-200 text-xs rounded z-10"
      >
        Load error: {{ layoutState.loadingMessage.value }}
      </div>

      <!-- 鼠标 EDA 坐标（屏幕 → 世界 → 显示） -->
      <div
        class="absolute top-2 right-2 z-20 flex flex-col items-end gap-1 pointer-events-none"
      >
        <div
          v-if="cursorEda"
          class="rounded border border-(--border-color) bg-(--bg-primary)/90 px-2 py-1 font-mono text-[11px] text-(--text-primary) tabular-nums shadow-sm"
          title="EDA / 显示坐标（左下原点，Y 向上，与标尺一致）"
        >
          <span class="text-(--text-secondary)">X</span> {{ formatCursorCoord(cursorEda.x) }}
          <span class="ml-2 text-(--text-secondary)">Y</span> {{ formatCursorCoord(cursorEda.y) }}
        </div>
        <div
          v-if="layoutState.renderMode.value === 'layout'"
          class="px-2 py-1 bg-green-900/60 text-green-300 text-[10px] rounded"
        >
          Layout Mode
        </div>
      </div>
    </div>
  </div>
</template>
