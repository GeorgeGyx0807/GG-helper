use keyring::v1::Entry;
use serde::Serialize;
use std::{
    io::{Read, Write},
    net::TcpListener,
    net::TcpStream,
    sync::Mutex,
    time::Duration,
};
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, RunEvent, State, WindowEvent,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_shell::{process::CommandChild, ShellExt};
use uuid::Uuid;

const KEYCHAIN_SERVICE: &str = "com.george.poppy";
const KEYCHAIN_ACCOUNT: &str = "deepseek-api-key";
const SIDECAR_NAME: &str = "poppy-gateway";

#[derive(Clone, Serialize)]
struct GatewayInfo {
    base_url: String,
    token: String,
}

#[derive(Default)]
struct GatewayRuntime {
    info: Option<GatewayInfo>,
    child: Option<CommandChild>,
}

#[derive(Default)]
struct GatewayState(Mutex<GatewayRuntime>);

fn keychain_entry() -> Result<Entry, String> {
    Entry::new(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT).map_err(|error| error.to_string())
}

fn read_api_key() -> Option<String> {
    keychain_entry()
        .and_then(|entry| entry.get_password().map_err(|error| error.to_string()))
        .ok()
        .filter(|value| !value.trim().is_empty())
}

fn stop_gateway(state: &GatewayState) {
    let stopped = state.0.lock().ok().map(|mut runtime| {
        let info = runtime.info.take();
        let child = runtime.child.take();
        (info, child)
    });
    if let Some((info, child)) = stopped {
        if let Some(info) = info {
            request_gateway_shutdown(&info);
        }
        if let Some(child) = child {
            let _ = child.kill();
        }
    }
}

fn request_gateway_shutdown(info: &GatewayInfo) {
    let Some(port) = info
        .base_url
        .rsplit(':')
        .next()
        .and_then(|value| value.parse::<u16>().ok())
    else {
        return;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(
        &std::net::SocketAddr::from(([127, 0, 0, 1], port)),
        Duration::from_millis(400),
    ) else {
        return;
    };
    let _ = stream.set_write_timeout(Some(Duration::from_millis(400)));
    let _ = stream.set_read_timeout(Some(Duration::from_millis(800)));
    let request = format!(
        "POST /shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-Pico-Token: {}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        info.token
    );
    if stream.write_all(request.as_bytes()).is_ok() {
        let mut response = [0_u8; 128];
        let _ = stream.read(&mut response);
        std::thread::sleep(Duration::from_millis(350));
    }
}

fn available_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0)).map_err(|error| error.to_string())?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| error.to_string())
}

fn start_gateway(app: &AppHandle, state: &GatewayState) -> Result<GatewayInfo, String> {
    stop_gateway(state);
    let port = available_port()?;
    let token = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
    let info = GatewayInfo {
        base_url: format!("http://127.0.0.1:{port}"),
        token: token.clone(),
    };

    let mut command = app
        .shell()
        .sidecar(SIDECAR_NAME)
        .map_err(|error| error.to_string())?
        .env_clear()
        .args(["--port", &port.to_string(), "--token", &token]);

    for name in ["HOME", "TMPDIR", "LANG", "PATH"] {
        if let Some(value) = std::env::var_os(name) {
            command = command.env(name, value);
        }
    }
    if let Some(api_key) = read_api_key() {
        command = command.env("PICO_DEEPSEEK_API_KEY", api_key);
    }

    let (mut events, child) = command.spawn().map_err(|error| error.to_string())?;
    {
        let mut runtime = state
            .0
            .lock()
            .map_err(|_| "gateway state lock poisoned".to_string())?;
        runtime.info = Some(info.clone());
        runtime.child = Some(child);
    }

    let app_handle = app.clone();
    let expected_token = token;
    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            if matches!(
                event,
                tauri_plugin_shell::process::CommandEvent::Terminated(_)
            ) {
                let state = app_handle.state::<GatewayState>();
                if let Ok(mut runtime) = state.0.lock() {
                    if runtime
                        .info
                        .as_ref()
                        .is_some_and(|item| item.token == expected_token)
                    {
                        runtime.info = None;
                        runtime.child = None;
                    }
                }
                break;
            }
        }
    });

    Ok(info)
}

#[tauri::command]
fn gateway_info(state: State<'_, GatewayState>) -> Result<GatewayInfo, String> {
    state
        .0
        .lock()
        .map_err(|_| "gateway state lock poisoned".to_string())?
        .info
        .clone()
        .ok_or_else(|| "Poppy local gateway is not running".to_string())
}

#[tauri::command]
fn set_api_key(
    app: AppHandle,
    state: State<'_, GatewayState>,
    api_key: String,
) -> Result<GatewayInfo, String> {
    let api_key = api_key.trim();
    if api_key.is_empty() {
        return Err("DeepSeek API key must not be empty".to_string());
    }
    keychain_entry()?
        .set_password(api_key)
        .map_err(|error| error.to_string())?;
    start_gateway(&app, &state)
}

#[tauri::command]
fn delete_api_key(app: AppHandle, state: State<'_, GatewayState>) -> Result<GatewayInfo, String> {
    if read_api_key().is_some() {
        keychain_entry()?
            .delete_credential()
            .map_err(|error| error.to_string())?;
    }
    start_gateway(&app, &state)
}

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn toggle_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        if window.is_visible().unwrap_or(false) {
            let _ = window.hide();
        } else {
            show_main_window(app);
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(GatewayState::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        toggle_main_window(app);
                    }
                })
                .build(),
        )
        .setup(|app| {
            app.global_shortcut()
                .register("CommandOrControl+Shift+Space")?;

            let show_item = MenuItem::with_id(app, "show", "Show Poppy", true, None::<&str>)?;
            let hide_item = MenuItem::with_id(app, "hide", "Hide Poppy", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Quit Poppy", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &hide_item, &quit_item])?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().expect("Poppy app icon").clone())
                .tooltip("Poppy 桌面个人助手")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_main_window(app),
                    "hide" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.hide();
                        }
                    }
                    "quit" => {
                        stop_gateway(&app.state::<GatewayState>());
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if matches!(
                        event,
                        TrayIconEvent::Click {
                            button: MouseButton::Left,
                            button_state: MouseButtonState::Up,
                            ..
                        }
                    ) {
                        show_main_window(tray.app_handle());
                    }
                })
                .build(app)?;

            let handle = app.handle().clone();
            start_gateway(&handle, &handle.state::<GatewayState>())?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![
            gateway_info,
            set_api_key,
            delete_api_key
        ])
        .build(tauri::generate_context!())
        .expect("error while building Poppy desktop application");

    app.run(|app, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            stop_gateway(&app.state::<GatewayState>());
        }
    });
}
