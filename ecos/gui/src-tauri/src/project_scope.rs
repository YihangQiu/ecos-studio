use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use log::info;
use serde::Serialize;
use tauri_plugin_fs::FsExt;

pub type ProjectRootState = Arc<Mutex<Option<PathBuf>>>;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PdkDetectedFiles {
    directories: Vec<String>,
    files: Vec<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ScannedPdkDirectory {
    canonical_path: String,
    name: String,
    description: String,
    tech_node: String,
    pdk_id: String,
    detected_files: PdkDetectedFiles,
}

fn canonicalize_existing_path(path: &str) -> Result<PathBuf, String> {
    let raw = PathBuf::from(path);
    let canonical = raw
        .canonicalize()
        .map_err(|e| format!("canonicalize {}: {}", raw.display(), e))?;
    Ok(canonical)
}

fn canonicalize_existing_directory(path: &str) -> Result<PathBuf, String> {
    let canonical = canonicalize_existing_path(path)?;
    if !canonical.is_dir() {
        return Err(format!("{} is not a directory", canonical.display()));
    }
    Ok(canonical)
}

fn is_within_root(candidate: &Path, root: &Path) -> bool {
    candidate == root || candidate.starts_with(root)
}

fn is_project_directory_candidate(path: &Path) -> bool {
    if !path.is_dir() {
        return false;
    }

    let home = path.join("home");
    if !home.is_dir() {
        return false;
    }

    ["home.json", "flow.json", "parameters.json"]
        .iter()
        .any(|name| home.join(name).is_file())
}

fn scan_top_level_entries(path: &Path) -> Result<PdkDetectedFiles, String> {
    let entries = fs::read_dir(path).map_err(|e| format!("read_dir {}: {}", path.display(), e))?;
    let mut directories = Vec::new();
    let mut files = Vec::new();

    for entry in entries {
        let entry = entry.map_err(|e| format!("read_dir {}: {}", path.display(), e))?;
        let file_type = entry
            .file_type()
            .map_err(|e| format!("file_type {}: {}", path.display(), e))?;
        let name = entry.file_name().to_string_lossy().into_owned();

        if file_type.is_dir() {
            directories.push(name);
        } else if file_type.is_file() {
            files.push(name);
        }
    }

    directories.sort();
    files.sort();
    directories.truncate(20);
    files.truncate(20);

    Ok(PdkDetectedFiles { directories, files })
}

pub fn validate_project_scoped_path(path: &str, root: &Path) -> Result<PathBuf, String> {
    let canonical = canonicalize_existing_path(path)?;
    if !is_within_root(&canonical, root) {
        return Err(format!(
            "Refusing to grant access outside current project root: {}",
            canonical.display()
        ));
    }
    Ok(canonical)
}

#[tauri::command]
pub async fn register_project_root(
    app: tauri::AppHandle,
    project_root_state: tauri::State<'_, ProjectRootState>,
    path: String,
) -> Result<String, String> {
    // NOTE: Tauri 的 fs::Scope 的 allowed_patterns / forbidden_patterns 都是只能
    // 追加、无法移除的 HashSet，且判定时 forbid 优先于 allow。因此这里 **绝不能**
    // 调用 `scope.forbid_directory(...)` 去「撤销」上一个工程的权限 —— 一旦进入
    // forbidden_patterns，该路径在进程生命周期内就永远被拒绝，导致用户切回或重开
    // 同一工程时 readTextFile 返回 "Forbidden path: ..."。
    //
    // 真正的工程越权拦截由 `request_project_permission` + `validate_project_scoped_path`
    // 在每次请求时校验 `current_root` 完成；这里只负责把当前工程根追加到 allowed。
    let canonical = canonicalize_existing_directory(&path)?;
    let scope = app.fs_scope();
    let mut current_root = project_root_state
        .lock()
        .map_err(|e| format!("project root lock error: {}", e))?;

    scope.allow_directory(&canonical, true).map_err(|e| {
        format!(
            "Unable to grant file system access {}: {}",
            canonical.display(),
            e
        )
    })?;

    info!("cmd=register_project_root path={}", canonical.display());
    *current_root = Some(canonical.clone());
    Ok(canonical.to_string_lossy().into_owned())
}

#[tauri::command]
pub async fn clear_project_root(
    _app: tauri::AppHandle,
    project_root_state: tauri::State<'_, ProjectRootState>,
) -> Result<(), String> {
    // 仅清掉 ProjectRootState，不再调用 `scope.forbid_directory`。
    // 原因见 `register_project_root` 注释：forbid 不可逆，会毒化同一路径，使得用户
    // 再次打开同一工程时所有文件读取都被 Tauri plugin-fs 判为 PathForbidden。
    //
    // 关闭后再发起的 `request_project_permission` 会因为 `current_root` 为 None 而
    // 被直接拒绝（见该命令实现），因此不会有新的文件 allow 被授予；旧工程目录虽然
    // 仍残留在 allowed_patterns 中，但前端已经切走，不会再主动请求，不构成泄漏。
    let mut current_root = project_root_state
        .lock()
        .map_err(|e| format!("project root lock error: {}", e))?;

    if let Some(existing) = current_root.take() {
        info!("cmd=clear_project_root path={}", existing.display());
    }

    Ok(())
}

#[tauri::command]
pub async fn request_project_permission(
    app: tauri::AppHandle,
    project_root_state: tauri::State<'_, ProjectRootState>,
    path: String,
) -> Result<String, String> {
    info!("cmd=request_project_permission path={}", path);
    let project_root = project_root_state
        .lock()
        .map_err(|e| format!("project root lock error: {}", e))?;
    let root = project_root
        .as_ref()
        .ok_or_else(|| "Project root is not registered".to_string())?;
    let canonical = validate_project_scoped_path(&path, root)?;

    let scope = app.fs_scope();
    if canonical.is_dir() {
        scope.allow_directory(&canonical, true).map_err(|e| {
            format!(
                "Unable to grant file system access permissions {}: {}",
                canonical.display(),
                e
            )
        })?;
    } else {
        scope.allow_file(&canonical).map_err(|e| {
            format!(
                "Unable to grant file access permission {}: {}",
                canonical.display(),
                e
            )
        })?;
    }
    Ok(canonical.to_string_lossy().into_owned())
}

#[tauri::command]
pub fn is_project_directory(path: String) -> bool {
    canonicalize_existing_directory(&path)
        .map(|canonical| is_project_directory_candidate(&canonical))
        .unwrap_or(false)
}

#[tauri::command]
pub fn scan_pdk_directory(path: String) -> Result<ScannedPdkDirectory, String> {
    let canonical = canonicalize_existing_directory(&path)?;
    let detected_files = scan_top_level_entries(&canonical)?;

    let mut name = canonical
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "Unknown PDK".to_string());
    let mut description = String::new();
    let mut tech_node = String::new();
    let mut pdk_id = name
        .to_lowercase()
        .replace(|c: char| !c.is_ascii_alphanumeric(), "_");

    if detected_files.directories.iter().any(|d| d == "prtech")
        && detected_files.directories.iter().any(|d| d == "IP")
    {
        name = "ics55".to_string();
        description = "ICSPROUT 55nm process library (auto-detected)".to_string();
        tech_node = "55nm".to_string();
        pdk_id = "ics55".to_string();
    } else if detected_files
        .directories
        .iter()
        .any(|d| d.starts_with("sky130"))
    {
        name = "SkyWater SKY130 PDK".to_string();
        description = "SkyWater 130nm open-source PDK (auto-detected)".to_string();
        tech_node = "130nm".to_string();
        pdk_id = "sky130".to_string();
    } else if detected_files.files.iter().any(|f| f.ends_with(".lef"))
        || detected_files.files.iter().any(|f| f.ends_with(".lib"))
    {
        description = "Process library files detected".to_string();
    }

    info!("cmd=scan_pdk_directory path={}", canonical.display());
    Ok(ScannedPdkDirectory {
        canonical_path: canonical.to_string_lossy().into_owned(),
        name,
        description,
        tech_node,
        pdk_id,
        detected_files,
    })
}

#[cfg(test)]
mod tests {
    use super::{is_project_directory_candidate, validate_project_scoped_path};
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn unique_temp_dir(label: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock before unix epoch")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("ecos-studio-{label}-{nanos}"));
        fs::create_dir_all(&dir).expect("create temp dir");
        dir
    }

    #[test]
    fn validate_project_scoped_path_allows_project_root_children_and_files() {
        let root = unique_temp_dir("root");
        let nested = root.join("home").join("flow");
        let file = nested.join("home.json");
        fs::create_dir_all(&nested).expect("create nested dir");
        fs::write(&file, "{}").expect("write file");

        let root_validated = validate_project_scoped_path(root.to_string_lossy().as_ref(), &root)
            .expect("root allowed");
        let nested_validated =
            validate_project_scoped_path(nested.to_string_lossy().as_ref(), &root)
                .expect("nested allowed");
        let file_validated = validate_project_scoped_path(file.to_string_lossy().as_ref(), &root)
            .expect("file allowed");

        assert_eq!(root_validated, root.canonicalize().expect("canonical root"));
        assert_eq!(
            nested_validated,
            nested.canonicalize().expect("canonical nested")
        );
        assert_eq!(file_validated, file.canonicalize().expect("canonical file"));

        fs::remove_dir_all(&root).expect("cleanup root");
    }

    #[test]
    fn validate_project_scoped_path_rejects_paths_outside_project_root() {
        let root = unique_temp_dir("project");
        let outside = unique_temp_dir("outside");
        let escaped = root
            .join("..")
            .join(outside.file_name().expect("outside dir name"));

        let sibling_err = validate_project_scoped_path(outside.to_string_lossy().as_ref(), &root)
            .expect_err("sibling dir must be rejected");
        assert!(sibling_err.contains("outside current project root"));

        let escaped_err = validate_project_scoped_path(escaped.to_string_lossy().as_ref(), &root)
            .expect_err("path traversal escape must be rejected");
        assert!(escaped_err.contains("outside current project root"));

        fs::remove_dir_all(&root).expect("cleanup root");
        fs::remove_dir_all(&outside).expect("cleanup outside");
    }

    #[test]
    fn project_directory_candidate_requires_home_marker_files() {
        let root = unique_temp_dir("workspace");
        let home = root.join("home");
        fs::create_dir_all(&home).expect("create home dir");

        assert!(
            !is_project_directory_candidate(&root),
            "home dir without marker files should not count as project"
        );

        fs::write(home.join("home.json"), "{}").expect("write home marker");
        assert!(
            is_project_directory_candidate(&root),
            "workspace with home/home.json should count as project"
        );

        fs::remove_dir_all(&root).expect("cleanup workspace");
    }
}
