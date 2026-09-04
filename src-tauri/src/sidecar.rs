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
    resource_dir: Option<PathBuf>,
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
    fn spawn_child(
        resource_dir: &Option<PathBuf>,
        info: &SidecarInfo,
    ) -> Result<Child, String> {
        let mut cmd = sidecar_command(resource_dir.clone())
            .ok_or("sidecar runtime not found".to_string())?;
        // The token is written to the child's stdin, never passed as an argument:
        // any local process can read another process's command line on Windows.
        cmd.arg("--port")
            .arg(info.port.to_string())
            .arg("--token-stdin")
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        let mut child = cmd.spawn().map_err(|e| format!("sidecar spawn: {e}"))?;
        // hand over the token, then close the pipe so the child's read returns
        if let Some(mut stdin) = child.stdin.take() {
            use std::io::Write;
            let _ = writeln!(stdin, "{}", info.token);
            let _ = stdin.flush();
        }
        Ok(child)
    }

    pub fn start(resource_dir: Option<PathBuf>) -> Result<Self, String> {
        let info = SidecarInfo {
            port: free_port(),
            token: random_token(),
        };
        let child = Self::spawn_child(&resource_dir, &info)?;
        Ok(Self {
            info,
            child: Mutex::new(Some(child)),
            resource_dir,
        })
    }

    /// Respawn with the SAME port/token so the UI's connection info stays valid.
    pub fn restart(&self) -> Result<(), String> {
        self.stop();
        let child = Self::spawn_child(&self.resource_dir, &self.info)?;
        *self.child.lock().unwrap() = Some(child);
        Ok(())
    }

    pub fn is_alive(&self) -> bool {
        let mut guard = self.child.lock().unwrap();
        if let Some(child) = guard.as_mut() {
            matches!(child.try_wait(), Ok(None))
        } else {
            false
        }
    }

    /// Does it actually ANSWER? `is_alive` only asks whether the process still
    /// exists, which is a different question and a much weaker one: on
    /// 2026-08-30 a dead audio device deadlocked the sidecar's event loop, and
    /// because the process was still there the supervisor watched it be frozen
    /// for forty minutes without ever restarting it. He had no assistant and
    /// nothing noticed. Liveness is answering, not existing.
    pub async fn is_responding(&self) -> bool {
        let client = reqwest::Client::new();
        matches!(
            client
                .get(format!("http://127.0.0.1:{}/health", self.info.port))
                .timeout(Duration::from_secs(3))
                .send()
                .await,
            Ok(resp) if resp.status().is_success()
        )
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
            // Kill the whole tree — the sidecar spawns llama-server, which a
            // plain kill() would orphan on Windows.
            #[cfg(windows)]
            {
                // CREATE_NO_WINDOW, like the spawn above. taskkill is a
                // console program; without the flag every close and every
                // supervisor restart flashed a command prompt on his screen —
                // the same defect the sidecar's own launchers all carry the
                // flag for.
                use std::os::windows::process::CommandExt;
                const CREATE_NO_WINDOW: u32 = 0x0800_0000;
                let _ = Command::new("taskkill")
                    .args(["/F", "/T", "/PID", &child.id().to_string()])
                    .creation_flags(CREATE_NO_WINDOW)
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
            }
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
