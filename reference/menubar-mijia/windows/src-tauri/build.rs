fn main() {
    #[cfg(all(target_os = "windows", target_env = "gnu"))]
    {
        if let Some(cc) =
            std::env::var_os("CC_x86_64_pc_windows_gnu").or_else(|| std::env::var_os("CC"))
        {
            if let Some(bin_dir) = std::path::Path::new(&cc).parent() {
                if bin_dir.join("windres.exe").is_file() {
                    let current_path = std::env::var_os("PATH").unwrap_or_default();
                    let mut paths = vec![bin_dir.to_path_buf()];
                    paths.extend(std::env::split_paths(&current_path));
                    if let Ok(joined) = std::env::join_paths(paths) {
                        std::env::set_var("PATH", joined);
                    }
                }
            }
        }
    }

    tauri_build::build()
}
