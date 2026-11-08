#!/usr/bin/env python3
"""Fail when the Swift/Go PTZ evidence tables drift from the shared fixture."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
fixture = json.loads((ROOT / "Tests/Fixtures/camera_ptz_models.json").read_text())
expected = {
    (row["model"], row["mode"]): row["confidence"]
    for row in fixture["models"]
}

go = (ROOT / "scripts/go2rtc-ptz/patches/0001-xiaomi-ptz.patch").read_text()
go_table = {}
in_table = False
for line in go.splitlines():
    if line.startswith("+var ptzProfiles"):
        in_table = True
        continue
    if in_table and line.startswith("+}"):
        break
    if not in_table:
        continue
    match = re.search(r'\+\s*"([a-z0-9.]+)":\s+ptz(Step|Continuous|Unknown)', line)
    if match:
        go_table[match.group(1)] = match.group(2).lower()

swift = (ROOT / "Sources/MijiaCore/CameraPTZ.swift").read_text()
swift_tables = {}
for name, mode in (("stepModels", "step"), ("continuousModels", "continuous"), ("pendingModels", "unknown")):
    block = re.search(rf"let {name}: Set<String> = \[(.*?)\]", swift, re.S)
    if not block:
        sys.exit(f"missing {name} in CameraPTZ.swift")
    for model in re.findall(r'"([a-z0-9.]+)"', block.group(1)):
        swift_tables[model] = mode
# The TUTK branch handles the two dafang models outside the sets.
for model in ("isa.camera.df3", "isa.camera.isc5c1"):
    swift_tables.setdefault(model, "continuous")

errors = []
for (model, mode), confidence in expected.items():
    if confidence != "verified" and confidence != "pendingRealDevice":
        errors.append(f"fixture {model}: bad confidence {confidence}")
    if go_table.get(model) != mode:
        errors.append(f"go {model}: {go_table.get(model)!r} != {mode!r}")
    if swift_tables.get(model) != mode:
        errors.append(f"swift {model}: {swift_tables.get(model)!r} != {mode!r}")
for model in set(go_table) | set(swift_tables):
    if not any(row["model"] == model for row in fixture["models"]):
        errors.append(f"{model}: present in source, missing from fixture")

if errors:
    print("❌ PTZ model tables drift:")
    for error in errors:
        print("  ", error)
    sys.exit(1)
print(f"✅ PTZ model tables agree ({len(expected)} models)")
