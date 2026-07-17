use serde::Serialize;

const MAX_SELECTION_CHARS: usize = 12_000;

#[derive(Clone, Debug, Serialize)]
pub struct SelectionCapture {
    pub text: String,
    pub source_app: String,
    pub bundle_id: String,
    pub window_title: String,
    pub capture_method: String,
    pub truncated: bool,
    pub accessibility_trusted: bool,
    pub error: String,
}

impl SelectionCapture {
    fn empty(trusted: bool, error: impl Into<String>) -> Self {
        Self {
            text: String::new(),
            source_app: String::new(),
            bundle_id: String::new(),
            window_title: String::new(),
            capture_method: "manual".to_string(),
            truncated: false,
            accessibility_trusted: trusted,
            error: error.into(),
        }
    }
}

#[cfg(target_os = "macos")]
mod platform {
    use super::{SelectionCapture, MAX_SELECTION_CHARS};
    use objc2::runtime::ProtocolObject;
    use objc2_app_kit::{
        NSPasteboard, NSPasteboardItem, NSPasteboardTypeString, NSPasteboardWriting, NSWorkspace,
    };
    use objc2_foundation::{NSArray, NSData, NSDictionary, NSNumber, NSString};
    use std::{
        ffi::{c_char, c_void},
        ptr, thread,
        time::{Duration, Instant},
    };

    type CFTypeRef = *const c_void;
    type CFStringRef = *const c_void;
    type AXUIElementRef = *const c_void;
    const K_CF_STRING_ENCODING_UTF8: u32 = 0x0800_0100;
    const K_CG_HID_EVENT_TAP: u32 = 0;
    const K_CG_EVENT_FLAG_MASK_COMMAND: u64 = 1 << 20;
    const KEY_CODE_C: u16 = 8;

    #[link(name = "ApplicationServices", kind = "framework")]
    unsafe extern "C" {
        fn AXIsProcessTrusted() -> bool;
        fn AXIsProcessTrustedWithOptions(options: CFTypeRef) -> bool;
        fn AXUIElementCreateSystemWide() -> AXUIElementRef;
        fn AXUIElementCopyAttributeValue(
            element: AXUIElementRef,
            attribute: CFStringRef,
            value: *mut CFTypeRef,
        ) -> i32;
        fn CGEventCreateKeyboardEvent(
            source: CFTypeRef,
            virtual_key: u16,
            key_down: bool,
        ) -> CFTypeRef;
        fn CGEventSetFlags(event: CFTypeRef, flags: u64);
        fn CGEventPost(tap: u32, event: CFTypeRef);
    }

    #[link(name = "CoreFoundation", kind = "framework")]
    unsafe extern "C" {
        fn CFRelease(value: CFTypeRef);
        fn CFGetTypeID(value: CFTypeRef) -> usize;
        fn CFStringGetTypeID() -> usize;
        fn CFStringGetLength(value: CFStringRef) -> isize;
        fn CFStringGetMaximumSizeForEncoding(length: isize, encoding: u32) -> isize;
        fn CFStringGetCString(
            value: CFStringRef,
            buffer: *mut c_char,
            buffer_size: isize,
            encoding: u32,
        ) -> bool;
    }

    pub fn accessibility_status(prompt: bool) -> bool {
        unsafe {
            if !prompt {
                return AXIsProcessTrusted();
            }
            let key = NSString::from_str("AXTrustedCheckOptionPrompt");
            let value = NSNumber::new_bool(true);
            let options = NSDictionary::from_slices(&[&*key], &[&*value]);
            AXIsProcessTrustedWithOptions((&*options as *const _) as CFTypeRef)
        }
    }

    pub fn capture() -> SelectionCapture {
        let (source_app, bundle_id) = frontmost_application();
        let trusted = accessibility_status(false);
        let mut window_title = String::new();
        if trusted {
            let (text, title, secure) = unsafe { accessibility_selection() };
            window_title = title;
            if secure {
                return SelectionCapture {
                    source_app,
                    bundle_id,
                    window_title,
                    ..SelectionCapture::empty(true, "出于安全原因，Poppy 不读取受保护输入框。")
                };
            }
            if !text.trim().is_empty() {
                let (text, truncated) = truncate(text);
                return SelectionCapture {
                    text,
                    source_app,
                    bundle_id,
                    window_title,
                    capture_method: "accessibility".to_string(),
                    truncated,
                    accessibility_trusted: true,
                    error: String::new(),
                };
            }
            if let Some(text) = clipboard_copy_selection() {
                let (text, truncated) = truncate(text);
                return SelectionCapture {
                    text,
                    source_app,
                    bundle_id,
                    window_title,
                    capture_method: "clipboard".to_string(),
                    truncated,
                    accessibility_trusted: true,
                    error: String::new(),
                };
            }
        }
        let error = if trusted {
            "没有读取到选中文字。请重新划选，或在小窗中粘贴文字/拖入文件。"
        } else {
            "需要开启“辅助功能”权限才能自动读取其他应用的选区；也可以直接粘贴或拖入文件。"
        };
        SelectionCapture {
            source_app,
            bundle_id,
            window_title,
            ..SelectionCapture::empty(trusted, error)
        }
    }

    fn frontmost_application() -> (String, String) {
        let workspace = NSWorkspace::sharedWorkspace();
        let Some(application) = workspace.frontmostApplication() else {
            return (String::new(), String::new());
        };
        let name = application
            .localizedName()
            .map(|value| value.to_string())
            .unwrap_or_default();
        let bundle_id = application
            .bundleIdentifier()
            .map(|value| value.to_string())
            .unwrap_or_default();
        (name, bundle_id)
    }

    unsafe fn accessibility_selection() -> (String, String, bool) {
        let system = unsafe { AXUIElementCreateSystemWide() };
        if system.is_null() {
            return (String::new(), String::new(), false);
        }
        let app = unsafe { copy_attribute(system, "AXFocusedApplication") };
        let focused = if app.is_null() {
            ptr::null()
        } else {
            unsafe { copy_attribute(app, "AXFocusedUIElement") }
        };
        let window = if app.is_null() {
            ptr::null()
        } else {
            unsafe { copy_attribute(app, "AXFocusedWindow") }
        };
        let subrole = if focused.is_null() {
            String::new()
        } else {
            unsafe { copy_string_attribute(focused, "AXSubrole") }
        };
        let secure = subrole == "AXSecureTextField";
        let selected = if focused.is_null() || secure {
            String::new()
        } else {
            unsafe { copy_string_attribute(focused, "AXSelectedText") }
        };
        let title = if window.is_null() {
            String::new()
        } else {
            unsafe { copy_string_attribute(window, "AXTitle") }
        };
        if !window.is_null() {
            unsafe { CFRelease(window) };
        }
        if !focused.is_null() {
            unsafe { CFRelease(focused) };
        }
        if !app.is_null() {
            unsafe { CFRelease(app) };
        }
        unsafe { CFRelease(system) };
        (selected, title, secure)
    }

    unsafe fn copy_attribute(element: AXUIElementRef, name: &str) -> CFTypeRef {
        let attribute = NSString::from_str(name);
        let mut value: CFTypeRef = ptr::null();
        let error = unsafe {
            AXUIElementCopyAttributeValue(
                element,
                (&*attribute as *const NSString).cast(),
                &mut value,
            )
        };
        if error == 0 {
            value
        } else {
            ptr::null()
        }
    }

    unsafe fn copy_string_attribute(element: AXUIElementRef, name: &str) -> String {
        let value = unsafe { copy_attribute(element, name) };
        if value.is_null() {
            return String::new();
        }
        let text = unsafe { cf_string(value) };
        unsafe { CFRelease(value) };
        text
    }

    unsafe fn cf_string(value: CFTypeRef) -> String {
        if unsafe { CFGetTypeID(value) } != unsafe { CFStringGetTypeID() } {
            return String::new();
        }
        let length = unsafe { CFStringGetLength(value) };
        let capacity =
            unsafe { CFStringGetMaximumSizeForEncoding(length, K_CF_STRING_ENCODING_UTF8) } + 1;
        if capacity <= 1 {
            return String::new();
        }
        let mut buffer = vec![0_u8; capacity as usize];
        if !unsafe {
            CFStringGetCString(
                value,
                buffer.as_mut_ptr().cast(),
                capacity,
                K_CF_STRING_ENCODING_UTF8,
            )
        } {
            return String::new();
        }
        let end = buffer
            .iter()
            .position(|byte| *byte == 0)
            .unwrap_or(buffer.len());
        String::from_utf8_lossy(&buffer[..end]).into_owned()
    }

    type PasteboardSnapshot = Vec<Vec<(String, Vec<u8>)>>;

    fn clipboard_copy_selection() -> Option<String> {
        let pasteboard = NSPasteboard::generalPasteboard();
        let snapshot = snapshot_pasteboard(&pasteboard);
        let original_change = pasteboard.changeCount();
        send_copy_event();
        let deadline = Instant::now() + Duration::from_millis(250);
        while pasteboard.changeCount() == original_change && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        let text = pasteboard
            .stringForType(unsafe { NSPasteboardTypeString })
            .map(|value| value.to_string())
            .filter(|value| !value.trim().is_empty());
        restore_pasteboard(&pasteboard, snapshot);
        text
    }

    fn snapshot_pasteboard(pasteboard: &NSPasteboard) -> PasteboardSnapshot {
        pasteboard
            .pasteboardItems()
            .map(|items| {
                items
                    .to_vec()
                    .into_iter()
                    .map(|item| {
                        item.types()
                            .to_vec()
                            .into_iter()
                            .filter_map(|data_type| {
                                item.dataForType(&data_type)
                                    .map(|data| (data_type.to_string(), data.to_vec()))
                            })
                            .collect()
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    fn restore_pasteboard(pasteboard: &NSPasteboard, snapshot: PasteboardSnapshot) {
        pasteboard.clearContents();
        if snapshot.is_empty() {
            return;
        }
        let items = snapshot
            .into_iter()
            .map(|types| {
                let item = NSPasteboardItem::new();
                for (data_type, bytes) in types {
                    let name = NSString::from_str(&data_type);
                    let data = NSData::with_bytes(&bytes);
                    item.setData_forType(&data, &name);
                }
                item
            })
            .collect::<Vec<_>>();
        let protocol_items = items
            .into_iter()
            .map(ProtocolObject::<dyn NSPasteboardWriting>::from_retained)
            .collect::<Vec<_>>();
        let array = NSArray::from_retained_slice(&protocol_items);
        pasteboard.writeObjects(&array);
    }

    fn send_copy_event() {
        unsafe {
            let key_down = CGEventCreateKeyboardEvent(ptr::null(), KEY_CODE_C, true);
            let key_up = CGEventCreateKeyboardEvent(ptr::null(), KEY_CODE_C, false);
            if !key_down.is_null() {
                CGEventSetFlags(key_down, K_CG_EVENT_FLAG_MASK_COMMAND);
                CGEventPost(K_CG_HID_EVENT_TAP, key_down);
                CFRelease(key_down);
            }
            if !key_up.is_null() {
                CGEventSetFlags(key_up, K_CG_EVENT_FLAG_MASK_COMMAND);
                CGEventPost(K_CG_HID_EVENT_TAP, key_up);
                CFRelease(key_up);
            }
        }
    }

    fn truncate(value: String) -> (String, bool) {
        let mut chars = value.chars();
        let text = chars.by_ref().take(MAX_SELECTION_CHARS).collect::<String>();
        let truncated = chars.next().is_some();
        (text, truncated)
    }
}

#[cfg(target_os = "macos")]
pub use platform::{accessibility_status, capture as capture_frontmost_selection};

#[cfg(not(target_os = "macos"))]
pub fn accessibility_status(_prompt: bool) -> bool {
    false
}

#[cfg(not(target_os = "macos"))]
pub fn capture_frontmost_selection() -> SelectionCapture {
    SelectionCapture::empty(false, "自动读取选区目前只支持 macOS。")
}
