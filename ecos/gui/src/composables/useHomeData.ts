import { ref, watch, onUnmounted } from 'vue'
import { readTextFile, readFile, watch as fsWatch, exists } from '@tauri-apps/plugin-fs'
import { dirname } from '@tauri-apps/api/path'
import { useWorkspace } from './useWorkspace'
import { useTauri } from './useTauri'
import { flowExecutionActive } from './useFlowRunner'
import { getHomePageApi } from '@/api/flow'
import { ResponseEnum } from '@/api/type'
import type { ECCResponse } from '@/api/sse'
import { requestProjectPathAccess, resolveProjectPathAccess } from '@/utils/projectFs'

// ============ 类型定义 ============

/** home.json 数据结构 */
export interface HomeData {
  flow: string
  layout: string
  parameters: string
  'GDS merge': string
  checklist: string
  metrics: Record<string, any>
  monitor: MonitorData
}

/** monitor 数据结构（step 为固定字段，其余为动态指标） */
export interface MonitorData {
  step: string[]
  [key: string]: (string | number)[]
}

/** checklist.json 中的单个检查项 */
export interface ChecklistItem {
  step: string
  type: string
  item: string
  state: string
}

/** checklist.json 数据结构 */
export interface ChecklistData {
  path: string
  checklist: ChecklistItem[]
}

/** 指标分析图表项（从 metrics 加载） */
export interface AnalysisChartItem {
  label: string
  imageBlobUrl: string
}

/** Home 页聚合展示的单个 flow 步骤日志块 */
export interface FlowLogSegment {
  stepName: string
  tool: string
  state: string
  /** flow.json 中为 Incomplete / Invalid */
  failed: boolean
  /** 磁盘上不存在或无法读取 */
  missing: boolean
  content: string
  /** 当前 flow.json 中该步为 Ongoing，且处于 flowExecutionActive 会话中 */
  live?: boolean
}

// ============ 共享 HomeData 缓存（模块级单例） ============

/**
 * 将远程 NFS 路径转换为本地项目路径（纯函数版，不依赖 composable 上下文）
 * 例: /nfs/.../project_name/sub/path → {projectPath}/sub/path
 */
export function convertRemoteToLocalPath(remotePath: string, projectPath: string): string {
  if (!remotePath || !remotePath.includes('/nfs/')) return remotePath
  if (!projectPath) return remotePath

  const projectName = projectPath.split(/[/\\]/).filter(Boolean).pop()
  if (!projectName) return remotePath

  const idx = remotePath.indexOf(`/${projectName}/`)
  if (idx === -1) return remotePath

  const relativePath = remotePath.slice(idx + projectName.length + 2)
  return `${projectPath}/${relativePath}`
}

/** 从 flow.json 路径解析 workspace 根目录（…/home/flow.json → …） */
export function workspaceRootFromFlowPath(flowJsonPath: string): string {
  const n = flowJsonPath.replace(/\\/g, '/')
  const m = n.match(/^(.*)\/home\/flow\.json$/i)
  return m ? m[1] : ''
}

/** 共享的 home.json 解析结果 */
export const sharedHomeData = ref<HomeData | null>(null)

/** 防止并发重复请求的 Promise */
let _fetchPromise: Promise<HomeData | null> | null = null
/** 缓存对应的项目路径（路径变化时自动失效） */
let _cachedForProject = ''
/** 递增的失效标记：项目切换/清空后，旧请求必须放弃结果 */
let _fetchGeneration = 0

/**
 * 获取 home.json 数据（共享 + 去重）
 *
 * 多个 composable（useHomeData / useFlowStages / useParameters）
 * 同时调用时只发起 **一次** API 请求 + 一次文件读取。
 *
 * @param projectPath 当前项目路径
 * @param isInTauri   是否在 Tauri 环境
 * @returns 解析后的 HomeData，失败返回 null
 */
export async function fetchSharedHomeData(
  projectPath: string,
  isInTauri: boolean,
): Promise<HomeData | null> {
  // 项目切换时使缓存失效
  if (projectPath !== _cachedForProject) {
    sharedHomeData.value = null
    _fetchPromise = null
    _cachedForProject = projectPath
    _fetchGeneration += 1
  }

  // 已有缓存，直接返回
  if (sharedHomeData.value) return sharedHomeData.value

  // 已有进行中的请求，复用同一个 Promise
  if (_fetchPromise) return _fetchPromise

  _fetchPromise = (async (): Promise<HomeData | null> => {
    const generation = _fetchGeneration
    const isStale = () => generation !== _fetchGeneration || projectPath !== _cachedForProject

    try {
      if (!isInTauri || !projectPath) return null

      // 请求文件系统权限
      if (!(await requestProjectPathAccess(projectPath))) return null

      if (isStale()) return null

      // 调用 API 获取 home.json 路径
      const apiResponse = await getHomePageApi()
      if (apiResponse.response !== ResponseEnum.success || !apiResponse.data?.path) {
        console.warn('get_home_page API failed:', apiResponse.message)
        return null
      }
      if (isStale()) return null

      // 读取 home.json
      const localPath = convertRemoteToLocalPath(apiResponse.data.path, projectPath)
      const resolvedHomePath = await resolveProjectPathAccess(localPath)
      if (!resolvedHomePath) return null
      console.log('Loading home.json from:', resolvedHomePath)

      const content = await readTextFile(resolvedHomePath)
      const data: HomeData = JSON.parse(content)

      if (isStale()) return null
      sharedHomeData.value = data
      console.log('Shared home data loaded:', Object.keys(data))
      return data
    } catch (err) {
      console.error('Failed to fetch shared home data:', err)
      return null
    } finally {
      _fetchPromise = null
    }
  })()

  return _fetchPromise
}

/** 从 SSE 路径更新共享缓存 */
export function updateSharedHomeData(data: HomeData) {
  sharedHomeData.value = data
}

/** 清除共享缓存 */
export function invalidateSharedHomeData() {
  sharedHomeData.value = null
  _fetchPromise = null
}

export function resetSharedHomeDataProjectState() {
  sharedHomeData.value = null
  _fetchPromise = null
  _cachedForProject = ''
  _fetchGeneration += 1
}

// ============ Composable ============

/**
 * Home 页面数据管理 Hook
 * 负责从 home.json 加载监控数据、checklist、layout 图片
 */
export function useHomeData() {
  const { isInTauri } = useTauri()
  const { currentProject } = useWorkspace()

  // 响应式数据
  const monitorData = ref<MonitorData | null>(null)
  const checklistItems = ref<ChecklistItem[]>([])
  const layoutBlobUrl = ref<string>('')
  const analysisCharts = ref<AnalysisChartItem[]>([])
  const flowLogSegments = ref<FlowLogSegment[]>([])
  const flowLogStepName = ref('')
  const flowLogError = ref<string | null>(null)
  /** True while flow.json and step log files are being read (progressive fill). */
  const flowLogLoading = ref(false)
  const isLoading = ref(false)
  const error = ref<string | null>(null)

  /** flow log 渐进刷新会话：递增后旧异步回调全部失效 */
  let liveSession = 0
  let unwatchFlowJson: (() => void) | null = null
  let unwatchLog: (() => void) | null = null
  let pollFlowJsonTimer: ReturnType<typeof setInterval> | null = null
  let pollLogFallbackTimer: ReturnType<typeof setInterval> | null = null
  let waitPathTimer: ReturnType<typeof setInterval> | null = null
  let lastOngoingKey: string | null = null

  // 用于清理 blob URL
  let currentBlobUrl: string | null = null
  let metricsBlobUrls: string[] = []

  /**
   * 将远程路径转换为本地项目路径
   * 例如: /nfs/share/home/xxx/benchmark/project_name/sub/path
   * 转换为: {projectPath}/sub/path
   */
  function convertToLocalPath(remotePath: string): string {
    if (!remotePath || !remotePath.includes('/nfs/')) {
      return remotePath
    }

    const projectPath = currentProject.value?.path
    if (!projectPath) {
      console.warn('No current project path available')
      return remotePath
    }

    // 从项目路径中提取项目名称（最后一个目录名）
    const projectName = projectPath.split(/[/\\]/).filter(Boolean).pop()
    if (!projectName) {
      console.warn('Cannot extract project name from path:', projectPath)
      return remotePath
    }

    // 在远程路径中找到项目名称的位置
    const projectNameIndex = remotePath.indexOf(`/${projectName}/`)
    if (projectNameIndex === -1) {
      console.warn('Project name not found in remote path:', remotePath)
      return remotePath
    }

    // 截取项目名称之后的相对路径部分
    const relativePath = remotePath.slice(projectNameIndex + projectName.length + 2)

    // 拼接本地项目路径
    const localPath = `${projectPath}/${relativePath}`
    console.log('Path converted:', remotePath, '->', localPath)

    return localPath
  }

  /**
   * 清理之前的 blob URL
   */
  function cleanupBlobUrl(): void {
    if (currentBlobUrl) {
      URL.revokeObjectURL(currentBlobUrl)
      currentBlobUrl = null
      layoutBlobUrl.value = ''
    }
  }

  /**
   * 加载 layout PNG 图片并转为 blob URL
   */
  async function loadLayoutImage(layoutPath: string): Promise<void> {
    if (!layoutPath) {
      cleanupBlobUrl()
      return
    }

    try {
      const localPath = convertToLocalPath(layoutPath)
      const resolvedPath = await resolveProjectPathAccess(localPath)
      console.log('Loading layout image from:', resolvedPath ?? localPath)
      if (!resolvedPath) {
        cleanupBlobUrl()
        return
      }

      const fileData = await readFile(resolvedPath)
      const blob = new Blob([fileData], { type: 'image/png' })
      const blobUrl = URL.createObjectURL(blob)

      // 清理旧的 blob URL
      cleanupBlobUrl()

      currentBlobUrl = blobUrl
      layoutBlobUrl.value = blobUrl
      console.log('Layout blob URL created:', blobUrl)
    } catch (err) {
      console.error('Failed to load layout image:', err)
      cleanupBlobUrl()
    }
  }

  /**
   * 清理 metrics 图表的 blob URLs
   */
  function cleanupMetricsBlobUrls(): void {
    for (const url of metricsBlobUrls) {
      URL.revokeObjectURL(url)
    }
    metricsBlobUrls = []
    analysisCharts.value = []
  }

  /**
   * 加载 metrics 指标图片
   * metrics 格式: { "label": "/path/to/image.png", ... }
   */
  async function loadMetricsImages(metrics: Record<string, any>): Promise<void> {
    if (!metrics || typeof metrics !== 'object') {
      cleanupMetricsBlobUrls()
      return
    }

    const entries = Object.entries(metrics).filter(([_, v]) => v && typeof v === 'string')
    if (entries.length === 0) {
      cleanupMetricsBlobUrls()
      return
    }

    // 清理旧的 blob URLs
    cleanupMetricsBlobUrls()

    const charts: AnalysisChartItem[] = []
    const newBlobUrls: string[] = []

    // 并行加载所有图片
    const results = await Promise.allSettled(
      entries.map(async ([label, imagePath]) => {
        try {
          const localPath = convertToLocalPath(imagePath as string)
          const resolvedPath = await resolveProjectPathAccess(localPath)
          if (!resolvedPath) {
            return { label, blobUrl: '' }
          }
          const fileData = await readFile(resolvedPath)
          const blob = new Blob([fileData], { type: 'image/png' })
          const blobUrl = URL.createObjectURL(blob)
          return { label, blobUrl }
        } catch (err) {
          console.warn(`Failed to load metric image for "${label}":`, err)
          return { label, blobUrl: '' }
        }
      })
    )

    for (const result of results) {
      if (result.status === 'fulfilled') {
        const { label, blobUrl } = result.value
        charts.push({ label, imageBlobUrl: blobUrl })
        if (blobUrl) {
          newBlobUrls.push(blobUrl)
        }
      }
    }

    metricsBlobUrls = newBlobUrls
    analysisCharts.value = charts
    console.log('Metrics images loaded:', charts.length)
  }

  /**
   * 加载 checklist 数据
   */
  async function loadChecklist(checklistPath: string): Promise<void> {
    if (!checklistPath) {
      checklistItems.value = []
      return
    }

    try {
      const localPath = convertToLocalPath(checklistPath)
      const resolvedPath = await resolveProjectPathAccess(localPath)
      console.log('Loading checklist from:', resolvedPath ?? localPath)
      if (!resolvedPath) {
        checklistItems.value = []
        return
      }

      const fileContent = await readTextFile(resolvedPath)
      const data: ChecklistData = JSON.parse(fileContent)

      checklistItems.value = data.checklist || []
      console.log('Checklist loaded:', checklistItems.value)
    } catch (err) {
      console.error('Failed to load checklist:', err)
      checklistItems.value = []
    }
  }

  function stepLogAbsPath(rootNorm: string, name: string, tool: string): string {
    return `${rootNorm}/${name}_${tool}/log/${name}.log`
  }

  /**
   * 从已解析的 flow.json 本地路径构建步骤日志列表。
   * @param includeOngoingLive 为 true 时包含 Ongoing 步，并标记 live（用于 run 期间渐进展示）
   */
  async function buildFlowLogSegmentsFromFlowLocal(
    flowLocal: string,
    includeOngoingLive: boolean,
  ): Promise<FlowLogSegment[]> {
    const workspaceRoot = workspaceRootFromFlowPath(flowLocal)
    if (!workspaceRoot) return []
    const resolvedFlowPath = await resolveProjectPathAccess(flowLocal)
    const resolvedWorkspaceRoot = await resolveProjectPathAccess(workspaceRoot)
    if (!resolvedFlowPath || !resolvedWorkspaceRoot) return []

    const fileContent = await readTextFile(resolvedFlowPath)
    const flowData = JSON.parse(fileContent) as { steps?: Array<{ name: string; tool: string; state: string }> }
    const steps = flowData.steps ?? []
    const root = resolvedWorkspaceRoot.replace(/\\/g, '/')
    const out: FlowLogSegment[] = []

    const yieldToUi = () =>
      new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve())
      })

    for (const step of steps) {
      const stateNorm = (step.state ?? '').trim()
      const stateLc = stateNorm.toLowerCase()
      if (stateLc === 'unstart') continue

      if (stateLc === 'ongoing') {
        if (!includeOngoingLive) continue
        const logPath = stepLogAbsPath(root, step.name, step.tool)
        let content = ''
        let missing = false
        try {
          content = await readTextFile(logPath)
        } catch {
          missing = true
          content = `(Log file not yet available or unreadable; waiting…)\n${logPath}`
        }
        out.push({
          stepName: step.name,
          tool: step.tool,
          state: step.state,
          failed: false,
          missing,
          content,
          live: true,
        })
        await yieldToUi()
        continue
      }

      const failed = step.state === 'Incomplete' || step.state === 'Invalid'
      const logPath = stepLogAbsPath(root, step.name, step.tool)
      let content = ''
      let missing = false
      try {
        content = await readTextFile(logPath)
      } catch {
        missing = true
        content = `(Log file not found or unreadable)\n${logPath}`
      }
      out.push({
        stepName: step.name,
        tool: step.tool,
        state: step.state,
        failed,
        missing,
        content,
      })
      await yieldToUi()
    }
    return out
  }

  function cleanupLogWatchOnly(): void {
    unwatchLog?.()
    unwatchLog = null
    if (pollLogFallbackTimer != null) {
      clearInterval(pollLogFallbackTimer)
      pollLogFallbackTimer = null
    }
    if (waitPathTimer != null) {
      clearInterval(waitPathTimer)
      waitPathTimer = null
    }
  }

  function cleanupFlowLogLiveWatch(): void {
    unwatchFlowJson?.()
    unwatchFlowJson = null
    cleanupLogWatchOnly()
    if (pollFlowJsonTimer != null) {
      clearInterval(pollFlowJsonTimer)
      pollFlowJsonTimer = null
    }
    lastOngoingKey = null
  }

  async function bindLogFileWatch(sid: number, logPath: string): Promise<void> {
    cleanupLogWatchOnly()

    const patchLive = async (): Promise<void> => {
      if (sid !== liveSession) return
      try {
        const resolvedLogPath = await resolveProjectPathAccess(logPath)
        if (!resolvedLogPath) return
        const t = await readTextFile(resolvedLogPath)
        const i = flowLogSegments.value.findIndex((s) => s.live)
        if (i >= 0) {
          const cur = flowLogSegments.value[i]!
          if (cur.content === t) return
          flowLogSegments.value[i] = { ...cur, content: t, missing: false }
        }
      } catch {
        /* 尚未写入或短暂不可读 */
      }
    }

    function startBackupPoll(): void {
      if (pollLogFallbackTimer != null) {
        clearInterval(pollLogFallbackTimer)
      }
      pollLogFallbackTimer = setInterval(() => {
        void patchLive()
      }, 1200)
    }

    const tryAttachWatch = async (): Promise<boolean> => {
      if (sid !== liveSession) return true
      try {
        if (await exists(logPath)) {
          const resolvedLogPath = await resolveProjectPathAccess(logPath)
          if (!resolvedLogPath) return false
          try {
            unwatchLog = await fsWatch(resolvedLogPath, () => {
              void patchLive()
            }, { delayMs: 100 })
          } catch (we) {
            console.warn('watch step log file failed, fallback poll:', we)
          }
          await patchLive()
          startBackupPoll()
          return true
        }
        const logDir = await dirname(logPath)
        if (await exists(logDir)) {
          const resolvedLogDir = await resolveProjectPathAccess(logDir)
          if (!resolvedLogDir) return false
          try {
            unwatchLog = await fsWatch(resolvedLogDir, () => {
              void patchLive()
            }, { delayMs: 120 })
          } catch (we) {
            console.warn('watch log directory failed, fallback poll:', we)
          }
          await patchLive()
          startBackupPoll()
          return true
        }
      } catch (e) {
        console.warn('bindLogFileWatch path check:', e)
      }
      return false
    }

    if (await tryAttachWatch()) {
      return
    }

    pollLogFallbackTimer = setInterval(() => {
      void patchLive()
    }, 650)

    waitPathTimer = setInterval(() => {
      void (async () => {
        if (sid !== liveSession) return
        if (await tryAttachWatch()) {
          if (waitPathTimer != null) {
            clearInterval(waitPathTimer)
            waitPathTimer = null
          }
        }
      })()
    }, 500)
  }

  async function refreshFlowLogLivePanel(sid: number): Promise<void> {
    if (sid !== liveSession) return
    let flowRemote = sharedHomeData.value?.flow
    if (!flowRemote && currentProject.value?.path) {
      const h = await fetchSharedHomeData(currentProject.value.path, isInTauri)
      flowRemote = h?.flow ?? ''
    }
    if (!flowRemote) return

    const flowLocal = convertToLocalPath(flowRemote)
    if (!workspaceRootFromFlowPath(flowLocal)) return

    flowLogError.value = null
    try {
      const segments = await buildFlowLogSegmentsFromFlowLocal(flowLocal, true)
      if (sid !== liveSession) return
      flowLogSegments.value = segments
      const ongoing = segments.find((s) => s.live)
      flowLogStepName.value = ongoing?.stepName ?? ''
      const key = ongoing ? `${ongoing.stepName}|${ongoing.tool}` : null
      if (key !== lastOngoingKey) {
        lastOngoingKey = key
        cleanupLogWatchOnly()
        if (ongoing) {
          const root = workspaceRootFromFlowPath(flowLocal)!.replace(/\\/g, '/')
          const lp = stepLogAbsPath(root, ongoing.stepName, ongoing.tool)
          await bindLogFileWatch(sid, lp)
        }
      }
    } catch (err) {
      console.error('refreshFlowLogLivePanel:', err)
    }
  }

  async function loadAllFlowStepLogsFromFlowPath(flowPathRemote: string): Promise<void> {
    if (!isInTauri || !flowPathRemote) {
      flowLogSegments.value = []
      flowLogLoading.value = false
      return
    }

    flowLogError.value = null
    flowLogLoading.value = true

    try {
      const flowLocal = convertToLocalPath(flowPathRemote)
      const resolvedFlowPath = await resolveProjectPathAccess(flowLocal)
      if (!resolvedFlowPath) {
        flowLogSegments.value = []
        return
      }
      if (!workspaceRootFromFlowPath(resolvedFlowPath)) {
        flowLogError.value = 'Cannot resolve workspace root from flow.json path'
        flowLogSegments.value = []
        return
      }

      flowLogSegments.value = await buildFlowLogSegmentsFromFlowLocal(resolvedFlowPath, false)
      console.log('Flow step logs loaded:', flowLogSegments.value.length, 'segments')
    } catch (err) {
      console.error('Failed to load flow step logs:', err)
      flowLogSegments.value = []
      flowLogError.value = err instanceof Error ? err.message : String(err)
    } finally {
      flowLogLoading.value = false
    }
  }

  /**
   * 在已有 home 数据或共享缓存的前提下，按 flow.json 拉取全部步骤日志（含失败步骤，失败段标红）
   */
  async function ensureFlowLogsLoaded(): Promise<void> {
    let flowPath = sharedHomeData.value?.flow
    if (!flowPath && isInTauri && currentProject.value?.path) {
      const homeData = await fetchSharedHomeData(currentProject.value.path, isInTauri)
      flowPath = homeData?.flow ?? ''
    }
    if (flowPath) {
      await loadAllFlowStepLogsFromFlowPath(flowPath)
    }
  }

  /**
   * 从 home.json 加载所有 Home 页面数据
   * 使用共享缓存避免重复 API 调用
   */
  async function loadHomeData(): Promise<void> {
    if (!isInTauri || !currentProject.value?.path) {
      console.warn('Cannot load home.json: not in Tauri environment or no project is open')
      clearHomeData()
      return
    }

    isLoading.value = true
    error.value = null

    try {
      invalidateSharedHomeData()

      const homeData = await fetchSharedHomeData(currentProject.value.path, isInTauri)
      if (!homeData) {
        console.warn('Failed to get home data from shared cache')
        clearHomeData()
        return
      }

      console.log('Loaded home data:', homeData)

      // 加载 monitor 数据
      if (homeData.monitor) {
        monitorData.value = homeData.monitor
      }

      // 并行加载 checklist、layout、metrics 与各步骤日志
      await Promise.all([
        loadChecklist(homeData.checklist),
        loadLayoutImage(homeData.layout),
        loadMetricsImages(homeData.metrics),
        loadAllFlowStepLogsFromFlowPath(homeData.flow),
      ])

      console.log('Home data fully loaded')
    } catch (err) {
      console.error('Failed to load home data:', err)
      error.value = err instanceof Error ? err.message : String(err)
      clearHomeData()
    } finally {
      isLoading.value = false
    }
  }

  /**
   * 从指定的 home.json 路径加载 Home 页面数据
   * 用于 SSE 通知推送的 home_page 路径
   */
  async function loadHomeDataFromPath(homePath: string): Promise<void> {
    if (!isInTauri || !homePath) {
      console.warn('Cannot load home data: not in Tauri environment or path is empty')
      return
    }

    isLoading.value = true
    error.value = null

    try {
      // 转换远程路径为本地路径
      const localPath = convertToLocalPath(homePath)
      const resolvedHomePath = await resolveProjectPathAccess(localPath)
      console.log('Loading home data from SSE path:', resolvedHomePath ?? localPath)

      // 请求文件系统访问权限
      if (!resolvedHomePath) return

      const fileContent = await readTextFile(resolvedHomePath)
      const homeData: HomeData = JSON.parse(fileContent)

      // 更新共享缓存，让其他 composable 也能获取最新数据
      updateSharedHomeData(homeData)

      console.log('Loaded home data from SSE path:', homeData)

      // 更新 monitor 数据
      if (homeData.monitor) {
        monitorData.value = homeData.monitor
      }

      // 并行加载 checklist、layout、metrics 与各步骤日志
      await Promise.all([
        loadChecklist(homeData.checklist),
        loadLayoutImage(homeData.layout),
        loadMetricsImages(homeData.metrics),
        loadAllFlowStepLogsFromFlowPath(homeData.flow),
      ])

      console.log('Home data from SSE path fully loaded')
    } catch (err) {
      console.error('Failed to load home data from path:', homePath, err)
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      isLoading.value = false
    }
  }

  /**
   * 重新加载所有数据
   */
  async function refreshHomeData(): Promise<void> {
    await loadHomeData()
  }

  /**
   * 清空所有数据
   */
  function clearHomeData(resetProjectState = false): void {
    liveSession++
    cleanupFlowLogLiveWatch()
    monitorData.value = null
    checklistItems.value = []
    flowLogSegments.value = []
    flowLogStepName.value = ''
    flowLogError.value = null
    flowLogLoading.value = false
    cleanupBlobUrl()
    cleanupMetricsBlobUrls()
    error.value = null
    if (resetProjectState) {
      resetSharedHomeDataProjectState()
    } else {
      invalidateSharedHomeData()
    }
  }

  // run_step / rtl2gds 期间：监听 flow.json 与当前步日志文件，渐进更新 Flow step log
  watch(
    flowExecutionActive,
    async (active) => {
      if (!isInTauri) return
      if (!active) {
        liveSession++
        cleanupFlowLogLiveWatch()
        try {
          await ensureFlowLogsLoaded()
        } catch (e) {
          console.error('ensureFlowLogsLoaded after flow:', e)
        }
        return
      }

      if (!currentProject.value?.path) return

      liveSession++
      const sid = liveSession
      cleanupFlowLogLiveWatch()

      let flowRemote = sharedHomeData.value?.flow
      if (!flowRemote) {
        const h = await fetchSharedHomeData(currentProject.value.path, isInTauri)
        flowRemote = h?.flow ?? ''
      }
      if (!flowRemote) {
        console.warn('flow-log live: no flow path in home data')
        return
      }

      const flowLocal = convertToLocalPath(flowRemote)
      const resolvedFlowPath = await resolveProjectPathAccess(flowLocal)
      if (!resolvedFlowPath) return

      try {
        unwatchFlowJson = await fsWatch(resolvedFlowPath, () => {
          void refreshFlowLogLivePanel(sid)
        }, { delayMs: 340 })
      } catch (e) {
        console.warn('watch flow.json failed (using interval only):', e)
      }

      pollFlowJsonTimer = setInterval(() => {
        void refreshFlowLogLivePanel(sid)
      }, 1600)

      await refreshFlowLogLivePanel(sid)
    },
    { immediate: true },
  )

  // 监听当前项目变化，自动重新加载
  watch(
    () => currentProject.value?.path,
    async (newPath) => {
      if (newPath) {
        await loadHomeData()
      } else {
        clearHomeData(true)
      }
    },
    { immediate: true }
  )

  // 监听 SSE 通知，当收到包含 home_page 的通知时自动刷新 Home 数据
  const { sseMessages } = useWorkspace()

  watch(
    () => sseMessages.value.length,
    async (newLen, oldLen) => {
      if (newLen <= (oldLen ?? 0)) return

      // 获取最新一条 SSE 消息
      const latest: ECCResponse = sseMessages.value[newLen - 1]
      if (!latest) return

      // 判断是否为 notify 类型的消息
      if (latest.cmd !== 'notify') return

      const info = latest.data?.info as Record<string, unknown> | undefined
      const homePage = info?.home_page as string | undefined
      const logFile = info?.log_file as string | undefined
      const stepName = latest.data?.step as string | undefined

      if (!homePage && !logFile) return

      if (homePage) {
        console.log('Received SSE notification containing home_page path:', homePage)
      }
      if (logFile) {
        console.log('Received SSE notification containing log_file path:', logFile)
      }

      if (homePage) {
        await loadHomeDataFromPath(homePage)
      }
      if (stepName !== undefined && logFile) {
        flowLogStepName.value = stepName
      }
      // 仅 log_file、无 home_page 时仍按 flow.json 聚合刷新（例如某步刚跑完）
      if (logFile && !homePage) {
        await ensureFlowLogsLoaded()
      }
    }
  )

  // 组件卸载时清理 blob URL 并失效共享缓存
  // 确保下次 mount 时从磁盘重新读取最新 home.json
  onUnmounted(() => {
    liveSession++
    cleanupFlowLogLiveWatch()
    cleanupBlobUrl()
    cleanupMetricsBlobUrls()
    invalidateSharedHomeData()
  })

  return {
    // 状态
    monitorData,
    checklistItems,
    layoutBlobUrl,
    analysisCharts,
    flowLogSegments,
    flowLogStepName,
    flowLogError,
    flowLogLoading,
    isLoading,
    error,

    // 方法
    loadHomeData,
    loadHomeDataFromPath,
    refreshHomeData,
    clearHomeData,
    convertToLocalPath,
    loadAllFlowStepLogsFromFlowPath,
    ensureFlowLogsLoaded,
  }
}
