# MenuBarMijia for Raycast

Search and control MenuBarMijia from Raycast. The extension never talks to Mi Home or Home Assistant:
it reads the snapshot the app writes and asks the app to run each command through `menumijia://`
links, so the same parsing, high-risk confirmation and lock-screen rules apply.

## Setup

1. In MenuBarMijia: **Settings → 自动化与外部控制 → Raycast 集成** (Settings → Automation & External
   Control → Raycast integration). The app then writes
   `~/Library/Application Support/MenuBarMijia/raycast-snapshot.json` (mode `0600`) and issues a local
   bridge token.
2. Keep MenuBarMijia running. Turning the integration off deletes the snapshot and revokes the token.
3. Install the extension: in Raycast, **Extensions → + → Import Extension**, then pick this `raycast/`
   folder. For development, `npm ci && npm run dev` here while Raycast is open.

## Commands

| Command | What it does |
| :--- | :--- |
| **Search Devices** | Search by name, room, model or pinyin initials. Turn a device on or off, set brightness, colour temperature, thermostat target or fan speed, open its drawer in the panel, or copy its id. |
| **Scenes & Quick Actions** | Run Home Assistant scenes and the built-in quick actions: all lights off, soft night light, sleep climate off, all off, and undo. |
| **Ask MenuBarMijia** | Send one natural-language command (for example 把客厅灯调到 40%) with the same local parser the app uses. |

Results come back through `lastCommandFeedback`, so a toast says whether the app actually ran the
command (or why it refused).

## Preferences

- **Snapshot Path**: leave empty for the default path. `MENUBARMIJIA_SNAPSHOT_PATH` overrides it for
  every command.
- **Result Timeout**: seconds to wait for the app to report a result (default 12).

## Contract

The extension is a client of the same `menumijia://` contract as `scripts/mijia`:

- `power?device=<id>&on=true|false`
- `brightness?device=<id>&percent=0…100`
- `color-temp?device=<id>&kelvin=<k>`
- `temperature?device=<id>&celsius=<°C>`
- `fan?device=<id>&percent=<1…100>` or `&preset=<mode>` (exactly one; a link with both is rejected)
- `focus?device=<id>`
- `toggle?device=<id>`, `scene?name=<id|quick:<id>>`, `ask?text=<sentence>`

Every link carries `id=<requestId>` and `token=<bridgeToken>`. Field names in `raycast-snapshot.json`
match `Sources/MijiaCore/RaycastSnapshot.swift`; a rename there is a breaking change and must bump the
snapshot schema version.
