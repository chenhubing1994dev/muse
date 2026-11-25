fn main() {
    // Expose the compilation target so the shell can locate `muse-engine-<target-triple>`.
    let target = std::env::var("TARGET").unwrap_or_default();
    println!("cargo:rustc-env=MUSE_TARGET_TRIPLE={target}");
    println!("cargo:rerun-if-changed=tauri.conf.json");
    println!("cargo:rerun-if-changed=capabilities");
    tauri_build::build()
}
