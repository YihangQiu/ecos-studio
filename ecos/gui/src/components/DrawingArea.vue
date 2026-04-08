<script setup lang="ts">
import { shallowRef, markRaw, watch, ref, onUnmounted, onMounted, computed } from 'vue'
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
import {
  TileManager,
  TileInteraction,
  ViewportAnimator,
  EditManager,
  PlacementTool,
  DrcViolationOverlay,
} from '@/applications/editor/tile'
import type { CellDefStore } from '@/applications/editor/tile/CellDefStore'
import type { GlobalLayerStore } from '@/applications/editor/tile/GlobalLayerStore'
import DrawingToolbar from './DrawingToolbar.vue'
import { useWorkspace } from '@/composables/useWorkspace'
import { useEDA } from '@/composables/useEDA'
import { useLayoutState } from '@/composables/useLayoutState'
import { isTauri } from '@/composables/useTauri'
import {
  deriveDrcStepPathFromLayoutJsonRelative,
  pickDrcJsonPath,
  pickLayoutJsonPath,
  resolveLayoutJsonAbsolutePath,
} from '@/composables/useLayoutTileGen'
import { parseDrcStepJson, violationToFitRect } from '@/composables/drcStepParser'
import { readTextFile } from '@tauri-apps/plugin-fs'
import { runLayoutTileGenerationSingleFlight } from '@/composables/layoutTilePipeline'
import { useLayoutTilePrefetchStore } from '@/stores/layoutTilePrefetchStore'
import { getInfoApi } from '@/api/flow'
import { CMDEnum, InfoEnum, StepEnum, ResponseEnum } from '@/api/type'
import { RULER_THICKNESS } from '@/applications/editor/core/rulerConfig'

const route = useRoute()
const { currentProject, sseMessages, stepRefreshCounter } = useWorkspace()
const { getResourceUrl } = useEDA()
const layoutState = useLayoutState()
const tilePrefetchStore = useLayoutTilePrefetchStore()

const editor = shallowRef<Editor | null>(null)

/** get_info(layout) 返回的布局 JSON 相对路径，供工具栏生成瓦片 */
const layoutJsonRelativePath = ref<string | null>(null)
/** DRC 结果 JSON 相对路径：get_info 显式字段，或与布局同目录的 `drc.step.json` */
const drcJsonRelativePath = ref<string | null>(null)
const tileGenBusy = ref(false)
const showTileGenerate = computed(() => isTauri() && import.meta.env.DEV)

/** 当前路由阶段名，用作瓦片缓存子目录 stepKey（与 handleStageChange 一致） */
const currentStepKey = computed(() => {
  const pathParts = route.path.split('/')
  return pathParts[pathParts.length - 1] || 'home'
})

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
  () => currentProject.value?.path,
  (p) => {
    tilePrefetchStore.setProject(p ?? null)
  },
  { immediate: true },
)

watch(
  () => editor.value,
  (ed) => {
    detachCanvasPointerListeners?.()
    cursorEda.value = null
    if (ed) attachCanvasPointerTracking(ed)
  },
  { immediate: true }
)

/** 画布底部居中、标尺上方：版图快捷键（可点击，与 TileInteraction / PlacementTool 一致） */
const LAYOUT_HOTKEY_BAR_BOTTOM_PX = RULER_THICKNESS + 10

function isMacPlatform(): boolean {
  return typeof navigator !== 'undefined' && /Mac|iPhone|iPad/i.test(navigator.platform)
}

const showLayoutHotkeyBar = computed(() =>
  layoutState.renderMode.value === 'layout'
  && layoutState.tileActions.value != null
  && layoutState.tileSelection.value != null,
)

const hotkeyDeleteApplicable = computed(() => {
  if (layoutState.isPlacementMode.value) return false
  const t = layoutState.tileSelection.value?.type
  return t === 'instance' || t === 'segment'
})

const hotkeyCApplicable = computed(() =>
  !layoutState.isPlacementMode.value
  && layoutState.tileSelection.value?.type === 'instance'
  && layoutState.tileSelection.value.cellId != null,
)

const hotkeyRApplicable = computed(() => layoutState.isPlacementMode.value)

const hotkeyFitApplicable = computed(() => layoutState.tileSelection.value != null)

function dispatchDeleteKey(): void {
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete', bubbles: true, cancelable: true }))
}

function dispatchBackspaceKey(): void {
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true, cancelable: true }))
}

function dispatchPlaceKey(): void {
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', bubbles: true, cancelable: true }))
}

function dispatchEscapeKey(): void {
  if (layoutState.isPlacementMode.value) {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
  } else {
    layoutState.tileActions.value?.clearSelection()
  }
}

function dispatchRotateKey(): void {
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'r', bubbles: true, cancelable: true }))
}

function dispatchUndoChord(): void {
  const mac = isMacPlatform()
  window.dispatchEvent(new KeyboardEvent('keydown', {
    key: 'z',
    bubbles: true,
    cancelable: true,
    ctrlKey: !mac,
    metaKey: mac,
  }))
}

function dispatchRedoChord(): void {
  const mac = isMacPlatform()
  if (mac) {
    window.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'z',
      bubbles: true,
      cancelable: true,
      metaKey: true,
      shiftKey: true,
    }))
  } else {
    window.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'y',
      bubbles: true,
      cancelable: true,
      ctrlKey: true,
    }))
  }
}

// Layout modules (not reactive — managed imperatively)
let dataStore: LayoutDataStore | null = null
let renderer: LayoutRenderer | null = null
let styleManager: LayerStyleManager | null = null
let spatialIndex: SpatialIndex | null = null
let interactionManager: InteractionManager | null = null
let styleStateUnlisten: (() => void) | null = null
// Tile rendering module
let tileManager: TileManager | null = null
let tileInteraction: TileInteraction | null = null
let viewportAnimator: ViewportAnimator | null = null
let editManager: EditManager | null = null
let placementTool: PlacementTool | null = null
let drcViolationOverlay: DrcViolationOverlay | null = null

// 记住最近选中的 instance cell 信息，用于 Place 工具
let lastSelectedCellId: number | null = null
let lastSelectedOrient = 0

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
  placementTool?.destroy()
  editManager?.destroy()
  tileInteraction?.destroy()
  viewportAnimator?.destroy()
  tileManager?.destroy()

  interactionManager = null
  renderer = null
  spatialIndex = null
  dataStore = null
  styleManager = null
  placementTool = null
  editManager = null
  tileInteraction = null
  viewportAnimator = null
  tileManager = null

  drcViolationOverlay?.destroy()
  drcViolationOverlay = null
  layoutState.drcOverlayReady.value = false
  layoutState.drcViolationCount.value = 0
  layoutState.drcViolations.value = []
  layoutState.focusDrcViolationByIndex.value = null
  layoutState.tileDieWorldH.value = 0

  layoutState.selectedGroups.value = []
  layoutState.dataStore.value = null
  layoutState.tileSelection.value = null
  layoutState.tileActions.value = null
  layoutState.tileLayers.value = []
  layoutState.tileLayerActions.value = null
  layoutState.tileEditActions.value = null
  layoutState.hasUnsavedEdits.value = false
  layoutState.isPlacementMode.value = false
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

/** manifest.layer id（= layerIdx）在 cells.bin / global.bin 中是否出现几何 */
function manifestLayerIdsWithGeometry(
  cellStore: CellDefStore,
  globalStore: GlobalLayerStore,
): Set<number> {
  const ids = new Set<number>()
  for (const cid of cellStore.getAllCellIds()) {
    const def = cellStore.getCellDef(cid)
    if (!def) continue
    for (const { layerIdx, rects } of def.layers) {
      if (rects.length > 0) ids.add(layerIdx)
    }
  }
  for (const s of globalStore.shapes) {
    ids.add(s.layerIdx)
  }
  return ids
}

async function loadDrcViolationOverlayAfterTiles(_ed: Editor, dieWorldH: number): Promise<void> {
  layoutState.drcOverlayReady.value = false
  layoutState.drcViolationCount.value = 0
  layoutState.drcViolations.value = []
  if (!isTauri() || !drcViolationOverlay) return

  const projectPath = currentProject.value?.path
  const drcRel = drcJsonRelativePath.value
  if (!projectPath || !drcRel) return

  try {
    const abs = await resolveLayoutJsonAbsolutePath(projectPath, drcRel)
    const text = await readTextFile(abs)
    const raw = JSON.parse(text) as unknown
    const violations = parseDrcStepJson(raw, dieWorldH)
    drcViolationOverlay.setViolations(violations)
    layoutState.drcViolations.value = violations
    layoutState.drcViolationCount.value = violations.length
    layoutState.drcOverlayReady.value = true
  } catch (e) {
    console.warn('[drc overlay] load failed:', e)
    drcViolationOverlay.setViolations([])
    layoutState.drcViolations.value = []
  }
}

/** @param localRoot 瓦片输出目录绝对路径；在 Tauri 下传入可避免 asset:// 无法用 fetch 读 manifest/瓦片 */
async function loadTileLayout(baseUrl: string, localRoot?: string): Promise<void> {
  const ed = editor.value
  if (!ed?.view) return

  cleanupLayout()

  layoutState.loadingState.value = 'loading'
  layoutState.loadingMessage.value = 'Loading tile manifest...'

  try {
    tileManager = markRaw(new TileManager(ed.view, baseUrl, localRoot))
    await tileManager.init()
    await Promise.all([tileManager.cellStore.ready, tileManager.globalStore.ready])

    // 与 COORDINATES.md 一致：瓦片数据是 Pixi 世界坐标 [0,dieW)×[0,dieH)，须同步 Editor 世界盒，
    // 否则 worldToDisplay / 标尺使用的 worldHeight 仍是旧值（如默认 4000），鼠标 EDA 读数会错。
    {
      const d = tileManager.manifest!.dieArea
      ed.setWorldBounds(d.w, d.h)
    }

    // ViewportAnimator
    viewportAnimator = markRaw(new ViewportAnimator(ed.view))
    if (tileManager.manifest) {
      viewportAnimator.setManifest(tileManager.manifest)
    }

    layoutState.focusDrcViolationByIndex.value = (index: number) => {
      const list = layoutState.drcViolations.value
      const v = list[index]
      if (!v || !viewportAnimator) return
      void viewportAnimator.fitToBbox(violationToFitRect(v), 0.18, 450)
    }

    // TileInteraction (RBush + hit-test + selection overlay)
    tileInteraction = markRaw(new TileInteraction(
      ed.view,
      tileManager,
      tileManager.cellStore,
      tileManager.globalStore,
    ))

    // EditManager
    editManager = markRaw(new EditManager(tileManager, tileManager.cellStore))
    ed.view.addChild(editManager.editOverlay)

    tileManager.setEditDirtyGetter(() => editManager!.hasUnsavedChanges)

    // 绑定 EditManager → TileInteraction
    tileInteraction.setEditManager(editManager)

    // 挂载 overlays 到 viewport（渲染顺序：edit → ghost → highlight → drc → selection）
    ed.view.addChild(tileInteraction.ghostOverlay)
    ed.view.addChild(tileInteraction.highlightOverlay)
    drcViolationOverlay = markRaw(new DrcViolationOverlay(ed.view))
    drcViolationOverlay.bindViewportEvents()
    ed.view.addChild(drcViolationOverlay)
    ed.view.addChild(tileInteraction.selectionOverlay)

    // PlacementTool
    placementTool = markRaw(new PlacementTool(
      ed.view,
      editManager,
      tileManager,
      tileManager.cellStore,
    ))
    ed.view.addChild(placementTool.ghostOverlay)

    // EditManager 变更 → 更新 hasUnsavedEdits
    editManager.onChange(() => {
      layoutState.hasUnsavedEdits.value = editManager?.hasUnsavedChanges ?? false
    })

    // 选中回调 → 更新 Vue 响应式状态 + 记住 cellId 供 Place 使用
    tileInteraction.onSelectionChange((info) => {
      layoutState.tileSelection.value = info
      if (info?.type === 'instance' && info.cellId != null) {
        lastSelectedCellId = info.cellId
        lastSelectedOrient = info.orient ?? 0
      }
    })

    // C 键 → 进入放置模式
    tileInteraction.onRequestPlacement((cellId, orient) => {
      _enterPlacement(cellId, orient)
    })

    // PlacementTool 停用 → 回到 select 模式
    placementTool.onDeactivate(() => {
      layoutState.isPlacementMode.value = false
      tileInteraction?.enable()
    })

    // viewport 缩放时刷新选中框线宽
    ed.view.on('zoomed', () => tileInteraction?.refreshSelectionStroke())

    // 注册 tile 操作回调给 PropertiesPanel 使用
    const mf = tileManager.manifest!
    layoutState.tileDbuPerMicron.value = mf.dbuPerMicron
    layoutState.tileDieWorldH.value = mf.dieArea.h
    layoutState.tileActions.value = {
      clearSelection: () => tileInteraction?.clearSelection(),
      fitToView: () => handleFitToView(),
    }

    // 注册编辑操作
    layoutState.tileEditActions.value = {
      deleteSelected: () => {
        const sel = tileInteraction?.currentSelection
        if (sel?.type === 'instance' && sel.instanceId != null && editManager) {
          editManager.deleteInstance(sel.instanceId)
          tileInteraction?.clearSelection()
        }
      },
      undo: () => editManager?.undo(),
      redo: () => editManager?.redo(),
      startPlacement: (cellId: number, orient?: number) => {
        _enterPlacement(cellId, orient ?? 0)
      },
      cancelPlacement: () => {
        placementTool?.deactivate()
      },
    }

    // 注册图层列表和操作给 LayerPanel：只列当前数据集中有几何的 layer（cells + global）
    const usedLayerIds = manifestLayerIdsWithGeometry(tileManager.cellStore, tileManager.globalStore)
    const layersForUi = mf.layers.filter(l => usedLayerIds.has(l.id))
    layoutState.tileLayers.value = layersForUi.map(l => ({
      id: l.id, name: l.name, color: l.color,
      alpha: l.alpha, zOrder: l.zOrder, visible: true,
    }))
    layoutState.tileLayerActions.value = {
      toggleLayer: (id: number) => {
        const vis = !tileManager!.isLayerVisible(id)
        tileManager!.setLayerVisible(id, vis)
        layoutState.tileLayers.value = layoutState.tileLayers.value.map(l =>
          l.id === id ? { ...l, visible: vis } : l,
        )
      },
      showAll: () => {
        for (const l of layersForUi) tileManager!.setLayerVisible(l.id, true)
        layoutState.tileLayers.value = layoutState.tileLayers.value.map(l => ({ ...l, visible: true }))
      },
      hideAll: () => {
        for (const l of layersForUi) tileManager!.setLayerVisible(l.id, false)
        layoutState.tileLayers.value = layoutState.tileLayers.value.map(l => ({ ...l, visible: false }))
      },
    }

    // 瓦片就绪后去掉步骤预览用的底图，避免与矢量/栅格瓦片叠在一起
    ed.clearBackground()

    void loadDrcViolationOverlayAfterTiles(ed, mf.dieArea.h)

    layoutState.renderMode.value = 'layout'
    layoutState.loadingState.value = 'ready'
    layoutState.loadingMessage.value = ''
  } catch (err) {
    console.error('Failed to load tile layout:', err)
    layoutState.loadingState.value = 'error'
    layoutState.loadingMessage.value = String(err)
    cleanupLayout()
  }
}

function _enterPlacement(cellId: number, orient: number): void {
  if (!placementTool || !tileInteraction) return
  tileInteraction.disable()
  tileInteraction.clearSelection()
  tileInteraction.highlightOverlay.clear()
  placementTool.activate(cellId, orient)
  layoutState.isPlacementMode.value = true
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
      layoutJsonRelativePath.value = pickLayoutJsonPath(info)
      drcJsonRelativePath.value = pickDrcJsonPath(info)
        ?? deriveDrcStepPathFromLayoutJsonRelative(layoutJsonRelativePath.value ?? '')
        ?? null

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
    layoutJsonRelativePath.value = null
    drcJsonRelativePath.value = null
  } catch (error) {
    console.error('Failed to load stage results:', error)
    editor.value?.clearBackground()
    cleanupLayout()
    layoutJsonRelativePath.value = null
    drcJsonRelativePath.value = null
  }
}

async function onGenerateTilesFromToolbar(): Promise<void> {
  const projectPath = currentProject.value?.path
  const rel = layoutJsonRelativePath.value
  if (!projectPath || !rel) {
    layoutState.loadingState.value = 'error'
    layoutState.loadingMessage.value =
      '未找到布局 JSON 路径：请确认当前步骤的 get_info(layout) 已返回 json/info 等字段。'
    return
  }

  tileGenBusy.value = true
  layoutState.loadingState.value = 'loading'
  layoutState.loadingMessage.value = 'Rendering layout…'
  try {
    tilePrefetchStore.clearDeferredPrefetchQueue()
    const { baseUrl, outDir, fromCache } = await runLayoutTileGenerationSingleFlight({
      projectPath,
      layoutJsonRelative: rel,
      stepKey: currentStepKey.value,
      source: 'user',
    })
    if (fromCache) {
      layoutState.loadingMessage.value = '加载缓存的版图瓦片…'
    }
    await loadTileLayout(baseUrl, outDir)
  } catch (err) {
    console.error('Tile generation failed:', err)
    layoutState.loadingState.value = 'error'
    layoutState.loadingMessage.value = String(err)
    cleanupLayout()
  } finally {
    tileGenBusy.value = false
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

// ─── 工具切换 → Tile 交互模式管理 ─────────────────────────────────────────────

function onToolChange(toolId: string): void {
  if (!tileInteraction) return

  // 退出放置模式（如果在）
  placementTool?.deactivate()

  if (toolId === 'select') {
    tileInteraction.enable()
  } else if (toolId === 'place') {
    // 进入放置模式：使用最近选中的 cellId
    if (lastSelectedCellId != null) {
      _enterPlacement(lastSelectedCellId, lastSelectedOrient)
    } else {
      // 没有选过 instance → 回退到 select 模式
      tileInteraction.enable()
    }
  } else {
    tileInteraction.disable()
    tileInteraction.clearSelection()
    tileInteraction.highlightOverlay.clear()
  }
}

// ─── Tile 交互操作 ──────────────────────────────────────────────────────────

function handleFitToView(): void {
  const sel = layoutState.tileSelection.value
  if (!sel || !viewportAnimator) return
  viewportAnimator.fitToBbox({ x: sel.bboxX, y: sel.bboxY, w: sel.bboxW, h: sel.bboxH })
}

/** 版图选中时：F 适应选中包围盒（与 Fit 按钮一致） */
function onWindowKeyDownForLayoutFit(e: KeyboardEvent): void {
  if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
  if (e.key !== 'f' && e.key !== 'F') return
  if (e.ctrlKey || e.metaKey || e.altKey) return
  if (layoutState.renderMode.value !== 'layout') return
  if (!layoutState.tileSelection.value) return
  e.preventDefault()
  handleFitToView()
}

onMounted(() => {
  window.addEventListener('keydown', onWindowKeyDownForLayoutFit)
})

onUnmounted(() => {
  detachCanvasPointerListeners?.()
  window.removeEventListener('keydown', onWindowKeyDownForLayoutFit)
})

// 保留 loadLayoutData 供未来切换回 JSON 模式使用
void loadLayoutData
</script>

<template>
  <div class="flex flex-col h-full overflow-hidden">
    <DrawingToolbar
      :editor="editor"
      :show-tile-generate="showTileGenerate"
      :tile-gen-busy="tileGenBusy"
      :layout-tile-shortcuts-hint="layoutState.renderMode.value === 'layout' && layoutState.tileSelection.value != null"
      @toolChange="onToolChange"
      @generateTiles="onGenerateTilesFromToolbar"
    />

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

      <!-- 有选中时展示：底部居中、标尺上方，版图快捷键（可点击） -->
      <div
        v-if="showLayoutHotkeyBar"
        class="absolute left-1/2 z-30 max-w-[min(100%,56rem)] -translate-x-1/2 pointer-events-none px-3"
        :style="{ bottom: `${LAYOUT_HOTKEY_BAR_BOTTOM_PX}px` }"
      >
        <div
          class="pointer-events-auto flex flex-wrap items-center justify-center gap-x-1 gap-y-1 rounded-lg border border-(--border-color) bg-(--bg-primary)/95 px-2 py-1.5 shadow-lg backdrop-blur-sm"
          role="toolbar"
          aria-label="版图快捷键"
        >
          <button
            type="button"
            class="rounded border border-(--border-color) bg-(--bg-secondary) px-1.5 py-0.5 font-mono text-[10px] leading-tight text-(--text-primary) hover:bg-(--bg-hover) sm:text-[11px]"
            title="放置模式：退出放置；否则：清除选中"
            @click="dispatchEscapeKey"
          >
            Esc
          </button>
          <button
            type="button"
            class="rounded border border-(--border-color) bg-(--bg-secondary) px-1.5 py-0.5 font-mono text-[10px] leading-tight text-(--text-primary) hover:bg-(--bg-hover) disabled:cursor-not-allowed disabled:opacity-40 sm:text-[11px]"
            :disabled="!hotkeyRApplicable"
            title="放置模式：旋转 cell 朝向（90° 步进）"
            @click="dispatchRotateKey"
          >
            R
          </button>
          <button
            type="button"
            class="rounded border border-(--border-color) bg-(--bg-secondary) px-1.5 py-0.5 font-mono text-[10px] leading-tight text-(--text-primary) hover:bg-(--bg-hover) disabled:cursor-not-allowed disabled:opacity-40 sm:text-[11px]"
            :disabled="!hotkeyCApplicable"
            title="选中 instance：复制 cell 并进入放置（C）"
            @click="dispatchPlaceKey"
          >
            C
          </button>
          <button
            type="button"
            class="rounded border border-(--border-color) bg-(--bg-secondary) px-1.5 py-0.5 font-mono text-[10px] leading-tight text-(--text-primary) hover:bg-(--bg-hover) disabled:cursor-not-allowed disabled:opacity-40 sm:text-[11px]"
            :disabled="!hotkeyDeleteApplicable"
            title="删除（Delete）"
            @click="dispatchDeleteKey"
          >
            Del
          </button>
          <button
            type="button"
            class="rounded border border-(--border-color) bg-(--bg-secondary) px-1.5 py-0.5 font-mono text-[10px] leading-tight text-(--text-primary) hover:bg-(--bg-hover) disabled:cursor-not-allowed disabled:opacity-40 sm:text-[11px]"
            :disabled="!hotkeyDeleteApplicable"
            title="删除（Backspace）"
            @click="dispatchBackspaceKey"
          >
            ⌫
          </button>
          <button
            type="button"
            class="rounded border border-(--border-color) bg-(--bg-secondary) px-1.5 py-0.5 font-mono text-[10px] leading-tight text-(--text-primary) hover:bg-(--bg-hover) sm:text-[11px]"
            title="撤销（Ctrl+Z）"
            @click="dispatchUndoChord"
          >
            {{ isMacPlatform() ? '⌘Z' : 'Ctrl+Z' }}
          </button>
          <button
            type="button"
            class="rounded border border-(--border-color) bg-(--bg-secondary) px-1.5 py-0.5 font-mono text-[10px] leading-tight text-(--text-primary) hover:bg-(--bg-hover) sm:text-[11px]"
            :title="isMacPlatform() ? '重做（⇧⌘Z）' : '重做（Ctrl+Y）'"
            @click="dispatchRedoChord"
          >
            {{ isMacPlatform() ? '⇧⌘Z' : 'Ctrl+Y' }}
          </button>
          <button
            type="button"
            class="rounded border border-(--accent-color)/40 bg-(--accent-color)/15 px-1.5 py-0.5 font-mono text-[10px] leading-tight text-(--text-primary) hover:bg-(--accent-color)/25 disabled:cursor-not-allowed disabled:opacity-40 sm:text-[11px]"
            :disabled="!hotkeyFitApplicable"
            title="适应选中（F）"
            aria-label="适应选中到视口（F）"
            @click="handleFitToView"
          >
            F
          </button>
        </div>
      </div>

      <!-- 鼠标 EDA 坐标（屏幕 → 世界 → 显示） -->
      <div
        class="absolute top-2 right-2 z-20 flex flex-col items-end gap-1 pointer-events-none"
      >
        <div
          v-if="cursorEda"
          class="rounded border border-(--border-color) bg-(--bg-primary)/90 px-2 py-1 font-mono text-[11px] text-(--text-primary) tabular-nums shadow-sm"
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
