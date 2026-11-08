#!/usr/bin/env python3
"""Compare README capability tables with docs/capabilities.json, then check code anchors.

Every row is checked against code: CODE_ANCHORS fails when a claimed platform status
contradicts a source file, and a row's `miotTypes` derives both platforms' status from the
MIoT coverage baseline (#185, #26). A row without `miotTypes` must have an anchor, so the
manifest cannot grow a documentation-only line again.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/capabilities.json"
COVERAGE = ROOT / "Tests/Fixtures/miot_coverage_golden.json"
TABLES = (
    ("README.md", "| Feature Area |", "en"),
    ("README.zh-CN.md", "| 功能维度 |", "zh"),
)


def status(cell: str) -> str | None:
    if "✅" in cell:
        return "supported"
    if "⚠" in cell:
        return "partial"
    if "—" in cell or "❌" in cell:
        return "unsupported"
    return None


def read_rows(path: str, header: str) -> list[tuple[str, str | None, str | None]]:
    lines = (ROOT / path).read_text(encoding="utf-8").splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.startswith(header))
    except StopIteration:
        raise SystemExit(f"❌ {path}: 找不到能力表表头 {header!r}")

    rows: list[tuple[str, str | None, str | None]] = []
    for line in lines[start + 1:]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            continue
        if cells[0].startswith(":---") or cells[0].startswith("---"):
            continue
        rows.append((cells[0].strip("*").strip(), status(cells[1]), status(cells[2])))
    return rows


SOURCE_SUFFIXES = {".swift", ".ts", ".tsx", ".js", ".mjs", ".rs", ".json"}
SKIP_DIRS = {"node_modules", "target", "dist", ".git"}

# Every row without `miotTypes` needs an anchor. supported/partial needs `path`+`symbol`.
# unsupported needs `roots`+`symbol` and fails when any source file contains the symbol.
CODE_ANCHORS = (
    {
        "id": "mi-home-qr",
        "macos": {
            "path": "Sources/MenuBarMijiaApp/MiAccountSection.swift",
            "symbol": "QRLoginTicket",
        },
        "windows": {
            "path": "windows/src/core/miCloud/accountLogin.ts",
            "symbol": "MiAccountLogin",
        },
    },
    {
        "id": "home-assistant",
        "macos": {
            "path": "Sources/MijiaCore/HADiscovery.swift",
            "symbol": "HADiscovery",
        },
        "windows": {
            "path": "windows/src/core/homeAssistant.ts",
            "symbol": "HomeAssistantClient",
        },
    },
    {
        "id": "keyboard-search",
        "macos": {
            "path": "Sources/MenuBarMijiaApp/ControlCenterPanel.swift",
            "symbol": "isSearchFieldFocused",
        },
        "windows": {
            "path": "windows/src/core/panelSearch.ts",
            "symbol": "searchMatches",
        },
    },
    {
        "id": "global-hotkey",
        "macos": {
            "path": "Sources/MenuBarMijiaApp/HotkeyAndNotificationManager.swift",
            "symbol": "HotkeyAndNotificationManager",
        },
        "windows": {
            "path": "windows/src-tauri/src/main.rs",
            "symbol": "parse_global_shortcut",
        },
    },
    {
        "id": "voice",
        "macos": {
            "path": "Sources/MenuBarMijiaApp/AssistantCommandBar.swift",
            "symbol": "SpeechRuntimeBridge",
        },
        "windows": {
            "path": "windows/src/ui/appTypes.ts",
            "symbol": "SpeechRecognitionInstance",
        },
    },
    {
        "id": "local-command",
        "macos": {
            "path": "Sources/MijiaCore/LocalIntentParser.swift",
            "symbol": "LocalIntentParser",
        },
        "windows": {
            "path": "windows/src/ui/naturalCommand.ts",
            "symbol": "handleNaturalCommand",
        },
    },
    {
        "id": "optional-ai",
        "macos": {
            "path": "Sources/MijiaCore/AssistantEndpoint.swift",
            "symbol": "AssistantEndpoint",
        },
        "windows": {
            "path": "windows/src/core/assistant/provider.ts",
            "symbol": "parseAssistantEndpoint",
        },
    },
    {
        "id": "proactive-suggestions",
        "macos": {
            "path": "Sources/MijiaCore/HomeStore+Assistant.swift",
            "symbol": "maybeRefreshProactiveSuggestionsInBackground",
        },
        "windows": {
            "path": "windows/src/ui/proactive.ts",
            "symbol": "currentProactiveSuggestions",
        },
    },
    {
        "id": "grokball",
        "macos": {
            "path": "Sources/MenuBarMijiaApp/GrokBallRenderer.swift",
            "symbol": "GrokBallRenderer",
        },
        "windows": {
            "path": "windows/src/ui/grokBall.ts",
            "symbol": "GrokBallView",
        },
    },
    {
        "id": "quick-actions",
        "macos": {
            "path": "Sources/MijiaCore/Models.swift",
            "symbol": "QuickAction",
        },
        "windows": {
            "path": "windows/src/ui/demo.ts",
            "symbol": "quickActions",
        },
    },
    {
        "id": "all-off-undo",
        "macos": {
            "path": "Sources/MijiaCore/HomeStore+Undo.swift",
            "symbol": "undoTurnAllOff",
        },
        "windows": {
            "path": "windows/src/core/commandEffects.ts",
            "symbol": "inverseCommands",
        },
    },
    {
        "id": "mac-event-automation",
        "macos": {
            "path": "Sources/MijiaCore/MacContextAutomation.swift",
            "symbol": "MacContextAutomation",
        },
        "windows": {
            "roots": ("windows/src", "windows/src-tauri/src"),
            "symbol": "MacContextAutomation",
        },
    },
    {
        "id": "app-intents",
        "macos": {
            "path": "Sources/MenuBarMijiaApp/AppIntents.swift",
            "symbol": "AppIntent",
        },
        "windows": {
            "roots": ("windows/src", "windows/src-tauri/src"),
            "symbol": "AppIntent",
        },
    },
    {
        "id": "url-scheme",
        "macos": {
            "path": "Sources/MenuBarMijiaApp/MenuBarMijiaApp.swift",
            "symbol": "onOpenURL",
        },
        "windows": {
            "roots": ("windows/src", "windows/src-tauri/src"),
            "symbol": "onOpenURL",
        },
    },
    {
        "id": "raycast-cli",
        "macos": {
            "path": "Sources/MijiaCore/RaycastSnapshot.swift",
            "symbol": "RaycastSnapshot",
        },
        "windows": {
            "roots": ("windows/src", "windows/src-tauri/src"),
            "symbol": "RaycastSnapshot",
        },
    },
    {
        "id": "sensor-history",
        "macos": {
            "path": "Sources/MenuBarMijiaApp/SensorCardView.swift",
            "symbol": "SensorHistoryPoint",
        },
        "windows": {
            "path": "windows/src/ui/deviceViews.ts",
            "symbol": "renderDeviceTile",
        },
    },
    {
        "id": "plug-energy",
        "macos": {
            "path": "Sources/MijiaCore/EnergyHistory.swift",
            "symbol": "EnergyHistory",
        },
        "windows": {
            "roots": ("windows/src", "windows/src-tauri/src"),
            "symbol": "EnergyHistory",
        },
    },
    {
        "id": "ha-humidifiers-water-heaters-valves",
        "macos": {
            "path": "Sources/MijiaCore/DeviceSynthesizer.swift",
            "symbol": "water_heater.",
        },
        "windows": {
            "roots": ("windows/src", "windows/src-tauri/src"),
            "symbol": "water_heater.",
        },
    },
    {
        "id": "generic-device-cards",
        "macos": {
            "path": "Sources/MijiaCore/MiCloud/MiotSynthesizer.swift",
            "symbol": "genericCard",
        },
        "windows": {
            "path": "windows/src/core/miCloud/synthesis.ts",
            "symbol": "genericCard",
        },
    },
    {
        "id": "fish-tanks-pet-feeders",
        "macos": {
            "path": "Sources/MijiaCore/MiCloud/MiotFacets.swift",
            "symbol": "FeedProfile",
        },
        "windows": {
            "path": "windows/src/core/miCloud/facets.ts",
            "symbol": "feeding",
        },
    },
    {
        "id": "camera-live",
        "macos": {
            "path": "Sources/MenuBarMijiaApp/CameraLivePanelController.swift",
            "symbol": "CameraLivePanelController",
        },
        "windows": {
            "path": "windows/src/ui/cameraLive.ts",
            "symbol": "CameraLiveController",
        },
    },
    {
        "id": "layout-resize",
        "macos": {
            "path": "Sources/MenuBarMijiaApp/PanelLayoutStore.swift",
            "symbol": "PanelLayoutStore",
        },
        "windows": {
            "path": "windows/src/ui/layoutGestures.ts",
            "symbol": "LayoutGestureHost",
        },
    },
    {
        "id": "visual-look",
        "macos": {
            "path": "Sources/MenuBarMijiaApp/DesignTokens.swift",
            "symbol": "DesignTokens",
        },
        "windows": {
            "path": "windows/src/ui/styles.css",
            "symbol": "--glass-bg",
        },
    },
    {
        "id": "covers-curtains",
        "macos": {
            "path": "Sources/MenuBarMijiaApp/CoverDetailDrawer.swift",
            "symbol": "CoverDetailDrawer",
        },
        "windows": {
            "path": "windows/src/core/miCloud/synthesis.ts",
            "symbol": "coverSubtype",
        },
    },
    {
        "id": "code-signing",
        "macos": {
            "path": "Sources/MijiaCore/UpdateManifest.swift",
            "symbol": "UpdateManifest",
        },
        "windows": {
            "path": "windows/src-tauri/src/update.rs",
            "symbol": "verify_ed25519_signature",
        },
    },
)


def first_symbol_hit(root: pathlib.Path, roots: tuple[str, ...], symbol: str) -> str | None:
    for rel_root in roots:
        base = root / rel_root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if symbol in text:
                return str(path.relative_to(root))
    return None


def code_anchor_errors(root: pathlib.Path, rows: list[dict]) -> list[str]:
    by_id = {
        row["id"]: row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    errors: list[str] = []
    for anchor in CODE_ANCHORS:
        row = by_id.get(anchor["id"])
        if row is None:
            errors.append(f"代码锚点 {anchor['id']} 在 capabilities.json 里没有这一行")
            continue
        for platform in ("macos", "windows"):
            claimed = row.get(platform)
            spec = anchor[platform]
            symbol = spec.get("symbol")
            if claimed in ("supported", "partial"):
                rel = spec.get("path")
                if not rel or not symbol:
                    errors.append(
                        f"{anchor['id']} 的 {platform} 标为 {claimed}，但锚点没有可核对的源文件"
                    )
                    continue
                file = root / rel
                if not file.is_file() or symbol not in file.read_text(encoding="utf-8", errors="replace"):
                    errors.append(
                        f"{anchor['id']} 的 {platform} 标为 {claimed}，但 {rel} 里没有 {symbol}"
                    )
            elif claimed == "unsupported":
                roots = spec.get("roots")
                if not symbol or not roots:
                    errors.append(
                        f"{anchor['id']} 的 {platform} 标为 unsupported，但锚点没有可搜索的源码树"
                    )
                    continue
                hit = first_symbol_hit(root, roots, symbol)
                if hit:
                    errors.append(
                        f"{anchor['id']} 的 {platform} 标为 unsupported，但 {hit} 含有 {symbol}"
                    )
            else:
                errors.append(f"{anchor['id']} 的 {platform} 状态无法核对：{claimed!r}")

    anchored = {anchor["id"] for anchor in CODE_ANCHORS}
    for row in rows:
        if row.get("miotTypes"):
            continue
        if row["id"] not in anchored:
            errors.append(
                f"{row['id']} 没有代码锚点：请补 CODE_ANCHORS，或给它加上 miotTypes 由基线推出"
            )
    return errors


def coverage_status(golden: dict, types: list[str], platform: str) -> str:
    """Status a platform earns for these MIoT device types, per the coverage baseline (#295).

    Both platforms' test suites assert their synthesis equals the baseline, so this reads the
    code's behaviour on one real public spec per type. Windows reads `windowsDivergences`
    where it classifies a spec differently from macOS.
    """
    divergences = golden.get("windowsDivergences", {}) if platform == "windows" else {}
    categories = [
        divergences.get(model, entry)["category"]
        for model, entry in golden["models"].items()
        if entry["deviceType"] in types
    ]
    matched = categories.count("matched")
    if categories and matched == len(categories):
        return "supported"
    return "partial" if matched else "unsupported"


def coverage_errors(golden: dict, rows: list[dict]) -> list[str]:
    known = {entry["deviceType"] for entry in golden["models"].values()}
    errors: list[str] = []
    for row in rows:
        types = row.get("miotTypes")
        if not types:
            continue
        missing = [kind for kind in types if kind not in known]
        if missing:
            errors.append(f"{row['id']} 的 miotTypes 不在覆盖率基线里：{', '.join(missing)}")
            continue
        for platform in ("macos", "windows"):
            derived = coverage_status(golden, types, platform)
            if row.get(platform) != derived:
                unmatched = sorted(
                    kind for kind in types if coverage_status(golden, [kind], platform) != "supported"
                )
                errors.append(
                    f"{row['id']} 的 {platform} 标为 {row.get(platform)}，覆盖率基线推出 {derived}"
                    + (f"（没对上的类型：{', '.join(unmatched)}）" if unmatched else "")
                )
    return errors


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    expected = manifest.get("rows")
    if not isinstance(expected, list) or not expected:
        raise SystemExit("❌ docs/capabilities.json 缺少 rows")

    errors: list[str] = []
    parsed: dict[str, list[tuple[str, str | None, str | None]]] = {}
    for path, header, key in TABLES:
        rows = read_rows(path, header)
        parsed[key] = rows
        if len(rows) != len(expected):
            errors.append(f"{path}: {len(rows)} 行，capabilities.json 有 {len(expected)} 行")
        for index, (row, wanted) in enumerate(zip(rows, expected)):
            label, macos, windows = row
            if macos is None or windows is None:
                errors.append(f"{path} 第 {index + 1} 行 {label!r}: 无法识别状态标记")
                continue
            if label != wanted[key]:
                errors.append(
                    f"{path} 第 {index + 1} 行标签是 {label!r}，manifest 是 {wanted[key]!r}"
                )
            if (macos, windows) != (wanted["macos"], wanted["windows"]):
                errors.append(
                    f"{path} 第 {index + 1} 行 {label!r}: "
                    f"README={macos}/{windows}，manifest={wanted['macos']}/{wanted['windows']}"
                )

    for index, (english, chinese) in enumerate(zip(parsed["en"], parsed["zh"])):
        if english[1:] != chinese[1:]:
            errors.append(
                f"第 {index + 1} 行中英文状态不一致: {english[1:]!r} vs {chinese[1:]!r}"
            )

    errors.extend(code_anchor_errors(ROOT, expected))
    errors.extend(coverage_errors(json.loads(COVERAGE.read_text(encoding="utf-8")), expected))

    if errors:
        print("❌ 能力表与文档或代码锚点不一致：")
        for error in errors:
            print(f"  - {error}")
        print("文档行请同时更新 README.md、README.zh-CN.md 与 docs/capabilities.json。")
        print("代码锚点行要改 capabilities.json 的状态，或改掉对不上的源文件。")
        print("带 miotTypes 的行，状态以覆盖率基线为准：改合成并重录基线，或照推出的状态改文档。")
        return 1
    derived = sum(1 for row in expected if row.get("miotTypes"))
    print(
        f"✅ {len(expected)} 行能力状态与两份 README 一致，全部对照了源码："
        f"{len(CODE_ANCHORS)} 行用文件与符号锚点，{derived} 行由 MIoT 覆盖率基线推出。"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
