//! Pure-Rust hardware / VRAM probe. Never spawns a process (no console flash, no `wmic`).
//!
//! * Windows: `GlobalMemoryStatusEx` + display-adapter class registry keys
//!   (`DriverDesc`, `HardwareInformation.qwMemorySize` / `HardwareInformation.MemorySize`).
//! * Linux: `/proc/meminfo`, `/sys/class/drm/card*/device/mem_info_vram_total`,
//!   `/proc/driver/nvidia/gpus/*/information`.
//! * macOS: `sysctlbyname("hw.memsize")`; Apple Silicon GPUs share unified memory.

use serde::Serialize;

#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct GpuInfo {
    pub name: String,
    pub vram_mb: u64,
    pub unified_memory: bool,
}

#[derive(Clone, Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HardwareProfile {
    pub os: String,
    pub arch: String,
    pub cpu_threads: usize,
    pub total_memory_mb: u64,
    pub available_memory_mb: u64,
    pub gpus: Vec<GpuInfo>,
    pub max_vram_mb: u64,
    pub recommended_profile: String,
    pub recommended_profile_label: String,
}

pub fn probe() -> HardwareProfile {
    let (total, available) = memory_mb();
    let gpus = gpus(total);
    let max_vram_mb = gpus.iter().map(|gpu| gpu.vram_mb).max().unwrap_or(0);
    let (profile, label) = recommend(max_vram_mb, total);
    HardwareProfile {
        os: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
        cpu_threads: std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(1),
        total_memory_mb: total,
        available_memory_mb: available,
        gpus,
        max_vram_mb,
        recommended_profile: profile.to_string(),
        recommended_profile_label: label.to_string(),
    }
}

pub fn recommend(max_vram_mb: u64, total_memory_mb: u64) -> (&'static str, &'static str) {
    if max_vram_mb >= 16 * 1024 {
        ("workstation", "工作站档：可本地运行大型视频/图像模型")
    } else if max_vram_mb >= 8 * 1024 {
        ("creator", "创作者档：适合中型本地模型与视频合成")
    } else if max_vram_mb >= 4 * 1024 || total_memory_mb >= 16 * 1024 {
        ("lite", "轻量档：建议小模型 + 云端/外部渲染")
    } else {
        ("cpu_safe", "安全档：仅 CPU 任务，重渲染请交给外部服务")
    }
}

// ------------------------------------------------------------------------------------------
// Windows
// ------------------------------------------------------------------------------------------

#[cfg(windows)]
fn memory_mb() -> (u64, u64) {
    use windows_sys::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};
    unsafe {
        let mut status: MEMORYSTATUSEX = std::mem::zeroed();
        status.dwLength = std::mem::size_of::<MEMORYSTATUSEX>() as u32;
        if GlobalMemoryStatusEx(&mut status) != 0 {
            (status.ullTotalPhys / 1_048_576, status.ullAvailPhys / 1_048_576)
        } else {
            (0, 0)
        }
    }
}

#[cfg(windows)]
fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}

#[cfg(windows)]
fn read_registry(subkey: &str, value: &str) -> Option<(u32, Vec<u8>)> {
    use windows_sys::Win32::System::Registry::{RegGetValueW, HKEY_LOCAL_MACHINE, RRF_RT_ANY};
    let subkey_w = wide(subkey);
    let value_w = wide(value);
    let mut kind: u32 = 0;
    let mut size: u32 = 0;
    let status = unsafe {
        RegGetValueW(
            HKEY_LOCAL_MACHINE,
            subkey_w.as_ptr(),
            value_w.as_ptr(),
            RRF_RT_ANY,
            &mut kind,
            std::ptr::null_mut(),
            &mut size,
        )
    };
    if status != 0 || size == 0 || size > 1 << 20 {
        return None;
    }
    let mut buffer = vec![0u8; size as usize];
    let status = unsafe {
        RegGetValueW(
            HKEY_LOCAL_MACHINE,
            subkey_w.as_ptr(),
            value_w.as_ptr(),
            RRF_RT_ANY,
            &mut kind,
            buffer.as_mut_ptr() as *mut core::ffi::c_void,
            &mut size,
        )
    };
    if status != 0 {
        return None;
    }
    buffer.truncate(size as usize);
    Some((kind, buffer))
}

#[cfg(windows)]
fn registry_string(bytes: &[u8]) -> String {
    let units: Vec<u16> = bytes
        .chunks_exact(2)
        .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
        .take_while(|unit| *unit != 0)
        .collect();
    String::from_utf16_lossy(&units).trim().to_string()
}

#[cfg(windows)]
fn registry_number(bytes: &[u8]) -> u64 {
    match bytes.len() {
        8 => u64::from_le_bytes([
            bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
        ]),
        4 => u64::from(u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]])),
        _ => 0,
    }
}

#[cfg(windows)]
fn gpus(_total_memory_mb: u64) -> Vec<GpuInfo> {
    const DISPLAY_CLASS: &str =
        "SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}";
    let mut found = Vec::new();
    for index in 0..16 {
        let key = format!("{DISPLAY_CLASS}\\{index:04}");
        let Some((_, name_raw)) = read_registry(&key, "DriverDesc") else {
            continue;
        };
        let name = registry_string(&name_raw);
        if name.is_empty() || name.contains("Basic Display") || name.contains("Remote Display") {
            continue;
        }
        let bytes = read_registry(&key, "HardwareInformation.qwMemorySize")
            .map(|(_, raw)| registry_number(&raw))
            .filter(|value| *value > 0)
            .or_else(|| {
                read_registry(&key, "HardwareInformation.MemorySize")
                    .map(|(_, raw)| registry_number(&raw))
            })
            .unwrap_or(0);
        if found.iter().any(|gpu: &GpuInfo| gpu.name == name) {
            continue;
        }
        found.push(GpuInfo {
            name,
            vram_mb: bytes / 1_048_576,
            unified_memory: false,
        });
    }
    found
}

// ------------------------------------------------------------------------------------------
// Linux
// ------------------------------------------------------------------------------------------

#[cfg(all(unix, not(target_os = "macos")))]
fn memory_mb() -> (u64, u64) {
    let text = std::fs::read_to_string("/proc/meminfo").unwrap_or_default();
    let field = |name: &str| -> u64 {
        text.lines()
            .find(|line| line.starts_with(name))
            .and_then(|line| line.split_whitespace().nth(1))
            .and_then(|value| value.parse::<u64>().ok())
            .map(|kib| kib / 1024)
            .unwrap_or(0)
    };
    (field("MemTotal:"), field("MemAvailable:"))
}

#[cfg(all(unix, not(target_os = "macos")))]
fn gpus(_total_memory_mb: u64) -> Vec<GpuInfo> {
    let mut found = Vec::new();
    if let Ok(entries) = std::fs::read_dir("/sys/class/drm") {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if !name.starts_with("card") || name.contains('-') {
                continue;
            }
            let device = entry.path().join("device");
            let vram = std::fs::read_to_string(device.join("mem_info_vram_total"))
                .ok()
                .and_then(|value| value.trim().parse::<u64>().ok())
                .unwrap_or(0);
            let vendor = std::fs::read_to_string(device.join("vendor"))
                .unwrap_or_default()
                .trim()
                .to_string();
            let vendor_name = match vendor.as_str() {
                "0x10de" => "NVIDIA GPU",
                "0x1002" => "AMD GPU",
                "0x8086" => "Intel GPU",
                _ => "GPU",
            };
            if vendor == "0x10de" {
                continue; // reported below with the real model name
            }
            found.push(GpuInfo {
                name: format!("{vendor_name} ({name})"),
                vram_mb: vram / 1_048_576,
                unified_memory: vendor == "0x8086",
            });
        }
    }
    if let Ok(entries) = std::fs::read_dir("/proc/driver/nvidia/gpus") {
        for entry in entries.flatten() {
            let info = std::fs::read_to_string(entry.path().join("information")).unwrap_or_default();
            let model = info
                .lines()
                .find(|line| line.starts_with("Model:"))
                .map(|line| line.trim_start_matches("Model:").trim().to_string())
                .unwrap_or_else(|| "NVIDIA GPU".to_string());
            found.push(GpuInfo {
                name: model,
                vram_mb: 0,
                unified_memory: false,
            });
        }
    }
    found
}

// ------------------------------------------------------------------------------------------
// macOS
// ------------------------------------------------------------------------------------------

#[cfg(target_os = "macos")]
fn sysctl_u64(name: &str) -> u64 {
    let Ok(c_name) = std::ffi::CString::new(name) else {
        return 0;
    };
    let mut value: u64 = 0;
    let mut size = std::mem::size_of::<u64>();
    let status = unsafe {
        libc::sysctlbyname(
            c_name.as_ptr(),
            &mut value as *mut u64 as *mut libc::c_void,
            &mut size,
            std::ptr::null_mut(),
            0,
        )
    };
    if status == 0 {
        value
    } else {
        0
    }
}

#[cfg(target_os = "macos")]
fn memory_mb() -> (u64, u64) {
    let total = sysctl_u64("hw.memsize") / 1_048_576;
    (total, 0)
}

#[cfg(target_os = "macos")]
fn gpus(total_memory_mb: u64) -> Vec<GpuInfo> {
    if cfg!(target_arch = "aarch64") {
        // Apple Silicon: GPU shares unified memory; ~70% is a conservative usable budget.
        vec![GpuInfo {
            name: "Apple Silicon GPU（统一内存）".to_string(),
            vram_mb: total_memory_mb * 7 / 10,
            unified_memory: true,
        }]
    } else {
        vec![GpuInfo {
            name: "Intel Mac GPU".to_string(),
            vram_mb: 0,
            unified_memory: false,
        }]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recommendation_tiers_are_monotonic() {
        assert_eq!(recommend(24 * 1024, 64 * 1024).0, "workstation");
        assert_eq!(recommend(8 * 1024, 16 * 1024).0, "creator");
        assert_eq!(recommend(0, 32 * 1024).0, "lite");
        assert_eq!(recommend(0, 4 * 1024).0, "cpu_safe");
    }

    #[test]
    fn probe_never_panics() {
        let profile = probe();
        assert!(profile.cpu_threads >= 1);
        assert!(!profile.os.is_empty());
    }
}
