use keyring::v1::Entry;
use serde::{Deserialize, Serialize};
use std::{
    collections::{HashMap, HashSet},
    fs,
    io::{Read, Write},
    net::TcpListener,
    net::TcpStream,
    path::PathBuf,
    sync::Mutex,
    time::Duration,
};
use tauri::{
    ipc::Response,
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, Manager, RunEvent, State, WindowEvent,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
use tauri_plugin_shell::{process::CommandChild, ShellExt};
use uuid::Uuid;

mod macos_selection;
mod quick_window;

const KEYCHAIN_SERVICE: &str = "com.george.poppy";
const DEEPSEEK_KEYCHAIN_ACCOUNT: &str = "deepseek-api-key";
const DASHSCOPE_KEYCHAIN_ACCOUNT: &str = "dashscope-api-key";
const FEISHU_KEYCHAIN_ACCOUNT: &str = "feishu-app-secret";
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
    secrets: HashMap<String, String>,
    checked_secrets: HashSet<String>,
}

#[derive(Default)]
struct GatewayState(Mutex<GatewayRuntime>);

#[derive(Default)]
struct OpenedPdfState(Mutex<Vec<String>>);

#[derive(Clone, Deserialize, Serialize)]
struct SecretUsage {
    provider: String,
    feishu_enabled: bool,
}

impl Default for SecretUsage {
    fn default() -> Self {
        Self {
            provider: "deepseek".to_string(),
            feishu_enabled: false,
        }
    }
}

fn keychain_account(provider: &str) -> Result<&'static str, String> {
    match provider {
        "deepseek" => Ok(DEEPSEEK_KEYCHAIN_ACCOUNT),
        "dashscope" => Ok(DASHSCOPE_KEYCHAIN_ACCOUNT),
        "feishu" => Ok(FEISHU_KEYCHAIN_ACCOUNT),
        _ => Err("不支持的密钥类型".to_string()),
    }
}

fn keychain_entry(provider: &str) -> Result<Entry, String> {
    Entry::new(KEYCHAIN_SERVICE, keychain_account(provider)?).map_err(|error| error.to_string())
}

fn read_api_key(provider: &str) -> Option<String> {
    keychain_entry(provider)
        .and_then(|entry| entry.get_password().map_err(|error| error.to_string()))
        .ok()
        .filter(|value| !value.trim().is_empty())
}

fn cached_api_key(state: &GatewayState, provider: &str) -> Option<String> {
    if let Ok(runtime) = state.0.lock() {
        if runtime.checked_secrets.contains(provider) {
            return runtime.secrets.get(provider).cloned();
        }
    }
    let value = read_api_key(provider);
    if let Ok(mut runtime) = state.0.lock() {
        runtime.checked_secrets.insert(provider.to_string());
        if let Some(secret) = value.as_ref() {
            runtime
                .secrets
                .insert(provider.to_string(), secret.to_string());
        }
    }
    value
}

fn cache_api_key(state: &GatewayState, provider: &str, value: Option<&str>) {
    if let Ok(mut runtime) = state.0.lock() {
        runtime.checked_secrets.insert(provider.to_string());
        match value {
            Some(secret) => {
                runtime
                    .secrets
                    .insert(provider.to_string(), secret.to_string());
            }
            None => {
                runtime.secrets.remove(provider);
            }
        }
    }
}

fn secret_usage_path(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_data_dir()
        .map(|path| path.join("secret-usage.json"))
        .map_err(|error| error.to_string())
}

fn read_secret_usage(app: &AppHandle) -> SecretUsage {
    secret_usage_path(app)
        .ok()
        .and_then(|path| fs::read(path).ok())
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .filter(|usage: &SecretUsage| matches!(usage.provider.as_str(), "deepseek" | "dashscope"))
        .unwrap_or_default()
}

fn write_secret_usage(app: &AppHandle, usage: &SecretUsage) -> Result<(), String> {
    let path = secret_usage_path(app)?;
    let parent = path
        .parent()
        .ok_or_else(|| "无法确定 Poppy 数据目录".to_string())?;
    fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    let temporary = path.with_extension("json.tmp");
    fs::write(
        &temporary,
        serde_json::to_vec(usage).map_err(|error| error.to_string())?,
    )
    .map_err(|error| error.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&temporary, fs::Permissions::from_mode(0o600))
            .map_err(|error| error.to_string())?;
    }
    fs::rename(temporary, path).map_err(|error| error.to_string())
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
        "POST /shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-Poppy-Token: {}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
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
    let usage = read_secret_usage(app);
    if let Some(api_key) = cached_api_key(state, &usage.provider) {
        let variable = if usage.provider == "dashscope" {
            "POPPY_DASHSCOPE_API_KEY"
        } else {
            "POPPY_DEEPSEEK_API_KEY"
        };
        command = command.env(variable, api_key);
    }
    if usage.feishu_enabled {
        if let Some(app_secret) = cached_api_key(state, "feishu") {
            command = command.env("POPPY_FEISHU_APP_SECRET", app_secret);
        }
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
    provider: String,
) -> Result<GatewayInfo, String> {
    let api_key = api_key.trim();
    if api_key.is_empty() {
        return Err("API Key 不能为空".to_string());
    }
    keychain_entry(&provider)?
        .set_password(api_key)
        .map_err(|error| error.to_string())?;
    cache_api_key(&state, &provider, Some(api_key));
    start_gateway(&app, &state)
}

#[tauri::command]
fn delete_api_key(
    app: AppHandle,
    state: State<'_, GatewayState>,
    provider: String,
) -> Result<GatewayInfo, String> {
    let _ = keychain_entry(&provider)?.delete_credential();
    cache_api_key(&state, &provider, None);
    start_gateway(&app, &state)
}

#[tauri::command]
fn configure_secret_usage(
    app: AppHandle,
    state: State<'_, GatewayState>,
    provider: String,
    feishu_enabled: bool,
) -> Result<GatewayInfo, String> {
    if !matches!(provider.as_str(), "deepseek" | "dashscope") {
        return Err("不支持的模型密钥类型".to_string());
    }
    write_secret_usage(
        &app,
        &SecretUsage {
            provider,
            feishu_enabled,
        },
    )?;
    start_gateway(&app, &state)
}

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn show_quick_capture(app: &AppHandle) {
    let capture = macos_selection::capture_frontmost_selection();
    quick_window::show(app, capture);
    let info = app
        .state::<GatewayState>()
        .0
        .lock()
        .ok()
        .and_then(|runtime| runtime.info.clone());
    if let Some(info) = info {
        let _ = app.emit_to("quick", "gateway-ready", info);
    }
}

#[tauri::command]
fn capture_selection() -> macos_selection::SelectionCapture {
    macos_selection::capture_frontmost_selection()
}

#[tauri::command]
fn request_accessibility_permission() -> bool {
    macos_selection::accessibility_status(true)
}

#[tauri::command]
fn hide_quick_window(app: AppHandle) {
    quick_window::hide(&app);
}

#[tauri::command]
fn read_pdf_bytes(path: String) -> Result<Response, String> {
    const MAX_READER_BYTES: u64 = 100 * 1024 * 1024;
    let resolved = PathBuf::from(path)
        .canonicalize()
        .map_err(|error| format!("无法打开 PDF：{error}"))?;
    if !resolved.is_file()
        || !resolved
            .extension()
            .is_some_and(|extension| extension.eq_ignore_ascii_case("pdf"))
    {
        return Err("请选择有效的 PDF 文件".to_string());
    }
    let metadata = fs::metadata(&resolved).map_err(|error| format!("无法读取 PDF：{error}"))?;
    if metadata.len() > MAX_READER_BYTES {
        return Err("内置阅读器当前支持最大 100MB 的 PDF".to_string());
    }
    let bytes = fs::read(&resolved).map_err(|error| format!("无法读取 PDF：{error}"))?;
    Ok(Response::new(bytes))
}

#[tauri::command]
fn take_opened_pdf_paths(state: State<'_, OpenedPdfState>) -> Vec<String> {
    state
        .0
        .lock()
        .map(|mut paths| std::mem::take(&mut *paths))
        .unwrap_or_default()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(GatewayState::default())
        .manage(OpenedPdfState::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        show_quick_capture(app);
                    }
                })
                .build(),
        )
        .setup(|app| {
            app.global_shortcut()
                .register("CommandOrControl+Shift+Space")?;

            let show_item = MenuItem::with_id(app, "show", "Show Poppy", true, None::<&str>)?;
            let quick_item = MenuItem::with_id(app, "quick", "Quick question", true, None::<&str>)?;
            let hide_item = MenuItem::with_id(app, "hide", "Hide Poppy", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Quit Poppy", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&quick_item, &show_item, &hide_item, &quit_item])?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().expect("Poppy app icon").clone())
                .tooltip("Poppy 桌面个人助手")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "quick" => show_quick_capture(app),
                    "show" => show_main_window(app),
                    "hide" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.hide();
                        }
                        quick_window::hide(app);
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
            delete_api_key,
            configure_secret_usage,
            capture_selection,
            request_accessibility_permission,
            hide_quick_window,
            read_pdf_bytes,
            take_opened_pdf_paths
        ])
        .build(tauri::generate_context!())
        .expect("error while building Poppy desktop application");

    app.run(|app, event| {
        #[cfg(target_os = "macos")]
        if let RunEvent::Opened { urls } = &event {
            let paths = urls
                .iter()
                .filter_map(|url| url.to_file_path().ok())
                .filter(|path| {
                    path.extension()
                        .is_some_and(|extension| extension.eq_ignore_ascii_case("pdf"))
                })
                .map(|path| path.to_string_lossy().to_string())
                .collect::<Vec<_>>();
            if !paths.is_empty() {
                if let Ok(mut queued) = app.state::<OpenedPdfState>().0.lock() {
                    queued.extend(paths.clone());
                }
                show_main_window(app);
                let _ = app.emit("open-pdf", paths);
            }
        }
        #[cfg(target_os = "macos")]
        if matches!(event, RunEvent::Reopen { .. }) {
            show_main_window(app);
        }
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            stop_gateway(&app.state::<GatewayState>());
        }
    });
}
