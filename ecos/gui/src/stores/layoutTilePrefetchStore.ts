import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { CMDEnum, InfoEnum, ResponseEnum, StepEnum } from '@/api/type'
import { getInfoApi } from '@/api/flow'
import { isTauri } from '@/composables/useTauri'
import { loadFlowRunStepKeysFromProject } from '@/composables/useFlowStages'
import { pickLayoutJsonPath } from '@/composables/useLayoutTileGen'
import {
  runLayoutTileGenerationSingleFlight,
  type LayoutTileGenResult,
} from '@/composables/layoutTilePipeline'
import { requestIdle } from '@/composables/requestIdle'

const STORAGE_KEY = 'ecos.layoutTilePrefetch.enabled'

export type StepPrefetchState = 'idle' | 'prefetching' | 'ready' | 'error'

function canPrefetchRuntime(): boolean {
  return isTauri() && import.meta.env.DEV
}

function findStepEnumForPath(pathSegment: string): StepEnum | undefined {
  return Object.values(StepEnum).find((s) => s.toLowerCase() === pathSegment.toLowerCase())
}

export const useLayoutTilePrefetchStore = defineStore('layoutTilePrefetch', () => {
  /** 默认开启；仅 `localStorage === '0'` 时关闭（无 UI，可控制台手动写） */
  const enabled = ref(true)
  const paused = ref(false)
  const projectPath = ref<string | null>(null)
  const stepStates = ref<Record<string, StepPrefetchState>>({})
  const cachedTiles = ref<Record<string, LayoutTileGenResult>>({})
  const pendingQueue = ref<Array<{ stepKey: string; layoutJsonRelative: string }>>([])

  let loopRunning = false

  const currentPrefetchingStepKey = computed(() => {
    const e = stepStates.value
    return Object.keys(e).find((k) => e[k] === 'prefetching') ?? null
  })

  /** Tauri + DEV 下自动在空闲时间预热，无 UI 开关 */
  const prefetchSupported = computed(() => canPrefetchRuntime() && enabled.value)

  try {
    if (localStorage.getItem(STORAGE_KEY) === '0') enabled.value = false
  } catch {
    /* ignore */
  }

  function setEnabled(v: boolean) {
    enabled.value = v
    try {
      localStorage.setItem(STORAGE_KEY, v ? '1' : '0')
    } catch {
      /* ignore */
    }
    if (v && projectPath.value && canPrefetchRuntime()) {
      void enqueueAllFlowSteps(projectPath.value)
    }
  }

  function setProject(path: string | null) {
    if (projectPath.value === path) return
    projectPath.value = path
    pendingQueue.value = []
    stepStates.value = {}
    cachedTiles.value = {}
    paused.value = false
    if (path && enabled.value && canPrefetchRuntime()) {
      void enqueueAllFlowSteps(path)
    }
  }

  function clearDeferredPrefetchQueue() {
    pendingQueue.value = []
  }

  function pause() {
    paused.value = true
  }

  function resume() {
    paused.value = false
    void runQueueLoop()
  }

  function enqueuePrefetch(steps: Array<{ stepKey: string; layoutJsonRelative: string }>) {
    if (!enabled.value || !canPrefetchRuntime() || !projectPath.value) return
    for (const s of steps) {
      const k = s.stepKey
      const state = stepStates.value[k]
      if (state === 'ready' || state === 'prefetching') continue
      const dup = pendingQueue.value.some(
        (q) => q.stepKey === k && q.layoutJsonRelative === s.layoutJsonRelative,
      )
      if (dup) continue
      pendingQueue.value = [...pendingQueue.value, { ...s }]
    }
    void runQueueLoop()
  }

  async function enqueueAllFlowSteps(path: string) {
    if (!enabled.value || !canPrefetchRuntime()) return
    const stepKeys = await loadFlowRunStepKeysFromProject(path)
    for (const stepKey of stepKeys) {
      await requestIdle()
      if (!enabled.value || paused.value || projectPath.value !== path) return
      const stepEnum = findStepEnumForPath(stepKey)
      if (!stepEnum) continue
      try {
        const layoutResponse = await getInfoApi({
          cmd: CMDEnum.get_info,
          data: { step: stepEnum, id: InfoEnum.layout },
        })
        if (layoutResponse.response !== ResponseEnum.success || !layoutResponse.data?.info) continue
        const rel = pickLayoutJsonPath(layoutResponse.data.info)
        if (!rel) continue
        enqueuePrefetch([{ stepKey, layoutJsonRelative: rel }])
      } catch {
        /* 单步失败跳过 */
      }
    }
  }

  async function runQueueLoop() {
    if (loopRunning) return
    const root = projectPath.value
    if (!enabled.value || !canPrefetchRuntime() || paused.value || !root) return
    loopRunning = true
    try {
      while (pendingQueue.value.length > 0 && !paused.value) {
        const job = pendingQueue.value[0]
        if (!job) break
        await requestIdle()
        if (paused.value || projectPath.value !== root) break
        pendingQueue.value = pendingQueue.value.slice(1)
        const { stepKey, layoutJsonRelative } = job
        stepStates.value = { ...stepStates.value, [stepKey]: 'prefetching' }
        try {
          const result = await runLayoutTileGenerationSingleFlight({
            projectPath: root,
            layoutJsonRelative,
            stepKey,
            source: 'prefetch',
          })
          cachedTiles.value = { ...cachedTiles.value, [stepKey]: result }
          stepStates.value = { ...stepStates.value, [stepKey]: 'ready' }
        } catch (e) {
          console.warn('[layoutTilePrefetch]', stepKey, e)
          stepStates.value = { ...stepStates.value, [stepKey]: 'error' }
        }
      }
    } finally {
      loopRunning = false
    }
  }

  return {
    enabled,
    setEnabled,
    prefetchSupported,
    paused,
    projectPath,
    stepStates,
    currentPrefetchingStepKey,
    cachedTiles,
    pendingQueue,
    setProject,
    enqueuePrefetch,
    enqueueAllFlowSteps,
    clearDeferredPrefetchQueue,
    pause,
    resume,
  }
})
