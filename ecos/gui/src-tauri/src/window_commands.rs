use log::debug;
use tauri::LogicalSize;

/// 将窗口尺寸限制在当前显示器可视区域内，并重新居中。
///
/// 背景：`tauri.conf.json` 里配置的 `width/height` 是逻辑像素。当用户屏幕
/// 物理分辨率较低（如 1366×768）或系统存在 DPI 缩放（125%/150%）时，
/// 初始窗口可能超出屏幕边界，使得标题栏右侧的窗口控制按钮被挤到可视
/// 区外点不到。这里在窗口显示之前做一次限幅 + 居中兜底。
pub fn clamp_window_to_monitor(window: &tauri::WebviewWindow) {
    // 留一点边距给任务栏/顶栏/窗口装饰，尽量保证所有边都落在可视区内。
    const HORIZONTAL_MARGIN: f64 = 48.0;
    const VERTICAL_MARGIN: f64 = 96.0;
    const MIN_WIDTH: f64 = 720.0;
    const MIN_HEIGHT: f64 = 480.0;

    let monitor = match window.current_monitor() {
        Ok(Some(m)) => m,
        _ => return,
    };

    let scale = monitor.scale_factor().max(1.0);
    let mon_logical_w = monitor.size().width as f64 / scale;
    let mon_logical_h = monitor.size().height as f64 / scale;

    let max_w = (mon_logical_w - HORIZONTAL_MARGIN).max(MIN_WIDTH);
    let max_h = (mon_logical_h - VERTICAL_MARGIN).max(MIN_HEIGHT);

    let Ok(current) = window.inner_size() else {
        return;
    };
    let cur_w = current.width as f64 / scale;
    let cur_h = current.height as f64 / scale;

    let new_w = cur_w.min(max_w);
    let new_h = cur_h.min(max_h);

    if (new_w - cur_w).abs() > 1.0 || (new_h - cur_h).abs() > 1.0 {
        debug!(
            "Clamp window: monitor={}x{} (scale={}), {}x{} -> {}x{}",
            mon_logical_w as u32,
            mon_logical_h as u32,
            scale,
            cur_w as u32,
            cur_h as u32,
            new_w as u32,
            new_h as u32,
        );
        let _ = window.set_size(LogicalSize::new(new_w, new_h));
        let _ = window.center();
    }
}

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
