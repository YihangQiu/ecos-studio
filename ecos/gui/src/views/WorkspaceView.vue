<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import Splitter from 'primevue/splitter'
import SplitterPanel from 'primevue/splitterpanel'
import { StepEnum } from '@/api/type'
import { useLayoutState } from '@/composables/useLayoutState'
import DrawingArea from '../components/DrawingArea.vue'
import ChatInspectorPanel from '../components/ChatInspectorPanel.vue'
import ThumbnailGallery from '../components/ThumbnailGallery.vue'
import PropertiesPanel from '../components/PropertiesPanel.vue'
import LayerPanel from '../components/LayerPanel.vue'
import DrcViolationPanel from '../components/DrcViolationPanel.vue'

const layoutState = useLayoutState()
const route = useRoute()

/** 图片预览模式不显示图层/属性栏；勿用整树 :key 切换，否则 DrawingArea 会重挂载并再次跑 handleStageChange，把矢量版图打回图片预览 */
const showLayoutSidePanels = computed(() => layoutState.renderMode.value === 'layout')

/** 仅 DRC 路由显示违例面板（与 DrawingArea 当前步骤段一致） */
const isDrcStep = computed(() => {
  const pathParts = route.path.split('/')
  const stage = pathParts[pathParts.length - 1] || ''
  return stage.toLowerCase() === StepEnum.DRC.toLowerCase()
})

const mainSplitter = ref<InstanceType<typeof Splitter> | null>(null)

watch(
  [showLayoutSidePanels, isDrcStep],
  async () => {
    await nextTick()
    mainSplitter.value?.resetState?.()
  },
  { flush: 'post' },
)

let isResizing = false

const handleMouseDown = (e: MouseEvent) => {
  const target = e.target as HTMLElement
  const gutter = target.closest('.p-splitter-gutter')
  if (gutter) {
    isResizing = true
    document.body.classList.add('splitter-resizing')

    const splitter = gutter.closest('.p-splitter')
    if (splitter?.classList.contains('p-splitter-vertical')) {
      document.body.classList.add('splitter-resizing-vertical')
    }

    // 立即清除任何已存在的选区（Linux WebKitGTK 兼容）
    window.getSelection()?.removeAllRanges()
  }
}

const handleMouseUp = () => {
  if (isResizing) {
    isResizing = false
    document.body.classList.remove('splitter-resizing')
    document.body.classList.remove('splitter-resizing-vertical')
  }
}

onMounted(() => {
  document.addEventListener('mousedown', handleMouseDown)
  document.addEventListener('mouseup', handleMouseUp)
})

onUnmounted(() => {
  document.removeEventListener('mousedown', handleMouseDown)
  document.removeEventListener('mouseup', handleMouseUp)
  document.body.classList.remove('splitter-resizing')
  document.body.classList.remove('splitter-resizing-vertical')
})
</script>
<template>
  <div class="editor-view">
    <Splitter ref="mainSplitter" class="flex-1 h-full border-none min-w-0">
      <!-- Left: Drawing + Thumbnails（无中间栏时 60+15 合并为 75） -->
      <SplitterPanel :size="showLayoutSidePanels ? 60 : 75" :minSize="35" class="flex flex-col min-w-0">
        <Splitter layout="vertical" class="h-full border-none">
          <SplitterPanel :size="70" :minSize="30" class="flex flex-col">
            <DrawingArea />
          </SplitterPanel>
          <SplitterPanel :size="30" class="flex flex-col">
            <ThumbnailGallery />
          </SplitterPanel>
        </Splitter>
      </SplitterPanel>

      <!-- Middle: Layout panels（仅矢量/Tile 版图模式） -->
      <SplitterPanel
        v-if="showLayoutSidePanels"
        :size="15"
        :minSize="10"
        class="flex flex-col min-w-0 overflow-hidden"
      >
        <Splitter layout="vertical" class="h-full border-none">
          <SplitterPanel
            v-if="isDrcStep"
            :size="32"
            :minSize="16"
            class="flex flex-col overflow-hidden min-h-0"
          >
            <DrcViolationPanel />
          </SplitterPanel>
          <SplitterPanel
            :size="isDrcStep ? 34 : 45"
            :minSize="14"
            class="flex flex-col overflow-hidden min-h-0"
          >
            <LayerPanel />
          </SplitterPanel>
          <SplitterPanel
            :size="isDrcStep ? 34 : 55"
            :minSize="18"
            class="flex flex-col overflow-hidden min-h-0"
          >
            <PropertiesPanel />
          </SplitterPanel>
        </Splitter>
      </SplitterPanel>

      <!-- Right: Chat -->
      <SplitterPanel :size="25" :minSize="25" class="chat-panel overflow-hidden min-w-0 max-w-full">
        <ChatInspectorPanel />
      </SplitterPanel>
    </Splitter>
  </div>
</template>
<style scoped>
.editor-view {
  width: 100%;
  height: 100%;
}

:deep(.p-splitter) {
  background: transparent;
  border: none;
  /* 优化性能 */
  contain: layout style;
}

:deep(.p-splitter-panel) {
  /* 优化重绘性能 */
  contain: layout style paint;
  will-change: auto;
}

:deep(.p-splitter-gutter) {
  background: var(--border-color);
  transition: background-color 0.15s ease-out;
  display: flex;
  align-items: center;
  justify-content: center;
  /* 减少重绘 */
  will-change: background-color;
}

:deep(.p-splitter-gutter:hover) {
  background: var(--accent-color);
  opacity: 0.5;
}

:deep(.p-splitter-gutter-handle) {
  display: none !important;
  /* 隐藏默认的大手柄 */
}

/* 针对横向分割条 */
:deep(.p-splitter-horizontal > .p-splitter-gutter) {
  width: 2px !important;
  cursor: col-resize;
}

/* 针对纵向分割条 */
:deep(.p-splitter-vertical > .p-splitter-gutter) {
  height: 2px !important;
  cursor: row-resize;
}

/* 确保 Splitter 面板可以正确收缩 */
:deep(.p-splitter-panel) {
  min-width: 0;
  overflow: hidden;
}

/* Chat 面板严格约束 - 防止内容撑开 */
.chat-panel {
  contain: layout style size;
  box-sizing: border-box;
}

:deep(.chat-panel > *) {
  max-width: 100%;
  min-width: 0;
}
</style>