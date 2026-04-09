import { isAbsolute, join, normalize } from '@tauri-apps/api/path'
import { convertFileSrc, invoke } from '@tauri-apps/api/core'
import { isTauri } from '@/composables/useTauri'

/**
 * 将 get_info(layout) 给出的路径解析为绝对路径。
 * 后端有时会返回少了前导 `/` 的 POSIX 绝对路径（如 `home/ekko/...`），若再与 projectPath 拼接会重复成
 * `/proj/home/ekko/...` 导致 ENOENT。
 */
/** 与 `runLayoutTileGeneration` 使用同一解析规则，供 single-flight 键与调用方复用 */
export async function resolveLayoutJsonAbsolutePath(
  projectPath: string,
  layoutJsonRelative: string,
): Promise<string> {
  const trimmed = layoutJsonRelative.trim()
  if (!trimmed) {
    throw new Error('布局 JSON 路径为空')
  }
  if (await isAbsolute(trimmed)) {
    return normalize(trimmed)
  }
  // Linux 下缺 `/`；macOS 下缺 `/` 的 `Users/...`
  if (trimmed.startsWith('home/') || trimmed.startsWith('Users/')) {
    return normalize('/' + trimmed)
  }
  return join(projectPath, trimmed)
}

/** 逻辑任务键：同键并发应合并为 single-flight（路径已解析为绝对路径） */
export function buildLayoutTileJobKey(
  projectPath: string,
  stepKey: string,
  layoutJsonAbsolute: string,
): string {
  return `${projectPath}\0${stepKey}\0${layoutJsonAbsolute}`
}

/** 从 get_info layout 的 info 对象中取出布局 JSON 相对路径 */
export function pickLayoutJsonPath(info: unknown): string | null {
  if (!info || typeof info !== 'object') return null
  const o = info as Record<string, unknown>
  for (const k of ['json', 'info', 'infoJson', 'layoutJson']) {
    const v = o[k]
    if (typeof v === 'string' && v.trim().length) return v.trim()
  }
  return null
}

/** 从 get_info(layout) 的 info 中取出 DRC JSON 相对路径（可选） */
export function pickDrcJsonPath(info: unknown): string | null {
  if (!info || typeof info !== 'object') return null
  const o = info as Record<string, unknown>
  for (const k of ['drcJson', 'drcStep', 'drc_step', 'drc']) {
    const v = o[k]
    if (typeof v === 'string' && v.trim().length) return v.trim()
  }
  return null
}

/**
 * 默认 DRC 路径（无 get_info 显式字段时使用）：
 * - 布局在 `…/output/` 下时，约定 DRC 在同级 `…/feature/drc.step.json`（与输出目录分离）。
 * - 否则与布局 JSON 同目录，例如 `feature/foo.json` → `feature/drc.step.json`。
 */
export function deriveDrcStepPathFromLayoutJsonRelative(layoutJsonRelative: string): string | null {
  const t = layoutJsonRelative.trim()
  if (!t) return null
  const lastSlash = Math.max(t.lastIndexOf('/'), t.lastIndexOf('\\'))
  let parent = lastSlash >= 0 ? t.slice(0, lastSlash + 1) : ''
  if (/\/output\/$/i.test(parent) || /^output\/$/i.test(parent)) {
    parent = parent
      .replace(/\/output\/$/i, '/feature/')
      .replace(/^output\/$/i, 'feature/')
  }
  return parent + 'drc.step.json'
}

/**
 * 从布局 JSON 生成瓦片包（Rust `generate_layout_tiles`），并返回可供 TileManager 使用的 baseUrl。
 * 缓存目录按 `stepKey` 分文件夹；若源 JSON 内容 SHA-256 未变则跳过生成（由 Rust `prepare_layout_tile_cache` 判定）。
 */
export async function runLayoutTileGeneration(params: {
  projectPath: string
  layoutJsonRelative: string
  /** 与路由阶段一致，用于 `.ecos/tile-cache/layout/<stepKey>/` */
  stepKey: string
}): Promise<{ baseUrl: string; outDir: string; fromCache: boolean }> {
  if (!isTauri()) {
    throw new Error('瓦片生成仅可在 ECOS Studio 桌面应用中使用。')
  }

  const { projectPath, layoutJsonRelative, stepKey } = params
  const inputAbs = await resolveLayoutJsonAbsolutePath(projectPath, layoutJsonRelative)

  // 与打开工程时一致：工程根目录纳入 scope（读布局 JSON 等）
  await invoke('request_project_permission', { path: projectPath })

  const prep = await invoke<{
    outDir: string
    fromCache: boolean
    contentSha256: string
  }>('prepare_layout_tile_cache', {
    payload: {
      projectPath,
      stepKey,
      layoutJsonPath: inputAbs,
    },
  })

  const { outDir, fromCache, contentSha256 } = prep

  if (fromCache) {
    return {
      baseUrl: convertFileSrc(outDir),
      outDir,
      fromCache: true,
    }
  }

  await invoke('generate_layout_tiles', {
    payload: {
      layoutJsonPath: inputAbs,
      outDir,
    },
  })

  await invoke('finalize_layout_tile_cache_meta', {
    payload: {
      outDir,
      layoutJsonPath: inputAbs,
      contentSha256,
    },
  })

  const baseUrl = convertFileSrc(outDir)
  return { baseUrl, outDir, fromCache: false }
}
