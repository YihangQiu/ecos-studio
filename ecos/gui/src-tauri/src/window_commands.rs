use log::debug;

/// 窗口最小化
#[tauri::command]
pub fn window_minimize(window: tauri::Window) {
    debug!("cmd=window_minimize window={}", window.label());
    let _ = window.minimize();
}

/// 窗口最大化/还原
#[tauri::command]
pub fn window_maximize(window: tauri::Window) {
    let is_maximized = window.is_maximized().unwrap_or(false);
    debug!(
        "cmd=window_maximize window={} action={}",
        window.label(),
        if is_maximized {
            "unmaximize"
        } else {
            "maximize"
        }
    );
    if is_maximized {
        let _ = window.unmaximize();
    } else {
        let _ = window.maximize();
    }
}

/// 窗口关闭
#[tauri::command]
pub fn window_close(window: tauri::Window) {
    debug!("cmd=window_close window={}", window.label());
    let _ = window.close();
}
