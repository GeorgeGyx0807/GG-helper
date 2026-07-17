use crate::macos_selection::SelectionCapture;
use tauri::{Emitter, Manager, PhysicalPosition, Position};

pub fn show(app: &tauri::AppHandle, capture: SelectionCapture) {
    let Some(window) = app.get_webview_window("quick") else {
        return;
    };
    if let Ok(cursor) = app.cursor_position() {
        let size = window.outer_size().ok();
        let monitor = app.monitor_from_point(cursor.x, cursor.y).ok().flatten();
        let (mut x, mut y) = (cursor.x as i32 + 18, cursor.y as i32 + 18);
        if let (Some(size), Some(monitor)) = (size, monitor) {
            let origin = monitor.position();
            let screen = monitor.size();
            let right = origin.x + screen.width as i32;
            let bottom = origin.y + screen.height as i32;
            if x + size.width as i32 > right - 12 {
                x = cursor.x as i32 - size.width as i32 - 18;
            }
            if y + size.height as i32 > bottom - 12 {
                y = bottom - size.height as i32 - 12;
            }
            x = x.max(origin.x + 12);
            y = y.max(origin.y + 12);
        }
        let _ = window.set_position(Position::Physical(PhysicalPosition::new(x, y)));
    }
    let _ = window.show();
    let _ = window.set_focus();
    let _ = window.emit("quick-capture", capture);
}

pub fn hide(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("quick") {
        let _ = window.hide();
    }
}
