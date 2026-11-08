#!/usr/bin/env python3
"""Rebuild Tests/Fixtures/miot_coverage_specs.json from the public miot-spec.org library.

One released spec per MIoT device type (two for types with 100+ models), plus the
special shapes named in EXTRA_MODELS. Specs are copied verbatim; only public data.

    python3 scripts/miot_coverage_fixtures.py

After regenerating, re-record the baseline (see Tests/Fixtures/miot_coverage_golden.json).
"""
import json
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

BASE = "https://miot-spec.org/miot-spec-v2"
OUT = Path(__file__).resolve().parent.parent / "Tests/Fixtures/miot_coverage_specs.json"
SECOND_PICK_THRESHOLD = 100
# Dual-track curtain motor (two curtain services), the multi-service fish tanks, and a Xiaomi
# feeder with a desiccant service (#7).
EXTRA_MODELS = [
    "czmydz.curtain.myf5",
    "xiaomi.fishbowl.m200",
    "xiaomi.fishbowl.m300",
    "hfjh.fishbowl.m100",
    "xiaomi.feeder.iv2002",
]


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.load(response)


def device_type(urn):
    return urn.split(":")[3]


def main():
    latest = {}
    for inst in fetch(f"{BASE}/instances?status=released")["instances"]:
        prev = latest.get(inst["model"])
        if prev is None or inst["version"] > prev["version"]:
            latest[inst["model"]] = inst

    by_type = defaultdict(list)
    for inst in latest.values():
        by_type[device_type(inst["type"])].append(inst["model"])

    picks = set()
    for models in by_type.values():
        models.sort()
        picks.add(models[0])
        if len(models) >= SECOND_PICK_THRESHOLD:
            picks.add(models[len(models) // 2])
    for model in EXTRA_MODELS:
        if model not in latest:
            sys.exit(f"{model} is not a released model any more; pick another for EXTRA_MODELS")
        picks.add(model)

    specs = {}
    for model in sorted(picks):
        urn = latest[model]["type"]
        specs[model] = fetch(f"{BASE}/instance?type={urllib.parse.quote(urn, safe='')}")
        print(f"{len(specs):4d}/{len(picks)} {model}", file=sys.stderr)

    lines = [f"  {json.dumps(m)}: {json.dumps(s, ensure_ascii=False, separators=(',', ':'))}" for m, s in specs.items()]
    OUT.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8")
    print(f"{len(by_type)} device types, {len(latest)} models, {len(specs)} specs -> {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
