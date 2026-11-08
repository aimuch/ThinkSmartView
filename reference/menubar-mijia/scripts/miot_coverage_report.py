#!/usr/bin/env python3
"""Print the MIoT coverage baseline (#295) from Tests/Fixtures/miot_coverage_golden.json.

Both platform test suites assert that synthesis matches this file, so the report is the
current state of the code. Counts are per fixture spec (1-2 per device type), not per model,
so they are not comparable with the model-weighted estimates in docs/research.
"Unexposed" counts writable bool/enum/range properties the card never reads; a property it
reads without offering a control counts as exposed, so the figure is a lower bound.
"""
import json
import sys
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent.parent / "Tests/Fixtures/miot_coverage_golden.json"
LABELS = [
    ("matched", "类型对得上，有专用卡片"),
    ("genericControllable", "通用卡片，有可写项"),
    ("genericReadOnly", "通用卡片，只读"),
    ("misclaimed", "被误判成别的类型"),
    ("droppedControllable", "丢弃但有可控项"),
    ("droppedReadOnly", "丢弃且只读"),
    ("noUsableProperties", "无可用属性"),
]


def main():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    summary = golden["summary"]
    total = summary["specs"]
    out = sys.stdout
    out.write(f"📊 MIoT 覆盖率基线（{total} 份规格，{summary['deviceTypes']} 个设备类型，macOS）\n")
    for key, label in LABELS:
        count = summary["categories"][key]
        out.write(f"   {count:4d}  {count / total:6.1%}  {label}\n")
    matched = summary["categories"]["matched"]
    if matched:
        avg = summary["unexposedWritableTotal"] / matched
        out.write(f"   对得上的设备平均未暴露可写控件：{avg:.1f} 个\n")
    divergences = golden.get("windowsDivergences", {})
    if divergences:
        out.write(f"   Windows 分类不同的规格：{len(divergences)} 份（{', '.join(sorted(divergences))}）\n")


if __name__ == "__main__":
    main()
