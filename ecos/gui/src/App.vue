<template>
  <div class="app-wrapper">
    <!-- 窗口调整大小的边缘区域 -->
    <div class="resize-edge resize-top" @mousedown="startResize('North')"></div>
    <div class="resize-edge resize-bottom" @mousedown="startResize('South')"></div>
    <div class="resize-edge resize-left" @mousedown="startResize('West')"></div>
    <div class="resize-edge resize-right" @mousedown="startResize('East')"></div>
    <div class="resize-corner resize-top-left" @mousedown="startResize('NorthWest')"></div>
    <div class="resize-corner resize-top-right" @mousedown="startResize('NorthEast')"></div>
    <div class="resize-corner resize-bottom-left" @mousedown="startResize('SouthWest')"></div>
    <div class="resize-corner resize-bottom-right" @mousedown="startResize('SouthEast')"></div>

    <!-- 主应用容器 -->
    <div class="app-container">
      <!-- 全局顶部菜单栏 -->
      <TopBar :project-name="isWelcome ? null : currentProject?.name" @menu-action="handleMenuAction" />
      <!-- 页面内容 -->
      <div class="app-content">
        <router-view />
      </div>
    </div>

    <!-- 全局 Toast 通知 -->
    <Toast position="top-right" />

    <!-- 全局新建工程向导 -->
    <NewProjectWizard v-if="showNewProjectWizard" @close="showNewProjectWizard = false" @create="handleWizardCreate" />

    <!-- Full-screen loading while the workspace is being prepared (open/new project, session restore) -->
    <Teleport to="body">
      <Transition name="api-backend-overlay">
        <div
          v-if="apiBackendConnecting"
          class="api-backend-overlay"
          role="status"
          aria-busy="true"
          aria-live="polite"
        >
          <div class="api-backend-panel">
            <div class="api-backend-spinner" aria-hidden="true" />
            <p class="api-backend-title">Preparing your workspace</p>
            <p class="api-backend-sub">First load or restoring your project may take a moment</p>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getCurrentWindow } from '@tauri-apps/api/window'
import { open as shellOpen } from '@tauri-apps/plugin-shell'
import { useThemeStore } from '@/stores/themeStore'
import { useWorkspace } from '@/composables/useWorkspace'
import { usePdkManager } from '@/composables/usePdkManager'

import TopBar from '@/components/TopBar.vue'
import Toast from 'primevue/toast'
import NewProjectWizard from '@/components/NewProjectWizard.vue'
import type { WorkspaceConfig } from '@/types'
import { setWindowResizing } from '@/composables/useWindowResizeState'

const router = useRouter()
const themeStore = useThemeStore()
const route = useRoute()
const isWelcome = computed(() => route.path === '/')
const { loadRecentProjects, currentProject, openProject, newProject, apiBackendConnecting } =
  useWorkspace()
const { loadPdks } = usePdkManager()
const { showToast } = useWorkspace()
// ---- 新建工程向导 ----
const showNewProjectWizard = ref(false)

const handleWizardCreate = async (config: WorkspaceConfig) => {
  showNewProjectWizard.value = false
  const success = await newProject(config)
  if (success) router.push('/workspace')
}

// ---- TopBar 菜单事件 ----
const handleMenuAction = async (action: string) => {
  switch (action) {
    case 'new-project':
      showNewProjectWizard.value = true
      break
    case 'open-project': {
      const success = await openProject()
      if (success) router.push('/workspace')
      break
    }
    case 'documentation':
      try {
        await shellOpen('https://github.com/openecos-projects/ecos-studio/blob/main/ecos/docs/user-guide.md')
      } catch (error) {
        console.error('Failed to open documentation:', error)
        showToast({
          severity: 'error',
          summary: 'Error',
          detail: `Failed to open documentation because of ${error instanceof Error ? error.message : String(error)}`,
          life: 3000
        })
      }
      break
    case 'about':
      // TODO: 打开关于对话框
      break
  }
}

// 窗口调整大小
let isResizing = false
type ResizeDirection = 'East' | 'North' | 'NorthEast' | 'NorthWest' | 'South' | 'SouthEast' | 'SouthWest' | 'West';

// 统一管理 `.window-resizing` class：
// - `startResize` / `onResized` 任一来源都会打上这个 class
// - 超过 RESIZE_IDLE_MS 没有新尺寸事件即视为结束
// 这样即使 Linux 下 `startResizeDragging` 让 WM 接管鼠标、浏览器收不到 mouseup，
// 也能正确解除降级状态，不会出现"resize 完后界面一直丢失阴影/模糊"。
const RESIZE_IDLE_MS = 180
let resizeIdleTimer: ReturnType<typeof setTimeout> | undefined
let unlistenWindowResized: (() => void) | undefined

const markResizing = () => {
  isResizing = true
  document.body.classList.add('window-resizing')
  // 广播全局状态，组件（如 HomeView 的 ECharts）可据此跳过昂贵重绘
  setWindowResizing(true)
  // 立即同步一次最大化状态：最大化发生在 resize 的瞬间，
  // 如果只在 idle 回调里同步，会有 RESIZE_IDLE_MS 的窗口期露白。
  void syncMaximizedClass()
  if (resizeIdleTimer) clearTimeout(resizeIdleTimer)
  resizeIdleTimer = setTimeout(() => {
    resizeIdleTimer = undefined
    isResizing = false
    document.body.classList.remove('window-resizing')
    setWindowResizing(false)
    // 停歇时再同步一次，兜底系统贴边 / 快捷键等中间态没覆盖的情况
    void syncMaximizedClass()
  }, RESIZE_IDLE_MS)
}

const startResize = async (direction: ResizeDirection) => {
  markResizing()
  await getCurrentWindow().startResizeDragging(direction)
}

/**
 * 同步窗口最大化状态到 body.window-maximized。
 *
 * 目的：Linux (WebKitGTK) 下「透明 + 无装饰 + 最大化」组合会让 webview
 * 露出白色画布，因此最大化时需要把根层背景改成主题色、去掉圆角边框，
 * 见 styles/index.css 与本文件 scoped 样式中的 `.window-maximized` 规则。
 */
async function syncMaximizedClass() {
  try {
    const maxed = await getCurrentWindow().isMaximized()
    document.body.classList.toggle('window-maximized', maxed)
  } catch {
    /* ignore: window API unavailable (e.g. SSR / test) */
  }
}

// 阻止拖拽调整窗口大小时的文本选择
const handleSelectStart = (e: Event) => {
  if (isResizing) {
    e.preventDefault()
    return false
  }
}

onMounted(async () => {
  themeStore.initTheme()
  // 在应用启动时加载最近项目和已导入的 PDK
  await Promise.all([loadRecentProjects(), loadPdks()])

  document.addEventListener('selectstart', handleSelectStart)

  // 启动时先同步一次最大化状态（从持久化会话恢复的场景）
  void syncMaximizedClass()

  // 由 Tauri 的 resize 事件统一驱动降级状态，覆盖所有缩放来源
  // （边缘拖拽、标题栏双击最大化、系统贴边、快捷键等）。
  getCurrentWindow()
    .onResized(() => markResizing())
    .then((unlisten) => {
      unlistenWindowResized = unlisten
    })
    .catch(() => {
      /* ignore */
    })
})

onUnmounted(() => {
  document.removeEventListener('selectstart', handleSelectStart)
  if (resizeIdleTimer) {
    clearTimeout(resizeIdleTimer)
    resizeIdleTimer = undefined
  }
  unlistenWindowResized?.()
  document.body.classList.remove('window-resizing')
  document.body.classList.remove('window-maximized')
  setWindowResizing(false)
})
</script>

<style>
/* Teleport 到 body，需非 scoped 才能作用在传送后的节点上 */
.api-backend-overlay {
  position: fixed;
  inset: 0;
  z-index: 20050;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.42);
  backdrop-filter: blur(5px);
}

.api-backend-panel {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 28px 40px;
  border-radius: 12px;
  background: var(--bg-primary);
  border: 1px solid rgba(128, 128, 128, 0.28);
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.4);
  min-width: 240px;
}

.api-backend-spinner {
  width: 36px;
  height: 36px;
  border: 3px solid var(--border-color, rgba(128, 128, 128, 0.35));
  border-top-color: var(--accent-color, #4a9eff);
  border-radius: 50%;
  animation: api-backend-spin 0.75s linear infinite;
}

.api-backend-title {
  margin: 4px 0 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary, #e8e8e8);
}

.api-backend-sub {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary, #9ca3af);
  text-align: center;
  line-height: 1.45;
}

@keyframes api-backend-spin {
  to {
    transform: rotate(360deg);
  }
}

.api-backend-overlay-enter-active,
.api-backend-overlay-leave-active {
  transition: opacity 0.22s ease;
}

.api-backend-overlay-enter-active .api-backend-panel,
.api-backend-overlay-leave-active .api-backend-panel {
  transition: transform 0.22s ease, opacity 0.22s ease;
}

.api-backend-overlay-enter-from,
.api-backend-overlay-leave-to {
  opacity: 0;
}

.api-backend-overlay-enter-from .api-backend-panel,
.api-backend-overlay-leave-to .api-backend-panel {
  transform: scale(0.96);
  opacity: 0.85;
}

/*
 * 窗口 resize 期间的性能降级：
 * 无装饰 + 透明窗口下，每一帧的布局/合成代价都很高，叠加 blur、阴影、
 * 过渡/动画会让拖边界的手感明显卡顿。resize 停歇后（App.vue 里通过
 * onResized + 去抖移除 class）自动恢复，所以视觉上几乎感觉不到差别。
 */
.window-resizing,
.window-resizing * {
  transition: none !important;
  animation: none !important;
  filter: none !important;
  -webkit-filter: none !important;
  backdrop-filter: none !important;
  -webkit-backdrop-filter: none !important;
  box-shadow: none !important;
  text-shadow: none !important;
  scroll-behavior: auto !important;
}

/*
 * 额外降级：隐藏带 background-image 渐变绘制的 HUD 角标 / 栅格线等装饰。
 * 这些元素每帧都需要 repaint，单独一个就抵掉半帧预算，resize 期间不渲染
 * 它们能显著提升拖拽流畅度。
 */
.window-resizing .bg-grid,
.window-resizing .layout-content {
  background-image: none !important;
}

.window-resizing .section-card::after {
  display: none !important;
}

/* resize 期间图片用最快速路径重采样，避免触发高质量重采样造成的抖动 */
.window-resizing img {
  image-rendering: auto;
}

.window-resizing {
  cursor: default;
}
</style>

<style scoped>
.app-wrapper {
  width: 100%;
  height: 100%;
  position: relative;
}

.app-container {
  width: 100%;
  height: 100%;
  max-width: 100vw;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  /* 圆角窗口效果 */
  border-radius: 10px;
  background: var(--bg-primary);
  /* 边框 - 微弱的亮色边框 */
  border: 1px solid rgba(128, 128, 128, 0.3);
}

/*
 * 最大化时取消圆角 + 边框：
 * 最大化后窗口占满屏幕，圆角外露出的就是 webview 白画布（也就是截图里
 * 那片白屏）。把圆角去掉后 .app-container 能贴住窗口四边，彻底没处可露。
 * body 不会被 scoped 加 data-v 属性（它是 ancestor），`.app-container`
 * 是本组件自身元素，scoped 转换后选择器仍能正确命中。
 */
body.window-maximized .app-container {
  border-radius: 0;
  border: none;
}

.app-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  background: var(--bg-primary);
}

/* 调整大小的边缘区域 */
.resize-edge,
.resize-corner {
  position: absolute;
  z-index: 9999;
}

/* 上边缘（左右留出顶栏按钮/菜单区域，避免与自定义标题栏重叠导致点击被当成 resize） */
.resize-top {
  top: 0;
  left: 220px;
  right: 220px;
  height: 6px;
  cursor: ns-resize;
}

/* 下边缘 */
.resize-bottom {
  bottom: 0;
  left: 20px;
  right: 20px;
  height: 6px;
  cursor: ns-resize;
}

/* 左边缘 */
.resize-left {
  left: 0;
  top: 20px;
  bottom: 20px;
  width: 6px;
  cursor: ew-resize;
}

/* 右边缘：从顶栏下方开始，避免盖住右侧最小化/最大化/关闭 */
.resize-right {
  right: 0;
  top: 40px;
  bottom: 20px;
  width: 6px;
  cursor: ew-resize;
}

/* 左上角：下移避免压住顶栏左侧菜单/图标点击 */
.resize-top-left {
  top: 40px;
  left: 0;
  width: 20px;
  height: 20px;
  cursor: nwse-resize;
}

/* 右上角：下移避免压住顶栏右侧窗口控制按钮 */
.resize-top-right {
  top: 40px;
  right: 0;
  width: 20px;
  height: 20px;
  cursor: nesw-resize;
}

/* 左下角 */
.resize-bottom-left {
  bottom: 0;
  left: 0;
  width: 20px;
  height: 20px;
  cursor: nesw-resize;
}

/* 右下角 */
.resize-bottom-right {
  bottom: 0;
  right: 0;
  width: 20px;
  height: 20px;
  cursor: nwse-resize;
}
</style>
