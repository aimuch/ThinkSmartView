#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod home_assistant;
mod camera;
mod credentials;
mod network;
mod placement;
mod update;
mod window;
mod update_manifest;
use camera::*;
use credentials::*;
use home_assistant::{ha_execute, ha_snapshot};
use network::*;
use placement::{
    drag_settle_finished, fit_panel_height, key_state_is_down, move_is_user_driven,
    move_suppression_deadline, place, primary_button_virtual_key, resized_top, taskbar_edge,
    PanelAnchor, PanelPlacement, PlacementInput, Rect, DRAG_SETTLE_MS,
};
use update::*;
use window::*;

#[cfg(test)]
static ORIGIN_TEST_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use ring::digest::{digest, SHA256};
use ring::rand::{SecureRandom, SystemRandom};
use ring::signature::{UnparsedPublicKey, ED25519};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::str::FromStr;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{LazyLock, RwLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::{
    menu::{Menu, MenuBuilder},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Manager, Monitor, PhysicalPosition, PhysicalSize, WebviewWindow, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

/// Keeps spawned helper processes (go2rtc, ffmpeg) from flashing a console window.
#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

struct TrayAndHotkeyState {
    active_shortcut: Option<Shortcut>,
    shortcut_label: Option<String>,
    update_version: Option<String>,
}

static TRAY_AND_HOTKEY_STATE: LazyLock<RwLock<TrayAndHotkeyState>> = LazyLock::new(|| {
    RwLock::new(TrayAndHotkeyState {
        active_shortcut: None,
        shortcut_label: Some("Alt+M".to_string()),
        update_version: None,
    })
});

fn is_standalone_function_key(code: Code) -> bool {
    matches!(
        code,
        Code::F1
            | Code::F2
            | Code::F3
            | Code::F4
            | Code::F5
            | Code::F6
            | Code::F7
            | Code::F8
            | Code::F9
            | Code::F10
            | Code::F11
            | Code::F12
            | Code::F13
            | Code::F14
            | Code::F15
            | Code::F16
            | Code::F17
            | Code::F18
            | Code::F19
            | Code::F20
    )
}

/// Validates and parses a global shortcut string (#43).
/// Requires at least one modifier (`Ctrl`/`Alt`/`Shift`/`Super`) or a standalone `F1`–`F20` key.
fn parse_global_shortcut(raw: &str) -> Result<(Shortcut, String), String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err("快捷键不能为空".to_string());
    }
    let shortcut = Shortcut::from_str(trimmed)
        .map_err(|_| format!("快捷键格式无效：{trimmed}"))?;
    if shortcut.mods.is_empty() && !is_standalone_function_key(shortcut.key) {
        return Err("快捷键需要包含 Ctrl / Alt / Shift / Win 修饰键，或使用 F1–F20".to_string());
    }
    Ok((shortcut, trimmed.to_string()))
}

/// Windows release page opened from the tray/update row (#39).
const RELEASE_PAGE_URL: &str = "https://github.com/jryang1997/MenuBarMijia/releases/latest";

/// Offline Ed25519 public key (32-byte raw, base64) shared with `UpdateChecker.updateSigningPublicKeyBase64` (ADR-0005).
const UPDATE_SIGNING_PUBLIC_KEY_BASE64: &str = "rLwWXuP0sOZvgd/KusLTTmBXN9DAZ9EzPBNhFSn9yUo=";

/// Optional second key trusted during a rotation; empty when unused.
/// See `docs/security/update-signing-key-rotation.md`.
const UPDATE_SIGNING_OVERLAP_PUBLIC_KEY_BASE64: &str = "";


fn now_epoch_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

fn tray_labels_for_shortcut(shortcut_label: Option<&str>) -> (String, String) {
    match shortcut_label.map(str::trim).filter(|s| !s.is_empty()) {
        Some(label) => (
            format!("打开/隐藏控制中心 ({label})"),
            format!("MenuBarMijia · 菜单栏米家 ({label})"),
        ),
        None => (
            "打开/隐藏控制中心".to_string(),
            "MenuBarMijia · 菜单栏米家".to_string(),
        ),
    }
}

/// Rebuilds the tray menu, adding the "有新版本" entry only when a release is known (#39)
/// and showing the active global shortcut when enabled (#43).
fn build_tray_menu(
    app: &AppHandle,
    shortcut_label: Option<&str>,
    update_version: Option<&str>,
) -> tauri::Result<Menu<tauri::Wry>> {
    let (toggle_text, _) = tray_labels_for_shortcut(shortcut_label);
    let mut builder = MenuBuilder::new(app).text("toggle", toggle_text);
    if let Some(version) = update_version {
        builder = builder.text("update", format!("有新版本 v{version}，点击查看"));
    }
    builder.text("quit", "退出 MenuBarMijia").build()
}

fn refresh_tray_chrome(app: &AppHandle, shortcut_label: Option<&str>, update_version: Option<&str>) {
    let Some(tray) = app.tray_by_id("main-tray") else {
        return;
    };
    let (_, tooltip) = tray_labels_for_shortcut(shortcut_label);
    let _ = tray.set_tooltip(Some(tooltip));
    if let Ok(menu) = build_tray_menu(app, shortcut_label, update_version) {
        let _ = tray.set_menu(Some(menu));
    }
}

/// Applies the user's global shortcut preference (#43), adhering to Coding Standard #3:
/// disabling immediately unregisters the system-wide hotkey; enabling or changing unregisters
/// any previous hotkey before registering the new one and surfaces conflicts as `Err`.
#[tauri::command]
fn set_global_shortcut(app: AppHandle, enabled: bool, shortcut: String) -> Result<(), String> {
    let (old_shortcut, update_version) = {
        let guard = TRAY_AND_HOTKEY_STATE
            .read()
            .map_err(|_| "快捷键状态不可用".to_string())?;
        (guard.active_shortcut, guard.update_version.clone())
    };

    if !enabled {
        if let Some(old) = old_shortcut {
            let _ = app.global_shortcut().unregister(old);
        }
        if let Ok(mut guard) = TRAY_AND_HOTKEY_STATE.write() {
            guard.active_shortcut = None;
            guard.shortcut_label = None;
        }
        refresh_tray_chrome(&app, None, update_version.as_deref());
        return Ok(());
    }

    let (parsed, label) = parse_global_shortcut(&shortcut)?;
    if old_shortcut == Some(parsed) && app.global_shortcut().is_registered(parsed) {
        if let Ok(mut guard) = TRAY_AND_HOTKEY_STATE.write() {
            guard.shortcut_label = Some(label.clone());
        }
        refresh_tray_chrome(&app, Some(&label), update_version.as_deref());
        return Ok(());
    }

    if let Some(old) = old_shortcut {
        let _ = app.global_shortcut().unregister(old);
    }

    if let Err(err) = app.global_shortcut().register(parsed) {
        if let Ok(mut guard) = TRAY_AND_HOTKEY_STATE.write() {
            guard.active_shortcut = None;
            guard.shortcut_label = None;
        }
        refresh_tray_chrome(&app, None, update_version.as_deref());
        return Err(format!("这个组合已被其他应用占用或无法注册（{err}），换一个再试。"));
    }

    if let Ok(mut guard) = TRAY_AND_HOTKEY_STATE.write() {
        guard.active_shortcut = Some(parsed);
        guard.shortcut_label = Some(label.clone());
    }
    refresh_tray_chrome(&app, Some(&label), update_version.as_deref());
    Ok(())
}

fn main() {
    let default_shortcut = Shortcut::new(Some(Modifiers::ALT), Code::KeyM);
    if let Ok(mut guard) = TRAY_AND_HOTKEY_STATE.write() {
        guard.active_shortcut = Some(default_shortcut);
        guard.shortcut_label = Some("Alt+M".to_string());
    }

    tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, shortcut, event| {
                    if event.state() != ShortcutState::Pressed {
                        return;
                    }
                    let matches = TRAY_AND_HOTKEY_STATE
                        .read()
                        .ok()
                        .and_then(|guard| guard.active_shortcut)
                        .is_some_and(|active| &active == shortcut);
                    if matches {
                        toggle_main_window(app, None, false);
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            ha_snapshot,
            ha_execute,
            native_http,
            set_allowed_origins,
            set_service_endpoints,
            secure_store_get,
            secure_store_set,
            secure_store_delete,
            hide_panel,
            quit_app,
            set_panel_height,
            set_pin_popover,
            panel_placement,
            set_panel_placement,
            start_panel_drag,
            set_update_available,
            set_global_shortcut,
            open_release_page,
            install_update,
            start_xiaomi_camera_bridge,
            bridge_hls_camera_stream,
            stop_xiaomi_camera_bridge,
            go2rtc_endpoint,
            go2rtc_jpeg_frame
        ])
        .setup(move |app| {
            sweep_stale_update_installers();
            if let Some(path) = authorization_store_path() {
                if let Ok(mut guard) = EXTRA_ALLOWED_ORIGINS.write() {
                    *guard = read_origin_store(&path);
                }
            }
            let _ = app.global_shortcut().register(default_shortcut);
            load_panel_placement();
            if let Some(main_win) = app.get_webview_window("main") {
                position_popover(&main_win, None);
                let win_for_event = main_win.clone();
                let app_for_events = app.handle().clone();
                main_win.on_window_event(move |event| match event {
                    WindowEvent::Focused(false) => {
                        if !PIN_POPOVER.load(Ordering::SeqCst) {
                            LAST_FOCUS_LOSS_MS.store(now_epoch_ms(), Ordering::SeqCst);
                            let _ = win_for_event.hide();
                        }
                    }
                    // Drag-to-move: a header press hands over to the OS move loop, and the resulting
                    // moves snap to the nearest work-area corner once the drag settles (#204).
                    WindowEvent::Moved(_) => note_user_move(&app_for_events),
                    _ => {}
                });
            }

            let menu = build_tray_menu(app.handle(), Some("Alt+M"), None)?;

            let mut tray_builder = TrayIconBuilder::with_id("main-tray")
                .tooltip("MenuBarMijia · 菜单栏米家 (Alt+M)")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "toggle" => toggle_main_window(app, None, false),
                    "update" => {
                        if let Some(window) = app.get_webview_window("main") {
                            PIN_POPOVER.store(true, Ordering::SeqCst);
                            position_popover(&window, None);
                            let _ = window.show();
                            let _ = window.set_focus();
                            let _ = window.eval(
                                "window.dispatchEvent(new CustomEvent('menumijia:open-update'))",
                            );
                        } else if let Ok(url) = validated_release_url(RELEASE_PAGE_URL) {
                            open_url_in_browser(&url);
                        }
                    }
                    "quit" => {
                        shutdown_camera_daemons();
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        position,
                        ..
                    } = event
                    {
                        toggle_main_window(tray.app_handle(), Some(position), true);
                    }
                });

            if let Some(icon) = app.default_window_icon() {
                tray_builder = tray_builder.icon(icon.clone());
            }

            let _tray = tray_builder.build(app)?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running MenuBarMijia Windows application");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sidecar_reserves_the_complete_expanded_footprint_at_each_scale() {
        for scale in [1.0, 1.25, 1.5, 2.0] {
            assert!(panel_sidecar_on_left(1178.0 * scale, 246.0 * scale, scale));
            assert!(!panel_sidecar_on_left(1178.0 * scale, 252.0 * scale, scale));
            assert!(panel_sidecar_on_left(244.0 * scale, 251.0 * scale, scale));
        }
    }



    fn extra(origins: &[&str]) -> HashSet<String> {
        origins
            .iter()
            .filter_map(|origin| normalize_extra_origin(origin))
            .collect()
    }

    fn allows(url: &str, origins: &[&str]) -> bool {
        let parsed = reqwest::Url::parse(url).expect("test URL");
        check_url_allowed(&parsed, &extra(origins)).is_ok()
    }

    #[test]
    fn native_http_invoke_rejects_an_unconfigured_host() {
        let app = tauri::test::mock_builder()
            .invoke_handler(tauri::generate_handler![native_http])
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .expect("isolated IPC application");
        let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
            .build()
            .expect("isolated IPC window");
        let response = tauri::test::get_ipc_response(
            &window,
            tauri::webview::InvokeRequest {
                cmd: "native_http".into(),
                callback: tauri::ipc::CallbackFn(0),
                error: tauri::ipc::CallbackFn(1),
                url: "http://tauri.localhost".parse().expect("local app origin"),
                body: tauri::ipc::InvokeBody::Json(serde_json::json!({
                    "url": "https://not-configured.invalid/private",
                    "method": "POST",
                    "headers": {"Authorization": "Bearer isolated-test-value"},
                    "body": "private-data",
                    "timeoutMs": 100
                })),
                headers: Default::default(),
                invoke_key: tauri::test::INVOKE_KEY.to_string(),
            },
        );
        let error = response.expect_err("unconfigured hosts must fail before networking");
        let message = error.as_str().expect("policy error returned to the frontend");
        assert!(message.contains("not-configured.invalid"), "unexpected IPC error: {message}");
        assert!(message.contains("拒绝访问未授权来源"), "request reached transport: {message}");
    }

    #[test]
    fn allows_only_xiaomi_api_and_configured_origins() {
        assert!(allows("https://api.io.mi.com/app/home/device_list", &[]));
        assert!(allows("https://de.api.io.mi.com/app/home/device_list", &[]));
        assert!(allows(
            "https://account.xiaomi.com/longPolling/loginUrl",
            &[]
        ));
        assert!(allows("https://lp.account.xiaomi.com/lp/s?d=x", &[]));
        assert!(allows("https://miot-spec.org/miot-spec-v2/instance", &[]));
        assert!(allows(
            "https://api.deepseek.com/v1/chat/completions",
            &["https://api.deepseek.com"]
        ));
        assert!(allows("http://192.168.1.8:8123/api/", &["http://192.168.1.8:8123"]));

        assert!(!allows("https://evil.example/steal", &[]));
        assert!(!allows("https://api.io.mi.com.evil.example/app", &[]));
        assert!(!allows(
            "https://api.deepseek.com.evil.example/v1",
            &["https://api.deepseek.com"]
        ));
        assert!(!allows("file:///C:/Windows/win.ini", &[]));
    }

    #[test]
    fn built_in_services_reject_plaintext_and_alternate_ports() {
        assert!(!allows("http://api.io.mi.com/app/private", &[]));
        assert!(!allows("https://api.io.mi.com:8443/app/private", &[]));
        assert!(!allows("http://account.xiaomi.com/pass/serviceLogin", &[]));
        assert!(!allows("http://api.io.mi.com/app/private", &["http://api.io.mi.com"]));
        assert!(!allows("https://api.io.mi.com:8443/app/private", &["https://api.io.mi.com:8443"]));
    }

    #[test]
    fn configured_origins_keep_scheme_and_effective_port() {
        assert!(allows("https://api.deepseek.com/v1", &["https://api.deepseek.com:443"]));
        assert!(!allows("http://api.deepseek.com/v1", &["https://api.deepseek.com"]));
        assert!(!allows("https://api.deepseek.com:8443/v1", &["https://api.deepseek.com"]));
        assert!(allows("http://localhost:11434/api", &["http://localhost:11434"]));
        assert!(!allows("http://localhost:8123/api", &["http://localhost:11434"]));
        assert!(allows("http://[::1]:11434/api", &["http://[::1]:11434"]));
        assert!(!allows("https://user@api.io.mi.com/app", &[]));
    }

    #[test]
    fn normalizes_configured_origins_and_rejects_non_origins() {
        assert_eq!(normalize_extra_origin(" HTTPS://API.DeepSeek.com.:443/ "), Some("https://api.deepseek.com:443".to_string()));
        assert_eq!(normalize_extra_origin("http://[::1]:11434"), Some("http://[::1]:11434".to_string()));
        for invalid in ["api.deepseek.com", "https://evil.example/path", "https://evil.example?key=value", "https://evil.example#fragment", "https://user@evil.example", "file:///tmp/config", ""] {
            assert_eq!(normalize_extra_origin(invalid), None, "{invalid}");
        }
    }

    #[test]
    fn redirects_cannot_change_origin_even_to_another_allowed_origin() {
        let origins = extra(&["https://configured.example", "http://configured.example", "https://configured.example:8443"]);
        let original = reqwest::Url::parse("https://configured.example/start").unwrap();
        let original_origin = url_origin(&original).unwrap();
        assert!(redirect_allowed(&original_origin, &reqwest::Url::parse("https://configured.example:443/next").unwrap(), &origins));
        for target in ["http://configured.example/next", "https://configured.example:8443/next", "https://api.io.mi.com/app"] {
            assert!(!redirect_allowed(&original_origin, &reqwest::Url::parse(target).unwrap(), &origins), "{target}");
        }
    }

    #[test]
    fn credential_names_are_limited_to_known_entries() {
        assert_eq!(
            allowed_credential_name("mi_session").ok(),
            Some("mi_session")
        );
        assert_eq!(allowed_credential_name("ha_token").ok(), Some("ha_token"));
        assert_eq!(
            allowed_credential_name("assistant_api_key").ok(),
            Some("assistant_api_key")
        );
        assert_eq!(
            allowed_credential_name("assistant_api_key_openrouter").ok(),
            Some("assistant_api_key_openrouter")
        );
        assert!(allowed_credential_name("arbitrary_key").is_err());
        assert!(allowed_credential_name("assistant_api_key_").is_err());
        assert!(allowed_credential_name("assistant_api_key_a/b").is_err());
    }

    #[test]
    fn allows_the_public_github_release_api_only() {
        assert!(allows(
            "https://api.github.com/repos/jryang1997/MenuBarMijia/releases/latest",
            &[]
        ));
        // The browser-facing host is opened through the OS, never proxied by native_http.
        assert!(!allows("https://github.com/jryang1997/MenuBarMijia/releases", &[]));
        assert!(!allows("https://api.github.com.evil.example/latest", &[]));
    }

    #[test]
    fn installer_asset_payload_matches_the_windows_frontend_contract() {
        let raw = include_str!("../../tests/fixtures/windows-installer-asset.json");
        let payload: InstallerAssetPayload =
            serde_json::from_str(raw).expect("the Windows frontend installer payload must deserialize");
        assert_eq!(payload.name, "MenuBarMijia-9.9.9-windows-x64-setup.exe");
        assert_eq!(
            payload.download_url,
            "https://github.com/jryang1997/MenuBarMijia/releases/download/v9.9.9/MenuBarMijia-9.9.9-windows-x64-setup.exe"
        );
        assert_eq!(payload.size_bytes, 3_145_728);
        assert_eq!(
            payload.manifest_url,
            "https://github.com/jryang1997/MenuBarMijia/releases/download/v9.9.9/MenuBarMijia-9.9.9.update.json"
        );
    }

    #[test]
    fn only_opens_menu_barmijia_release_pages() {
        assert!(validated_release_url(RELEASE_PAGE_URL).is_ok());
        assert!(validated_release_url(
            "https://github.com/jryang1997/MenuBarMijia/releases/tag/v1.0.7"
        )
        .is_ok());

        assert!(validated_release_url("http://github.com/jryang1997/MenuBarMijia/releases").is_err());
        assert!(validated_release_url("https://evil.example/jryang1997/MenuBarMijia/releases").is_err());
        assert!(validated_release_url("https://github.com/other/repo/releases").is_err());
        assert!(validated_release_url("https://github.com/jryang1997/MenuBarMijia").is_err());
        assert!(validated_release_url("javascript:alert(1)").is_err());
    }

    #[test]
    fn frontend_cannot_grant_an_arbitrary_origin_and_then_request_it() {
        let _guard = ORIGIN_TEST_LOCK.lock().unwrap();
        {
            let mut guard = EXTRA_ALLOWED_ORIGINS.write().expect("allowlist lock");
            guard.clear();
        }
        let app = tauri::test::mock_builder()
            .invoke_handler(tauri::generate_handler![native_http, set_allowed_origins, set_service_endpoints])
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .expect("isolated IPC application");
        let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
            .build()
            .expect("isolated IPC window");
        let invoke_url: reqwest::Url = "http://tauri.localhost".parse().expect("local app origin");
        let _grant = tauri::test::get_ipc_response(
            &window,
            tauri::webview::InvokeRequest {
                cmd: "set_allowed_origins".into(),
                callback: tauri::ipc::CallbackFn(0),
                error: tauri::ipc::CallbackFn(1),
                url: invoke_url.clone(),
                body: tauri::ipc::InvokeBody::Json(serde_json::json!({
                    "origins": ["https://evil.example"]
                })),
                headers: Default::default(),
                invoke_key: tauri::test::INVOKE_KEY.to_string(),
            },
        );
        let service = tauri::test::get_ipc_response(
            &window,
            tauri::webview::InvokeRequest {
                cmd: "set_service_endpoints".into(),
                callback: tauri::ipc::CallbackFn(0),
                error: tauri::ipc::CallbackFn(1),
                url: invoke_url.clone(),
                body: tauri::ipc::InvokeBody::Json(serde_json::json!({
                    "assistantUrl": "https://evil.example",
                    "haUrl": "",
                    "assistantIsLocalNetwork": false,
                    "haIsLocalNetwork": false
                })),
                headers: Default::default(),
                invoke_key: tauri::test::INVOKE_KEY.to_string(),
            },
        );
        let response = tauri::test::get_ipc_response(
            &window,
            tauri::webview::InvokeRequest {
                cmd: "native_http".into(),
                callback: tauri::ipc::CallbackFn(0),
                error: tauri::ipc::CallbackFn(1),
                url: invoke_url,
                body: tauri::ipc::InvokeBody::Json(serde_json::json!({
                    "url": "https://evil.example/steal",
                    "method": "POST",
                    "headers": {"Authorization": "Bearer isolated-test-value"},
                    "body": "private-data",
                    "timeoutMs": 100
                })),
                headers: Default::default(),
                invoke_key: tauri::test::INVOKE_KEY.to_string(),
            },
        );
        EXTRA_ALLOWED_ORIGINS.write().expect("allowlist lock").clear();
        let service_error = service.expect_err("service endpoints must ask before authorizing");
        let service_message = service_error.as_str().expect("policy error returned to the frontend");
        assert!(
            service_message.contains("未授权"),
            "endpoint command rejected the new arguments: {service_message}"
        );
        let error = response.expect_err("a frontend grant must not make native_http reach evil.example");
        let message = error.as_str().expect("policy error returned to the frontend");
        assert!(
            message.contains("拒绝访问未授权来源"),
            "frontend grant reached transport: {message}"
        );
        assert!(message.contains("evil.example"), "unexpected IPC error: {message}");
    }

    #[test]
    fn confirmation_authorizes_only_the_configured_service_origins() {
        let empty = HashSet::new();
        let granted = decide_service_origins(
            &empty,
            "HTTPS://API.DeepSeek.com:8443/v1",
            "http://[::1]:8123/api",
            false,
            true,
            |_, _| true,
        )
        .expect("confirmed endpoints authorize");
        assert!(check_url_allowed(
            &reqwest::Url::parse("https://api.deepseek.com:8443/v1/chat/completions").unwrap(),
            &granted
        )
        .is_ok());
        assert!(check_url_allowed(
            &reqwest::Url::parse("http://[::1]:8123/api/states").unwrap(),
            &granted
        )
        .is_ok());
        assert!(check_url_allowed(
            &reqwest::Url::parse("https://evil.example/steal").unwrap(),
            &granted
        )
        .is_err());
        assert!(check_url_allowed(
            &reqwest::Url::parse("http://api.deepseek.com:8443/v1").unwrap(),
            &granted
        )
        .is_err());

        let denied = decide_service_origins(
            &granted,
            "https://evil.example",
            "http://[::1]:8123/api",
            false,
            true,
            |_, _| false,
        );
        assert!(denied.is_err());
        assert!(check_url_allowed(
            &reqwest::Url::parse("https://evil.example/steal").unwrap(),
            &granted
        )
        .is_err());

        let shrunk = decide_service_origins(
            &granted,
            "",
            "http://[::1]:8123/api",
            false,
            true,
            |_, _| false,
        )
        .expect("dropping an endpoint does not need a new grant");
        assert!(check_url_allowed(
            &reqwest::Url::parse("https://api.deepseek.com:8443/v1").unwrap(),
            &shrunk
        )
        .is_err());
        assert!(check_url_allowed(
            &reqwest::Url::parse("http://[::1]:8123/api/states").unwrap(),
            &shrunk
        )
        .is_ok());
    }

    #[test]
    fn confirmation_message_appends_warning_only_when_requested() {
        let base = confirmation_message("https://ha.example.com", false);
        assert!(!base.contains(PLAINTEXT_WARNING));

        let local_http = confirmation_message("http://192.168.1.8:8123", false);
        assert!(!local_http.contains(PLAINTEXT_WARNING));

        let public_http = confirmation_message("http://ha.example.com:8123", true);
        assert!(public_http.contains(PLAINTEXT_WARNING));
        assert!(public_http.starts_with("允许 MenuBarMijia 连接这个地址吗？"));
    }

    #[test]
    fn confirmation_warning_matches_the_shared_local_network_golden() {
        let raw = include_str!("../../../Tests/Fixtures/local_network_endpoint_golden.json");
        let golden: serde_json::Value = serde_json::from_str(raw).expect("LAN endpoint golden");
        let cases = golden["cases"].as_array().expect("golden cases");
        assert!(cases.len() >= 50);
        for case in cases {
            let host = case["host"].as_str().expect("host");
            let is_local = case["isLocalNetwork"].as_bool().expect("isLocalNetwork");
            let url = if host.contains(':') {
                format!("http://[{host}]")
            } else {
                format!("http://{host}")
            };
            let requested = requested_service_origins("", &url, false, is_local)
                .expect("golden endpoint must parse");
            assert!(requested.rejected_assistant.is_none(), "{host}");
            assert_eq!(requested.origins.len(), 1, "{host}");
            let (origin, warning) = requested.origins.iter().next().expect("requested origin");
            let message = confirmation_message(origin, *warning);
            assert_eq!(
                message.contains(PLAINTEXT_WARNING),
                !is_local,
                "plaintext warning mismatch for {host}"
            );
        }
    }

    #[test]
    fn public_plaintext_home_assistant_asks_with_a_warning_but_lan_and_https_do_not() {
        let empty = HashSet::new();
        let prompts = std::cell::RefCell::new(Vec::<(String, String)>::new());
        let confirm = |origin: &str, warning: bool| {
            prompts
                .borrow_mut()
                .push((origin.to_string(), confirmation_message(origin, warning)));
            true
        };

        decide_service_origins(
            &empty,
            "https://api.deepseek.com/v1",
            "http://ha.example.com:8123/api",
            false,
            false,
            confirm,
        )
        .expect("public plaintext Home Assistant is confirmed");
        let messages = prompts.borrow();
        assert!(messages.iter().any(|(origin, message)| {
            origin == "http://ha.example.com:8123" && message.contains(PLAINTEXT_WARNING)
        }));
        assert!(messages.iter().any(|(origin, message)| {
            origin == "https://api.deepseek.com:443" && !message.contains(PLAINTEXT_WARNING)
        }));
        drop(messages);

        prompts.borrow_mut().clear();
        decide_service_origins(
            &empty,
            "https://api.deepseek.com/v1",
            "http://192.168.1.8:8123/api",
            false,
            true,
            confirm,
        )
        .expect("LAN Home Assistant is confirmed");
        let messages = prompts.borrow();
        assert!(messages.iter().any(|(origin, message)| {
            origin == "http://192.168.1.8:8123" && !message.contains(PLAINTEXT_WARNING)
        }));
        drop(messages);

        prompts.borrow_mut().clear();
        decide_service_origins(
            &empty,
            "https://api.deepseek.com/v1",
            "https://ha.example.com/api",
            false,
            false,
            confirm,
        )
        .expect("HTTPS Home Assistant is confirmed");
        assert!(prompts
            .borrow()
            .iter()
            .all(|(_, message)| !message.contains(PLAINTEXT_WARNING)));
    }

    #[test]
    fn public_plaintext_ai_endpoints_are_rejected_without_prompting_their_origin() {
        let prompts = std::cell::RefCell::new(Vec::<(String, bool)>::new());
        let rejected = decide_service_origins(
            &HashSet::new(),
            "http://api.deepseek.com/v1",
            "http://ha.example.com:8123/api",
            false,
            false,
            |origin, warning| {
                prompts.borrow_mut().push((origin.to_string(), warning));
                true
            },
        );
        assert!(rejected.is_err());
        let prompts = prompts.borrow();
        assert!(
            !prompts
                .iter()
                .any(|(origin, _)| origin == "http://api.deepseek.com:80"),
            "public plaintext AI must not open a dialog"
        );
        assert!(
            prompts
                .iter()
                .any(|(origin, warning)| origin == "http://ha.example.com:8123" && *warning),
            "an independently valid Home Assistant endpoint must still be confirmed"
        );
    }

    #[test]
    fn public_plaintext_ai_rejection_does_not_block_a_valid_home_assistant_origin() {
        let _guard = ORIGIN_TEST_LOCK.lock().unwrap();
        {
            let mut guard = EXTRA_ALLOWED_ORIGINS.write().expect("allowlist lock");
            guard.clear();
        }
        let prompts = std::cell::RefCell::new(Vec::<(String, bool)>::new());
        let result = authorize_service_endpoints(
            "http://api.deepseek.com/v1",
            "https://ha.example.com/api",
            false,
            false,
            |origin, warning| {
                prompts.borrow_mut().push((origin.to_string(), warning));
                true
            },
        );
        let allowed = allowed_origins_snapshot().expect("allowlist snapshot");
        EXTRA_ALLOWED_ORIGINS.write().expect("allowlist lock").clear();
        assert!(result.is_err(), "public plaintext AI must still be rejected");
        assert_eq!(
            *prompts.borrow(),
            vec![("https://ha.example.com:443".to_string(), false)]
        );
        assert!(allowed.contains("https://ha.example.com:443"));
        assert!(!allowed.contains("http://api.deepseek.com:80"));
    }

    #[test]
    fn lan_plaintext_ai_endpoints_keep_the_existing_confirmation() {
        let mut prompts: Vec<(String, bool)> = Vec::new();
        decide_service_origins(
            &HashSet::new(),
            "http://192.168.1.8:11434/v1",
            "",
            true,
            false,
            |origin, warning| {
                prompts.push((origin.to_string(), warning));
                true
            },
        )
        .expect("LAN AI endpoint is confirmed");
        assert_eq!(prompts, vec![("http://192.168.1.8:11434".to_string(), false)]);
    }

    #[test]
    fn authorized_origin_store_roundtrips_exact_origins() {
        let dir = std::env::temp_dir().join(format!("menubarmijia-origins-{}", std::process::id()));
        let path = dir.join("authorized-http-origins.json");
        let mut origins = HashSet::new();
        origins.insert("https://api.deepseek.com:8443".to_string());
        origins.insert("http://[::1]:8123".to_string());
        write_origin_store(&path, &origins).expect("write store");
        assert_eq!(read_origin_store(&path), origins);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn panel_placement_roundtrips_and_falls_back_to_auto() {
        let dir = std::env::temp_dir().join(format!("menubarmijia-placement-{}", std::process::id()));
        let path = dir.join("panel-placement.json");
        assert_eq!(read_panel_placement(&path).anchor, PanelAnchor::Auto);
        write_panel_placement(&path, PanelPlacement { anchor: PanelAnchor::TopRight }).expect("write placement");
        assert_eq!(read_panel_placement(&path).anchor, PanelAnchor::TopRight);
        std::fs::write(&path, "{not json").expect("corrupt placement");
        assert_eq!(read_panel_placement(&path).anchor, PanelAnchor::Auto);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn global_shortcut_parser_accepts_modified_keys_and_function_keys_only() {
        let (alt_m, label) = parse_global_shortcut("Alt+M").expect("Alt+M is valid");
        assert_eq!(label, "Alt+M");
        assert_eq!(alt_m, Shortcut::new(Some(Modifiers::ALT), Code::KeyM));

        let (ctrl_shift_k, _) = parse_global_shortcut("Ctrl+Shift+K").expect("Ctrl+Shift+K is valid");
        assert_eq!(
            ctrl_shift_k,
            Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyK)
        );

        let (f8, _) = parse_global_shortcut("F8").expect("standalone F8 is valid");
        assert_eq!(f8, Shortcut::new(None, Code::F8));

        let (ctrl_numpad1, _) = parse_global_shortcut("Ctrl+Numpad1").expect("Ctrl+Numpad1 is valid");
        assert_eq!(ctrl_numpad1, Shortcut::new(Some(Modifiers::CONTROL), Code::Numpad1));

        for invalid in ["", "   ", "M", "1", "Space", "Ctrl+", "NotAKey"] {
            assert!(
                parse_global_shortcut(invalid).is_err(),
                "expected {invalid:?} to be rejected"
            );
        }
    }

    #[test]
    fn tray_labels_include_shortcut_only_when_enabled() {
        assert_eq!(
            tray_labels_for_shortcut(Some("Ctrl+Alt+M")),
            (
                "打开/隐藏控制中心 (Ctrl+Alt+M)".to_string(),
                "MenuBarMijia · 菜单栏米家 (Ctrl+Alt+M)".to_string()
            )
        );
        assert_eq!(
            tray_labels_for_shortcut(None),
            (
                "打开/隐藏控制中心".to_string(),
                "MenuBarMijia · 菜单栏米家".to_string()
            )
        );
    }

    #[test]
    fn validates_release_asset_urls_filenames_and_sha256() {
        assert!(validated_release_asset_url(
            "https://github.com/jryang1997/MenuBarMijia/releases/download/v1.1.4/MenuBarMijia-1.1.4-windows-x64-setup.exe"
        )
        .is_ok());
        assert!(validated_release_asset_url(
            "https://github.com/jryang1997/MenuBarMijia/releases/download/v1.1.4/MenuBarMijia-1.1.4-windows-x64-setup.exe.sha256"
        )
        .is_ok());

        for bad in [
            "http://github.com/jryang1997/MenuBarMijia/releases/download/v1.1.4/setup.exe",
            "https://evil.example/jryang1997/MenuBarMijia/releases/download/v1.1.4/setup.exe",
            "https://github.com/other/repo/releases/download/v1.1.4/setup.exe",
            "https://github.com/jryang1997/MenuBarMijia/releases/tag/v1.1.4",
            "https://github.com/jryang1997/MenuBarMijia/releases/download/v1.1.4/setup.exe?foo=1",
        ] {
            assert!(validated_release_asset_url(bad).is_err(), "{bad}");
        }

        assert!(release_asset_redirect_allowed(
            &reqwest::Url::parse("https://objects.githubusercontent.com/github-production-release-asset/123").unwrap()
        ));
        assert!(!release_asset_redirect_allowed(
            &reqwest::Url::parse("https://evil.example/setup.exe").unwrap()
        ));
        assert!(!release_asset_redirect_allowed(
            &reqwest::Url::parse("http://objects.githubusercontent.com/setup.exe").unwrap()
        ));

        assert_eq!(
            sanitize_installer_filename("MenuBarMijia-1.1.4-windows-x64-setup.exe").ok(),
            Some("MenuBarMijia-1.1.4-windows-x64-setup.exe".to_string())
        );
        for bad_name in [
            "../setup.exe",
            "dir/setup.exe",
            "dir\\setup.exe",
            "C:setup.exe",
            "payload.bat",
            "",
        ] {
            assert!(sanitize_installer_filename(bad_name).is_err(), "{bad_name}");
        }

        let hex = compute_sha256_hex(b"verified-windows-installer");
        assert_eq!(parse_sha256_hex(&format!("sha256:{}", hex.to_uppercase())), Some(hex.clone()));
        assert_eq!(parse_sha256_hex(&format!("{hex}  MenuBarMijia-1.1.4-windows-x64-setup.exe\n")), Some(hex));
        assert_eq!(parse_sha256_hex("sha256:1234"), None);

        // Built-in Ed25519 public key decodes to 32 bytes (ADR-0005).
        assert_eq!(
            decode_base64_strict(UPDATE_SIGNING_PUBLIC_KEY_BASE64).map(|b| b.len()),
            Some(32)
        );
        assert_eq!(
            update_signing_public_keys().first().copied(),
            Some(UPDATE_SIGNING_PUBLIC_KEY_BASE64)
        );
        assert!(update_signing_public_keys().iter().all(|key| !key.is_empty()));
        // Cross-platform CryptoKit Ed25519 signature over `b"verified-windows-installer"`.
        let test_pub_b64 = "IVL40Zt5HSRFMkLhXy6rbLfP+ntqXtMAl5YOBpiB2xI=";
        let test_sig_b64 =
            "KtT4QT0xAahlehckpgTkX9/sMX/HypxQGInJ5/dR9Nl78X8bKzLcRNNLEzDGH6y+W3CRzkGiuf1qgIs6DCQDAQ==";
        assert!(verify_ed25519_signature(
            test_sig_b64,
            test_pub_b64,
            b"verified-windows-installer"
        )
        .is_ok());
        assert!(verify_ed25519_signature(
            test_sig_b64,
            test_pub_b64,
            b"tampered-windows-installer"
        )
        .is_err());
        assert!(verify_ed25519_signature(
            test_sig_b64,
            UPDATE_SIGNING_PUBLIC_KEY_BASE64,
            b"verified-windows-installer"
        )
        .is_err());
        assert!(verify_ed25519_signature("", UPDATE_SIGNING_PUBLIC_KEY_BASE64, b"payload").is_err());
        assert!(verify_ed25519_signature("not-base64!", UPDATE_SIGNING_PUBLIC_KEY_BASE64, b"payload").is_err());

        assert!(elevated_launch_powershell_command(false).contains("-Verb RunAs"));
        assert!(elevated_launch_powershell_command(false).contains("-ErrorAction Stop"));
        assert!(elevated_launch_powershell_command(true).contains("msiexec.exe"));
        assert!(elevated_launch_powershell_command(true).contains("-Verb RunAs"));
    }

    #[test]
    fn go2rtc_config_requires_local_auth_and_ignores_appended_streams() {
        let session = Go2RtcSession {
            api_listen: "127.0.0.1:43123".to_string(),
            rtsp_listen: "127.0.0.1:43124".to_string(),
            username: "muser0001".to_string(),
            password: "mpass0001".to_string(),
            nonce: "mnonce0001".to_string(),
        };
        let ffmpeg = "C:/Users/test/AppData/Local/MenuBarMijia/bin/ffmpeg-silent.exe";
        let written = desired_go2rtc_config(&session, ffmpeg);
        assert!(
            !written.contains("origin:"),
            "go2rtc config must not expose wildcard CORS to arbitrary web pages: {written}"
        );
        assert!(!written.contains('*'));
        assert!(!written.contains("1984"));
        assert!(!written.contains("8554"));
        assert!(written.contains("listen: 127.0.0.1:43123"));
        assert!(written.contains("listen: 127.0.0.1:43124"));
        assert!(written.contains("local_auth: true"));
        assert!(written.contains("username: \"muser0001\""));
        assert!(written.contains("password: \"mpass0001\""));
        assert!(written.contains("nonce: \"mnonce0001\""));
        assert!(written.contains("allow_paths:"));
        assert!(written.contains(&yaml_double_quoted(ffmpeg)));
        assert!(written.contains("webrtc:\n  listen: \"\""));
        assert_eq!(
            basic_authorization_value("muser", "mpass"),
            "Basic bXVzZXI6bXBhc3M="
        );
        assert!(parse_owner(
            "pid=1\napi_listen=127.0.0.1:1984\nrtsp_listen=127.0.0.1:43124\nusername=muser0001\npassword=mpass0001\nnonce=mnonce0001\n"
        )
        .is_none());

        let rewritten_by_go2rtc = format!(
            "{written}\nstreams:\n  cam_raw_1105610385:\n    - xiaomi://127.0.0.1:5000?client_private=secret\n"
        );
        assert_eq!(
            base_go2rtc_config(&rewritten_by_go2rtc),
            base_go2rtc_config(&written)
        );
        assert_ne!(
            base_go2rtc_config(&written.replace("43123", "43125")),
            base_go2rtc_config(&written)
        );
    }

    #[test]
    fn go2rtc_reuses_only_an_authenticated_owned_instance() {
        let owned = Go2RtcOwnershipProbe {
            pid_alive: true,
            authenticated: true,
            nonce_matches: true,
            config_changed: false,
        };
        assert_eq!(decide_go2rtc_action(owned), Go2RtcAction::ReuseOwned);
        assert_eq!(
            decide_go2rtc_action(Go2RtcOwnershipProbe {
                config_changed: true,
                ..owned
            }),
            Go2RtcAction::RestartOwned
        );
        assert_eq!(
            decide_go2rtc_action(Go2RtcOwnershipProbe {
                pid_alive: true,
                authenticated: false,
                nonce_matches: false,
                config_changed: true,
            }),
            Go2RtcAction::SpawnFresh
        );
        assert_eq!(
            decide_go2rtc_action(Go2RtcOwnershipProbe {
                pid_alive: true,
                authenticated: true,
                nonce_matches: false,
                config_changed: false,
            }),
            Go2RtcAction::SpawnFresh
        );
        assert_eq!(
            decide_go2rtc_action(Go2RtcOwnershipProbe {
                pid_alive: false,
                authenticated: true,
                nonce_matches: true,
                config_changed: false,
            }),
            Go2RtcAction::SpawnFresh
        );
        let session = Go2RtcSession {
            api_listen: "127.0.0.1:43123".to_string(),
            rtsp_listen: "127.0.0.1:43124".to_string(),
            username: "muser0001".to_string(),
            password: "mpass0001".to_string(),
            nonce: "mnonce0001".to_string(),
        };
        assert_eq!(
            go2rtc_jpeg_frame_url(&session, "cam_mjpeg_dev_cam").expect("frame url"),
            "http://127.0.0.1:43123/api/frame.jpeg?src=cam_mjpeg_dev_cam"
        );
        assert_eq!(
            go2rtc_mjpeg_stream_url(&session, "cam_mjpeg_dev_cam").expect("stream url"),
            "http://127.0.0.1:43123/api/stream.mjpeg?src=cam_mjpeg_dev_cam"
        );
        assert!(go2rtc_jpeg_frame_url(&session, "http://127.0.0.1:1984/api/config").is_err());
        assert!(go2rtc_jpeg_frame_url(&session, "../api/config").is_err());
        assert!(go2rtc_mjpeg_stream_url(&session, "http://127.0.0.1:1984/api/config").is_err());
        assert!(go2rtc_mjpeg_stream_url(&session, "../api/config").is_err());
        let stranger = Go2RtcSession {
            api_listen: "127.0.0.1:1984".to_string(),
            ..session
        };
        assert!(go2rtc_jpeg_frame_url(&stranger, "cam_mjpeg_dev_cam").is_err());
        assert!(go2rtc_mjpeg_stream_url(&stranger, "cam_mjpeg_dev_cam").is_err());

        let ports = reserve_loopback_ports(2).expect("loopback ports");
        assert_eq!(ports.len(), 2);
        assert_ne!(ports[0], ports[1]);
        for port in ports {
            assert!(!FORBIDDEN_GO2RTC_PORTS.contains(&port));
        }
    }

    #[test]
    fn xiaomi_quality_maps_to_go2rtc_subtype() {
        assert_eq!(xiaomi_camera_subtype(None), Ok("hd"));
        assert_eq!(xiaomi_camera_subtype(Some("auto")), Ok("auto"));
        assert_eq!(xiaomi_camera_subtype(Some("sd")), Ok("sd"));
        assert_eq!(xiaomi_camera_subtype(Some("hd")), Ok("hd"));
        assert_eq!(xiaomi_camera_subtype(Some("max")), Ok("3"));
        assert!(xiaomi_camera_subtype(Some("4k")).is_err());
    }

    #[test]
    fn bundled_component_copy_repairs_a_damaged_copy_and_stays_idempotent() {
        let root = std::env::temp_dir().join(format!("mbm-camera-bin-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let bin = root.join("bin");
        std::fs::create_dir_all(&root).expect("temp dir");
        let src = root.join("go2rtc.exe");
        std::fs::write(&src, b"bundled-component").expect("write source");
        let dest = bin.join("go2rtc.exe");

        copy_bundled_component(&src, &dest).expect("copy missing component");
        assert_eq!(std::fs::read(&dest).expect("read copied file"), b"bundled-component");

        std::fs::write(&dest, b"").expect("truncate destination");
        copy_bundled_component(&src, &dest).expect("repair empty component");
        assert_eq!(std::fs::read(&dest).expect("read repaired file"), b"bundled-component");

        std::fs::write(&dest, b"interrupted").expect("write stale destination");
        copy_bundled_component(&src, &dest).expect("repair stale component");
        assert_eq!(std::fs::read(&dest).expect("read repaired file"), b"bundled-component");

        let installed = std::fs::metadata(&dest)
            .expect("dest metadata")
            .modified()
            .expect("dest mtime");
        copy_bundled_component(&src, &dest).expect("keep identical component");
        assert_eq!(
            std::fs::metadata(&dest)
                .expect("dest metadata")
                .modified()
                .expect("dest mtime"),
            installed,
            "an identical component must not be rewritten"
        );

        let leftovers = std::fs::read_dir(&bin)
            .expect("read bin dir")
            .filter_map(|entry| entry.ok())
            .filter(|entry| entry.file_name().to_string_lossy().contains(".staging-"))
            .count();
        assert_eq!(leftovers, 0, "staging files must not survive a copy");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn bundled_component_copy_reports_a_failed_install_without_leaving_staging_files() {
        let root = std::env::temp_dir().join(format!("mbm-camera-bin-fail-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let bin = root.join("bin");
        std::fs::create_dir_all(&bin).expect("bin dir");
        let src = root.join("go2rtc.exe");
        std::fs::write(&src, b"bundled-component").expect("write source");
        let dest = bin.join("go2rtc.exe");
        // A non-empty directory cannot be replaced by a file, so the install fails after the
        // copy has already written the staging file.
        std::fs::create_dir_all(dest.join("occupied")).expect("blocking directory");

        assert!(
            copy_bundled_component(&src, &dest).is_err(),
            "installing over a directory must fail"
        );
        let leftovers = std::fs::read_dir(&bin)
            .expect("read bin dir")
            .filter_map(|entry| entry.ok())
            .filter(|entry| entry.file_name().to_string_lossy().contains(".staging-"))
            .count();
        assert_eq!(leftovers, 0, "a failed install must not leave staging files");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn component_usability_rejects_empty_and_foreign_copies() {
        let root = std::env::temp_dir().join(format!("mbm-component-usable-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).expect("temp dir");

        let ffmpeg = root.join("ffmpeg-silent.exe");
        assert!(!component_is_usable(&ffmpeg, None), "a missing component is not usable");
        std::fs::write(&ffmpeg, b"").expect("write empty component");
        assert!(!component_is_usable(&ffmpeg, None), "a zero-byte leftover must not count");
        std::fs::write(&ffmpeg, b"helper").expect("write component");
        assert!(component_is_usable(&ffmpeg, None));

        let go2rtc = root.join("go2rtc.exe");
        std::fs::write(&go2rtc, b"v1.1.6-leftover").expect("write foreign build");
        assert!(!component_is_usable(&go2rtc, Some(GO2RTC_EXE_SHA256)));
        let pinned = compute_sha256_hex(b"pinned-build");
        std::fs::write(&go2rtc, b"pinned-build").expect("write pinned build");
        assert!(component_is_usable(&go2rtc, Some(pinned.as_str())));
        assert_eq!(expected_component_sha256("go2rtc.exe"), Some(GO2RTC_EXE_SHA256));
        assert_eq!(expected_component_sha256("ffmpeg-silent.exe"), None);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn install_component_repairs_a_damaged_destination_from_the_bundled_copy() {
        let root = std::env::temp_dir().join(format!("mbm-install-component-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let bin = root.join("bin");
        std::fs::create_dir_all(&bin).expect("bin dir");
        let src = root.join("go2rtc.exe");
        std::fs::write(&src, b"bundled-component").expect("write bundled component");
        let dest = bin.join("go2rtc.exe");
        let expected = Some(compute_sha256_hex(b"bundled-component"));

        std::fs::write(&dest, b"").expect("write empty destination");
        assert_eq!(
            install_component(Some(src.as_path()), &dest, expected.as_deref()),
            Ok(true),
            "an empty file left by an interrupted download must be repaired"
        );
        assert_eq!(std::fs::read(&dest).expect("read repaired file"), b"bundled-component");

        std::fs::write(&dest, b"truncated").expect("write damaged destination");
        assert_eq!(install_component(Some(src.as_path()), &dest, None), Ok(true));
        assert_eq!(std::fs::read(&dest).expect("read repaired file"), b"bundled-component");

        std::fs::write(&src, b"").expect("corrupt the bundled copy");
        std::fs::write(&dest, b"downloaded-build").expect("write working destination");
        assert_eq!(
            install_component(Some(src.as_path()), &dest, None),
            Ok(true),
            "a truncated bundled copy must not replace a working destination"
        );
        assert_eq!(std::fs::read(&dest).expect("read kept file"), b"downloaded-build");

        let unbundled = bin.join("go2rtc.exe");
        let _ = std::fs::remove_file(&unbundled);
        assert_eq!(
            install_component(None, &unbundled, Some(GO2RTC_EXE_SHA256)),
            Ok(false),
            "a missing pinned component must fall back to downloading"
        );
        std::fs::write(&unbundled, b"v1.1.6-leftover").expect("write foreign build");
        assert_eq!(
            install_component(None, &unbundled, Some(GO2RTC_EXE_SHA256)),
            Ok(false),
            "an unverified go2rtc.exe must not short-circuit the download"
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn file_sha256_hex_matches_the_in_memory_digest_across_chunk_boundaries() {
        let root = std::env::temp_dir().join(format!("mbm-file-sha-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).expect("temp dir");
        // 200 KB spans three 64 KB reads, so a chunk dropped at a boundary would change the sum.
        let content: Vec<u8> = (0..200_000u32).map(|index| (index % 251) as u8).collect();
        let path = root.join("component.bin");
        std::fs::write(&path, &content).expect("write component");
        assert_eq!(
            file_sha256_hex(&path).expect("hash component"),
            compute_sha256_hex(&content)
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn bundled_component_lookup_uses_the_camera_bin_resource_folder() {
        let root = std::env::temp_dir().join(format!("mbm-resource-dir-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let camera_bin = root.join(CAMERA_COMPONENT_DIR);
        std::fs::create_dir_all(&camera_bin).expect("resource dir");
        let component = camera_bin.join("go2rtc.exe");
        std::fs::write(&component, b"exe").expect("write component");

        assert_eq!(
            bundled_component_source(&root, "go2rtc.exe"),
            Some(component)
        );
        assert_eq!(bundled_component_source(&root, "missing.exe"), None);
        std::fs::create_dir(camera_bin.join("ffmpeg-silent.exe")).expect("directory placeholder");
        assert_eq!(
            bundled_component_source(&root, "ffmpeg-silent.exe"),
            None,
            "a directory must not be copied as a helper binary"
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    fn build_synthetic_jpeg(app0_payload: &[u8], scan_words: &[u32]) -> Vec<u8> {
        let mut jpeg = vec![0xFF, 0xD8];
        if !app0_payload.is_empty() {
            let seg_len = (app0_payload.len() + 2) as u16;
            jpeg.extend_from_slice(&[0xFF, 0xE0]);
            jpeg.extend_from_slice(&seg_len.to_be_bytes());
            jpeg.extend_from_slice(app0_payload);
        }
        // SOS (FF DA) with 12-byte header segment
        jpeg.extend_from_slice(&[
            0xFF, 0xDA, 0x00, 0x0C, 0x03, 0x01, 0x00, 0x02, 0x11, 0x03, 0x11, 0x00, 0x3F, 0x00,
        ]);
        for &word in scan_words {
            for b in word.to_be_bytes() {
                jpeg.push(b);
                if b == 0xFF {
                    jpeg.push(0x00);
                }
            }
        }
        jpeg.extend_from_slice(&[0xFF, 0xD9]);
        jpeg
    }

    #[test]
    fn mjpeg_stream_parser_extracts_frames_and_ignores_header_eoi_and_truncated_frames() {
        // Embed a fake `FF D9` and `FF D8` inside APP0 metadata before `SOS` to prove header
        // segments do not prematurely terminate or restart the frame.
        let frame1 = build_synthetic_jpeg(
            &[0x11, 0xFF, 0xD9, 0x22, 0xFF, 0xD8, 0x33],
            &[0x1234_5678, 0x9ABC_DEF0],
        );
        // Frame with a spec-valid `0xFF` fill byte immediately preceding `FF D9` (`FF FF D9`).
        let mut frame2 = build_synthetic_jpeg(&[0x33, 0x44], &[0xCAFE_BABE, 0x0FF0_1234]);
        let frame2_eoi_pos = frame2.len() - 2;
        frame2.insert(frame2_eoi_pos, 0xFF);
        let frame3 = build_synthetic_jpeg(&[0x55, 0x66], &[0xDEAD_BEEF, 0xFEED_FACE]);

        let mut stream = Vec::new();
        stream.extend_from_slice(
            format!(
                "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n",
                frame1.len()
            )
            .as_bytes(),
        );
        stream.extend_from_slice(&frame1);
        stream.extend_from_slice(b"\r\n--frame\r\nContent-Type: image/jpeg\r\n\r\n");
        // 1. Insert a frame truncated in its entropy scan ending with `0xFF` right before `frame2`'s `FF D8` (`FF FF D8`).
        stream.extend_from_slice(&[
            0xFF, 0xD8, 0xFF, 0xDA, 0x00, 0x04, 0x00, 0x00, 0x11, 0x22, 0xFF,
        ]);
        stream.extend_from_slice(&frame2);
        stream.extend_from_slice(b"\r\n--frame\r\nContent-Type: image/jpeg\r\n\r\n");
        // 2. Insert a frame truncated in its pre-SOS header followed by multipart boundary text,
        // plus a corrupt header with `seg_len < 2` (`FF DA 00 01`), before `frame3`.
        stream.extend_from_slice(&[0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x04, 0xAA, 0xBB]);
        stream.extend_from_slice(b"\r\n--frame\r\nContent-Type: image/jpeg\r\n\r\n");
        stream.extend_from_slice(&[0xFF, 0xD8, 0xFF, 0xDA, 0x00, 0x01]);
        stream.extend_from_slice(&frame3);
        stream.extend_from_slice(b"\r\n");

        // Feed byte-by-byte / small odd chunks to exercise boundary splits on `FF D8` and `FF D9`.
        let mut buffer = Vec::new();
        let mut extracted = Vec::new();
        for chunk in stream.chunks(7) {
            buffer.extend_from_slice(chunk);
            extracted.extend(drain_complete_jpeg_frames(&mut buffer));
        }
        assert_eq!(extracted, vec![frame1, frame2, frame3]);
    }

    #[test]
    fn concealed_gray_jpeg_detector_distinguishes_flat_error_concealment_from_real_frames() {
        let mut gray_words = vec![0x28A2_8A00u32; 1200];
        // Simulate a ~10% OSD timestamp overlay with non-repeating words.
        for (idx, slot) in gray_words[..120].iter_mut().enumerate() {
            *slot = 0x1357_9BDFu32.wrapping_mul((idx as u32) + 1);
        }
        let concealed_annex_k = build_synthetic_jpeg(&[], &gray_words);
        assert!(is_likely_concealed_gray_jpeg(&concealed_annex_k));

        let concealed_optimal = build_synthetic_jpeg(&[], &vec![0x0000_0000u32; 1200]);
        assert!(is_likely_concealed_gray_jpeg(&concealed_optimal));

        // Simulate a real `ffmpeg-silent.exe` 2960x1666 partial error-concealed frame (~78 KB):
        // ~10-15% of pixels are decoded intra macroblocks (contributing ~8,000 varied words) and
        // ~85-90% of pixels are flat concealed gray (contributing ~6,400 zero words via optimal
        // Huffman tables, so global repetition is 44.4% < 75% but has a massive contiguous flat run).
        let mut partial_concealed_words: Vec<u32> = (0..8000u32)
            .map(|i| (i + 1).wrapping_mul(0x9E37_79B9).rotate_left(11))
            .collect();
        partial_concealed_words.extend(std::iter::repeat_n(0x0000_0000u32, 6400));
        let partial_concealed = build_synthetic_jpeg(&[], &partial_concealed_words);
        assert!(is_likely_concealed_gray_jpeg(&partial_concealed));

        // Simulate a late-GOP inter-predicted P-frame on `chuangmi.camera.061a01` (1920x1080,
        // ~17.9 KB, 4,425 words) right before the 4.0-second IDR keyframe arrives: inter-prediction
        // residuals break up long zero runs so `max_run <= 9`, while `same_as_prev` is ~7.5% and
        // `zero_words` (`0x0000_0000`) is ~15.0%.
        let mut late_gop_concealed_words = Vec::with_capacity(4425);
        for i in 0..4425u32 {
            if i % 10 < 2 && (i / 10) % 4 != 0 {
                late_gop_concealed_words.push(0x0000_0000);
            } else {
                late_gop_concealed_words
                    .push(((i + 1).wrapping_mul(0x9E37_79B9).rotate_left(13) & 0x7F7F_7F7F) | 1);
            }
        }
        let late_gop_concealed = build_synthetic_jpeg(&[], &late_gop_concealed_words);
        assert!(is_likely_concealed_gray_jpeg(&late_gop_concealed));

        // Simulate a real 1920x1080 clean keyframe/P-frame from `chuangmi.camera.061a01` (~50 KB,
        // 12,500 words) with flat wall/ceiling areas producing ~1.5% adjacent word repetition and
        // ~2.2% zero words (`max_run <= 4`).
        let mut room_scene_words = Vec::with_capacity(12500);
        for i in 0..12500u32 {
            if i % 64 == 0 || i % 64 == 1 {
                room_scene_words.push(0x0000_0000);
            } else {
                room_scene_words
                    .push(((i + 1).wrapping_mul(0x85EB_CA6B).rotate_left(9) & 0x7F7F_7F7F) | 1);
            }
        }
        let room_scene_frame = build_synthetic_jpeg(&[], &room_scene_words);
        assert!(!is_likely_concealed_gray_jpeg(&room_scene_frame));

        let varied_words: Vec<u32> = (0..1200u32)
            .map(|i| i.wrapping_mul(0x9E37_79B9).rotate_left(7))
            .collect();
        let clean_frame = build_synthetic_jpeg(&[], &varied_words);
        assert!(!is_likely_concealed_gray_jpeg(&clean_frame));

        // Short repeated runs (e.g. SMPTE color bar segments of 16 words each) must not be
        // misclassified as full-width error-concealment bands.
        let short_bar_words: Vec<u32> = (0..6000u32)
            .map(|i| (i / 16).wrapping_mul(0x85EB_CA6B) ^ ((i % 3) * 0x0101_0101))
            .collect();
        let color_bars_frame = build_synthetic_jpeg(&[], &short_bar_words);
        assert!(!is_likely_concealed_gray_jpeg(&color_bars_frame));

        let tiny_frame = build_synthetic_jpeg(&[], &[0x28A2_8A00u32; 64]);
        assert!(!is_likely_concealed_gray_jpeg(&tiny_frame));
    }

    #[test]
    fn persistent_mjpeg_stream_reader_holds_single_connection_and_skips_initial_gray_frame() {
        use std::io::{Read, Write};

        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind mjpeg server");
        let port = listener.local_addr().expect("local addr").port();
        let accept_count = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let accept_count_server = std::sync::Arc::clone(&accept_count);

        let gray_frame = build_synthetic_jpeg(&[], &vec![0x28A2_8A00u32; 1200]);
        let clean_words_1: Vec<u32> = (0..1200u32)
            .map(|i| (i + 1).wrapping_mul(0x9E37_79B9))
            .collect();
        let clean_words_2: Vec<u32> = (0..1200u32)
            .map(|i| (i + 5000).wrapping_mul(0x85EB_CA6B))
            .collect();
        let clean_frame_1 = build_synthetic_jpeg(&[], &clean_words_1);
        let clean_frame_2 = build_synthetic_jpeg(&[], &clean_words_2);
        let expected_clean_1 = clean_frame_1.clone();
        let expected_clean_2 = clean_frame_2.clone();

        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept stream client");
            accept_count_server.fetch_add(1, Ordering::SeqCst);
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .expect("read timeout");
            let mut req_buf = [0u8; 1024];
            let read_n = stream.read(&mut req_buf).expect("read request");
            let req_text = String::from_utf8_lossy(&req_buf[..read_n]);
            assert!(
                req_text.starts_with("GET /api/stream.mjpeg?src=cam_mjpeg_test HTTP/1.1"),
                "unexpected request line: {req_text}"
            );
            assert!(
                req_text
                    .to_ascii_lowercase()
                    .contains("authorization: basic "),
                "missing Basic auth header: {req_text}"
            );

            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\nCache-Control: no-cache\r\n\r\n",
                )
                .expect("write response headers");

            // Frame #1: concealed gray frame emitted before the first keyframe.
            stream
                .write_all(
                    format!(
                        "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n",
                        gray_frame.len()
                    )
                    .as_bytes(),
                )
                .expect("write gray header");
            stream.write_all(&gray_frame).expect("write gray body");
            stream.write_all(b"\r\n").expect("write gray trailer");
            stream.flush().expect("flush gray");

            std::thread::sleep(Duration::from_millis(40));

            // Frame #2: first clean keyframe on the same persistent connection.
            stream
                .write_all(
                    format!(
                        "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n",
                        clean_frame_1.len()
                    )
                    .as_bytes(),
                )
                .expect("write clean1 header");
            stream.write_all(&clean_frame_1).expect("write clean1 body");
            stream.write_all(b"\r\n").expect("write clean1 trailer");
            stream.flush().expect("flush clean1");

            std::thread::sleep(Duration::from_millis(60));

            // Frame #3: second clean frame on the same persistent connection.
            stream
                .write_all(
                    format!(
                        "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n",
                        clean_frame_2.len()
                    )
                    .as_bytes(),
                )
                .expect("write clean2 header");
            stream.write_all(&clean_frame_2).expect("write clean2 body");
            stream.write_all(b"\r\n").expect("write clean2 trailer");
            stream.flush().expect("flush clean2");

            // Hold the stream open until `stop_mjpeg_streams` drops the client.
            let mut eof_probe = [0u8; 16];
            let _ = stream.read(&mut eof_probe);
        });

        let session = Go2RtcSession {
            api_listen: format!("127.0.0.1:{port}"),
            rtsp_listen: "127.0.0.1:43124".to_string(),
            username: "muser0001".to_string(),
            password: "mpass0001".to_string(),
            nonce: "mnonce0001".to_string(),
        };

        tauri::async_runtime::block_on(async {
            let first = fetch_mjpeg_stream_frame(
                &session,
                "cam_mjpeg_test",
                Duration::from_secs(2),
                Duration::from_millis(500),
            )
            .await
            .expect("first clean frame");
            assert_eq!(first.as_slice(), expected_clean_1.as_slice());

            // Stopping a stale/different camera bridge must not kill `cam_mjpeg_test`'s reader.
            stop_xiaomi_camera_bridge("other_cam".to_string())
                .await
                .expect("stop unrelated camera bridge");

            let second = fetch_mjpeg_stream_frame(
                &session,
                "cam_mjpeg_test",
                Duration::from_secs(2),
                Duration::from_millis(500),
            )
            .await
            .expect("second clean frame");
            assert_eq!(second.as_slice(), expected_clean_2.as_slice());

            stop_xiaomi_camera_bridge("test".to_string())
                .await
                .expect("stop active camera bridge");
        });

        let stop_started = std::time::Instant::now();
        let _ = server.join();
        assert!(
            stop_started.elapsed() < Duration::from_millis(500),
            "stop_mjpeg_streams must immediately abort the persistent HTTP connection instead of waiting for server read_timeout"
        );
        assert_eq!(
            accept_count.load(Ordering::SeqCst),
            1,
            "multiple frame reads must reuse a single persistent /api/stream.mjpeg connection"
        );

        // If a stream emits only flat/gray frames past the grace window, the fallback frame must
        // still be promoted rather than timing out.
        let fallback_listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind fallback");
        let fallback_port = fallback_listener.local_addr().expect("fallback addr").port();
        let gray_only = build_synthetic_jpeg(&[], &vec![0x28A2_8A00u32; 1200]);
        let expected_gray = gray_only.clone();
        let fallback_server = std::thread::spawn(move || {
            let (mut stream, _) = fallback_listener.accept().expect("accept fallback");
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .expect("read timeout");
            let mut req_buf = [0u8; 1024];
            let _ = stream.read(&mut req_buf);
            stream
                .write_all(
                    b"HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n--frame\r\nContent-Type: image/jpeg\r\n\r\n",
                )
                .expect("write fallback header");
            stream.write_all(&gray_only).expect("write fallback gray");
            stream.write_all(b"\r\n").expect("write fallback trailer");
            stream.flush().expect("flush fallback");
            let mut eof_probe = [0u8; 16];
            let _ = stream.read(&mut eof_probe);
        });
        let fallback_session = Go2RtcSession {
            api_listen: format!("127.0.0.1:{fallback_port}"),
            ..session
        };
        tauri::async_runtime::block_on(async {
            let promoted = fetch_mjpeg_stream_frame(
                &fallback_session,
                "cam_mjpeg_flat",
                Duration::from_secs(2),
                Duration::from_millis(40),
            )
            .await
            .expect("fallback gray frame after grace");
            assert_eq!(promoted.as_slice(), expected_gray.as_slice());
            stop_mjpeg_streams(&["cam_mjpeg_flat"]);
        });
        let _ = fallback_server.join();
    }

    #[test]
    fn cs2_command_channel_gate_and_udp_relay_handle_bursts_and_channel_zero_overflow() {
        let mut gate = Cs2CommandChannelGate::new();
        for seq in 0u16..6 {
            let [hi, lo] = seq.to_be_bytes();
            let pkt = [0xF1, 0xD0, 0x00, 0x04, 0xD1, 0x00, hi, lo];
            assert_eq!(gate.should_drop(&pkt), None, "initial cmd seq {seq} must pass");
            // Retransmissions of an allowed sequence must also pass.
            assert_eq!(gate.should_drop(&pkt), None, "retransmit of seq {seq} must pass");
        }
        // 7th unique channel-0 sequence must be dropped before go2rtc and auto-ACKed.
        let overflow_pkt = [0xF1, 0xD0, 0x00, 0x04, 0xD1, 0x00, 0x00, 0x06];
        assert_eq!(gate.should_drop(&overflow_pkt), Some((0x00, 0x06)));
        assert_eq!(
            Cs2CommandChannelGate::drw_ack(0x00, 0x06),
            [0xF1, 0xD1, 0x00, 0x06, 0xD1, 0x00, 0x00, 0x01, 0x00, 0x06]
        );
        // Video channel 1 (0xD1, 0x01) must never be dropped regardless of sequence number.
        for seq in 0u16..32 {
            let [hi, lo] = seq.to_be_bytes();
            let video_pkt = [0xF1, 0xD0, 0x00, 0x04, 0xD1, 0x01, hi, lo];
            assert_eq!(gate.should_drop(&video_pkt), None);
        }
        gate.reset();
        assert_eq!(gate.should_drop(&overflow_pkt), None);

        // End-to-end loopback test of `spawn_cs2_udp_relay`:
        // 1. Forwards >2048-byte video packets without WSAEMSGSIZE truncation.
        // 2. Auto-ACKs the 7th channel-0 DRW packet back to the camera while dropping it before go2rtc.
        if let Ok(cam_discovery_sock) = std::net::UdpSocket::bind("127.0.0.1:32108") {
            cam_discovery_sock
                .set_read_timeout(Some(Duration::from_millis(500)))
                .expect("discovery timeout");
            let (relay_port, stop) =
                spawn_cs2_udp_relay(std::net::Ipv4Addr::LOCALHOST).expect("spawn relay");
            let go2rtc_sock = std::net::UdpSocket::bind("127.0.0.1:0").expect("bind go2rtc mock");
            go2rtc_sock
                .set_read_timeout(Some(Duration::from_millis(500)))
                .expect("go2rtc timeout");
            let cam_sock = std::net::UdpSocket::bind("127.0.0.1:0").expect("bind camera mock");
            cam_sock
                .set_read_timeout(Some(Duration::from_millis(500)))
                .expect("camera timeout");

            go2rtc_sock
                .send_to(&[0xF1, 0x30, 0x00, 0x00], ("127.0.0.1", relay_port))
                .expect("send lan_search");
            let mut probe_buf = [0u8; 64];
            let (_, relay_lan_addr) = cam_discovery_sock
                .recv_from(&mut probe_buf)
                .expect("receive lan_search on 32108");
            drop(cam_discovery_sock);

            // 1. Send a 4096-byte video channel-1 DRW packet (>2048 bytes).
            let mut large_video = vec![0xABu8; 4096];
            large_video[..8].copy_from_slice(&[0xF1, 0xD0, 0x0F, 0xFC, 0xD1, 0x01, 0x00, 0x01]);
            cam_sock
                .send_to(&large_video, relay_lan_addr)
                .expect("send 4096-byte video packet");
            let mut recv_buf = vec![0u8; 8192];
            let (n, _) = go2rtc_sock
                .recv_from(&mut recv_buf)
                .expect("receive 4096-byte video packet at go2rtc");
            assert_eq!(&recv_buf[..n], large_video.as_slice());

            // 2. Send 7 unique channel-0 DRW packets; first 6 reach go2rtc, 7th is dropped and auto-ACKed.
            for seq in 0u16..7 {
                let [hi, lo] = seq.to_be_bytes();
                let pkt = [0xF1, 0xD0, 0x00, 0x04, 0xD1, 0x00, hi, lo];
                cam_sock
                    .send_to(&pkt, relay_lan_addr)
                    .expect("send channel-0 drw");
            }
            for seq in 0u16..6 {
                let (n, _) = go2rtc_sock
                    .recv_from(&mut recv_buf)
                    .unwrap_or_else(|e| panic!("expected channel-0 seq {seq} at go2rtc: {e}"));
                assert_eq!(n, 8);
            }
            let (ack_n, _) = cam_sock
                .recv_from(&mut recv_buf)
                .expect("camera must receive auto DRW_ACK for 7th channel-0 packet");
            assert_eq!(
                &recv_buf[..ack_n],
                &[0xF1, 0xD1, 0x00, 0x06, 0xD1, 0x00, 0x00, 0x01, 0x00, 0x06]
            );

            stop.store(true, Ordering::SeqCst);
        }
    }

    #[test]
    fn native_http_timeout_preserves_timed_out_marker_for_long_polling() {
        let _guard = ORIGIN_TEST_LOCK.lock().unwrap();
        let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind loopback");
        let addr = listener.local_addr().expect("listener addr");
        let server = std::thread::spawn(move || {
            if let Ok((_stream, _peer)) = listener.accept() {
                std::thread::sleep(Duration::from_millis(250));
            }
        });
        let origin = format!("http://127.0.0.1:{}", addr.port());
        {
            let mut guard = EXTRA_ALLOWED_ORIGINS.write().expect("allowlist lock");
            guard.clear();
            guard.insert(origin.clone());
        }
        let app = tauri::test::mock_builder()
            .invoke_handler(tauri::generate_handler![native_http])
            .build(tauri::test::mock_context(tauri::test::noop_assets()))
            .expect("isolated IPC application");
        let window = tauri::WebviewWindowBuilder::new(&app, "main", Default::default())
            .build()
            .expect("isolated IPC window");
        let response = tauri::test::get_ipc_response(
            &window,
            tauri::webview::InvokeRequest {
                cmd: "native_http".into(),
                callback: tauri::ipc::CallbackFn(0),
                error: tauri::ipc::CallbackFn(1),
                url: "http://tauri.localhost".parse().expect("local app origin"),
                body: tauri::ipc::InvokeBody::Json(serde_json::json!({
                    "url": format!("{origin}/lp/s?k=test"),
                    "method": "GET",
                    "timeoutMs": 40
                })),
                headers: Default::default(),
                invoke_key: tauri::test::INVOKE_KEY.to_string(),
            },
        );
        let _ = server.join();
        let error = response.expect_err("slow poll must time out");
        let message = error.as_str().expect("error string").to_ascii_lowercase();
        assert!(
            message.contains("timed out") || message.contains("timeout"),
            "reqwest 0.12 timeout must surface cause to frontend: {message}"
        );
    }
}

