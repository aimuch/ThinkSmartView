import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it } from "node:test";
import { percentFromRaw, rawFromPercent, type PercentCurve } from "../src/core/miCloud/percentScale.js";
import { miotShortName, parseMiotDeviceSpec, type MiotProperty } from "../src/core/miCloud/spec.js";

// Mirrors Tests/MijiaCoreTests/MiotPercentScaleTests.swift: a percent read off a card writes back
// the raw value it came from.
const fixtures = resolve(import.meta.dirname, "../../Tests/Fixtures");

function wire(raw: number, prop: MiotProperty): number {
  let n = raw;
  const range = prop.valueRange;
  if (range && range.length >= 2) n = Math.min(range[1]!, Math.max(range[0]!, n));
  return prop.format.startsWith("int") || prop.format.startsWith("uint") ? Math.round(n) : n;
}

function prop(name: string, extra: Partial<MiotProperty>): MiotProperty {
  return { iid: 2, type: `urn:miot-spec-v2:property:${name}:00000000:test:1`, format: "uint16", access: ["read", "write"], ...extra };
}

describe("MIoT percent scale", () => {
  it("round-trips every level of a list starting at 1", () => {
    const p = prop("fan-level", { format: "uint8", valueList: [1, 2, 3].map((value) => ({ value, description: `L${value}` })) });
    for (const level of [1, 2, 3]) {
      assert.equal(rawFromPercent(percentFromRaw(level, p, "speed"), p, "speed"), level);
    }
  });

  it("round-trips a speed range starting above 1 and a volume with a non-zero minimum", () => {
    const fan = prop("fan-level", { valueRange: [2, 5, 1] });
    for (const raw of [2, 3, 4, 5]) assert.equal(wire(rawFromPercent(percentFromRaw(raw, fan, "speed"), fan, "speed"), fan), raw);
    const vol = prop("volume", { valueRange: [1, 100, 1] });
    for (const raw of [1, 30, 50, 100]) {
      assert.equal(wire(rawFromPercent(percentFromRaw(raw, vol, "proportional"), vol, "proportional"), vol), raw);
    }
  });

  it("keeps every coverage-spec property stable under write-back", () => {
    const curves: Record<string, PercentCurve> = {
      brightness: "proportional", volume: "proportional", "target-position": "proportional",
      "fan-level": "speed", "favorite-fan-level": "speed", "favorite-level": "speed", "favorite-speed": "speed",
    };
    const rawSpecs = JSON.parse(readFileSync(resolve(fixtures, "miot_coverage_specs.json"), "utf8")) as Record<string, unknown>;
    let checked = 0;
    for (const [model, raw] of Object.entries(rawSpecs)) {
      const spec = parseMiotDeviceSpec(raw);
      if (!spec) continue;
      for (const service of spec.services) {
        for (const p of service.properties) {
          const curve = curves[miotShortName(p.type)];
          if (!curve || !p.access.includes("write")) continue;
          const range = p.valueRange;
          const isList = (p.valueList?.length ?? 0) > 0 && curve === "speed" && !range;
          let raws: number[];
          if (isList) {
            raws = p.valueList!.map((i) => i.value);
          } else if (range && range.length >= 2 && range[1]! > range[0]!) {
            const step = Math.max(range[2] ?? 1, (range[1]! - range[0]!) / 50);
            raws = [];
            for (let v = range[0]!; v <= range[1]! + 1e-9; v += step) raws.push(v);
          } else {
            continue;
          }
          for (const r of raws) {
            const percent = percentFromRaw(r, p, curve);
            const back = wire(rawFromPercent(percent, p, curve), p);
            assert.equal(percentFromRaw(back, p, curve), percent, `${model} ${p.type} raw ${r}`);
            if (isList) assert.equal(back, r, `${model} level ${r}`);
            checked += 1;
          }
        }
      }
    }
    assert.ok(checked > 100);
  });
});
