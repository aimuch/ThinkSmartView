use super::*;

#[tauri::command]
pub(crate) async fn start_xiaomi_camera_bridge(
    app: AppHandle,
    did: String,
    model: String,
    local_ip: String,
    client_public: String,
    client_private: String,
    device_public: String,
    sign: String,
    vendor: String,
    uid: Option<String>,
    quality: Option<String>,
) -> Result<String, String> {
    let _bridge_order = CAMERA_BRIDGE_LOCK.lock().await;
    let cam_ip: std::net::Ipv4Addr = local_ip
        .trim()
        .parse()
        .map_err(|_| format!("无效的局域网摄像机 IP：{local_ip}"))?;
    if !cam_ip.is_private() && !cam_ip.is_loopback() {
        return Err("仅允许局域网私有 IP 地址直连摄像机".to_string());
    }
    let subtype = xiaomi_camera_subtype(quality.as_deref())?;

    ensure_go2rtc_running(&app).await?;
    let session = current_go2rtc_session()?;

    let clean_did: String = did
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_')
        .collect();
    let raw_name = format!("cam_raw_{clean_did}");
    let h264_name = format!("cam_h264_{clean_did}");
    let mjpeg_name = format!("cam_mjpeg_{clean_did}");

    let http = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(6))
        .build()
        .map_err(|e| e.to_string())?;

    // A camera keeps exactly one P2P session, and the relay thread dies with its old port.
    // Dropping the previous bridge first is what makes the second open work.
    stop_bridge(&http, Some(&session), &clean_did, &[&raw_name, &h264_name, &mjpeg_name]).await;

    let relay_port = camera_relay_port(&clean_did, cam_ip)?;

    let mut query = format!(
        "client_public={}&client_private={}&device_public={}&sign={}&vendor={}&transport=udp&subtype={}&audio=0&model={}",
        urlencoding_simple(&client_public),
        urlencoding_simple(&client_private),
        urlencoding_simple(&device_public),
        urlencoding_simple(&sign),
        urlencoding_simple(&vendor),
        subtype,
        urlencoding_simple(&model)
    );
    if let Some(u) = uid.as_deref().filter(|s| !s.is_empty()) {
        query.push_str(&format!("&uid={}", urlencoding_simple(u)));
    }
    let xiaomi_url = format!("xiaomi://127.0.0.1:{relay_port}?{query}");

    put_stream(&http, &session, &raw_name, &xiaomi_url)
        .await
        .map_err(|e| format!("注册摄像机原始流失败：{e}"))?;
    // Two transcode targets: WebRTC plays H.264 with real-time pacing (it drops late frames
    // instead of buffering), while MJPEG is the fallback that paints on the first frame.
    put_stream(&http, &session, &h264_name, &format!("ffmpeg:{raw_name}#video=h264"))
        .await
        .map_err(|e| format!("注册摄像机 H.264 转码流失败：{e}"))?;
    put_stream(&http, &session, &mjpeg_name, &format!("ffmpeg:{raw_name}#video=mjpeg"))
        .await
        .map_err(|e| format!("注册摄像机 MJPEG 转码流失败：{e}"))?;

    Ok(format!("{h264_name}|{mjpeg_name}"))
}

#[tauri::command]
pub(crate) async fn bridge_hls_camera_stream(
    app: AppHandle,
    did: String,
    hls_url: String,
) -> Result<String, String> {
    let _bridge_order = CAMERA_BRIDGE_LOCK.lock().await;
    let parsed =
        reqwest::Url::parse(hls_url.trim()).map_err(|_| "无效的 HLS 流地址".to_string())?;
    if !matches!(parsed.scheme(), "http" | "https") {
        return Err("HLS 流地址必须使用 http 或 https 协议".to_string());
    }
    ensure_go2rtc_running(&app).await?;
    let session = current_go2rtc_session()?;
    let clean_did: String = did
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_')
        .collect();
    let mjpeg_name = format!("cam_mjpeg_{clean_did}");
    let http = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(6))
        .build()
        .map_err(|e| e.to_string())?;
    stop_mjpeg_streams(&[&mjpeg_name]);
    let _ = authed(
        http.delete(api_url(
            &session,
            &format!("/api/streams?src={}", urlencoding_simple(&mjpeg_name)),
        )),
        &session,
    )
    .send()
    .await;
    let path_lower = parsed.path().to_ascii_lowercase();
    let stream_src = if path_lower.contains("/api/camera_proxy")
        || path_lower.ends_with(".mjpeg")
        || path_lower.ends_with(".mjpg")
        || path_lower.ends_with("/stream.mjpeg")
    {
        parsed.as_str().to_string()
    } else {
        format!("ffmpeg:{}#video=mjpeg", parsed.as_str())
    };
    put_stream(&http, &session, &mjpeg_name, &stream_src)
        .await
        .map_err(|e| format!("注册 HLS 转码流失败：{e}"))?;
    Ok(mjpeg_name)
}

#[tauri::command]
pub(crate) async fn stop_xiaomi_camera_bridge(did: String) -> Result<(), String> {
    let _bridge_order = CAMERA_BRIDGE_LOCK.lock().await;
    let clean_did: String = did
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '_')
        .collect();
    if clean_did.is_empty() {
        stop_all_mjpeg_streams();
    }
    let http = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(3))
        .build()
        .map_err(|e| e.to_string())?;
    let raw_name = format!("cam_raw_{clean_did}");
    let h264_name = format!("cam_h264_{clean_did}");
    let mjpeg_name = format!("cam_mjpeg_{clean_did}");
    let session = current_go2rtc_session().ok();
    stop_bridge(
        &http,
        session.as_ref(),
        &clean_did,
        &[&raw_name, &h264_name, &mjpeg_name],
    )
    .await;
    Ok(())
}

pub(crate) async fn put_stream(
    http: &reqwest::Client,
    session: &Go2RtcSession,
    name: &str,
    src: &str,
) -> Result<(), String> {
    let url = api_url(
        session,
        &format!(
            "/api/streams?name={}&src={}",
            urlencoding_simple(name),
            urlencoding_simple(src)
        ),
    );
    let resp = authed(http.put(&url), session)
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if let Some(path) = go2rtc_config_path() {
        restrict_secret_file(&path);
    }
    if resp.status().is_success() {
        Ok(())
    } else {
        Err(format!("HTTP {}", resp.status()))
    }
}

pub(crate) async fn stop_bridge(
    http: &reqwest::Client,
    session: Option<&Go2RtcSession>,
    clean_did: &str,
    names: &[&str],
) {
    stop_mjpeg_streams(names);
    if let Some(session) = session {
        for name in names {
            let url = api_url(
                session,
                &format!("/api/streams?src={}", urlencoding_simple(name)),
            );
            let _ = authed(http.delete(&url), session).send().await;
        }
        if let Some(path) = go2rtc_config_path() {
            restrict_secret_file(&path);
        }
    }
    let no_active_relays = if let Ok(mut relays) = CAMERA_RELAYS.lock() {
        if let Some(relay) = relays.remove(clean_did) {
            relay.stop.store(true, Ordering::SeqCst);
        }
        relays.is_empty()
    } else {
        false
    };
    if no_active_relays {
        scrub_persisted_streams_from_config();
    }
}

struct CameraRelay {
    port: u16,
    stop: std::sync::Arc<AtomicBool>,
}

static CAMERA_RELAYS: LazyLock<std::sync::Mutex<HashMap<String, CameraRelay>>> =
    LazyLock::new(|| std::sync::Mutex::new(HashMap::new()));

/// Tracks the go2rtc child process spawned by this application instance so we never
/// terminate user-started `go2rtc.exe` processes by image name (#97).
static GO2RTC_PROCESS: LazyLock<std::sync::Mutex<Option<std::process::Child>>> =
    LazyLock::new(|| std::sync::Mutex::new(None));
static GO2RTC_SESSION: LazyLock<std::sync::Mutex<Option<Go2RtcOwnerRecord>>> =
    LazyLock::new(|| std::sync::Mutex::new(None));

/// Serializes bridge start/stop end to end: two overlapping starts for the same
/// camera otherwise interleave their stop_bridge/register sequences and the
/// newer bridge ends up deleted by the stale one (#97).
static CAMERA_BRIDGE_LOCK: LazyLock<tauri::async_runtime::Mutex<()>> =
    LazyLock::new(|| tauri::async_runtime::Mutex::new(()));

pub(crate) fn scrub_persisted_streams_from_config() {
    let Some(cfg_path) = go2rtc_config_path() else {
        return;
    };
    let Ok(existing) = std::fs::read_to_string(&cfg_path) else {
        return;
    };
    if !existing.contains("streams:") {
        return;
    }
    let cleaned = format!("{}\n", base_go2rtc_config(&existing));
    let _ = write_secret_file(&cfg_path, &cleaned);
}

pub(crate) fn shutdown_camera_daemons() {
    stop_all_mjpeg_streams();
    if let Ok(mut relays) = CAMERA_RELAYS.lock() {
        for (_, relay) in relays.drain() {
            relay.stop.store(true, Ordering::SeqCst);
        }
    }
    if let Ok(mut guard) = GO2RTC_PROCESS.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
    if let Ok(mut guard) = GO2RTC_SESSION.lock() {
        *guard = None;
    }
    scrub_persisted_streams_from_config();
}

/// The app-owned part of go2rtc.yaml, without the `streams:` section go2rtc appends.
pub(crate) fn base_go2rtc_config(yaml: &str) -> String {
    let mut kept = Vec::new();
    let mut in_streams = false;
    for line in yaml.split('\n') {
        let trimmed_cr = line.trim_end_matches('\r');
        let is_top_level = trimmed_cr
            .chars()
            .next()
            .is_some_and(|c| !c.is_whitespace());
        if is_top_level {
            in_streams = trimmed_cr.starts_with("streams:");
        }
        if !in_streams {
            kept.push(trimmed_cr);
        }
    }
    kept.join("\n").trim().to_string()
}

#[derive(Clone)]
pub(crate) struct Go2RtcSession {
    pub(crate) api_listen: String,
    pub(crate) rtsp_listen: String,
    pub(crate) username: String,
    pub(crate) password: String,
    pub(crate) nonce: String,
}

pub(crate) struct Go2RtcOwnerRecord {
    pub(crate) pid: u32,
    pub(crate) session: Go2RtcSession,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Go2RtcAction {
    ReuseOwned,
    RestartOwned,
    SpawnFresh,
}

#[derive(Clone, Copy)]
pub(crate) struct Go2RtcOwnershipProbe {
    pub(crate) pid_alive: bool,
    pub(crate) authenticated: bool,
    pub(crate) nonce_matches: bool,
    pub(crate) config_changed: bool,
}

pub(crate) const FORBIDDEN_GO2RTC_PORTS: [u16; 3] = [1984, 8554, 8555];

pub(crate) fn decide_go2rtc_action(probe: Go2RtcOwnershipProbe) -> Go2RtcAction {
    let owned = probe.pid_alive && probe.authenticated && probe.nonce_matches;
    if !owned {
        Go2RtcAction::SpawnFresh
    } else if probe.config_changed {
        Go2RtcAction::RestartOwned
    } else {
        Go2RtcAction::ReuseOwned
    }
}

/// v1.9.15 skips Basic auth on loopback unless `local_auth` is true.
pub(crate) fn desired_go2rtc_config(session: &Go2RtcSession, ffmpeg_bin: &str) -> String {
    let ffmpeg = yaml_double_quoted(ffmpeg_bin);
    let api = sanitized_listen(&session.api_listen);
    let rtsp = sanitized_listen(&session.rtsp_listen);
    format!(
        "api:\n  listen: {api}\n  username: {user}\n  password: {password}\n  local_auth: true\nrtsp:\n  listen: {rtsp}\nwebrtc:\n  listen: \"\"\nlog:\n  level: info\n  ffmpeg: error\nffmpeg:\n  bin: {ffmpeg}\nexec:\n  allow_paths:\n    - {ffmpeg}\nmenubarmijia:\n  nonce: {nonce}\n",
        user = yaml_double_quoted(&session.username),
        password = yaml_double_quoted(&session.password),
        nonce = yaml_double_quoted(&session.nonce),
    )
}

/// Reuses a live relay so reopening the panel does not renegotiate P2P from scratch.
pub(crate) fn camera_relay_port(clean_did: &str, cam_ip: std::net::Ipv4Addr) -> Result<u16, String> {
    let mut relays = CAMERA_RELAYS.lock().map_err(|_| "中继表不可用".to_string())?;
    if let Some(existing) = relays.get(clean_did) {
        if !existing.stop.load(Ordering::SeqCst) {
            return Ok(existing.port);
        }
    }
    relays.remove(clean_did);
    let (port, stop) = spawn_cs2_udp_relay(cam_ip)?;
    relays.insert(clean_did.to_string(), CameraRelay { port, stop });
    Ok(port)
}

pub(crate) fn urlencoding_simple(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b'~') {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

const GO2RTC_DOWNLOAD_URL: &str =
    "https://github.com/AlexxIT/go2rtc/releases/download/v1.9.15/go2rtc_win64.zip";
const GO2RTC_DOWNLOAD_SHA256: &str =
    "5b448fc90f15c925406d8da0984301985e385050222c1c2bbdf0a76fe4b1e9cb";
/// SHA-256 of the `go2rtc.exe` inside that archive. It is a different value from the archive
/// hash above, and it is what lets a truncated or stale copy left in LOCALAPPDATA be
/// detected instead of trusted (#14).
pub(crate) const GO2RTC_EXE_SHA256: &str =
    "994aa42768d2d6e1bbcf561c85bf9c46cafa705a90a13537cd38be2faee287de";
/// Tauri resource directory subfolder populated by `bundle.resources` (see tauri.conf.json).
pub(crate) const CAMERA_COMPONENT_DIR: &str = "camera-bin";
const CAMERA_MANUAL_BIN_HINT: &str =
    "或手动下载 go2rtc_win64.zip，解压后把 go2rtc.exe 放到 %LOCALAPPDATA%\\MenuBarMijia\\bin\\";

/// go2rtc `subtype` for a Xiaomi MISS stream (#97). `max` is numeric subtype 3, which
/// several 4K models require; `None` keeps the historical `hd` default.
pub(crate) fn xiaomi_camera_subtype(quality: Option<&str>) -> Result<&'static str, String> {
    match quality.unwrap_or("hd") {
        "auto" => Ok("auto"),
        "sd" => Ok("sd"),
        "hd" => Ok("hd"),
        "max" => Ok("3"),
        other => Err(format!("不支持的摄像机画质：{other}")),
    }
}

pub(crate) fn bundled_component_source(
    resource_dir: &std::path::Path,
    name: &str,
) -> Option<std::path::PathBuf> {
    let path = resource_dir.join(CAMERA_COMPONENT_DIR).join(name);
    path.is_file().then_some(path)
}

/// Components whose exact bytes are pinned. A mismatch means a truncated leftover or a
/// different build, and go2rtc's config and auth options change between releases, so neither
/// may be used as-is.
pub(crate) fn expected_component_sha256(name: &str) -> Option<&'static str> {
    (name == "go2rtc.exe").then_some(GO2RTC_EXE_SHA256)
}

pub(crate) fn file_sha256_hex(path: &std::path::Path) -> std::io::Result<String> {
    use std::io::Read;
    let mut file = std::fs::File::open(path)?;
    let mut context = ring::digest::Context::new(&ring::digest::SHA256);
    let mut buffer = vec![0u8; 64 * 1024];
    loop {
        match file.read(&mut buffer) {
            Ok(0) => break,
            Ok(read) => context.update(&buffer[..read]),
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error),
        }
    }
    let sum = context.finish();
    let mut hex = String::with_capacity(64);
    for byte in sum.as_ref() {
        use std::fmt::Write;
        let _ = write!(&mut hex, "{byte:02x}");
    }
    Ok(hex)
}

/// A component counts as installed only when it is a non-empty file and, for a pinned
/// component, an exact match. Probing `is_file()` alone accepted the zero-byte or truncated
/// leftovers of an interrupted download forever (#14).
pub(crate) fn component_is_usable(
    path: &std::path::Path,
    expected_sha256: Option<&str>,
) -> bool {
    let Ok(metadata) = std::fs::metadata(path) else {
        return false;
    };
    if !metadata.is_file() || metadata.len() == 0 {
        return false;
    }
    let Some(expected) = expected_sha256 else {
        return true;
    };
    matches!(file_sha256_hex(path), Ok(actual) if actual == expected)
}

fn read_chunk(file: &mut std::fs::File, buffer: &mut [u8]) -> std::io::Result<usize> {
    use std::io::Read;
    let mut filled = 0;
    while filled < buffer.len() {
        match file.read(&mut buffer[filled..]) {
            Ok(0) => break,
            Ok(read) => filled += read,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error),
        }
    }
    Ok(filled)
}

fn same_file_contents(left: &std::path::Path, right: &std::path::Path) -> bool {
    let (Ok(left_meta), Ok(right_meta)) = (std::fs::metadata(left), std::fs::metadata(right)) else {
        return false;
    };
    if !left_meta.is_file() || !right_meta.is_file() || left_meta.len() != right_meta.len() {
        return false;
    }
    if left_meta.len() == 0 {
        return false;
    }
    let (Ok(mut left_file), Ok(mut right_file)) =
        (std::fs::File::open(left), std::fs::File::open(right))
    else {
        return false;
    };
    let mut left_buffer = vec![0u8; 64 * 1024];
    let mut right_buffer = vec![0u8; 64 * 1024];
    loop {
        let (Ok(left_read), Ok(right_read)) = (
            read_chunk(&mut left_file, &mut left_buffer),
            read_chunk(&mut right_file, &mut right_buffer),
        ) else {
            return false;
        };
        if left_read != right_read || left_buffer[..left_read] != right_buffer[..right_read] {
            return false;
        }
        if left_read == 0 {
            return true;
        }
    }
}

/// Copies a bundled helper into the writable component directory. MSI installs may keep
/// the install directory read-only, while go2rtc also writes its config and owner record
/// next to the executable, so both binaries and state live under LOCALAPPDATA.
///
/// The destination is compared with the source rather than only probed for existence, so a
/// damaged copy is repaired instead of shadowing the good bundled one (#14).
pub(crate) fn copy_bundled_component(src: &std::path::Path, dest: &std::path::Path) -> Result<(), String> {
    if same_file_contents(src, dest) {
        return Ok(());
    }
    let parent = dest.parent().ok_or_else(|| "组件目录不可用".to_string())?;
    std::fs::create_dir_all(parent).map_err(|e| format!("创建组件目录失败：{e}"))?;
    let name = dest
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("component");
    // A per-call serial keeps two concurrent installs of the same component from writing
    // the same staging file and racing a mixed copy into place.
    let serial = COMPONENT_STAGING_SERIAL.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let staging = dest.with_file_name(format!("{name}.staging-{}-{serial}", std::process::id()));
    let _ = std::fs::remove_file(&staging);
    std::fs::copy(src, &staging).map_err(|e| {
        let _ = std::fs::remove_file(&staging);
        format!("复制内置组件 {} 失败：{e}", src.display())
    })?;
    let source_len = std::fs::metadata(src).map(|meta| meta.len()).unwrap_or(0);
    if std::fs::metadata(&staging).map(|meta| meta.len()).unwrap_or(0) != source_len {
        let _ = std::fs::remove_file(&staging);
        return Err(format!("复制内置组件 {} 不完整", src.display()));
    }
    // std::fs::rename cannot replace an existing file on Windows, so the stale copy goes
    // first. A crash inside that window leaves no component at all, which the next start
    // repairs by re-running this copy; a half-written file is never installed.
    let _ = std::fs::remove_file(dest);
    if let Err(error) = std::fs::rename(&staging, dest) {
        let _ = std::fs::remove_file(&staging);
        return Err(format!("安装内置组件 {} 失败：{error}", dest.display()));
    }
    Ok(())
}

static COMPONENT_STAGING_SERIAL: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);

/// Decides whether `dest` counts as installed and repairs it from a bundled copy when one is
/// available. A valid bundled copy wins over whatever the destination holds, so a damaged or
/// stale file left by an older version is replaced instead of trusted (#14).
pub(crate) fn install_component(
    bundled: Option<&std::path::Path>,
    dest: &std::path::Path,
    expected_sha256: Option<&str>,
) -> Result<bool, String> {
    if let Some(src) = bundled.filter(|src| component_is_usable(src, expected_sha256)) {
        copy_bundled_component(src, dest)?;
        return Ok(true);
    }
    Ok(component_is_usable(dest, expected_sha256))
}

/// Ensures `bin_dir/name` holds a usable copy. A bundled resource is the source of truth;
/// without one, only a validated destination counts so the caller falls back to downloading
/// it.
pub(crate) fn ensure_component(
    app: &AppHandle,
    bin_dir: &std::path::Path,
    name: &str,
) -> Result<bool, String> {
    let bundled = app
        .path()
        .resource_dir()
        .ok()
        .and_then(|dir| bundled_component_source(&dir, name));
    install_component(
        bundled.as_deref(),
        &bin_dir.join(name),
        expected_component_sha256(name),
    )
}

pub(crate) fn resolve_ffmpeg_bin(app: &AppHandle, bin_dir: &std::path::Path) -> Result<String, String> {
    for candidate in ["ffmpeg-silent.exe", "ffmpeg.exe"] {
        if ensure_component(app, bin_dir, candidate)? {
            return Ok(bin_dir.join(candidate).to_string_lossy().replace('\\', "/"));
        }
    }
    // A user-installed ffmpeg on PATH is the last resort; the bundled helper is preferred
    // because it is silent and version-pinned.
    Ok("ffmpeg".to_string())
}

pub(crate) async fn download_go2rtc_win64(
    bin_dir: &std::path::Path,
    exe_path: &std::path::Path,
) -> Result<(), String> {
    let mut last_error = String::new();
    for attempt in 1..=3 {
        match download_go2rtc_once(bin_dir, exe_path).await {
            Ok(()) => return Ok(()),
            Err(error) => {
                last_error = error;
                if attempt < 3 {
                    tokio::time::sleep(Duration::from_millis(700 * attempt)).await;
                }
            }
        }
    }
    Err(format!(
        "首次打开摄像机需自动下载流媒体组件 go2rtc，但下载失败（{last_error}）。请检查网络后重试，{CAMERA_MANUAL_BIN_HINT}"
    ))
}

pub(crate) async fn download_go2rtc_once(
    bin_dir: &std::path::Path,
    exe_path: &std::path::Path,
) -> Result<(), String> {
    std::fs::create_dir_all(bin_dir).map_err(|e| format!("创建组件目录失败：{e}"))?;
    // GitHub release assets redirect to release-assets.githubusercontent.com; 45 s was too
    // short for a slow link and surfaced as an opaque body-decode error (#200).
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()
        .map_err(|e| e.to_string())?;
    let resp = client
        .get(GO2RTC_DOWNLOAD_URL)
        .send()
        .await
        .map_err(|e| format!("网络连接失败（{e}）"))?;
    if !resp.status().is_success() {
        return Err(format!("HTTP {}", resp.status().as_u16()));
    }
    let bytes = resp
        .bytes()
        .await
        .map_err(|e| format!("读取组件数据失败（{e}）"))?;
    let actual_sha256 = compute_sha256_hex(&bytes);
    if actual_sha256 != GO2RTC_DOWNLOAD_SHA256 {
        return Err("SHA-256 校验未通过，已拒绝加载".to_string());
    }

    let staging_dir = bin_dir.join(format!("staging-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&staging_dir);
    let installed = match stage_go2rtc_exe(&staging_dir, &bytes) {
        Ok(extracted) => {
            let _ = std::fs::remove_file(exe_path);
            std::fs::rename(&extracted, exe_path).map_err(|e| format!("安装 go2rtc.exe 失败：{e}"))
        }
        Err(error) => Err(error),
    };
    // One owner for the staging directory, so no failure path leaves the archive or the
    // extracted executable behind.
    let _ = std::fs::remove_dir_all(&staging_dir);
    installed
}

/// Writes the verified archive into `staging_dir`, extracts it and returns the executable
/// after checking it against the pinned hash. The caller removes the directory on every path.
fn stage_go2rtc_exe(
    staging_dir: &std::path::Path,
    archive: &[u8],
) -> Result<std::path::PathBuf, String> {
    std::fs::create_dir_all(staging_dir).map_err(|e| format!("创建解压目录失败：{e}"))?;
    let zip_path = staging_dir.join("go2rtc_win64.zip");
    std::fs::write(&zip_path, archive).map_err(|e| format!("写入临时压缩包失败：{e}"))?;
    let status = std::process::Command::new("tar.exe")
        .args([
            "-xf",
            &zip_path.to_string_lossy(),
            "-C",
            &staging_dir.to_string_lossy(),
        ])
        .creation_flags(CREATE_NO_WINDOW)
        .status()
        .map_err(|e| format!("解压 go2rtc.exe 失败：{e}"))?;
    if !status.success() {
        return Err("解压流媒体组件 go2rtc.exe 失败".to_string());
    }
    let extracted = staging_dir.join("go2rtc.exe");
    if !component_is_usable(&extracted, Some(GO2RTC_EXE_SHA256)) {
        return Err("解压出的 go2rtc.exe 缺失或校验未通过，已拒绝安装".to_string());
    }
    Ok(extracted)
}

pub(crate) fn running_child_pid() -> Option<u32> {
    let mut guard = GO2RTC_PROCESS.lock().ok()?;
    let child = guard.as_mut()?;
    match child.try_wait() {
        Ok(None) => Some(child.id()),
        _ => {
            *guard = None;
            None
        }
    }
}

pub(crate) fn current_go2rtc_session() -> Result<Go2RtcSession, String> {
    let guard = GO2RTC_SESSION
        .lock()
        .map_err(|_| "流媒体会话不可用".to_string())?;
    guard
        .as_ref()
        .map(|record| record.session.clone())
        .ok_or_else(|| "本地流媒体服务未启动".to_string())
}

pub(crate) fn store_go2rtc_session(record: Go2RtcOwnerRecord) {
    if let Ok(mut guard) = GO2RTC_SESSION.lock() {
        *guard = Some(record);
    }
}

pub(crate) fn bin_directory() -> Result<std::path::PathBuf, String> {
    let local = std::env::var("LOCALAPPDATA").map_err(|_| "无法读取 LOCALAPPDATA 目录".to_string())?;
    Ok(std::path::PathBuf::from(local).join("MenuBarMijia").join("bin"))
}

pub(crate) fn go2rtc_config_path() -> Option<std::path::PathBuf> {
    bin_directory().ok().map(|dir| dir.join("go2rtc.yaml"))
}

pub(crate) fn go2rtc_owner_path() -> Option<std::path::PathBuf> {
    bin_directory().ok().map(|dir| dir.join("go2rtc.owner"))
}

pub(crate) fn write_secret_file(path: &std::path::Path, contents: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("创建组件目录失败：{e}"))?;
    }
    std::fs::write(path, contents).map_err(|e| format!("写入 go2rtc 配置失败：{e}"))?;
    restrict_secret_file(path);
    Ok(())
}

pub(crate) fn restrict_secret_file(path: &std::path::Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
    }
    #[cfg(windows)]
    {
        let Ok(user) = std::env::var("USERNAME") else {
            return;
        };
        if user.is_empty()
            || user
                .bytes()
                .any(|byte| matches!(byte, b'"' | b'&' | b'|' | b'\n' | b'\r' | b'%'))
        {
            return;
        }
        let mut cmd = std::process::Command::new("icacls");
        cmd.arg(path)
            .arg("/inheritance:r")
            .arg("/grant:r")
            .arg(format!("{user}:(F)"))
            .creation_flags(CREATE_NO_WINDOW);
        let _ = cmd.status();
    }
}

pub(crate) fn api_url(session: &Go2RtcSession, path_and_query: &str) -> String {
    format!("http://{}{path_and_query}", session.api_listen)
}

pub(crate) fn authed(builder: reqwest::RequestBuilder, session: &Go2RtcSession) -> reqwest::RequestBuilder {
    builder.basic_auth(&session.username, Some(&session.password))
}

pub(crate) fn basic_authorization_value(username: &str, password: &str) -> String {
    format!(
        "Basic {}",
        base64_encode(format!("{username}:{password}").as_bytes())
    )
}

pub(crate) fn base64_encode(data: &[u8]) -> String {
    const TABLE: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    let mut index = 0;
    while index + 3 <= data.len() {
        let value = ((data[index] as u32) << 16)
            | ((data[index + 1] as u32) << 8)
            | (data[index + 2] as u32);
        out.push(TABLE[((value >> 18) & 63) as usize] as char);
        out.push(TABLE[((value >> 12) & 63) as usize] as char);
        out.push(TABLE[((value >> 6) & 63) as usize] as char);
        out.push(TABLE[(value & 63) as usize] as char);
        index += 3;
    }
    if index < data.len() {
        let remain = data.len() - index;
        let mut value = (data[index] as u32) << 16;
        if remain == 2 {
            value |= (data[index + 1] as u32) << 8;
        }
        out.push(TABLE[((value >> 18) & 63) as usize] as char);
        out.push(TABLE[((value >> 12) & 63) as usize] as char);
        if remain == 2 {
            out.push(TABLE[((value >> 6) & 63) as usize] as char);
            out.push('=');
        } else {
            out.push('=');
            out.push('=');
        }
    }
    out
}

pub(crate) fn yaml_double_quoted(value: &str) -> String {
    let mut out = String::from("\"");
    for ch in value.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' | '\r' => {}
            _ => out.push(ch),
        }
    }
    out.push('"');
    out
}

pub(crate) fn is_credential_token(value: &str) -> bool {
    (8..=128).contains(&value.len()) && value.bytes().all(|byte| byte.is_ascii_alphanumeric())
}

pub(crate) fn is_acceptable_go2rtc_listen(value: &str) -> bool {
    let Some(port_text) = value.strip_prefix("127.0.0.1:") else {
        return false;
    };
    let Ok(port) = port_text.parse::<u16>() else {
        return false;
    };
    port > 0 && !FORBIDDEN_GO2RTC_PORTS.contains(&port) && value == format!("127.0.0.1:{port}")
}

pub(crate) fn sanitized_listen(value: &str) -> String {
    if is_acceptable_go2rtc_listen(value) {
        value.to_string()
    } else {
        "127.0.0.1:9".to_string()
    }
}

pub(crate) fn random_credential_token() -> Result<String, String> {
    let rng = SystemRandom::new();
    let mut bytes = [0u8; 16];
    rng.fill(&mut bytes)
        .map_err(|_| "无法生成流媒体凭据".to_string())?;
    let mut out = String::from("m");
    for byte in bytes {
        out.push_str(&format!("{byte:02x}"));
    }
    Ok(out)
}

pub(crate) fn make_ephemeral_go2rtc_session() -> Result<Go2RtcSession, String> {
    let ports = reserve_loopback_ports(2)?;
    Ok(Go2RtcSession {
        api_listen: format!("127.0.0.1:{}", ports[0]),
        rtsp_listen: format!("127.0.0.1:{}", ports[1]),
        username: random_credential_token()?,
        password: random_credential_token()?,
        nonce: random_credential_token()?,
    })
}

pub(crate) fn reserve_loopback_ports(count: usize) -> Result<Vec<u16>, String> {
    let mut listeners = Vec::new();
    let mut ports = Vec::new();
    for _ in 0..64 {
        if ports.len() == count {
            break;
        }
        let Ok(listener) = std::net::TcpListener::bind("127.0.0.1:0") else {
            continue;
        };
        let port = listener.local_addr().map_err(|e| e.to_string())?.port();
        if FORBIDDEN_GO2RTC_PORTS.contains(&port) || ports.contains(&port) {
            drop(listener);
            continue;
        }
        ports.push(port);
        listeners.push(listener);
    }
    drop(listeners);
    if ports.len() != count {
        return Err("无法分配本地流媒体端口".to_string());
    }
    Ok(ports)
}

pub(crate) fn serialize_owner(record: &Go2RtcOwnerRecord) -> String {
    format!(
        "pid={}\nnonce={}\napi_listen={}\nrtsp_listen={}\nusername={}\npassword={}\n",
        record.pid,
        record.session.nonce,
        record.session.api_listen,
        record.session.rtsp_listen,
        record.session.username,
        record.session.password
    )
}

pub(crate) fn parse_owner(text: &str) -> Option<Go2RtcOwnerRecord> {
    let mut fields = std::collections::HashMap::<String, String>::new();
    for line in text.split('\n') {
        let line = line.trim_end_matches('\r');
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        if !key.is_empty() && !value.is_empty() {
            fields.insert(key.to_string(), value.to_string());
        }
    }
    let pid = fields.get("pid")?.parse::<u32>().ok()?;
    let nonce = fields.get("nonce")?.clone();
    let api_listen = fields.get("api_listen")?.clone();
    let rtsp_listen = fields.get("rtsp_listen")?.clone();
    let username = fields.get("username")?.clone();
    let password = fields.get("password")?.clone();
    if !is_credential_token(&nonce)
        || !is_credential_token(&username)
        || !is_credential_token(&password)
        || !is_acceptable_go2rtc_listen(&api_listen)
        || !is_acceptable_go2rtc_listen(&rtsp_listen)
    {
        return None;
    }
    Some(Go2RtcOwnerRecord {
        pid,
        session: Go2RtcSession {
            api_listen,
            rtsp_listen,
            username,
            password,
            nonce,
        },
    })
}

pub(crate) fn read_owner() -> Option<Go2RtcOwnerRecord> {
    let path = go2rtc_owner_path()?;
    let text = std::fs::read_to_string(path).ok()?;
    parse_owner(&text)
}

pub(crate) fn process_is_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    #[cfg(windows)]
    {
        extern "system" {
            fn OpenProcess(access: u32, inherit: i32, pid: u32) -> *mut core::ffi::c_void;
            fn GetExitCodeProcess(handle: *mut core::ffi::c_void, code: *mut u32) -> i32;
            fn CloseHandle(handle: *mut core::ffi::c_void) -> i32;
        }
        const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
        const STILL_ACTIVE: u32 = 259;
        unsafe {
            let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
            if handle.is_null() {
                return false;
            }
            let mut code = 0u32;
            let ok = GetExitCodeProcess(handle, &mut code);
            CloseHandle(handle);
            ok != 0 && code == STILL_ACTIVE
        }
    }
    #[cfg(not(windows))]
    {
        let _ = pid;
        false
    }
}

pub(crate) fn terminate_pid(pid: u32) {
    if pid == 0 {
        return;
    }
    #[cfg(windows)]
    {
        extern "system" {
            fn OpenProcess(access: u32, inherit: i32, pid: u32) -> *mut core::ffi::c_void;
            fn TerminateProcess(handle: *mut core::ffi::c_void, code: u32) -> i32;
            fn CloseHandle(handle: *mut core::ffi::c_void) -> i32;
        }
        const PROCESS_TERMINATE: u32 = 0x0001;
        unsafe {
            let handle = OpenProcess(PROCESS_TERMINATE, 0, pid);
            if !handle.is_null() {
                TerminateProcess(handle, 1);
                CloseHandle(handle);
            }
        }
    }
}

pub(crate) struct OwnedApiProbe {
    reachable: bool,
    authenticated: bool,
    nonce_matches: bool,
}

pub(crate) async fn probe_owned(http: &reqwest::Client, session: &Go2RtcSession) -> OwnedApiProbe {
    let missed = OwnedApiProbe {
        reachable: false,
        authenticated: false,
        nonce_matches: false,
    };
    let Ok(response) = authed(http.get(api_url(session, "/api")), session).send().await else {
        return missed;
    };
    if !response.status().is_success() {
        return OwnedApiProbe {
            reachable: true,
            authenticated: false,
            nonce_matches: false,
        };
    }
    let Ok(config) = authed(http.get(api_url(session, "/api/config")), session)
        .send()
        .await
    else {
        return missed;
    };
    if !config.status().is_success() {
        return missed;
    }
    let Ok(body) = config.text().await else {
        return missed;
    };
    let needle = format!("nonce: {}", yaml_double_quoted(&session.nonce));
    OwnedApiProbe {
        reachable: true,
        authenticated: true,
        nonce_matches: body.contains(&needle),
    }
}

pub(crate) fn existing_owned_record() -> Option<Go2RtcOwnerRecord> {
    if let Some(pid) = running_child_pid() {
        if let Ok(guard) = GO2RTC_SESSION.lock() {
            if let Some(record) = guard.as_ref() {
                return Some(Go2RtcOwnerRecord {
                    pid,
                    session: record.session.clone(),
                });
            }
        }
    }
    if let Ok(guard) = GO2RTC_SESSION.lock() {
        if let Some(record) = guard.as_ref() {
            if process_is_alive(record.pid) {
                return Some(Go2RtcOwnerRecord {
                    pid: record.pid,
                    session: record.session.clone(),
                });
            }
        }
    }
    let saved = read_owner()?;
    if process_is_alive(saved.pid) {
        Some(saved)
    } else {
        None
    }
}

pub(crate) async fn request_owned_exit(http: &reqwest::Client, session: &Go2RtcSession) {
    let _ = authed(http.post(api_url(session, "/api/exit?code=0")), session)
        .send()
        .await;
}

pub(crate) async fn stop_owned_process(http: &reqwest::Client, record: &Go2RtcOwnerRecord) {
    request_owned_exit(http, &record.session).await;
    let child_pid = running_child_pid();
    if child_pid == Some(record.pid) {
        if let Ok(mut guard) = GO2RTC_PROCESS.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
    for _ in 0..20 {
        if !process_is_alive(record.pid) {
            break;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    if process_is_alive(record.pid) {
        terminate_pid(record.pid);
    }
    if let Ok(mut guard) = GO2RTC_SESSION.lock() {
        *guard = None;
    }
}

pub(crate) async fn wait_for_owned_api(http: &reqwest::Client, session: &Go2RtcSession) -> bool {
    for _ in 0..25 {
        std::thread::sleep(Duration::from_millis(120));
        let probe = probe_owned(http, session).await;
        if probe.authenticated && probe.nonce_matches {
            return true;
        }
        if probe.reachable && !probe.authenticated {
            return false;
        }
        if probe.authenticated && !probe.nonce_matches {
            return false;
        }
    }
    false
}

pub(crate) async fn spawn_fresh_go2rtc(
    app: &AppHandle,
    http: &reqwest::Client,
    bin_dir: &std::path::Path,
    ffmpeg_bin: &str,
) -> Result<(), String> {
    let exe_path = bin_dir.join("go2rtc.exe");
    if !ensure_component(app, bin_dir, "go2rtc.exe")? {
        download_go2rtc_win64(bin_dir, &exe_path).await?;
    }
    let cfg_path = bin_dir.join("go2rtc.yaml");
    let owner_path = bin_dir.join("go2rtc.owner");
    for _ in 0..4 {
        let session = make_ephemeral_go2rtc_session()?;
        let desired = desired_go2rtc_config(&session, ffmpeg_bin);
        write_secret_file(&cfg_path, &desired)?;
        let mut command = std::process::Command::new(&exe_path);
        command.arg("-c").arg(&cfg_path).current_dir(bin_dir);
        #[cfg(windows)]
        {
            command.creation_flags(CREATE_NO_WINDOW);
        }
        let child = command.spawn().map_err(|e| format!("启动 go2rtc 失败：{e}"))?;
        let pid = child.id();
        if let Ok(mut guard) = GO2RTC_PROCESS.lock() {
            *guard = Some(child);
        }
        let record = Go2RtcOwnerRecord { pid, session: session.clone() };
        write_secret_file(&owner_path, &serialize_owner(&record))?;
        store_go2rtc_session(record);
        if wait_for_owned_api(http, &session).await {
            return Ok(());
        }
        if let Ok(mut guard) = GO2RTC_PROCESS.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
        if let Ok(mut guard) = GO2RTC_SESSION.lock() {
            *guard = None;
        }
    }
    Err("本地流媒体服务启动超时".to_string())
}

pub(crate) async fn ensure_go2rtc_running(app: &AppHandle) -> Result<(), String> {
    let bin_dir = bin_directory()?;
    std::fs::create_dir_all(&bin_dir).map_err(|e| format!("创建组件目录失败：{e}"))?;
    let http = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_millis(800))
        .build()
        .map_err(|e| e.to_string())?;
    let ffmpeg_bin = resolve_ffmpeg_bin(app, &bin_dir)?;

    if let Some(record) = existing_owned_record() {
        let probe = probe_owned(&http, &record.session).await;
        let desired = desired_go2rtc_config(&record.session, &ffmpeg_bin);
        let existing = go2rtc_config_path()
            .and_then(|path| std::fs::read_to_string(path).ok())
            .unwrap_or_default();
        let changed = base_go2rtc_config(&existing) != base_go2rtc_config(&desired);
        let action = decide_go2rtc_action(Go2RtcOwnershipProbe {
            pid_alive: true,
            authenticated: probe.authenticated,
            nonce_matches: probe.nonce_matches,
            config_changed: changed,
        });
        match action {
            Go2RtcAction::ReuseOwned => {
                store_go2rtc_session(record);
                return Ok(());
            }
            Go2RtcAction::RestartOwned => {
                stop_owned_process(&http, &record).await;
            }
            Go2RtcAction::SpawnFresh => {
                if running_child_pid() == Some(record.pid) {
                    if let Ok(mut guard) = GO2RTC_PROCESS.lock() {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                    }
                    if let Ok(mut guard) = GO2RTC_SESSION.lock() {
                        *guard = None;
                    }
                }
            }
        }
    }

    spawn_fresh_go2rtc(app, &http, &bin_dir, &ffmpeg_bin).await
}

#[derive(Serialize)]
pub(crate) struct Go2RtcEndpoint {
    origin: String,
    authorization: String,
}

#[tauri::command]
pub(crate) async fn go2rtc_endpoint(app: AppHandle) -> Result<Go2RtcEndpoint, String> {
    let _bridge_order = CAMERA_BRIDGE_LOCK.lock().await;
    ensure_go2rtc_running(&app).await?;
    let session = current_go2rtc_session()?;
    let origin = format!("http://{}", session.api_listen);
    if !origin.starts_with("http://127.0.0.1:") || !is_acceptable_go2rtc_listen(&session.api_listen) {
        return Err("本地流媒体地址无效".to_string());
    }
    Ok(Go2RtcEndpoint {
        origin,
        authorization: basic_authorization_value(&session.username, &session.password),
    })
}

pub(crate) fn is_go2rtc_stream_name(src: &str) -> bool {
    !src.is_empty()
        && src.len() <= 128
        && src
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
}

#[cfg(test)]
pub(crate) fn go2rtc_jpeg_frame_url(session: &Go2RtcSession, src: &str) -> Result<String, String> {
    if !is_go2rtc_stream_name(src) {
        return Err("无效的摄像机流名称".to_string());
    }
    if !is_acceptable_go2rtc_listen(&session.api_listen) {
        return Err("本地流媒体地址无效".to_string());
    }
    Ok(api_url(
        session,
        &format!("/api/frame.jpeg?src={}", urlencoding_simple(src)),
    ))
}

pub(crate) fn go2rtc_mjpeg_stream_url(session: &Go2RtcSession, src: &str) -> Result<String, String> {
    if !is_go2rtc_stream_name(src) {
        return Err("无效的摄像机流名称".to_string());
    }
    if !is_acceptable_go2rtc_listen(&session.api_listen) {
        return Err("本地流媒体地址无效".to_string());
    }
    Ok(api_url(
        session,
        &format!("/api/stream.mjpeg?src={}", urlencoding_simple(src)),
    ))
}

const MAX_JPEG_FRAME_BYTES: usize = 4 * 1024 * 1024;
const MJPEG_FRAME_WAIT_TIMEOUT: Duration = Duration::from_secs(10);
const MJPEG_STEADY_FRAME_WAIT: Duration = Duration::from_millis(1500);
const MJPEG_CONCEALMENT_GRACE: Duration = Duration::from_millis(5500);
const MJPEG_READER_IDLE_TIMEOUT: Duration = Duration::from_secs(15);

/// Extracts complete JPEG frames (`FF D8 ... FF D9`) from a streaming `multipart/x-mixed-replace`
/// byte buffer. Segment lengths before `SOS` (`FF DA`) are skipped so `FF D9` bytes inside header
/// metadata never truncate a frame early; after `SOS`, the entropy scan only contains stuffed
/// `FF 00`, fill bytes (`FF FF`), or restart markers (`FF D0`..`FF D7`) until `EOI` (`FF D9`).
pub(crate) fn drain_complete_jpeg_frames(buffer: &mut Vec<u8>) -> Vec<Vec<u8>> {
    let mut frames = Vec::new();
    let mut consumed = 0usize;

    loop {
        let slice = &buffer[consumed..];
        let Some(soi_rel) = slice.windows(2).position(|w| w == [0xFF, 0xD8]) else {
            let keep_tail = usize::from(slice.last() == Some(&0xFF));
            consumed = buffer.len().saturating_sub(keep_tail);
            break;
        };
        let soi = consumed + soi_rel;
        consumed = soi;

        let mut cursor = soi + 2;
        let mut in_scan = false;
        let mut found_eoi = None;
        let mut restart_at_soi = None;

        while cursor + 1 < buffer.len() {
            if !in_scan {
                if buffer[cursor] != 0xFF {
                    // Valid JPEG headers only contain marker segments between SOI and SOS;
                    // a non-0xFF byte means this SOI was truncated or false.
                    restart_at_soi = Some(cursor);
                    break;
                }
                while cursor + 1 < buffer.len() && buffer[cursor + 1] == 0xFF {
                    cursor += 1;
                }
                if cursor + 1 >= buffer.len() {
                    break;
                }
                let marker_pos = cursor;
                let marker = buffer[cursor + 1];
                cursor += 2;
                match marker {
                    0xD8 => {
                        // A new SOI before SOS means the previous header was truncated; restart there.
                        restart_at_soi = Some(marker_pos);
                        break;
                    }
                    0x00 | 0xD9 => {
                        restart_at_soi = Some(cursor);
                        break;
                    }
                    0x01 | 0xD0..=0xD7 => continue,
                    0xDA => {
                        if cursor + 2 > buffer.len() {
                            break;
                        }
                        let seg_len = u16::from_be_bytes([buffer[cursor], buffer[cursor + 1]]) as usize;
                        if seg_len < 2 {
                            restart_at_soi = Some(cursor + 2);
                            break;
                        }
                        if cursor + seg_len > buffer.len() {
                            break;
                        }
                        cursor += seg_len;
                        in_scan = true;
                    }
                    _ => {
                        if cursor + 2 > buffer.len() {
                            break;
                        }
                        let seg_len = u16::from_be_bytes([buffer[cursor], buffer[cursor + 1]]) as usize;
                        if seg_len < 2 {
                            restart_at_soi = Some(cursor + 2);
                            break;
                        }
                        if cursor + seg_len > buffer.len() {
                            break;
                        }
                        cursor += seg_len;
                    }
                }
            } else {
                let Some(ff_rel) = buffer[cursor..].iter().position(|&b| b == 0xFF) else {
                    break;
                };
                let ff = cursor + ff_rel;
                if ff + 1 >= buffer.len() {
                    break;
                }
                match buffer[ff + 1] {
                    0xD9 => {
                        found_eoi = Some(ff + 2);
                        break;
                    }
                    0xD8 => {
                        // A new SOI before EOI means the previous frame was truncated; drop it.
                        restart_at_soi = Some(ff);
                        break;
                    }
                    0xFF => {
                        // Advance by 1 byte on fill bytes so `FF FF D9` / `FF FF D8` is not skipped.
                        cursor = ff + 1;
                    }
                    _ => {
                        cursor = ff + 2;
                    }
                }
            }
        }

        if let Some(next_soi) = restart_at_soi {
            consumed = next_soi;
            continue;
        }
        let Some(end) = found_eoi else {
            break;
        };
        let frame_len = end - soi;
        if frame_len <= MAX_JPEG_FRAME_BYTES {
            frames.push(buffer[soi..end].to_vec());
        }
        consumed = end;
    }

    if consumed > 0 {
        buffer.drain(..consumed);
    }
    if buffer.len() > MAX_JPEG_FRAME_BYTES * 2 {
        let keep_from = buffer.len() - MAX_JPEG_FRAME_BYTES;
        buffer.drain(..keep_from);
    }
    frames
}

/// Detects FFmpeg's initial HEVC error-concealed gray frames (`Y=128, U=128, V=128`) across the
/// initial GOP before the first clean IDR keyframe arrives. In baseline 4:2:0 MJPEG, each flat
/// 16x16 MCU encodes six zero-AC blocks as 32 bits (`4` bytes) under default Annex K Huffman
/// tables (`0x28A28A00` cyclically shifted) or 12 zero bits (`0x00000000` every 4 bytes) under
/// FFmpeg 7.x optimal Huffman tables. Across a 4-second HEVC GOP, inter-prediction residuals on
/// concealed P-frames gradually reduce adjacent 4-byte word repetition from ~62% down to ~12.4%,
/// while real camera scenes measure ~1.0-1.5% adjacent word repetition (`max_run <= 5`).
pub(crate) fn is_likely_concealed_gray_jpeg(jpeg: &[u8]) -> bool {
    if jpeg.len() < 4096 || !jpeg.starts_with(&[0xFF, 0xD8]) {
        return false;
    }
    let mut cursor = 2usize;
    let mut scan_start = None;
    while cursor + 1 < jpeg.len() {
        if jpeg[cursor] != 0xFF {
            cursor += 1;
            continue;
        }
        while cursor + 1 < jpeg.len() && jpeg[cursor + 1] == 0xFF {
            cursor += 1;
        }
        if cursor + 1 >= jpeg.len() {
            break;
        }
        let marker = jpeg[cursor + 1];
        cursor += 2;
        match marker {
            0x00 | 0x01 | 0xD0..=0xD8 => continue,
            0xD9 => break,
            0xDA => {
                if cursor + 2 <= jpeg.len() {
                    let seg_len = u16::from_be_bytes([jpeg[cursor], jpeg[cursor + 1]]) as usize;
                    if seg_len >= 2 && cursor + seg_len <= jpeg.len() {
                        scan_start = Some(cursor + seg_len);
                    }
                }
                break;
            }
            _ => {
                if cursor + 2 > jpeg.len() {
                    break;
                }
                let seg_len = u16::from_be_bytes([jpeg[cursor], jpeg[cursor + 1]]) as usize;
                if seg_len < 2 || cursor + seg_len > jpeg.len() {
                    break;
                }
                cursor += seg_len;
            }
        }
    }
    let Some(start) = scan_start else {
        return false;
    };
    let end = jpeg.len().saturating_sub(2);
    if end <= start {
        return false;
    }
    let scan = &jpeg[start..end];
    let mut chunks = scan.chunks_exact(4);
    let Some(first) = chunks.next() else {
        return false;
    };
    let mut prev = u32::from_be_bytes([first[0], first[1], first[2], first[3]]);
    let mut total = 0usize;
    let mut same_as_prev = 0usize;
    let mut zero_words = usize::from(prev == 0);
    let mut cur_run = 0usize;
    let mut max_run = 0usize;
    let mut long_run_words = 0usize;
    for chunk in chunks {
        let word = u32::from_be_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
        if word == 0 {
            zero_words += 1;
        }
        if word == prev {
            same_as_prev += 1;
            cur_run += 1;
            if cur_run > max_run {
                max_run = cur_run;
            }
        } else {
            if cur_run >= 16 {
                long_run_words += cur_run;
            }
            cur_run = 0;
        }
        prev = word;
        total += 1;
    }
    if cur_run >= 16 {
        long_run_words += cur_run;
    }
    total >= 512
        && (same_as_prev * 100 >= total * 5
            || zero_words * 100 >= total * 10
            || max_run >= 64
            || long_run_words >= 512)
}

struct MjpegStreamState {
    seq: u64,
    last_returned_seq: u64,
    latest_jpeg: Option<std::sync::Arc<Vec<u8>>>,
    fallback_jpeg: Option<std::sync::Arc<Vec<u8>>>,
    first_concealed_at: Option<std::time::Instant>,
    last_frame_at: Option<std::time::Instant>,
    last_polled_at: std::time::Instant,
    error: Option<String>,
    closed: bool,
}

/// Keeps a single `GET /api/stream.mjpeg?src=...` connection open per stream so go2rtc sees a
/// continuous consumer. Polling one-shot `/api/frame.jpeg` drops the consumer count to 0 after
/// every frame, which makes go2rtc kill `ffmpeg-silent.exe` and close the Xiaomi CS2 P2P session
/// right after the initial gray error-concealed frame.
struct MjpegStreamReader {
    url: String,
    username: String,
    password: String,
    stop: std::sync::Arc<AtomicBool>,
    state: std::sync::Arc<std::sync::Mutex<MjpegStreamState>>,
    task: std::sync::Mutex<Option<tauri::async_runtime::JoinHandle<()>>>,
}

static CAMERA_MJPEG_STREAMS: LazyLock<std::sync::Mutex<HashMap<String, std::sync::Arc<MjpegStreamReader>>>> =
    LazyLock::new(|| std::sync::Mutex::new(HashMap::new()));

pub(crate) fn stop_mjpeg_streams(names: &[&str]) {
    if let Ok(mut streams) = CAMERA_MJPEG_STREAMS.lock() {
        for name in names {
            if let Some(reader) = streams.remove(*name) {
                reader.shutdown();
            }
        }
    }
}

pub(crate) fn stop_all_mjpeg_streams() {
    if let Ok(mut streams) = CAMERA_MJPEG_STREAMS.lock() {
        for (_, reader) in streams.drain() {
            reader.shutdown();
        }
    }
}

impl MjpegStreamReader {
    fn spawn(session: &Go2RtcSession, url: String, concealment_grace: Duration) -> std::sync::Arc<Self> {
        let stop = std::sync::Arc::new(AtomicBool::new(false));
        let now = std::time::Instant::now();
        let state = std::sync::Arc::new(std::sync::Mutex::new(MjpegStreamState {
            seq: 0,
            last_returned_seq: 0,
            latest_jpeg: None,
            fallback_jpeg: None,
            first_concealed_at: None,
            last_frame_at: None,
            last_polled_at: now,
            error: None,
            closed: false,
        }));
        let reader = std::sync::Arc::new(Self {
            url: url.clone(),
            username: session.username.clone(),
            password: session.password.clone(),
            stop: std::sync::Arc::clone(&stop),
            state: std::sync::Arc::clone(&state),
            task: std::sync::Mutex::new(None),
        });

        let session_clone = session.clone();
        let handle = tauri::async_runtime::spawn(async move {
            let http = match reqwest::Client::builder()
                .no_proxy()
                .connect_timeout(Duration::from_secs(5))
                .build()
            {
                Ok(client) => client,
                Err(error) => {
                    if let Ok(mut guard) = state.lock() {
                        guard.error = Some(error.to_string());
                        guard.closed = true;
                    }
                    return;
                }
            };

            let mut response = match tokio::time::timeout(
                Duration::from_secs(8),
                authed(http.get(&url), &session_clone).send(),
            )
            .await
            {
                Ok(Ok(resp)) => resp,
                Ok(Err(error)) => {
                    if let Ok(mut guard) = state.lock() {
                        guard.error = Some(error.to_string());
                        guard.closed = true;
                    }
                    return;
                }
                Err(_) => {
                    if let Ok(mut guard) = state.lock() {
                        guard.error = Some("连接摄像机画面流超时".to_string());
                        guard.closed = true;
                    }
                    return;
                }
            };

            if !response.status().is_success() {
                if let Ok(mut guard) = state.lock() {
                    guard.error = Some(format!("读取摄像机画面失败 (HTTP {})", response.status()));
                    guard.closed = true;
                }
                return;
            }

            let mut buffer = Vec::with_capacity(256 * 1024);
            while !stop.load(Ordering::SeqCst) {
                if let Ok(guard) = state.lock() {
                    if guard.last_polled_at.elapsed() > MJPEG_READER_IDLE_TIMEOUT {
                        break;
                    }
                }
                match tokio::time::timeout(Duration::from_secs(10), response.chunk()).await {
                    Ok(Ok(Some(chunk))) => {
                        buffer.extend_from_slice(&chunk);
                        let frames = drain_complete_jpeg_frames(&mut buffer);
                        if frames.is_empty() {
                            continue;
                        }
                        if let Ok(mut guard) = state.lock() {
                            let frame_now = std::time::Instant::now();
                            for jpeg in frames {
                                let suppress_concealed = guard.latest_jpeg.is_none()
                                    && guard
                                        .first_concealed_at
                                        .map_or(true, |since| since.elapsed() < concealment_grace)
                                    && is_likely_concealed_gray_jpeg(&jpeg);
                                if suppress_concealed {
                                    if guard.first_concealed_at.is_none() {
                                        guard.first_concealed_at = Some(frame_now);
                                    }
                                    guard.fallback_jpeg = Some(std::sync::Arc::new(jpeg));
                                } else {
                                    guard.seq += 1;
                                    guard.latest_jpeg = Some(std::sync::Arc::new(jpeg));
                                    guard.fallback_jpeg = None;
                                    guard.last_frame_at = Some(frame_now);
                                    guard.error = None;
                                }
                            }
                        }
                    }
                    Ok(Ok(None)) => {
                        if let Ok(mut guard) = state.lock() {
                            if guard.latest_jpeg.is_none() {
                                if let Some(fallback) = guard.fallback_jpeg.take() {
                                    guard.seq += 1;
                                    guard.latest_jpeg = Some(fallback);
                                    guard.last_frame_at = Some(std::time::Instant::now());
                                }
                            }
                            if guard.error.is_none() {
                                guard.error = Some("摄像机流已结束".to_string());
                            }
                            guard.closed = true;
                        }
                        return;
                    }
                    Ok(Err(error)) => {
                        if let Ok(mut guard) = state.lock() {
                            guard.error = Some(error.to_string());
                            guard.closed = true;
                        }
                        return;
                    }
                    Err(_) => {
                        if let Ok(mut guard) = state.lock() {
                            guard.error = Some("读取摄像机画面超时".to_string());
                            guard.closed = true;
                        }
                        return;
                    }
                }
            }

            if let Ok(mut guard) = state.lock() {
                guard.closed = true;
            }
        });

        if let Ok(mut task_guard) = reader.task.lock() {
            *task_guard = Some(handle);
        }

        reader
    }

    fn shutdown(&self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Ok(mut guard) = self.state.lock() {
            guard.closed = true;
            if guard.error.is_none() {
                guard.error = Some("摄像机流已停止".to_string());
            }
        }
        if let Ok(mut task_guard) = self.task.lock() {
            if let Some(handle) = task_guard.take() {
                handle.abort();
            }
        }
    }

    fn matches(&self, url: &str, session: &Go2RtcSession) -> bool {
        !self.stop.load(Ordering::SeqCst)
            && self.url == url
            && self.username == session.username
            && self.password == session.password
            && self.state.lock().map_or(false, |guard| !guard.closed)
    }

    async fn next_frame_jpeg(
        &self,
        timeout: Duration,
        concealment_grace: Duration,
    ) -> Result<std::sync::Arc<Vec<u8>>, String> {
        let start = std::time::Instant::now();
        loop {
            if self.stop.load(Ordering::SeqCst) {
                return Err("摄像机流已停止".to_string());
            }
            if let Ok(mut guard) = self.state.lock() {
                let now = std::time::Instant::now();
                guard.last_polled_at = now;
                if guard.latest_jpeg.is_none() {
                    if let Some(since) = guard.first_concealed_at {
                        if since.elapsed() >= concealment_grace {
                            if let Some(fallback) = guard.fallback_jpeg.take() {
                                guard.seq += 1;
                                guard.latest_jpeg = Some(fallback);
                                guard.last_frame_at = Some(now);
                            }
                        }
                    }
                }

                if guard.seq > guard.last_returned_seq {
                    if let Some(frame) = guard.latest_jpeg.as_ref().cloned() {
                        guard.last_returned_seq = guard.seq;
                        return Ok(frame);
                    }
                }

                if guard.closed {
                    return Err(guard
                        .error
                        .clone()
                        .unwrap_or_else(|| "摄像机流已断开".to_string()));
                }

                if let Some(frame) = guard.latest_jpeg.as_ref() {
                    let frame_still_fresh = guard
                        .last_frame_at
                        .is_some_and(|at| at.elapsed() < timeout);
                    if frame_still_fresh && start.elapsed() >= timeout.min(MJPEG_STEADY_FRAME_WAIT) {
                        return Ok(std::sync::Arc::clone(frame));
                    }
                }
            } else {
                return Err("摄像机流状态不可用".to_string());
            }

            if start.elapsed() >= timeout {
                if let Ok(mut guard) = self.state.lock() {
                    if let Some(fallback) = guard.fallback_jpeg.take() {
                        guard.seq += 1;
                        guard.last_returned_seq = guard.seq;
                        guard.latest_jpeg = Some(std::sync::Arc::clone(&fallback));
                        guard.last_frame_at = Some(std::time::Instant::now());
                        return Ok(fallback);
                    }
                }
                return Err("等待摄像机画面超时".to_string());
            }

            tokio::time::sleep(Duration::from_millis(12)).await;
        }
    }
}

pub(crate) async fn fetch_mjpeg_stream_frame(
    session: &Go2RtcSession,
    src: &str,
    timeout: Duration,
    concealment_grace: Duration,
) -> Result<std::sync::Arc<Vec<u8>>, String> {
    let url = go2rtc_mjpeg_stream_url(session, src)?;
    let reader = {
        let mut streams = CAMERA_MJPEG_STREAMS
            .lock()
            .map_err(|_| "摄像机流列表不可用".to_string())?;
        if let Some(existing) = streams.get(src) {
            if existing.matches(&url, session) {
                std::sync::Arc::clone(existing)
            } else {
                existing.shutdown();
                let fresh = MjpegStreamReader::spawn(session, url, concealment_grace);
                streams.insert(src.to_string(), std::sync::Arc::clone(&fresh));
                fresh
            }
        } else {
            let fresh = MjpegStreamReader::spawn(session, url, concealment_grace);
            streams.insert(src.to_string(), std::sync::Arc::clone(&fresh));
            fresh
        }
    };

    match reader.next_frame_jpeg(timeout, concealment_grace).await {
        Ok(frame) => Ok(frame),
        Err(error) => {
            reader.shutdown();
            if let Ok(mut streams) = CAMERA_MJPEG_STREAMS.lock() {
                if streams
                    .get(src)
                    .is_some_and(|current| std::sync::Arc::ptr_eq(current, &reader))
                {
                    streams.remove(src);
                }
            }
            Err(error)
        }
    }
}

#[tauri::command]
pub(crate) async fn go2rtc_jpeg_frame(src: String) -> Result<String, String> {
    let session = current_go2rtc_session()?;
    let bytes = fetch_mjpeg_stream_frame(
        &session,
        &src,
        MJPEG_FRAME_WAIT_TIMEOUT,
        MJPEG_CONCEALMENT_GRACE,
    )
    .await?;
    if bytes.len() > MAX_JPEG_FRAME_BYTES {
        return Err("摄像机画面过大".to_string());
    }
    Ok(base64_encode(&bytes))
}

/// Caps inbound CS2 command-channel (`channel = 0`) `DRW` packets forwarded to `go2rtc`.
///
/// After the initial command handshake (`CMD 102`login, `CMD 100` start video, and response),
/// `go2rtc` stops reading channel 0 while only reading video channel 1 and audio channel 2.
/// Xiaomi cameras continue sending periodic channel-0 status `DRW` messages; once 6 accumulate
/// in `go2rtc`'s 6-slot channel-0 buffer, `go2rtc` aborts the entire session with
/// `cs2: pop buffer is full`. For channel-0 packets past the first 6 unique sequence numbers,
/// the relay drops the packet before `go2rtc` and replies with `DRW_ACK` directly to the camera.
pub(crate) struct Cs2CommandChannelGate {
    allowed_sequences: Vec<u16>,
}

impl Cs2CommandChannelGate {
    pub(crate) const PASSTHROUGH_LIMIT: usize = 6;

    pub(crate) fn new() -> Self {
        Self {
            allowed_sequences: Vec::with_capacity(Self::PASSTHROUGH_LIMIT),
        }
    }

    pub(crate) fn reset(&mut self) {
        self.allowed_sequences.clear();
    }

    /// Inspects an inbound camera UDP packet and returns `Some((seq_hi, seq_lo))` when it is a
    /// channel-0 `DRW` packet that must be dropped before `go2rtc` and acknowledged directly.
    pub(crate) fn should_drop(&mut self, buf: &[u8]) -> Option<(u8, u8)> {
        if buf.len() < 8 || buf[0] != 0xF1 || buf[1] != 0xD0 {
            return None;
        }
        // DRW body starts at offset 4: `0xD1`, channel, seq_hi, seq_lo.
        if buf[4] != 0xD1 || buf[5] != 0x00 {
            return None;
        }
        let seq_hi = buf[6];
        let seq_lo = buf[7];
        let seq = u16::from_be_bytes([seq_hi, seq_lo]);
        if self.allowed_sequences.contains(&seq) {
            return None;
        }
        if self.allowed_sequences.len() < Self::PASSTHROUGH_LIMIT {
            self.allowed_sequences.push(seq);
            return None;
        }
        Some((seq_hi, seq_lo))
    }

    pub(crate) fn drw_ack(seq_hi: u8, seq_lo: u8) -> [u8; 10] {
        [0xF1, 0xD1, 0x00, 0x06, 0xD1, 0x00, 0x00, 0x01, seq_hi, seq_lo]
    }
}

fn configure_cs2_udp_socket(sock: &std::net::UdpSocket) {
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawSocket;
        const SOL_SOCKET: i32 = 0xFFFF;
        const SO_SNDBUF: i32 = 0x1001;
        const SO_RCVBUF: i32 = 0x1002;
        const SIO_UDP_CONNRESET: u32 = 0x9800_000C;

        extern "system" {
            fn setsockopt(
                s: usize,
                level: i32,
                optname: i32,
                optval: *const u8,
                optlen: i32,
            ) -> i32;
            fn WSAIoctl(
                s: usize,
                dwIoControlCode: u32,
                lpvInBuffer: *const core::ffi::c_void,
                cbInBuffer: u32,
                lpvOutBuffer: *mut core::ffi::c_void,
                cbOutBuffer: u32,
                lpcbBytesReturned: *mut u32,
                lpOverlapped: *mut core::ffi::c_void,
                lpCompletionRoutine: *mut core::ffi::c_void,
            ) -> i32;
        }

        let raw = sock.as_raw_socket() as usize;
        // 2.5K/4K HEVC keyframes arrive as back-to-back ~1 KB UDP datagrams that overflow the
        // 64 KB Windows default socket buffer and drop slices before user space can read them.
        let buf_size: i32 = 4 * 1024 * 1024;
        let disable_connreset: u32 = 0;
        let mut bytes_returned: u32 = 0;
        unsafe {
            let _ = setsockopt(
                raw,
                SOL_SOCKET,
                SO_RCVBUF,
                (&buf_size as *const i32).cast::<u8>(),
                core::mem::size_of::<i32>() as i32,
            );
            let _ = setsockopt(
                raw,
                SOL_SOCKET,
                SO_SNDBUF,
                (&buf_size as *const i32).cast::<u8>(),
                core::mem::size_of::<i32>() as i32,
            );
            // Disable Windows UDP `WSAECONNRESET` (10054) triggered by ICMP Port Unreachable
            // after broadcasting LAN_SEARCH to `.255`.
            let _ = WSAIoctl(
                raw,
                SIO_UDP_CONNRESET,
                (&disable_connreset as *const u32).cast::<core::ffi::c_void>(),
                core::mem::size_of::<u32>() as u32,
                core::ptr::null_mut(),
                0,
                &mut bytes_returned,
                core::ptr::null_mut(),
                core::ptr::null_mut(),
            );
        }
    }
}

pub(crate) fn spawn_cs2_udp_relay(
    cam_ip: std::net::Ipv4Addr,
) -> Result<(u16, std::sync::Arc<AtomicBool>), String> {
    use std::net::{SocketAddrV4, UdpSocket};
    use std::sync::Arc;

    // Discover the local IPv4 interface that routes to cam_ip
    let probe = UdpSocket::bind("0.0.0.0:0").map_err(|e| e.to_string())?;
    probe
        .connect(SocketAddrV4::new(cam_ip, 32108))
        .map_err(|e| e.to_string())?;
    let local_ip = match probe.local_addr().map_err(|e| e.to_string())?.ip() {
        std::net::IpAddr::V4(v4) => v4,
        _ => std::net::Ipv4Addr::UNSPECIFIED,
    };
    drop(probe);

    let octets = cam_ip.octets();
    let broadcast_ip = std::net::Ipv4Addr::new(octets[0], octets[1], octets[2], 255);

    let local_relay = UdpSocket::bind("127.0.0.1:0").map_err(|e| e.to_string())?;
    let relay_port = local_relay.local_addr().map_err(|e| e.to_string())?.port();
    configure_cs2_udp_socket(&local_relay);
    local_relay
        .set_nonblocking(true)
        .map_err(|e| e.to_string())?;

    let lan_sock = UdpSocket::bind(SocketAddrV4::new(local_ip, 0)).map_err(|e| e.to_string())?;
    lan_sock.set_broadcast(true).map_err(|e| e.to_string())?;
    configure_cs2_udp_socket(&lan_sock);
    lan_sock
        .set_nonblocking(true)
        .map_err(|e| e.to_string())?;

    let stop = Arc::new(AtomicBool::new(false));
    let stop_for_thread = Arc::clone(&stop);
    std::thread::spawn(move || {
        let mut go2rtc_addr: Option<std::net::SocketAddr> = None;
        let mut cam_dynamic_port: u16 = 32108;
        let mut cmd_gate = Cs2CommandChannelGate::new();
        let mut buf = vec![0u8; 65_536];

        while !stop_for_thread.load(Ordering::SeqCst) {
            let mut transferred = false;

            loop {
                match local_relay.recv_from(&mut buf) {
                    Ok((n, src)) => {
                        transferred = true;
                        go2rtc_addr = Some(src);
                        if n >= 2 && buf[0] == 0xF1 && buf[1] == 0x30 {
                            cmd_gate.reset();
                            let _ =
                                lan_sock.send_to(&buf[..n], SocketAddrV4::new(broadcast_ip, 32108));
                            let _ = lan_sock.send_to(&buf[..n], SocketAddrV4::new(cam_ip, 32108));
                        } else {
                            let _ = lan_sock
                                .send_to(&buf[..n], SocketAddrV4::new(cam_ip, cam_dynamic_port));
                        }
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(ref e) if matches!(e.raw_os_error(), Some(10040 | 10054)) => continue,
                    Err(_) => break,
                }
            }

            loop {
                match lan_sock.recv_from(&mut buf) {
                    Ok((n, src)) => {
                        transferred = true;
                        if src.ip() == std::net::IpAddr::V4(cam_ip) {
                            cam_dynamic_port = src.port();
                            if let Some((seq_hi, seq_lo)) = cmd_gate.should_drop(&buf[..n]) {
                                let ack = Cs2CommandChannelGate::drw_ack(seq_hi, seq_lo);
                                let _ = lan_sock
                                    .send_to(&ack, SocketAddrV4::new(cam_ip, cam_dynamic_port));
                                continue;
                            }
                            if let Some(client_addr) = go2rtc_addr {
                                let _ = local_relay.send_to(&buf[..n], client_addr);
                            }
                        }
                    }
                    Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(ref e) if matches!(e.raw_os_error(), Some(10040 | 10054)) => continue,
                    Err(_) => break,
                }
            }

            if !transferred {
                std::thread::sleep(Duration::from_millis(1));
            }
        }
    });

    Ok((relay_port, stop))
}
