import { ref, watch, onUnmounted } from 'vue'
import type { Ref } from 'vue'
import {
  readTextFile,
  readFile,
  watch as fsWatch,
  exists,
} from '@tauri-apps/plugin-fs'
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
  /** 当前 `content` 仅为文件尾部截取；UI 可据此显示"查看完整日志"按钮 */
  truncated?: boolean
  /** 完整日志字符数（未截断时 = content.length） */
  totalSize?: number
  /** 生成该段时对应的 log 文件绝对路径（用于展开完整内容） */
  logPath?: string
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

// ============ Flow log 模块级持久化 ============
//
// HomeView 不在 KeepAlive 里：每次路由切走再回来都会完整重新挂载。
// 原实现每次挂载都会：
//   1) 调用 `invalidateSharedHomeData()` 重拉 home.json
//   2) 串行 `readTextFile` 读 N 个 step log（N 次 IPC + N 次权限解析 IPC）
//   3) 清空 flowLogSegments 再重新填充 → UI 闪烁
//
// 这里把 flow log 相关的响应式状态和文件读取缓存都提到模块级：
// - 同一项目内的路由切换：直接复用现有 segments，无闪烁、无 IPC
// - 后台以 `stat().mtime+size` 重新验证，只有真正变化的文件才重读
// - 新读取走并发 + 超过阈值只读尾部，避免大日志阻塞主线程
//
// 只有 HomeView 消费这些状态，模块级 ref 不会被其他组件意外读到。

/** 跨挂载持久化的 flow step log 列表 */
const flowLogSegmentsState = ref<FlowLogSegment[]>([])
const flowLogStepNameState = ref('')
const flowLogErrorState = ref<string | null>(null)
/** 首次构建（segments 为空）时才会显示 loading；后续重新校验不再阻塞 UI */
const flowLogLoadingState = ref(false)
/** 递增的 load 会话号：新一次 loadAllFlowStepLogsFromFlowPath 发起后旧回调自动放弃 */
let flowLogLoadSession = 0

function resetFlowLogState(): void {
  flowLogSegmentsState.value = []
  flowLogStepNameState.value = ''
  flowLogErrorState.value = null
  flowLogLoadingState.value = false
  // 下发新的会话号，让进行中的 hydrate 早返回
  flowLogLoadSession++
}

/**
 * 单个 log 文件内容缓存。
 *
 * **Key 约定**：必须是 `resolveProjectPathAccess` 之后的**规范化绝对路径**。
 * 所有读取/失效入口都必须先 `resolvedPathMemo(localPath)` 再触达本 Map，
 * 否则 SSE 失效时会拿未 canonicalize 的路径去 delete，导致旧内容滞留。
 *
 * **上限**：采用简单 LRU（`Map` 迭代顺序 = 插入顺序）。同一项目内跑多次
 * flow、生成大量历史 step log 时，超限自动 evict 最早一条，避免无限增长。
 */
interface LogFileCacheEntry {
  content: string
  truncated: boolean
  /** 完整内容的字符数（未截断时 = content.length） */
  totalSize: number
}
const logFileCache = new Map<string, LogFileCacheEntry>()
/** 至多保留多少条日志缓存；命中时 refresh 插入顺序充当 LRU */
const LOG_CACHE_MAX_ENTRIES = 64

function logCacheGet(key: string): LogFileCacheEntry | undefined {
  const hit = logFileCache.get(key)
  if (!hit) return undefined
  // LRU: 访问一下就重排到末尾
  logFileCache.delete(key)
  logFileCache.set(key, hit)
  return hit
}

function logCacheSet(key: string, entry: LogFileCacheEntry): void {
  if (logFileCache.has(key)) logFileCache.delete(key)
  logFileCache.set(key, entry)
  while (logFileCache.size > LOG_CACHE_MAX_ENTRIES) {
    const oldest = logFileCache.keys().next().value
    if (oldest === undefined) break
    logFileCache.delete(oldest)
  }
}

/** resolveProjectPathAccess 结果缓存，key = local path, value = resolved/canonical path */
const resolvedPathCache = new Map<string, string>()

/** 跑 IO 时的并发上限（Tauri IPC 不适合全量并发） */
const LOG_READ_CONCURRENCY = 6
/**
 * 单个 log 文件默认展示上限（**JS 字符串 `.length`**，即 UTF-16 code unit 数；
 * ASCII 下与字节数等价，多字节字符下会偏小——这是有意的，我们要的是 UI
 * 展示规模上限，不是磁盘字节上限）。
 *
 * 超过时切尾部 + 置 `truncated: true`，UI 上的"查看完整日志"按钮会以
 * `forceFull: true` 再读一次，拿到整个文件。
 *
 * **TODO**: 真·流式尾部读还没做。`readLogFileSmart` 当前仍把**整个文件**
 * 读进 JS 字符串再切片（Tauri `plugin-fs` 的 `readTextFile` 没有 offset/length
 * 参数，且 `tauri.conf.json` 的 capabilities 只开了 `fs:allow-read-text-file`）。
 * 100MB 的日志会照样走一遍 IPC。后续要做真 tail read，需要：
 *   1) capabilities 加 `fs:allow-open` + `fs:allow-seek`；或
 *   2) Rust 侧加一个 `read_log_tail(path, max_chars)` 自定义命令，直接 `seek(End)` 读尾部。
 */
const MAX_LOG_READ_CHARS = 512 * 1024

async function resolvedPathMemo(localPath: string): Promise<string | null> {
  if (!localPath) return null
  const hit = resolvedPathCache.get(localPath)
  if (hit) return hit
  const resolved = await resolveProjectPathAccess(localPath)
  if (resolved) resolvedPathCache.set(localPath, resolved)
  return resolved
}

interface LogReadResult {
  content: string
  truncated: boolean
  missing: boolean
  totalSize: number
}

/**
 * 读取单个 step log 文件内容。
 *
 * 设计要点：
 * - 直接用 `readTextFile`。当前 tauri.conf 的 capabilities 里只有
 *   `fs:allow-read-text-file`，没有 `fs:allow-stat` / `fs:allow-open`，
 *   所以依赖 stat/open 的版本会被 plugin-fs 判为权限错误。
 * - 不再对每个 logPath 单独 `request_project_permission`：
 *     1) `register_project_root` 已经用 `allow_directory(path, true)` 递归授权整个工程；
 *     2) Rust 侧的 `request_project_permission` 会 `canonicalize(path)`，当 log
 *        还没落盘时 canonicalize 失败 → 前端误报 "log file not found or unreadable"。
 * - 大文件（> MAX_LOG_READ_CHARS）就地切尾部 + `truncated: true`，并把完整
 *   字符数返回给 UI；"查看完整日志"按钮会以 `forceFull` 再读一次。
 *   （尾部读的**读盘侧**当前未优化，见 `MAX_LOG_READ_CHARS` 的 TODO。）
 *
 * @param logPath **已 resolve / canonicalize 的绝对路径**。作为 `logFileCache` 的 key，
 *                调用方必须先走 `resolvedPathMemo` 保证同一文件始终用同一 key。
 */
async function readLogFileSmart(
  logPath: string,
  opts: { forceFull?: boolean; skipCache?: boolean } = {},
): Promise<LogReadResult> {
  if (!opts.skipCache) {
    const cached = logCacheGet(logPath)
    if (cached) {
      // forceFull 要完整内容；若缓存还是截断版则绕过缓存重新读
      if (!opts.forceFull || !cached.truncated) {
        return {
          content: cached.content,
          truncated: cached.truncated,
          missing: false,
          totalSize: cached.totalSize,
        }
      }
    }
  }

  try {
    const fullContent = await readTextFile(logPath)
    const totalSize = fullContent.length
    const shouldTruncate = !opts.forceFull && totalSize > MAX_LOG_READ_CHARS

    if (shouldTruncate) {
      const slice = fullContent.slice(-MAX_LOG_READ_CHARS)
      // 从行首开始切，避免首行半行乱码
      const firstNl = slice.indexOf('\n')
      const tail = firstNl >= 0 ? slice.slice(firstNl + 1) : slice
      const shownKb = Math.floor(MAX_LOG_READ_CHARS / 1024)
      const totalKb = Math.floor(totalSize / 1024)
      const content = `[… truncated — showing last ~${shownKb} KB of ${totalKb} KB. Click "Show full log" above to load everything. …]\n${tail}`
      logCacheSet(logPath, { content, truncated: true, totalSize })
      return { content, truncated: true, missing: false, totalSize }
    }

    logCacheSet(logPath, { content: fullContent, truncated: false, totalSize })
    return { content: fullContent, truncated: false, missing: false, totalSize }
  } catch {
    logFileCache.delete(logPath)
    return { content: '', truncated: false, missing: true, totalSize: 0 }
  }
}

/**
 * 使某个 log 文件的缓存失效。
 *
 * **前置条件**：`logPath` 必须与读取时的 cache key 一致——即也经过 `resolvedPathMemo`。
 * 传 undefined / 空串时清全部。调用方参考 SSE notify 分支的处理方式。
 */
function invalidateLogFileCache(logPath?: string): void {
  if (!logPath) {
    logFileCache.clear()
    return
  }
  logFileCache.delete(logPath)
}

/** 从缓存里删掉"不再出现在当前 plan 里"的 entry，避免单项目内长期积累 */
function pruneLogCacheKeepOnly(aliveKeys: Iterable<string>): void {
  const alive = aliveKeys instanceof Set ? aliveKeys : new Set(aliveKeys)
  for (const key of logFileCache.keys()) {
    if (!alive.has(key)) logFileCache.delete(key)
  }
}

// ============ Home 资源（monitor / checklist / layout / metrics）模块级持久化 ============
//
// HomeView 不在 KeepAlive：原实现每次 mount 都会
//   1) 重读 checklist.json
//   2) 重读 layout PNG → revoke 旧 blob → createObjectURL 新 blob
//   3) 并行重读 N 张 metrics PNG → revoke 一批旧 blob → createObjectURL 一批新 blob
// 即使文件一字节都没变。
//
// 做法：把这几个字段提到模块级，按「源路径签名」去重；
// 只有在 a) 项目切换 或 b) SSE 推送新 home.json 时才让签名失效。
// Blob URL 的 revoke 从"onUnmounted"推迟到"被新 blob 替换 / 项目切换"，
// 确保 remount 时 <img :src> 拿到的依旧是活的 URL。

const monitorDataState = ref<MonitorData | null>(null)
const checklistItemsState = ref<ChecklistItem[]>([])
const layoutBlobUrlState = ref<string>('')
const analysisChartsState = ref<AnalysisChartItem[]>([])

/** 记录当前持有的 blob URL，替换 / 失效时用来 revoke */
let _currentLayoutBlobUrl: string | null = null
let _currentMetricsBlobUrls: string[] = []

/** 上一次成功加载的源路径签名；命中时跳过整个 IO 流程 */
let _loadedChecklistPath = ''
let _loadedLayoutPath = ''
let _loadedMetricsSignature = ''

function invalidateLayoutCache(): void {
  if (_currentLayoutBlobUrl) {
    URL.revokeObjectURL(_currentLayoutBlobUrl)
    _currentLayoutBlobUrl = null
  }
  layoutBlobUrlState.value = ''
  _loadedLayoutPath = ''
}

function invalidateMetricsCache(): void {
  for (const url of _currentMetricsBlobUrls) {
    URL.revokeObjectURL(url)
  }
  _currentMetricsBlobUrls = []
  analysisChartsState.value = []
  _loadedMetricsSignature = ''
}

function invalidateChecklistCache(): void {
  checklistItemsState.value = []
  _loadedChecklistPath = ''
}

/** 项目切换 / 显式 reset 时一把梭 */
function invalidateHomeAssetCache(): void {
  invalidateLayoutCache()
  invalidateMetricsCache()
  invalidateChecklistCache()
  monitorDataState.value = null
}

/**
 * SSE 推送新 home.json 时调用：签名置空，让下一次 loader 被调用时真的重读磁盘；
 * 但 blob URL / UI 展示保持不变，等新数据到位再平滑替换，避免闪白。
 */
function markHomeAssetSignaturesStale(): void {
  _loadedChecklistPath = ''
  _loadedLayoutPath = ''
  _loadedMetricsSignature = ''
}

/**
 * 控制并发的简单 worker 池：把一堆 `() => Promise<void>` 任务限制在 `limit` 个同时进行。
 * 不抛错：单个任务失败由 runner 内部自行处理。
 */
async function runWithConcurrency<T>(
  items: T[],
  limit: number,
  handler: (item: T, index: number) => Promise<void>,
): Promise<void> {
  if (items.length === 0) return
  const total = items.length
  let cursor = 0
  const pool = Math.min(limit, total)
  const workers: Promise<void>[] = []
  const runOne = async (): Promise<void> => {
    for (;;) {
      const i = cursor++
      if (i >= total) return
      try {
        await handler(items[i]!, i)
      } catch (err) {
        console.warn('runWithConcurrency task failed:', err)
      }
    }
  }
  for (let i = 0; i < pool; i++) workers.push(runOne())
  await Promise.all(workers)
}

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
    // 项目路径不同：所有模块级缓存（log、路径解析、home 资源 blob / 签名）
    // 全部失效，否则新项目首屏会闪一下旧项目的 step log / layout / metrics。
    logFileCache.clear()
    resolvedPathCache.clear()
    resetFlowLogState()
    invalidateHomeAssetCache()
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
      const resolvedHomePath = await resolvedPathMemo(localPath)
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
  // SSE 代表 home.json 有新内容；把资源签名清空让 loader 下一次真重读。
  // 但 blob URL 暂时保留，新 blob 到位后再 revoke，避免 UI 闪白。
  markHomeAssetSignaturesStale()
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
  // 项目切换时，所有跨组件的模块级缓存一并失效
  logFileCache.clear()
  resolvedPathCache.clear()
  resetFlowLogState()
  invalidateHomeAssetCache()
}

// ============ Composable ============

/**
 * Home 页面数据管理 Hook
 * 负责从 home.json 加载监控数据、checklist、layout 图片
 */
export function useHomeData() {
  const { isInTauri } = useTauri()
  const { currentProject } = useWorkspace()

  // 响应式数据全部走模块级——HomeView remount 时直接复用上一次加载结果，
  // 只有源数据真的变了（项目切换 / SSE 推送 / 本地 flow 执行）才触发重读。
  const monitorData = monitorDataState
  const checklistItems = checklistItemsState
  const layoutBlobUrl = layoutBlobUrlState
  const analysisCharts = analysisChartsState
  const flowLogSegments = flowLogSegmentsState
  const flowLogStepName = flowLogStepNameState
  const flowLogError = flowLogErrorState
  /** True while flow.json and step log files are being read (progressive fill). */
  const flowLogLoading = flowLogLoadingState
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
   * 加载 layout PNG 图片并转为 blob URL
   *
   * 去重：与模块级 `_loadedLayoutPath` 一致且当前 blob 仍在，则直接返回。
   * SSE 触发时 `updateSharedHomeData` 会提前清签名，loader 被再次调用会真读磁盘。
   */
  async function loadLayoutImage(layoutPath: string): Promise<void> {
    if (!layoutPath) {
      invalidateLayoutCache()
      return
    }
    // 模块级短路：同路径 + blob 还活着 → 零 IPC 复用
    if (layoutPath === _loadedLayoutPath && layoutBlobUrlState.value) {
      return
    }

    try {
      const localPath = convertToLocalPath(layoutPath)
      const resolvedPath = await resolvedPathMemo(localPath)
      if (!resolvedPath) {
        invalidateLayoutCache()
        return
      }

      const fileData = await readFile(resolvedPath)
      const blob = new Blob([fileData], { type: 'image/png' })
      const nextBlobUrl = URL.createObjectURL(blob)

      // 新 blob 落位后，再 revoke 旧的——<img :src> 不会出现瞬断
      const prevBlobUrl = _currentLayoutBlobUrl
      _currentLayoutBlobUrl = nextBlobUrl
      layoutBlobUrlState.value = nextBlobUrl
      _loadedLayoutPath = layoutPath
      if (prevBlobUrl) URL.revokeObjectURL(prevBlobUrl)
      console.log('Layout blob URL created:', nextBlobUrl)
    } catch (err) {
      console.error('Failed to load layout image:', err)
      invalidateLayoutCache()
    }
  }

  /**
   * 加载 metrics 指标图片
   * metrics 格式: { "label": "/path/to/image.png", ... }
   *
   * 去重：label+path 组合签名一致 → 跳过（常见 mount 场景）。
   */
  async function loadMetricsImages(metrics: Record<string, any>): Promise<void> {
    if (!metrics || typeof metrics !== 'object') {
      invalidateMetricsCache()
      return
    }

    const entries = Object.entries(metrics).filter(([_, v]) => v && typeof v === 'string')
    if (entries.length === 0) {
      invalidateMetricsCache()
      return
    }

    const signature = entries
      .map(([label, p]) => `${label}=${p as string}`)
      .sort()
      .join('\u001f')
    if (signature === _loadedMetricsSignature && analysisChartsState.value.length > 0) {
      return
    }

    const charts: AnalysisChartItem[] = []
    const newBlobUrls: string[] = []

    const results = await Promise.allSettled(
      entries.map(async ([label, imagePath]) => {
        try {
          const localPath = convertToLocalPath(imagePath as string)
          const resolvedPath = await resolvedPathMemo(localPath)
          if (!resolvedPath) return { label, blobUrl: '' }
          const fileData = await readFile(resolvedPath)
          const blob = new Blob([fileData], { type: 'image/png' })
          const blobUrl = URL.createObjectURL(blob)
          return { label, blobUrl }
        } catch (err) {
          console.warn(`Failed to load metric image for "${label}":`, err)
          return { label, blobUrl: '' }
        }
      }),
    )

    for (const result of results) {
      if (result.status === 'fulfilled') {
        const { label, blobUrl } = result.value
        charts.push({ label, imageBlobUrl: blobUrl })
        if (blobUrl) newBlobUrls.push(blobUrl)
      }
    }

    // 新 blob 全部就位后再 revoke 旧的，避免 <img> 在 render 期间拿到失效 URL
    const prevBlobUrls = _currentMetricsBlobUrls
    _currentMetricsBlobUrls = newBlobUrls
    analysisChartsState.value = charts
    _loadedMetricsSignature = signature
    for (const url of prevBlobUrls) URL.revokeObjectURL(url)
    console.log('Metrics images loaded:', charts.length)
  }

  /**
   * 加载 checklist 数据
   *
   * 去重：同路径且已有数据 → 跳过。
   */
  async function loadChecklist(checklistPath: string): Promise<void> {
    if (!checklistPath) {
      invalidateChecklistCache()
      return
    }
    if (checklistPath === _loadedChecklistPath && checklistItemsState.value.length > 0) {
      return
    }

    try {
      const localPath = convertToLocalPath(checklistPath)
      const resolvedPath = await resolvedPathMemo(localPath)
      if (!resolvedPath) {
        invalidateChecklistCache()
        return
      }

      const fileContent = await readTextFile(resolvedPath)
      const data: ChecklistData = JSON.parse(fileContent)

      checklistItemsState.value = data.checklist || []
      _loadedChecklistPath = checklistPath
    } catch (err) {
      console.error('Failed to load checklist:', err)
      invalidateChecklistCache()
    }
  }

  function stepLogAbsPath(rootNorm: string, name: string, tool: string): string {
    return `${rootNorm}/${name}_${tool}/log/${name}.log`
  }

  function flowLogSegKey(stepName: string, tool: string): string {
    return `${stepName}\u001f${tool}`
  }

  /**
   * 读取 flow.json，构建出“步骤 -> 日志路径”的任务清单。
   * 不负责读日志文件本身，便于调用方选择是否先展示占位再并发填充。
   */
  async function planFlowLogSegments(
    flowLocal: string,
    includeOngoingLive: boolean,
  ): Promise<{
    tasks: Array<{
      seg: FlowLogSegment
      logPath: string
    }>
  } | null> {
    const workspaceRoot = workspaceRootFromFlowPath(flowLocal)
    if (!workspaceRoot) return null
    const resolvedFlowPath = await resolvedPathMemo(flowLocal)
    const resolvedWorkspaceRoot = await resolvedPathMemo(workspaceRoot)
    if (!resolvedFlowPath || !resolvedWorkspaceRoot) return null

    const fileContent = await readTextFile(resolvedFlowPath)
    const flowData = JSON.parse(fileContent) as {
      steps?: Array<{ name: string; tool: string; state: string }>
    }
    const steps = flowData.steps ?? []
    const root = resolvedWorkspaceRoot.replace(/\\/g, '/')

    const tasks: Array<{ seg: FlowLogSegment; logPath: string }> = []
    for (const step of steps) {
      const stateLc = (step.state ?? '').trim().toLowerCase()
      if (stateLc === 'unstart') continue
      if (stateLc === 'ongoing' && !includeOngoingLive) continue

      const logPath = stepLogAbsPath(root, step.name, step.tool)
      const failed = step.state === 'Incomplete' || step.state === 'Invalid'
      const live = stateLc === 'ongoing' && includeOngoingLive
      const seg: FlowLogSegment = {
        stepName: step.name,
        tool: step.tool,
        state: step.state,
        failed,
        missing: false,
        content: '',
        ...(live ? { live: true } : {}),
      }
      tasks.push({ seg, logPath })
    }
    return { tasks }
  }

  /**
   * 并发 + 缓存的日志读取；把结果就地写回 `segments[i]` 并同步到目标 ref，
   * UI 可以看到首批文件读完就显示。
   *
   * 不再对每个 logPath 单独走 `request_project_permission`——
   * `register_project_root` 里已经把工程根 `allow_directory(path, true)`，
   * 子文件全覆盖；而 canonicalize 在 log 还没写出时反倒会失败。
   */
  async function hydrateSegmentsWithLogs(
    target: Ref<FlowLogSegment[]>,
    segments: FlowLogSegment[],
    logPaths: string[],
    opts: { missingTextPrefix: string; isStale: () => boolean; emitEveryN: number },
  ): Promise<void> {
    // 1) 先同步看命中的模块级缓存：缓存里已经有内容的先展示（后面重读再覆盖）
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i]
      if (seg.content) continue
      const cached = logCacheGet(logPaths[i])
      if (cached) {
        segments[i] = {
          ...seg,
          content: cached.content,
          missing: false,
          truncated: cached.truncated,
          totalSize: cached.totalSize,
          logPath: logPaths[i],
        }
      } else {
        segments[i] = { ...seg, logPath: logPaths[i] }
      }
    }
    target.value = segments.slice()

    // 2) 并发按需读取；每累计 emitEveryN 个完成就 flush 一次 target.value
    let completedSinceEmit = 0
    await runWithConcurrency(
      segments.map((_, i) => i),
      LOG_READ_CONCURRENCY,
      async (idx) => {
        if (opts.isStale()) return
        const logPath = logPaths[idx]
        const result = await readLogFileSmart(logPath)
        if (opts.isStale()) return

        const cur = segments[idx]
        const nextContent = result.missing
          ? `${opts.missingTextPrefix}\n${logPath}`
          : result.content
        const contentUnchanged =
          cur.content === nextContent &&
          cur.missing === result.missing &&
          cur.truncated === result.truncated
        if (contentUnchanged) return

        segments[idx] = {
          ...cur,
          content: nextContent,
          missing: result.missing,
          truncated: result.truncated,
          totalSize: result.totalSize,
          logPath,
        }
        completedSinceEmit++
        if (completedSinceEmit >= opts.emitEveryN) {
          completedSinceEmit = 0
          target.value = segments.slice()
        }
      },
    )

    if (!opts.isStale()) {
      target.value = segments.slice()
    }
  }

  /**
   * 从已解析的 flow.json 本地路径构建步骤日志列表。
   * 主要用于 live-mode 的单次批量构建（外部不关心 stream 过程）。
   * @param includeOngoingLive 为 true 时包含 Ongoing 步，并标记 live
   */
  async function buildFlowLogSegmentsFromFlowLocal(
    flowLocal: string,
    includeOngoingLive: boolean,
  ): Promise<FlowLogSegment[]> {
    const plan = await planFlowLogSegments(flowLocal, includeOngoingLive)
    if (!plan) return []

    const segments = plan.tasks.map((t) => t.seg)
    const logPaths = plan.tasks.map((t) => t.logPath)

    // 模块级命中先用上，再并发补齐
    for (let i = 0; i < segments.length; i++) {
      const cached = logCacheGet(logPaths[i])
      if (cached) {
        segments[i] = {
          ...segments[i],
          content: cached.content,
          truncated: cached.truncated,
          totalSize: cached.totalSize,
          logPath: logPaths[i],
        }
      } else {
        segments[i] = { ...segments[i], logPath: logPaths[i] }
      }
    }

    const missingPrefix = includeOngoingLive
      ? '(Log file not yet available or unreadable; waiting…)'
      : '(Log file not found or unreadable)'

    await runWithConcurrency(
      segments.map((_, i) => i),
      LOG_READ_CONCURRENCY,
      async (idx) => {
        const logPath = logPaths[idx]
        const result = await readLogFileSmart(logPath)
        const cur = segments[idx]
        const nextContent = result.missing ? `${missingPrefix}\n${logPath}` : result.content
        segments[idx] = {
          ...cur,
          content: nextContent,
          missing: result.missing,
          truncated: result.truncated,
          totalSize: result.totalSize,
          logPath,
        }
      },
    )
    return segments
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
        const resolvedLogPath = await resolvedPathMemo(logPath)
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
          const resolvedLogPath = await resolvedPathMemo(logPath)
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
          const resolvedLogDir = await resolvedPathMemo(logDir)
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

  /**
   * 用 flow.json 定义的步骤列表刷新 `flowLogSegments`。
   *
   * 行为：
   *  1) 读 flow.json 拿到 step 清单，按 (stepName, tool) 与当前 segments 做 merge：
   *     已存在的步骤先复用旧 content，新增的步骤填空串；移除的步骤删掉。
   *     这一步瞬时完成，不走文件 IO —— remount 或 flow.json 小改动时 UI 零闪烁。
   *  2) 只有当第一屏没有任何 segments 时才置 `flowLogLoading = true`；
   *     revalidate 场景下保持原 segments 持续可见。
   *  3) 并发 stat + 按需读 log 文件，每完成若干个就 flush 一次 ref
   *     —— 首批 log 读完就能看到，不用等全部结束。
   *  4) 单文件超过 MAX_LOG_READ_CHARS 时只读尾部，拦住巨型日志拖垮前端。
   *  5) plan 构建后清理模块级 `logFileCache` 里本 plan 不再包含的 entry，
   *     避免同一项目内跑多次 flow 之后缓存无上限累积。
   */
  async function loadAllFlowStepLogsFromFlowPath(flowPathRemote: string): Promise<void> {
    if (!isInTauri || !flowPathRemote) {
      flowLogSegments.value = []
      flowLogLoading.value = false
      return
    }

    const callSession = ++flowLogLoadSession
    const isStale = () => callSession !== flowLogLoadSession

    flowLogError.value = null
    const startingEmpty = flowLogSegments.value.length === 0
    if (startingEmpty) flowLogLoading.value = true

    try {
      const flowLocal = convertToLocalPath(flowPathRemote)
      const resolvedFlowPath = await resolvedPathMemo(flowLocal)
      if (isStale()) return
      if (!resolvedFlowPath) {
        if (startingEmpty) flowLogSegments.value = []
        return
      }
      if (!workspaceRootFromFlowPath(resolvedFlowPath)) {
        flowLogError.value = 'Cannot resolve workspace root from flow.json path'
        flowLogSegments.value = []
        return
      }

      const plan = await planFlowLogSegments(resolvedFlowPath, false)
      if (isStale()) return
      if (!plan) {
        if (startingEmpty) flowLogSegments.value = []
        return
      }

      // 用旧 segments 的 content 预填入新 plan，保证展示不闪烁
      const existingByKey = new Map<string, FlowLogSegment>()
      for (const s of flowLogSegments.value) {
        existingByKey.set(flowLogSegKey(s.stepName, s.tool), s)
      }
      const segments = plan.tasks.map(({ seg }) => {
        const prior = existingByKey.get(flowLogSegKey(seg.stepName, seg.tool))
        if (!prior) return seg
        // 保留旧 content + missing，state/failed 跟 flow.json 走
        return {
          ...seg,
          content: prior.content,
          missing: prior.missing,
        }
      })
      const logPaths = plan.tasks.map((t) => t.logPath)

      // 本次 plan 里不再出现的 log 文件缓存直接清掉，防止同一项目内反复 run
      // 后 logFileCache 单调增长
      pruneLogCacheKeepOnly(logPaths)

      // 立即把新 segments 推到 UI（此时 content 来自旧缓存或为空）
      flowLogSegments.value = segments.slice()

      await hydrateSegmentsWithLogs(flowLogSegments, segments, logPaths, {
        missingTextPrefix: '(Log file not found or unreadable)',
        isStale,
        emitEveryN: 3,
      })

      if (!isStale()) {
        console.log('Flow step logs loaded:', flowLogSegments.value.length, 'segments')
      }
    } catch (err) {
      if (isStale()) return
      console.error('Failed to load flow step logs:', err)
      flowLogError.value = err instanceof Error ? err.message : String(err)
    } finally {
      if (!isStale()) {
        flowLogLoading.value = false
      }
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
   * 展开某个被截断的 step log：绕过缓存、以 `forceFull: true` 重新读整个文件，
   * 把完整内容写回对应 segment。UI 的"查看完整日志"按钮调用这个。
   *
   * 返回：true = 成功加载完整内容；false = 文件丢失 / 读取失败 / 目标 segment 已不存在
   */
  async function expandFlowLogSegment(segment: FlowLogSegment): Promise<boolean> {
    if (!isInTauri) return false
    const logPath = segment.logPath
    if (!logPath) return false

    const findIndex = (): number =>
      flowLogSegments.value.findIndex(
        (s) => s.stepName === segment.stepName && s.tool === segment.tool,
      )

    if (findIndex() < 0) return false

    const result = await readLogFileSmart(logPath, { forceFull: true, skipCache: true })
    const idx = findIndex()
    if (idx < 0) return false

    if (result.missing) {
      // 展开失败就保持原 truncated 状态，交由 UI 提示
      return false
    }

    const cur = flowLogSegments.value[idx]!
    flowLogSegments.value[idx] = {
      ...cur,
      content: result.content,
      truncated: false,
      totalSize: result.totalSize,
      missing: false,
    }
    return true
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
      // 不再主动 invalidateSharedHomeData()：只要项目没切，就复用上次拉到的
      // home.json（fetchSharedHomeData 内部会在项目路径变化时自动失效）。
      // 有更新时由 SSE notify → loadHomeDataFromPath 覆盖缓存，不需要每次
      // mount 都重请求后端再重读整个 home.json。
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
      const resolvedHomePath = await resolvedPathMemo(localPath)
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
   * 显式重新加载所有数据（用户点击刷新 / 外部主动拉取时用）
   *
   * 与 `loadHomeData` 的区别：后者走"缓存优先 + 签名去重"，这里强制把
   * 共享 home.json 以及下游资源的签名都清掉，loader 被再次调用时会真读磁盘。
   * blob 不立刻 revoke —— 新 blob 到位后由 loader 内部替换，避免闪一下白图。
   *
   * **特意不调用 `resetFlowLogState()`**：reset 会把 `flowLogSegments` 置空，
   * 导致 UI 瞬时变成 loading / 空列表，这和整套"防闪烁"目标相悖。改由
   * `loadAllFlowStepLogsFromFlowPath` 里的"按 key merge 旧 content"保证
   * 新旧数据平滑替换；`flowLogLoading` 会保持 false，避免误触发 loading 占位。
   * 如果要真的整屏清空（例如项目关闭），走 `clearHomeData(true)`。
   */
  async function refreshHomeData(): Promise<void> {
    invalidateSharedHomeData()
    markHomeAssetSignaturesStale()
    // 下发新的 flow log 会话号，让进行中的 hydrate 放弃；但不清 segments / 不触发 loading
    flowLogLoadSession++
    await loadHomeData()
  }

  /**
   * 清空所有数据
   */
  function clearHomeData(resetProjectState = false): void {
    liveSession++
    cleanupFlowLogLiveWatch()
    error.value = null
    if (resetProjectState) {
      // 项目真的切了：所有模块级缓存 + blob 全部失效
      resetSharedHomeDataProjectState()
    } else {
      // 仅本次加载失败 / 重新拉取：只让共享 home.json 重新取，但保留下游展示，
      // loader 下次成功时会走"新旧替换"平滑覆盖，避免中间闪一下白屏。
      // 需要整屏清空的场景（项目关闭 / 切换）会以 resetProjectState=true 再调一次。
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
      const resolvedFlowPath = await resolvedPathMemo(flowLocal)
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
      // 某步刚跑完会带 log_file；不管是否有 home_page，都把这个具体文件的缓存清掉，
      // 下一次 hydrate 才会真的重读磁盘，否则会一直拿老内容。
      //
      // 注意：`logFileCache` 的 key 是 **resolve 之后的规范化路径**（与
      // `planFlowLogSegments` 里组装的 `stepLogAbsPath(resolvedRoot, ...)` 保持一致）。
      // 这里必须先走 `resolvedPathMemo` 得到同样的 key，否则 `delete` 打不到目标条目，
      // UI 会继续显示旧内容直到项目切换。
      if (logFile) {
        const localLog = convertToLocalPath(logFile)
        const resolvedLog = await resolvedPathMemo(localLog)
        invalidateLogFileCache(resolvedLog ?? localLog)
      }
      // 仅 log_file、无 home_page 时仍按 flow.json 聚合刷新（例如某步刚跑完）
      if (logFile && !homePage) {
        await ensureFlowLogsLoaded()
      }
    }
  )

  // 组件卸载：只停掉本实例挂载的 live watcher / 定时器；
  // **不** 清模块级缓存或 revoke blob —— 下次 mount 直接复用 home.json、
  // checklist、layout blob、metrics blob、flowLogSegments。
  // Blob 的 revoke 改由"被新 blob 替换"或"项目切换"两个时机负责；
  // 在 onUnmounted 里 revoke 会导致下一次 mount 的 <img :src> 拿到已失效的 URL。
  // 数据新鲜度由 SSE 通知（markHomeAssetSignaturesStale）+ 项目切换里的 reset 负责。
  onUnmounted(() => {
    liveSession++
    cleanupFlowLogLiveWatch()
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
    expandFlowLogSegment,
  }
}
