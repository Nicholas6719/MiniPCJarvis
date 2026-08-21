mod credentials;
mod sidecar;

use sidecar::{Sidecar, SidecarInfo};
use std::sync::Arc;
use std::time::Duration;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{Manager, WindowEvent};
use tauri_plugin_autostart::MacosLauncher;
use tauri_plugin_global_shortcut::{Code, Modifiers, Shortcut, ShortcutState};

struct AppState {
    sidecar: Arc<Sidecar>,
}

#[tauri::command]
fn sidecar_info(state: tauri::State<AppState>) -> SidecarInfo {
    state.sidecar.info.clone()
}

#[tauri::command]
fn set_secret(name: String, value: String, state: tauri::State<AppState>) -> Result<(), String> {
    credentials::set_secret(&name, &value)?;
    push_secret_async(state.sidecar.clone(), name, value);
    Ok(())
}

#[tauri::command]
fn has_secret(name: String) -> bool {
    credentials::get_secret(&name).is_some()
}

fn push_secret_async(sc: Arc<Sidecar>, name: String, value: String) {
    tauri::async_runtime::spawn(async move {
        let client = reqwest::Client::new();
        let _ = client
            .post(format!("http://127.0.0.1:{}/secrets", sc.info.port))
            .header("X-Jarvis-Token", &sc.info.token)
            .json(&serde_json::json!({"name": name, "value": value}))
            .timeout(Duration::from_secs(5))
            .send()
            .await;
    });
}

async fn sidecar_post(sc: &Sidecar, path: &str) {
    let client = reqwest::Client::new();
    let _ = client
        .post(format!("http://127.0.0.1:{}{}", sc.info.port, path))
        .header("X-Jarvis-Token", &sc.info.token)
        .timeout(Duration::from_secs(5))
        .send()
        .await;
}

pub fn run() {
    // Production: bundled sidecar sits next to the exe; dev: repo venv fallback.
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()));
    let sc = Arc::new(
        Sidecar::start(exe_dir).expect("failed to start JARVIS sidecar — is it installed?"),
    );

    // After the sidecar is healthy, push stored secrets into it.
    {
        let sc2 = sc.clone();
        tauri::async_runtime::spawn(async move {
            if sc2.wait_healthy(Duration::from_secs(180)).await {
                for name in credentials::KNOWN_SECRETS {
                    if let Some(v) = credentials::get_secret(name) {
                        push_secret_async(sc2.clone(), name.to_string(), v);
                    }
                }
            }
        });
    }

    let sc_for_state = sc.clone();
    let sc_for_hotkey = sc.clone();
    let sc_for_exit = sc.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_shortcut(Shortcut::new(
                    Some(Modifiers::CONTROL | Modifiers::SHIFT),
                    Code::KeyJ,
                ))
                .expect("register hotkey")
                .with_handler(move |app, _shortcut, event| {
                    if event.state == ShortcutState::Pressed {
                        if let Some(win) = app.get_webview_window("main") {
                            let _ = win.show();
                            let _ = win.set_focus();
                        }
                        let sc = sc_for_hotkey.clone();
                        tauri::async_runtime::spawn(async move {
                            sidecar_post(&sc, "/listen/toggle").await;
                        });
                    }
                })
                .build(),
        )
        .manage(AppState {
            sidecar: sc_for_state,
        })
        .invoke_handler(tauri::generate_handler![sidecar_info, set_secret, has_secret])
        .setup(move |app| {
            // --- system tray ---
            let show = MenuItem::with_id(app, "show", "Open JARVIS", true, None::<&str>)?;
            let listen = MenuItem::with_id(app, "listen", "Listen", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Exit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &listen, &quit])?;
            let sc_tray = sc.clone();
            TrayIconBuilder::with_id("jarvis-tray")
                .icon(app.default_window_icon().unwrap().clone())
                .tooltip("JARVIS")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(move |app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(win) = app.get_webview_window("main") {
                            let _ = win.show();
                            let _ = win.set_focus();
                        }
                    }
                    "listen" => {
                        let sc = sc_tray.clone();
                        tauri::async_runtime::spawn(async move {
                            sidecar_post(&sc, "/listen/toggle").await;
                        });
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    use tauri::tray::TrayIconEvent;
                    if let TrayIconEvent::DoubleClick { .. } = event {
                        if let Some(win) = tray.app_handle().get_webview_window("main") {
                            let _ = win.show();
                            let _ = win.set_focus();
                        }
                    }
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Close button hides to tray; Exit lives in the tray menu.
            if let WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building JARVIS")
        .run(move |_app, event| {
            if let tauri::RunEvent::Exit = event {
                sc_for_exit.stop();
            }
        });
}
