//! Secrets live in Windows Credential Manager, never in files. The sidecar
//! receives them over loopback (token-authed) at startup / on change.

use keyring::Entry;

const SERVICE: &str = "JARVIS";

pub fn get_secret(name: &str) -> Option<String> {
    Entry::new(SERVICE, name).ok()?.get_password().ok()
}

pub fn set_secret(name: &str, value: &str) -> Result<(), String> {
    Entry::new(SERVICE, name)
        .map_err(|e| e.to_string())?
        .set_password(value)
        .map_err(|e| e.to_string())
}

pub fn delete_secret(name: &str) -> Result<(), String> {
    Entry::new(SERVICE, name)
        .map_err(|e| e.to_string())?
        .delete_credential()
        .map_err(|e| e.to_string())
}

/// Names of secrets JARVIS knows how to use; pushed to the sidecar when present.
pub const KNOWN_SECRETS: &[&str] = &["brave_api_key"];
