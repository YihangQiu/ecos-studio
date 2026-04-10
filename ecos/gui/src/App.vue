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

const startResize = async (direction: ResizeDirection) => {
  isResizing = true
  document.body.classList.add('window-resizing')
  const window = getCurrentWindow()
  await window.startResizeDragging(direction)
}

// 阻止拖拽调整窗口大小时的文本选择
const handleSelectStart = (e: Event) => {
  if (isResizing) {
    e.preventDefault()
    return false
  }
}

const handleMouseUp = () => {
  if (isResizing) {
    isResizing = false
    document.body.classList.remove('window-resizing')
  }
}

onMounted(async () => {
  themeStore.initTheme()
  // 在应用启动时加载最近项目和已导入的 PDK
  await Promise.all([loadRecentProjects(), loadPdks()])

  // 添加事件监听
  document.addEventListener('selectstart', handleSelectStart)
  document.addEventListener('mouseup', handleMouseUp)
})

onUnmounted(() => {
  document.removeEventListener('selectstart', handleSelectStart)
  document.removeEventListener('mouseup', handleMouseUp)
  document.body.classList.remove('window-resizing')
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

/* 上边缘 */
.resize-top {
  top: 0;
  left: 20px;
  right: 20px;
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

/* 右边缘 */
.resize-right {
  right: 0;
  top: 20px;
  bottom: 20px;
  width: 6px;
  cursor: ew-resize;
}

/* 左上角 */
.resize-top-left {
  top: 0;
  left: 0;
  width: 20px;
  height: 20px;
  cursor: nwse-resize;
}

/* 右上角 */
.resize-top-right {
  top: 0;
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
