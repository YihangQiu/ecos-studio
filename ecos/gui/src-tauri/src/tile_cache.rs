use std::path::{Path, PathBuf};

use log::info;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tauri_plugin_fs::FsExt;

use crate::gen_layout_tiles;
use crate::project_scope::{validate_project_scoped_path, ProjectRootState};

/// 相对于工程根的瓦片缓存基础目录（`.ecos/tile-cache/layout`）。
/// 所有瓦片缓存必须落在该目录下的子目录里，命令入参校验时会据此拦截越界写入。
const TILE_CACHE_BASE: [&str; 3] = [".ecos", "tile-cache", "layout"];

fn tile_cache_base(root: &Path) -> PathBuf {
    let mut path = root.to_path_buf();
    for segment in TILE_CACHE_BASE {
        path.push(segment);
    }
    path
}

fn sanitize_step_key(raw: &str) -> String {
    // 注意：不允许 '.'，避免 `..` 等路径遍历片段拼接到输出目录上。
    // 仅保留 ASCII 字母数字、下划线与连字符，其它字符统一替换为 '_'。
    let mapped: String = raw
        .trim()
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect();
    let mut collapsed = String::new();
    let mut prev_us = false;
    for c in mapped.chars() {
        if c == '_' {
            if !prev_us {
                collapsed.push('_');
            }
            prev_us = true;
        } else {
            collapsed.push(c);
            prev_us = false;
        }
    }
    let s = collapsed.trim_matches('_');
    if s.is_empty() {
        "_default".to_string()
    } else {
        s.to_string()
    }
}

/// 取当前工程根路径快照（未注册时返回错误）。
/// 之所以 clone 而不是持锁：防止在后续 I/O 操作期间长时间占用互斥锁，
/// 也避免把 `MutexGuard` 跨 await 点传递。
fn current_project_root(state: &ProjectRootState) -> Result<PathBuf, String> {
    let guard = state
        .lock()
        .map_err(|e| format!("project root lock error: {}", e))?;
    guard
        .as_ref()
        .cloned()
        .ok_or_else(|| "Project root is not registered".to_string())
}

/// 确认 `out_dir` 已经存在、位于工程根下，并且位于 `<root>/.ecos/tile-cache/layout/` 下。
/// 用于 `generate_layout_tiles` / `finalize_layout_tile_cache_meta` ——它们的 out_dir 由
/// `prepare_layout_tile_cache` 产生，调用时必然已存在。
fn validate_tile_cache_out_dir(out_dir: &str, root: &Path) -> Result<PathBuf, String> {
    let canonical = validate_project_scoped_path(out_dir, root)?;
    let base = tile_cache_base(root);
    let base_canonical = base.canonicalize().unwrap_or(base);
    if !canonical.starts_with(&base_canonical) {
        return Err(format!(
            "Refusing tile cache out_dir outside {}: {}",
            base_canonical.display(),
            canonical.display()
        ));
    }
    Ok(canonical)
}

fn sha256_hex_file(path: &Path) -> Result<String, String> {
    let bytes =
        std::fs::read(path).map_err(|e| format!("read layout json {}: {}", path.display(), e))?;
    let digest = Sha256::digest(&bytes);
    Ok(digest.iter().map(|b| format!("{:02x}", b)).collect())
}

#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TileCacheMeta {
    layout_json_path: String,
    content_sha256: String,
    generated_at: String,
}

/// 准备按步骤划分的瓦片缓存目录：计算布局 JSON 内容 SHA-256，若与 `tile-cache.meta.json` 一致且已有 `manifest.json` 则跳过生成。
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PrepareLayoutTileCachePayload {
    project_path: String,
    step_key: String,
    layout_json_path: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PrepareLayoutTileCacheResult {
    out_dir: String,
    from_cache: bool,
    content_sha256: String,
}

#[tauri::command]
pub async fn prepare_layout_tile_cache(
    app: tauri::AppHandle,
    project_root_state: tauri::State<'_, ProjectRootState>,
    payload: PrepareLayoutTileCachePayload,
) -> Result<PrepareLayoutTileCacheResult, String> {
    let root = current_project_root(&project_root_state)?;

    // 校验 project_path / layout_json_path 必须在当前工程根之内，防止前端传入
    // 越界路径诱导 `std::fs::remove_dir_all` / 读布局 JSON。project_path 校验后不再
    // 参与路径拼接 —— 输出目录一律基于 `root` 拼，这样 generate / finalize 的
    // out_dir 校验才能和这里自洽。
    let _project_path_in_root = validate_project_scoped_path(&payload.project_path, &root)?;
    let layout_json = validate_project_scoped_path(&payload.layout_json_path, &root)?;
    let step = sanitize_step_key(&payload.step_key);

    info!(
        "cmd=prepare_layout_tile_cache project_root={} step_key={} layout_json={}",
        root.display(),
        step,
        layout_json.display()
    );

    if !layout_json.is_file() {
        return Err(format!("布局 JSON 不存在: {}", layout_json.display()));
    }

    let content_sha256 = sha256_hex_file(&layout_json)?;

    // `out` 通过 canonical 化后的工程根拼装；`sanitize_step_key` 已保证不出现
    // `..` / `/` 等路径遍历片段，整体结果必然落在 <root>/.ecos/tile-cache/layout/ 下。
    let out = tile_cache_base(&root).join(&step);

    let meta_path = out.join("tile-cache.meta.json");
    let manifest_path = out.join("manifest.json");

    let cache_hit = if meta_path.is_file() && manifest_path.is_file() {
        let raw = std::fs::read_to_string(&meta_path)
            .map_err(|e| format!("read {}: {}", meta_path.display(), e))?;
        let meta: TileCacheMeta = serde_json::from_str(&raw)
            .map_err(|e| format!("parse {}: {}", meta_path.display(), e))?;
        meta.content_sha256 == content_sha256
    } else {
        false
    };

    if !cache_hit && out.exists() {
        std::fs::remove_dir_all(&out)
            .map_err(|e| format!("remove_dir_all {}: {}", out.display(), e))?;
    }
    std::fs::create_dir_all(&out)
        .map_err(|e| format!("create_dir_all {}: {}", out.display(), e))?;

    let canonical = out.canonicalize().unwrap_or_else(|_| out.clone());

    let scope = app.fs_scope();
    scope
        .allow_directory(&canonical, true)
        .map_err(|e| format!("fs_scope allow_directory {}: {}", canonical.display(), e))?;

    Ok(PrepareLayoutTileCacheResult {
        out_dir: canonical.to_string_lossy().into_owned(),
        from_cache: cache_hit,
        content_sha256,
    })
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FinalizeLayoutTileCacheMetaPayload {
    out_dir: String,
    layout_json_path: String,
    content_sha256: String,
}

/// 在瓦片生成成功写入输出目录后调用，持久化缓存指纹。
#[tauri::command]
pub async fn finalize_layout_tile_cache_meta(
    project_root_state: tauri::State<'_, ProjectRootState>,
    payload: FinalizeLayoutTileCacheMetaPayload,
) -> Result<(), String> {
    let root = current_project_root(&project_root_state)?;
    let out = validate_tile_cache_out_dir(&payload.out_dir, &root)?;
    // layout_json_path 只是写进 meta 的元信息，仍然校验以防前端借此把 meta 里
    // 的路径指向工程外位置，误导后续消费者。
    let layout_json = validate_project_scoped_path(&payload.layout_json_path, &root)?;

    let meta_path = out.join("tile-cache.meta.json");
    let meta = TileCacheMeta {
        layout_json_path: layout_json.to_string_lossy().into_owned(),
        content_sha256: payload.content_sha256,
        generated_at: chrono::Utc::now().to_rfc3339(),
    };
    let json = serde_json::to_string_pretty(&meta).map_err(|e| e.to_string())?;
    std::fs::write(&meta_path, json)
        .map_err(|e| format!("write {}: {}", meta_path.display(), e))?;
    info!(
        "cmd=finalize_layout_tile_cache_meta path={}",
        meta_path.display()
    );
    Ok(())
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct GenerateLayoutTilesPayload {
    layout_json_path: String,
    out_dir: String,
}

/// 从布局 JSON 生成瓦片目录。
#[tauri::command]
pub async fn generate_layout_tiles(
    project_root_state: tauri::State<'_, ProjectRootState>,
    payload: GenerateLayoutTilesPayload,
) -> Result<(), String> {
    let root = current_project_root(&project_root_state)?;
    let layout = validate_project_scoped_path(&payload.layout_json_path, &root)?;
    let out = validate_tile_cache_out_dir(&payload.out_dir, &root)?;

    info!(
        "cmd=generate_layout_tiles layout={} out={}",
        layout.display(),
        out.display()
    );

    tauri::async_runtime::spawn_blocking(move || gen_layout_tiles::generate(&layout, &out))
        .await
        .map_err(|e| e.to_string())?
}
