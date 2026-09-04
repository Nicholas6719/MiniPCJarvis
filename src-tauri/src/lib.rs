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
    state.sidecar.info()
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
            .post(format!("http://127.0.0.1:{}/secrets", sc.info().port))
            .header("X-Jarvis-Token", &sc.info().token)
            .json(&serde_json::json!({"name": name, "value": value}))
            .timeout(Duration::from_secs(5))
            .send()
            .await;
    });
}

/// Which secrets the sidecar currently holds. It keeps them in memory only, so a
/// restart it did not tell us about leaves it with none — and it then behaves
/// exactly like a machine that never had a key, telling the user to add one that
/// is already in Credential Manager.
async fn sidecar_secret_names(sc: &Sidecar) -> Option<Vec<String>> {
    #[derive(serde::Deserialize)]
    struct Held {
        present: Vec<String>,
    }
    let client = reqwest::Client::new();
    let r = client
        .get(format!("http://127.0.0.1:{}/secrets", sc.info().port))
        .header("X-Jarvis-Token", &sc.info().token)
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .ok()?;
    r.json::<Held>().await.ok().map(|h| h.present)
}

/// Push anything it is missing. Cheap, and the only way a lost key ever comes
/// back without the user restarting the app on a hunch.
async fn reconcile_secrets(sc: &Arc<Sidecar>) {
    let Some(held) = sidecar_secret_names(sc).await else {
        return;
    };
    for name in credentials::KNOWN_SECRETS {
        if held.iter().any(|h| h == name) {
            continue;
        }
        if let Some(v) = credentials::get_secret(name) {
            eprintln!("[jarvis] sidecar was missing {name} — pushing it again");
            push_secret_async(sc.clone(), name.to_string(), v);
        }
    }
}

async fn sidecar_post(sc: &Sidecar, path: &str) {
    let client = reqwest::Client::new();
    let _ = client
        .post(format!("http://127.0.0.1:{}{}", sc.info().port, path))
        .header("X-Jarvis-Token", &sc.info().token)
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

    // Supervisor: if the sidecar process dies, restart it (same port/token)
    // and re-push secrets. Backs off to avoid a crash loop.
    {
        let sc3 = sc.clone();
        tauri::async_runtime::spawn(async move {
            let mut recent_restarts: Vec<std::time::Instant> = Vec::new();
            // A hung sidecar answers nothing but exits nothing either. One
            // missed health check can just be a slow moment (a model loading, a
            // long turn), so it takes three in a row — about a minute — before
            // we call it wedged and rebuild it.
            let mut missed_health = 0u8;
            const WEDGED_AFTER: u8 = 3;
            loop {
                tokio::time::sleep(Duration::from_secs(20)).await;
                if sc3.is_alive() {
                    // Existing is not the same as WORKING. This is the check
                    // that was missing on 2026-08-30, when a deadlocked event
                    // loop left him with a silent assistant for forty minutes
                    // and a supervisor that was perfectly happy about it.
                    if sc3.is_responding().await {
                        missed_health = 0;
                        // Alive is not the same as equipped either: a restart we
                        // did not perform (or one past the give-up budget below)
                        // leaves it running with no secrets at all.
                        reconcile_secrets(&sc3).await;
                        continue;
                    }
                    missed_health = missed_health.saturating_add(1);
                    if missed_health < WEDGED_AFTER {
                        eprintln!(
                            "[jarvis] sidecar not answering ({missed_health}/{WEDGED_AFTER})"
                        );
                        continue;
                    }
                    // Fall through to the restart path below: restart() already
                    // stop()s the whole process tree first, so a wedged sidecar
                    // is torn down and rebuilt exactly like a dead one.
                    eprintln!("[jarvis] sidecar is wedged — rebuilding it");
                }
                missed_health = 0;
                recent_restarts.retain(|t| t.elapsed() < Duration::from_secs(600));
                if recent_restarts.len() >= 3 {
                    // crash-looping — stop trying for this 10-minute window.
                    // SAID OUT LOUD. This used to `continue` in silence, so a
                    // sidecar that died three times left him with a HUD
                    // reading OFFLINE and nothing anywhere saying why or for
                    // how long — the "silent assistant" this supervisor was
                    // rewritten to prevent, one level up.
                    let wait = 600u64.saturating_sub(
                        recent_restarts.iter().map(|t| t.elapsed().as_secs()).max().unwrap_or(0));
                    eprintln!(
                        "[jarvis] sidecar has died {} times in ten minutes — not restarting \
                         it again for about {}s; check %APPDATA%\\JARVIS\\logs\\sidecar.log",
                        recent_restarts.len(), wait);
                    continue;
                }
                eprintln!("[jarvis] sidecar died — restarting");
                if sc3.restart().is_ok() {
                    recent_restarts.push(std::time::Instant::now());
                    if sc3.wait_healthy(Duration::from_secs(180)).await {
                        for name in credentials::KNOWN_SECRETS {
                            if let Some(v) = credentials::get_secret(name) {
                                push_secret_async(sc3.clone(), name.to_string(), v);
                            }
                        }
                    }
                }
            }
        });
    }

    let sc_for_state = sc.clone();
    let sc_for_hotkey = sc.clone();
    let sc_for_exit = sc.clone();
    let sc_for_close = sc.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_shortcuts([
                    // talk to him
                    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyJ),
                    // hold to dictate into whatever app has focus (no turn taken)
                    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyD),
                    // Standing down (closing the conversation window early)
                    // is Ctrl+Shift+J AGAIN: /listen/toggle stands down when
                    // the window is open. It used to be Ctrl+Shift+S, which
                    // as a GLOBAL shortcut stole Save As from every app on
                    // the machine.
                ])
                .expect("register hotkeys")
                .with_handler(move |app, shortcut, event| {
                    let dictating = shortcut.key == Code::KeyD;
                    if dictating {
                        // press starts capture, release transcribes and pastes; the
                        // window is deliberately NOT raised — dictation belongs to the
                        // app the user is typing in, not to JARVIS.
                        let sc = sc_for_hotkey.clone();
                        let path = if event.state == ShortcutState::Pressed {
                            "/dictation/start"
                        } else {
                            "/dictation/stop"
                        };
                        tauri::async_runtime::spawn(async move {
                            sidecar_post(&sc, path).await;
                        });
                        return;
                    }
                    if event.state == ShortcutState::Pressed {
                        if let Some(win) = app.get_webview_window("main") {
                            // show() alone leaves a MINIMIZED window in the taskbar, which
                            // is exactly the state sleep mode puts him in.
                            let _ = win.unminimize();
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
                            let _ = win.unminimize();
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
                            let _ = win.unminimize();
                            let _ = win.show();
                            let _ = win.set_focus();
                        }
                    }
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(move |window, event| {
            // Close button = full shutdown, immediately: kill the backend
            // (which takes the model server and any in-progress speech with
            // it) and exit. The tray "Exit" does the same.
            if let WindowEvent::CloseRequested { api, .. } = event {
                // vanish instantly, tear down in the background, then exit
                api.prevent_close();
                let _ = window.hide();
                let sc = sc_for_close.clone();
                let app = window.app_handle().clone();
                std::thread::spawn(move || {
                    sc.stop();
                    app.exit(0);
                });
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
