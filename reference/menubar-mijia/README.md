English | [简体中文](README.zh-CN.md)

<p align="center">
  <a href="https://trendshift.io/repositories/309141?utm_source=trendshift-badge&amp;utm_medium=badge&amp;utm_campaign=badge-trendshift-309141" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/309141/daily?language=Swift" alt="jryang1997%2FMenuBarMijia | Trendshift" width="250" height="55"/></a>
  <a href="https://trendshift.io/repositories/309141?utm_source=trendshift-badge&amp;utm_medium=badge&amp;utm_campaign=badge-trendshift-309141" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/309141/weekly?language=Swift" alt="jryang1997%2FMenuBarMijia | Trendshift" width="250" height="55"/></a>
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
    <img src="docs/assets/logo.svg" width="120" alt="MenuBarMijia Logo">
  </picture>
</p>

<h1 align="center">MenuBarMijia</h1>

> [!IMPORTANT]
> **Not affiliated with Xiaomi.** MenuBarMijia is an independent open-source project, not an official Xiaomi or Mijia product, and is not affiliated with, endorsed by, or sponsored by Xiaomi Corporation or its affiliates. "Xiaomi", "Mijia", "Mi Home", and "米家" are trademarks of Xiaomi Corporation and its affiliates. The project uses these names only to describe compatibility with Xiaomi / Mijia devices; no endorsement is implied.

<p align="center">
  <b>Control your entire home right from your computer, without picking up your phone.</b><br>
  A native macOS menu-bar app for Xiaomi / Mijia smart home devices, with a companion Windows 11 tray build.<br>
  Scan a QR code to connect directly to Mi Home without setting up Home Assistant. Features keyboard fuzzy search, push-to-talk voice, Mac event automation, and one-click undo.
</p>

<p align="center">
  <a href="https://gitlab.com/jryang1997/MenuBarMijia/-/starrers"><img src="https://img.shields.io/gitlab/stars/jryang1997%2FMenuBarMijia?style=social" alt="GitLab Stars"></a>
  <a href="https://gitlab.com/jryang1997/MenuBarMijia/-/releases"><img src="https://img.shields.io/gitlab/v/release/jryang1997%2FMenuBarMijia?color=0A84FF&label=release" alt="Latest Release"></a>
  <img src="https://img.shields.io/badge/macOS-14%2B-000000?logo=apple&logoColor=white" alt="macOS 14+">
  <img src="https://img.shields.io/badge/Windows_11-Tauri_v2-0078D4?logo=windows11&logoColor=white" alt="Windows 11">
  <img src="https://img.shields.io/badge/Swift-6-F05138?logo=swift&logoColor=white" alt="Swift 6">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="MIT License">
</p>

<p align="center">
  <a href="https://gitlab.com/jryang1997/MenuBarMijia/-/releases">Download latest</a> ·
  <a href="#quick-start">Install & Connect</a> ·
  <a href="#features--platform-matrix">Platform Comparison</a> ·
  <a href="#safety-privacy--limitations">Safety & Privacy</a> ·
  <a href="CHANGELOG.md">Changelog</a>
</p>

> **Moved to GitLab (2026-11):** the GitHub account behind this project was suspended, so the repository now lives at [gitlab.com/jryang1997/MenuBarMijia](https://gitlab.com/jryang1997/MenuBarMijia). Installers and the Homebrew tap are being republished on [GitLab Releases](https://gitlab.com/jryang1997/MenuBarMijia/-/releases). Installed builds keep working, but their update check still points at GitHub, so install the next version by hand once.

---

<p align="center">
  <img src="docs/assets/readme-v2/hero-keyboard.gif" alt="macOS demo: open the panel, search for a study lamp by pinyin initials, and turn it on" width="900">
</p>

<sub>macOS demo mode with simulated devices.</sub>

---

## Why Put Smart Home Control on Your Desktop?

When coding, writing, or in a meeting, you don't need to reach for your phone:

1. **Keep Your Hands on the Keyboard:** Press `⌥M` to summon the panel, type a name or pinyin initials (e.g. `sftd` for "Study Desk Lamp"), and hit `Return` to toggle. Open a device’s details and use the arrow keys to adjust brightness or AC temperature. No scrolling through nested room trees.
2. **Push-to-Talk, Release to Run:** Hold `⌥M` or the menu bar icon to speak your command, and release when you are done. Everyday commands like switching lights or setting the thermostat are interpreted by local parsing rules—no wake words, no sluggish voice chatter. Optional AI backends handle multi-step requests.
3. **Sync with Your Daily Work Rhythm:** Automatically turn off lights when you lock your Mac or step away. Trigger your focus lighting when plugging in an external display. All automation rules are disabled by default; you stay in full control of what triggers when.
4. **All Off, with Undo:** Hold "All Off" when it is time to call it a day. Plugs and robot vacuums stay on by default, and if you change your mind, the undo button brings your devices back.

---

## Feature Demos

Here are a few everyday actions in the macOS demo home. The interface shown is in Chinese.

### 1. Push-to-Talk Voice Control

No wake words needed. Hold the menu bar icon or global shortcut to speak, and see execution receipts right away (showing local millisecond execution or AI resolution).

<p align="center">
  <img src="docs/assets/readme-v2/voice-ptt.gif" alt="macOS demo: hold the shortcut, speak, and switch the study lamp to moonlight mode" width="900">
</p>

### 2. Room All-Off with Safety Protection & Undo

Heading out or turning in? Hold "All Off" for 0.6 seconds to switch off participating devices in the current view. Plugs, robot vacuums, and cameras are left alone by default, and you can change that in settings. If you switch something off by mistake, tap Undo.

<p align="center">
  <img src="docs/assets/readme-v2/undo-all-off.gif" alt="macOS demo: hold All Off, leave the plug on, and undo" width="900">
</p>

### 3. macOS Display & Lock-Screen Automation

Turn everyday desktop transitions into ambient triggers. Configure a rule to turn off lights when you lock your screen or put your Mac to sleep, and another for returning. Mac-only rules can also react to camera/microphone use, AC power and battery thresholds, the Wi-Fi network name, Focus mode, upcoming calendar meetings, and late-night use. All of them are off by default; Wi-Fi name, Focus status, and Calendar require system permission before their rules fire.

<p align="center">
  <img src="docs/assets/readme-v2/automation-away.gif" alt="macOS automation demo: lock the screen and turn off the living room light" width="900">
</p>

### 4. Climate Trends & Energy Metrics

Wondering whether the room is warmer than yesterday? On macOS, open a sensor to see its temperature and humidity trends over 24 hours or 7 days. For plugs that report energy readings, device details show today’s usage and settings show the last 7 days.

<p align="center">
  <img src="docs/assets/readme-v2/sensor-chart.gif" alt="macOS demo: inspect a sensor trend over 24 hours and 7 days" width="900">
</p>

---

## Features & Platform Matrix

The macOS version is built natively in Swift 6, while the Windows 11 client is an independent implementation using Tauri v2 and TypeScript. Both share verified golden fixtures (`Tests/Fixtures/`) for protocol and intent matching, while respecting system platform differences.

| Feature Area | macOS Native (14+) | Windows 11 Tray | Details |
| :--- | :---: | :---: | :--- |
| **Mi Home Direct QR Connect** | ✅ Supported | ✅ Supported | Scan with Mi Home app; supports CN, SG, DE, US, and RU regional servers; loads homes you own plus homes shared with your account |
| **Home Assistant Integration** | ✅ Supported | ✅ Supported | Native WebSocket connection with `xiaomi_home` entity synthesis and physical-device grouping |
| **Keyboard Search & Control** | ✅ Supported | ✅ Supported | Search by device name, room, or pinyin initials. Arrow keys select and adjust devices. Enter toggles a result or runs a command |
| **Global Activation Hotkey** | ✅ `⌥M` (Customizable) | ✅ `Alt+M` (Customizable) | Windows 1.1.4 adds custom key recording with collision detection |
| **Voice Interaction** | ✅ Menu bar hold / Hotkey hold / Hands-free | ⚠️ Hands-free only | macOS supports native Speech frameworks; Windows uses WebView speech recognition |
| **Local Command Parsing** | ✅ Supported | ✅ Supported | Power, brightness, temp, fan speeds, and timers are parsed locally |
| **Optional AI Assistant** | ✅ Supported | ✅ Supported | Connect Jev, DeepSeek, Ollama, etc. "Ask me when unsure" is on by default; the exact threshold lives in advanced settings |
| **Proactive "For You" Suggestions** | ✅ Up to 4 chips | ✅ Up to 4 pills | Manual card actions train time-bucket and weekday/weekend habits with a 14-day decay half-life and next-action prediction (undo rolls them back; see [ADR-0009](docs/adr/0009-habit-ranking-algorithm.md)). Local picks appear immediately; a configured model refreshes the cache in the background. Homes with fewer executable actions show their actual count. |
| **GrokBall & Side Suggestions** | ✅ Native Canvas | ✅ Bundled SVG engine | Click the ball for up to four reversible device actions; click again or press Esc to collapse. On macOS, neutral expressions use a black ball with white eyes, dragging repositions it, and a 0.5-second hold starts voice input. Suggestions follow its side of the panel and adapt their rows near screen edges. Windows retains the fixed side attachment. Eyes follow the pointer, and Reduce Motion uses static expressions. Based on [TyCoding/grok-ball](https://github.com/TyCoding/grok-ball) (MIT). |
| **Quick Actions & Scenes Strip** | ✅ 3 actions | ✅ 4 actions | Toggle in Settings — "Show Scenes Under the Input" on macOS (the pills open under the input when you click it), "Show top quick actions bar" on Windows. macOS ships All Lights Off, Soft Night Light, and Sleep Climate Off; Windows ships All main lights on, Lights off for sleep, 25° cool, and Brighten lights. Home Assistant scenes are appended on both. On macOS, four or fewer pills stretch edge to edge and the rest scroll horizontally with a normal mouse wheel. |
| **All-Off & Instant Undo** | ✅ Supported | ✅ Supported | A 0.6s hold confirms the action; plugs and vacuums are excluded by default; reversible actions show an undo banner for 10–15s |
| **Mac Event Automation** | ✅ Supported | — Not yet | Triggers on lock, sleep, wake, external displays, camera/microphone use, power/battery, Wi-Fi, Focus, Calendar, and late-night use (all Mac-only signals off by default) |
| **Apple Shortcuts (App Intents)** | ✅ Supported (4 actions) | — Not yet | Toggle Device, Run Scene, Voice Command, Query Room Climate |
| **URL Scheme Control** | ✅ Supported | — Not yet | `menumijia://toggle` calls require confirmation by default; direct control can be enabled in settings |
| **Raycast Extension, Local CLI & MCP Server** | ✅ Supported | — Not yet | `raycast/`, `scripts/mijia`, and `scripts/mijia-mcp` read the 0600 `raycast-snapshot.json` (Settings → 自动化与外部控制 → Raycast 集成) and control devices, scenes and quick actions through `menumijia://` links, so none holds credentials; MCP responses strip `bridgeToken`, and high-risk confirmation plus the lock-screen block still run in the app |
| **Sensor Trend Line Charts** | ✅ 24h / 7d trends | ⚠️ Real-time value only | macOS charts temperature or humidity over selectable periods; Windows shows current readings |
| **Smart Plug Energy Logs** | ✅ Supported | — No history yet | macOS counts changes in cumulative hardware readings; missing data is not estimated |
| **Mi Air Purifiers, Air Fresheners & Humidifiers** | ✅ Supported | ✅ Supported | Both platforms synthesize `air-purifier`, `air-fresh`, and `humidifier` specs: PM2.5, air quality, temp/humidity, filter life, and a 1–100% fan slider driving the favorite level |
| **Fridges & Kitchen Appliances** | ✅ Supported | ✅ Supported | Fridges expose per-chamber current/target temperature and modes (Manual, Smart, Holiday, Super Cool, Super Freeze). Kettles, water dispensers, cookers, air fryers, ovens, microwaves, and health pots show working status, temperature, and minutes remaining; models exposing `start-cook` / `pause` actions can be started and stopped |
| **Water Heaters** | ✅ Supported | ✅ Supported | MIoT water heaters become the shared appliance card: power, mode, target temperature with the spec's range and step, status, fault strip, remaining time, and no-argument start/pause actions, each shown only when the spec publishes it. Current water temperature is the main reading. Turning one on or raising its target asks first; turning it off is direct. Water heaters stay out of All Off by default |
| **HA Humidifiers, Water Heaters & Valves** | ✅ Supported | — Not yet | The Home Assistant channel now synthesizes `humidifier` (power, mode, target humidity and its built-in humidity reading), `water_heater` (the shared appliance card, with opening or hotter targets asking first) and `valve` (open / close; opening a water or gas valve asks first). Unclaimed `switch`, `select`, `number`, `button`, `sensor` and `binary_sensor` entities join 更多设置 with optimistic writes and undo; `text` entities stay out |
| **Bath Heaters & Range Hoods** | ✅ Supported | ✅ Supported | MIoT bath heaters merge their PTC heat, fan speed, room reading and lamp into one appliance card: power, target temperature, the spec's mode list, fan level, light switch, and room temperature/humidity. Range hoods merge exhaust power, fan level and lamp. A companion light stays inside the card instead of becoming its own light card. Every control appears only when the model's spec publishes it |
| **Washers, Dryers & Dishwashers** | ✅ Supported | ✅ Supported | `washer`, `dryer`, `clothes-dryer` and `dishwasher` specs become the shared appliance card: programme list, the wash stage and minutes remaining on the card ("Washing · 42 min left"), fault strip, and no-argument start/pause/stop. Long programme lists scroll inside the drawer instead of stacking every option. Starting a wash asks first; pausing and stopping are direct. Actions that take arguments are ignored rather than failing the card |
| **Dehumidifiers, Blankets, Diffusers & Speakers** | ✅ Supported | ✅ Supported | Dehumidifiers become fan cards driven by their reported mode list plus their own temp/humidity reading, electric blankets become climate cards, aroma diffusers become switchable plugs, and speakers become media cards with volume, mute, play state, sleep mode, and TTS (TTS is macOS-only; Windows speaker cards carry volume, mute, play state and sleep mode) |
| **Locks & Presence Sensors** | ✅ Read-only cards | ✅ Read-only cards | Door locks report battery. A readable `door-state` or Home Assistant `lock` entity shows locked, unlocked, ajar, or jammed; otherwise the card says the lock status is unknown instead of “Guarding”. Motion/occupancy, contact, water-leak, illumination, and air-quality monitors synthesize as sensor cards. MIoT exposes no remote unlock, so locks stay status-only. On macOS, Mi gateways, routers, and repeaters get a generic card when their spec exposes a usable item; models with no spec or no usable item stay in the skipped list. |
| **Generic Fallback Cards** | ✅ Supported | ✅ Supported | Mi devices whose type has no dedicated card but still expose a usable control or reading get a generic card: name and room, up to two key readings, one low-risk main switch, and the rest under More Settings. Read-only devices say so. Locks, heating, and router configuration stay read-only by default; enabling advanced writes asks first. Generic cards stay out of All Off and assistant batches by default. Windows writes under More Settings are optimistic but not undoable |
| **Fish Tanks & Pet Feeders** | ✅ Supported | ⚠️ Generic card only | MIoT `fish-tank` and `pet-feeder` specs join the generic card: the tile shows the main switch and water temperature (food level for a feeder), while More Settings highlights the pump switch and level, the light (mode, brightness), a portioned feeding action, filter life and the water-temperature alarm. Feeding asks for the portion and names today's total before it dispenses; pump states (not inserted, low water, blocked, fault) show as an inline alert. Vendor-private services and properties stay hidden. Windows does not implement this yet. |
| **Camera Live View** | ✅ Supported | ✅ Supported | Click a card for a pinned live window: macOS uses HLS / HA snapshots; Windows ships a private P2P bridge (bundled helpers, SD/HD/Ultra quality). Xiaomi P2P, MJPEG and snapshot streams carry video only and the HUD labels them "Video only"; an HLS stream with an audio track gets a mute/unmute control (macOS) |
| **Layout Editing Resize** | ✅ Supported | ✅ Supported | Edit mode shows an iOS Control Center-style corner arc handle on each card's bottom-right: drag out/in to step half ↔ full ↔ panel, or tap it to cycle sizes |
| **Visual Look** | ✅ One system look + 4 heights | ✅ Control Center design | macOS keeps system materials only: a device that is on takes the loudest surface its appearance allows — a bright platter in light mode, a lifted well one step above the off glass in dark mode — everything else stays quiet glass. |
| **Curtains & Window Openers** | ✅ Travel state + 2% end snap | ✅ Travel state + 2% end snap | Both platforms show real opening/closing travel (including the vendor spellings `Dowm` / `Fallin` and `Up` / `Down`), snap parked positions within 2% of either end, and stop a moving card. Airers report height, so their MIoT position is inverted on both platforms. Opening a window opener or an airer asks first; a garage door asks in either direction |
| **Code Signing & Integrity** | ⚠️ Self-signed + Ed25519 updates | ⚠️ Unsigned package | macOS requires manual Gatekeeper approval; Windows shows SmartScreen prompt |

---

## Quick Start

### 1. Requirements & Installation

- **macOS:** macOS 14 (Sonoma) or later (Universal Binary: Apple Silicon & Intel).
- **Windows:** Windows 11 (64-bit).

#### Installing on macOS

**Homebrew:** the `jryang1997/tap` tap was hosted on GitHub and is offline until it is republished on GitLab.

**Direct DMG Download**
Download the latest macOS DMG from [GitLab Releases](https://gitlab.com/jryang1997/MenuBarMijia/-/releases) and drag `MenuBarMijia.app` into your Applications folder.
> **About macOS Gatekeeper:**<br>
> This project is an independent open-source tool and is not enrolled in the paid Apple Developer Program (no Developer ID notarization). If Gatekeeper blocks initial launch, open **System Settings → Privacy & Security**, scroll down, and click **"Open Anyway"** for MenuBarMijia.

#### Installing on Windows 11

Download the latest Windows x64 `.exe` or `.msi` from [GitLab Releases](https://gitlab.com/jryang1997/MenuBarMijia/-/releases).
> **About Windows SmartScreen:**<br>
> The Windows binary is not signed with an enterprise Authenticode certificate. If SmartScreen shows "Windows protected your PC", click **"More info" → "Run anyway"**. SHA-256 digests are published on each Release for manual verification.

---

### 2. Connect Your Devices

Click the menu bar icon to reveal the panel, then open Settings `⚙️`:



- **Option A: Mi Home Direct Cloud Connect (Recommended):**<br>
  Select "Mi Cloud Connect" in Settings. A QR code appears on screen. Scan it with the official Mi Home app on your phone to link your account. All your homes, rooms, and supported devices sync instantly. On macOS, you can click "Add Account" in Settings to sign in to multiple Xiaomi accounts across regions at once and merge their devices and homes in one panel (Windows currently supports a single signed-in account). When Xiaomi's login response declares a cookie expiry (or the server rejects an expired token), the panel warns 3 days before expiry and switches the menu-bar icon to `house.slash` once expired, offering a one-click in-panel QR re-scan without guessing a lifetime for undated sessions.
- **Option B: Home Assistant local connection:**<br>
  Select "Home Assistant" and enter your instance origin, such as `http://192.168.1.100:8123`, and Long-Lived Access Token. On Windows, save settings and approve the endpoint in the native dialog. The token stays in Windows Credential Manager. A rejected or failed connection never switches to demo. Windows supports the existing light, climate, fan, plug, fridge, vacuum, camera-control, and sensor card models, plus scenes and scripts. macOS-only device types and HA camera live streams are outside this Windows backend.<br>
  On macOS, connecting to a LAN address or `.local` host can be blocked by the system's local-network privacy gate. The panel says so explicitly ("Local network connection blocked by macOS") instead of showing a generic socket error. Grant MenuBarMijia permission under **System Settings → Privacy & Security → Local Network**; if it still fails, reinstall so the local app signature lines up.
- **Option C: Offline Demo Home:**<br>
  Explore the interface and controls without an account using simulated devices.

---

## Keyboard Shortcuts & Natural Commands

### Keyboard Navigation

These panel shortcuts apply to macOS. On Windows, the default shortcut to open the panel is `Alt+M`.

| Shortcut | Action |
| :--- | :--- |
| `⌥M` | Toggle the MenuBarMijia panel |
| Type letters | Instant search by device name, room, or pinyin initials (e.g. `sftd`) |
| `↑` / `↓` | Navigate through search matches and device cards |
| `Return` / `Space` | Toggle the focused device; expand the detail drawer if not directly switchable |
| `→` / `←` | Press Right to open details; then use Left and Right to adjust light brightness, AC temperature, or volume |
| `Tab` | Switch between room tabs |
| `Esc` | Clear search query, close drawer, or dismiss panel |

### Local Natural Language Commands

These phrases are parsed locally **without any AI API key** or cloud model tokens:

- **Power:** "turn off study light", "turn on living room ac", "turn off all lights"
- **Lighting presets:** "study light moonlight mode", "daylight mode"
- **Adjustments:** "dimmer", "brighter", "set bedroom light to 40%"
- **Climate & Fan:** "set ac to 24 degrees", "warmer", "fan natural wind"
- **Timers:** "in 10 minutes turn off study light", "cancel timer"
- **Undo:** "undo", "revert" (active for 10 minutes after any batch execution)

---

## Raycast Extension, Local CLI & MCP Server

MenuBarMijia can hand its device list to a companion without sharing any credential. Turn on **Settings → 自动化与外部控制 → Raycast 集成**: the app writes `~/Library/Application Support/MenuBarMijia/raycast-snapshot.json` (mode `0600`) with your devices, scenes, quick actions, environment readings, the last command result, and a random local bridge token. Links that carry the token skip the external-link confirmation sheet; high-risk devices still ask, and the lock-screen block still applies. Turning the integration off deletes the file and revokes the token. The contract and the trade-offs behind it are in [ADR-0013](docs/adr/0013-raycast-companion-contract.md).

### Local CLI

`scripts/mijia` is a dependency-free Python 3 script. Keep MenuBarMijia running while you use it.

| Command | What it does |
| :--- | :--- |
| `scripts/mijia list [--room 书房] [--json]` | Visible devices grouped by room, with online state and readings |
| `scripts/mijia status [--json]` | Connection, device counts, environment readings, and the last command's result |
| `scripts/mijia toggle 书房台灯` | Flip the primary switch by name, id, or pinyin initials (`sftd`) |
| `scripts/mijia on 客厅射灯` / `scripts/mijia off 客厅射灯` | Set an exact power state instead of flipping |
| `scripts/mijia brightness 书房台灯 60` | Set light brightness (`0`–`100`%) |
| `scripts/mijia color-temp 书房台灯 4000` | Set light colour temperature (`1000`–`20000` K) |
| `scripts/mijia temperature 书房空调 25` | Set target temperature (`-30`–`50` ℃) |
| `scripts/mijia fan 客厅风扇 --percent 60` / `--preset 自然风` | Set fan speed percentage (`0`–`100`%) or a reported preset mode |
| `scripts/mijia focus 书房台灯` | Reveal the device's room and detail drawer in the panel without actuating hardware |
| `scripts/mijia scene 观影模式` | Run a Home Assistant scene or a built-in quick action (全屋关灯, 一键全关, 撤销…) |
| `scripts/mijia ask "把客厅灯调到 40%"` | One natural-language command, with the app's own parsing and confirmation |
| `scripts/mijia mcp` (or `scripts/mijia-mcp`) | Run the local MCP server over stdio |

Exit codes: `0` success, `1` the app reported a failure, `2` local error (snapshot missing, unknown device, integration off), `3` no result in time. `--dry-run` prints the link with the token masked, `--no-wait` sends it without waiting, and `MENUBARMIJIA_SNAPSHOT_PATH` overrides the snapshot path.

### Local MCP Server (Claude Desktop / Claude Code / Cursor)

`scripts/mijia-mcp` (or `scripts/mijia mcp`) speaks MCP JSON-RPC 2.0 over stdio using only the Python 3 standard library. It exposes read-only resources (`mijia://status`, `mijia://devices`, `mijia://scenes`) and tools (`list_devices`, `get_home_status`, `toggle_device`, `set_device_power`, `set_light_brightness`, `set_light_color_temp`, `set_temperature`, `set_fan`, `run_scene`, `ask_home`, `focus_device`). The server uses `bridgeToken` internally to dispatch `menumijia://` links and strips `bridgeToken` from every MCP resource and tool response so it never enters the model's context.

```json
{
  "mcpServers": {
    "menubarmijia": {
      "command": "/ABSOLUTE/PATH/TO/MenuBarMijia/scripts/mijia-mcp"
    }
  }
}
```

### Raycast

`raycast/` is a standard Raycast extension with three commands: **Search Devices**, **Scenes & Quick Actions**, and **Ask MenuBarMijia**. Import it with Raycast → Extensions → `+` → **Import Extension**, or run `cd raycast && npm ci && npm run dev` while Raycast is open. Power, brightness, colour temperature, thermostat target and fan speed are sent as exact values (`menumijia://power`, `brightness`, `color-temp`, `temperature`, `fan`), so a slider never flips a device by guessing its state; each command shows a toast with the app's result.

---

## Safety, Privacy & Limitations

MenuBarMijia is an independent open-source project, not an official Xiaomi app. Mi Home cloud control needs an internet connection. You can connect your own Home Assistant instance on macOS or Windows.

### Where credentials live

| Secret | What is stored | Where |
| :--- | :--- | :--- |
| Xiaomi Home / Mijia | Every signed-in account's `userId`, `cUserId`, `ssecurity`, `serviceToken`, cloud region and declared expiry | macOS Keychain (one generic-password item) or Windows Credential Manager |
| Home Assistant | The long-lived access token (the instance URL is not secret) | macOS Keychain or Windows Credential Manager |
| AI assistants | The API key saved for each provider, plus the imported-key source label and remembered cloud-consent endpoints | API keys in macOS Keychain / Windows Credential Manager; the non-secret labels in app preferences |
| LG webOS TV | The pairing client key issued by the TV | macOS Keychain |

macOS encrypts these Keychain items at rest and protects them with the login Keychain; MenuBarMijia writes them with `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`, so they are available after the first unlock but are never synced to iCloud or another Mac. The app does not store these secrets in plain `UserDefaults` or log files. macOS may ask for Keychain permission after an app update because the build signature changes; answering "Always Allow" covers later reads and writes.

Versions up to 1.0.6 could mirror credentials into plain `UserDefaults`. On first launch after upgrading, each legacy plaintext value is written into the Keychain and the plaintext key is deleted only after that write succeeds. If the Keychain is unavailable, the old copy is left in place for the next attempt instead of silently pretending the migration succeeded.

### High-risk controls, lock screen, and revocation

Manual controls on high-risk devices ask for a second confirmation, not only AI-proposed actions: turning on a heater, switching a plug or camera, starting a vacuum, or turning a water heater on or hotter. Turning one of these off stays direct, so making the home safer never requires fighting a dialog. Door/window and gas-alarm sensors are read-only status cards in this app, so they cannot be actuated here at all. The confirmation table is shared with the AI policy and lives in `Sources/MijiaCore/ModelActionPolicy.swift`.

Settings → 隐私 / Privacy has **锁屏时禁止控制设备 / Block device control while locked**, on by default. While the Mac screen is locked, card/detail controls, `menumijia://` links, App Intents/Shortcuts and assistant commands are refused with a toast; scheduled commands and Mac-event automation rules still run. The toggle is persisted locally and the lock state comes from the system's screen-lock notifications.

The same Settings page has **退出并吊销令牌 / Sign Out & Revoke Tokens**, confirmed by a destructive dialog. It signs out every Xiaomi Home account, deletes the Home Assistant token and disconnects, and clears saved AI keys and cloud-consent records. Device names, room layout and local history stay. You can also revoke one thing at a time: 退出登录 in the Mi account section, replacing the HA token with an empty value, or the per-key 撤销 button in the AI settings.

On macOS the Home Assistant token only goes to an https address or a local-network endpoint without asking. For any other plain-http address (a public DDNS name, say) the panel first explains that the token would travel unencrypted and connects only after you choose 仍然连接 / Connect Anyway. The choice is remembered per address, a changed address asks again, and Sign Out & Revoke Tokens forgets every such choice.

Everyday commands are handled by local rules. You can also connect an AI service of your choice for more complex requests. The request includes your command, device and environment information, and any habit summary you have enabled. Manual card actions also train local time-bucket habit memories (used only for the "For You" suggestions); the habit learning toggle turns this off, learned entries can be reviewed and cleared, and undoing a banner rolls the matching records back. Speech recognition uses system services; on-device-only recognition is available for supported languages. Device, room and scene names are passed to the system speech service as recognition hints.

To help prevent mistakes, actions the AI is unsure about ask first ("Ask me when unsure" is on by default), and risky actions such as turning on a heater, switching a plug, or starting a vacuum always wait for your confirmation. URL controls also ask first by default, with an option to enable direct control.

The macOS app is not yet notarized by Apple, so you may need to allow it in System Settings the first time. Windows may show a SmartScreen prompt. macOS updates are verified with an Ed25519 public key; the signing key stays offline. See [ADR-0007](docs/adr/0007-lan-direct-deferred.md) for more on connection options.

Releases published on GitHub (up to v1.1.7) carried GitHub build provenance attestations and SPDX SBOMs; GitLab releases do not have attestations yet. See [verifying release artifacts](SECURITY.md#verifying-release-artifacts).

To report a vulnerability in private, follow [Security policy](SECURITY.md).

---

## Development & Architecture

- **Architecture:** The macOS version is written in pure Swift 6 with **no third-party Swift runtime dependencies**, adhering to strict concurrency rules. Core protocol handling and device synthesis live in `MijiaCore` with full headless test coverage. The Windows client is built with Tauri v2 (Rust + TypeScript + Vite).
- **Cross-Platform Consistency:** Both platforms run separate test suites against shared golden test fixtures in `Tests/Fixtures/` (intents, specs, regions, AI payloads) to prevent behavioral drift (see [ADR-0006](docs/adr/0006-dual-platform-implementation.md)).

### Local Build & Verification

```bash
# Run secret checks, linters, and Swift tests on macOS
bash scripts/check.sh

# Build, sign locally, and launch MenuBarMijia.app
bash scripts/build_app.sh

# Build and test the Windows tray app
cd windows
npm install
npm test
npm run build
```

Please review [CODING_STANDARDS.md](CODING_STANDARDS.md) before submitting pull requests. Key domain concepts are documented in [GLOSSARY.md](GLOSSARY.md).

---

## Contributors

Thanks to everyone who has contributed features, fixes, and feedback:

- [@jryang1997](https://gitlab.com/jryang1997) (一群小羊说) — Creator & maintainer
- [@almightyYantao](https://github.com/almightyYantao) (Alimighty.Yantao) — Multi-key Mesh switch split-device synthesis (GitHub #6, GitHub #7), multi-home switcher & light-group scene filtering (GitHub #8), visual skin system, inline climate module, drag-to-reorder layout & custom device groups (GitHub #9), macOS app bundle icon packaging (GitHub #10), panel drop-in animation, local device renaming & room-strip polish (GitHub #11), Windows feature parity and cross-platform UI fixes (GitHub #12), and redundant-write optimization for provider snapshots (GitHub #66)
- [@htx996](https://github.com/htx996) (han) — macOS status-item context menu with live/demo mode switching, dual-source status, and menu-safe click, hold-to-talk, and keyboard handling (GitHub #250)

## License & Acknowledgements

- **License:** Distributed under the [MIT License](LICENSE).
- **Third-Party Integrations:** This repository does not distribute third-party integrations. The optional Home Assistant scripts reference repositories: [xiaomi_home](https://github.com/XiaoMi/ha_xiaomi_home) and [midea_ac_lan](https://github.com/wuwentao/midea_ac_lan).
- **Trademarks:** "Xiaomi", "Mijia", and "Midea" are registered trademarks of their respective owners. Used here solely to indicate device compatibility.

Thanks to all contributors who have submitted PRs, reported issues, and helped refine the desktop smart home experience! If MenuBarMijia helps you keep your hands on the keyboard, please give it a ⭐ Star on GitHub.

---

## 常见问题（FAQ） / Frequently Asked Questions

### How much memory and CPU does MenuBarMijia use?

The macOS build is one native AppKit/SwiftUI process, with no bundled browser engine. Measured on 2026-11-07 with MenuBarMijia 1.1.7 (426) on an Apple-silicon Mac (10 cores, macOS 27.0.1), with about a dozen devices configured, one of them a camera:

| State | RSS | CPU (one core) |
| --- | --- | --- |
| Idle, panel closed | 159 MB | 0.1% |
| Panel open, no interaction | 147 MB | 0.1% |
| Holding Option-M for voice input | 152 MB | 26% |

- **RSS** is the resident set from `ps`. Activity Monitor's Memory column (physical footprint) read about 146 MB for the loaded app.
- **CPU** is a share of one core, computed from CPU-time deltas across three sample windows (five seconds each by default). `ps %cpu` is a lifetime average and keeps reporting a busy period long after it ended, so the script does not use it as the measurement.
- Opening the panel reuses the same process, so RSS stays flat and CPU stays near zero when you do not interact.
- Voice input is the only state with noticeable CPU. `AVAudioEngine` streams the microphone and the speech framework transcribes it. The system's `corespeechd` does the recognition, and its CPU is not part of the app's number.
- Memory depends on what is connected. A camera stream, many MIoT device specs and large scenes all add memory.

### What about Windows 11?

The Windows build is Tauri v2 with WebView2. One host process runs the tray app, either `MenuBarMijia.exe` from the installer or `menubarmijia-windows.exe` from a local Tauri build, and one or more `msedgewebview2.exe` processes render the UI. `scripts/measure_footprint.py` sums the host and its own renderer descendants, and ignores WebView2 processes that belong to other applications.

Windows 11 memory and CPU numbers are not in the table yet. They have to be taken on a Windows host. With the tray app running, run the same command there:

```powershell
python scripts\measure_footprint.py --state idle
python scripts\measure_footprint.py --state panel
python scripts\measure_footprint.py --state voice
```

### How long does the first open take with 20 or more devices?

| Platform | Devices | Synthesis (median) | Range | Runs |
| --- | --- | --- | --- | --- |
| macOS (MijiaCore) | 24 | 2.10 ms | 2.02–2.69 ms | 31 |
| Windows (TypeScript core) | 24 | 0.10 ms | 0.07–0.13 ms | 31 |

Both runs synthesize the same snapshot, 18 lights and 6 switches across six rooms. Device synthesis is a pure function and finishes in milliseconds, so a slow first open comes from the data fetch and view construction, not from synthesis itself.

### How do I reproduce these numbers?

`scripts/measure_footprint.py` samples the process that is already running. It never launches the app, and it cannot tell which state the app is in, so put the app in the state first:

```bash
# Idle: panel closed, app left alone for a minute
python3 scripts/measure_footprint.py --state idle

# Panel open: press Option-M, leave the panel open, then
python3 scripts/measure_footprint.py --state panel

# Voice: hold Option-M until the microphone indicator appears, and run this while it listens
python3 scripts/measure_footprint.py --state voice

# Machine-readable copy of the committed baseline
python3 scripts/measure_footprint.py --no-live --json
```

Live rows are marked `live` in the Source column. Without a running app the script prints the committed baseline and marks every row `recorded`. Quit any second copy of the app first. The script measures the first `MenuBarMijiaApp` it finds.

The unit tests for the parser and report live in `scripts/test_measure_footprint.py`:

```bash
python3 -m unittest discover -s scripts -p 'test_measure_footprint.py'
```

---
