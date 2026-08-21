//! Spawns, health-checks, and restarts the Python sidecar. The user never
//! launches anything manually — this module is why.

use rand::Rng;
use serde::Serialize;
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

#[derive(Clone, Serialize)]
pub struct SidecarInfo {
    pub port: u16,
    pub token: String,
}

pub struct Sidecar {
    pub info: SidecarInfo,
    child: Mutex<Option<Child>>,
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(8790)
}

fn random_token() -> String {
    let mut rng = rand::thread_rng();
    (0..32)
        .map(|_| format!("{:x}", rng.gen_range(0..16)))
        .collect()
}

/// Locate the sidecar entry. Dev: repo checkout + venv python.
/// Prod: bundled PyInstaller exe under the app's resource dir.
fn sidecar_command(resource_dir: Option<PathBuf>) -> Option<Command> {
    // Production layout: <resources>/sidecar/jarvis-sidecar.exe
    if let Some(res) = resource_dir {
        let exe = res.join("sidecar").join("jarvis-sidecar.exe");
        if exe.exists() {
            return Some(Command::new(exe));
        }
    }
    // Dev layout: ../sidecar/main.py with ../sidecar/.venv
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let sidecar_dir = manifest.parent()?.join("sidecar");
    let py = sidecar_dir.join(".venv").join("Scripts").join("python.exe");
    let main = sidecar_dir.join("main.py");
    if py.exists() && main.exists() {
        let mut cmd = Command::new(py);
        cmd.arg(main).current_dir(sidecar_dir);
        return Some(cmd);
    }
    None
}

impl Sidecar {
    pub fn start(resource_dir: Option<PathBuf>) -> Result<Self, String> {
        let info = SidecarInfo {
            port: free_port(),
            token: random_token(),
        };
        let mut cmd =
            sidecar_command(resource_dir).ok_or("sidecar runtime not found".to_string())?;
        cmd.arg("--port")
            .arg(info.port.to_string())
            .arg("--token")
            .arg(&info.token)
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        let child = cmd.spawn().map_err(|e| format!("sidecar spawn: {e}"))?;
        Ok(Self {
            info,
            child: Mutex::new(Some(child)),
        })
    }

    pub fn is_alive(&self) -> bool {
        let mut guard = self.child.lock().unwrap();
        if let Some(child) = guard.as_mut() {
            matches!(child.try_wait(), Ok(None))
        } else {
            false
        }
    }

    pub async fn wait_healthy(&self, timeout: Duration) -> bool {
        let url = format!("http://127.0.0.1:{}/health", self.info.port);
        let client = reqwest::Client::new();
        let deadline = std::time::Instant::now() + timeout;
        while std::time::Instant::now() < deadline {
            if let Ok(resp) = client
                .get(&url)
                .timeout(Duration::from_secs(2))
                .send()
                .await
            {
                if resp.status().is_success() {
                    return true;
                }
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
        false
    }

    pub fn stop(&self) {
        let mut guard = self.child.lock().unwrap();
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Drop for Sidecar {
    fn drop(&mut self) {
        self.stop();
    }
}
