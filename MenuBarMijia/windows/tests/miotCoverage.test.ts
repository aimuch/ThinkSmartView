import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it } from "node:test";
import { isDeepStrictEqual } from "node:util";
import { buildMiotFacets, miotFallbackAllows } from "../src/core/miCloud/facets.js";
import { miotShortName, parseMiotDeviceSpec } from "../src/core/miCloud/spec.js";
import type { MiotDeviceSpec, MiotProperty } from "../src/core/miCloud/spec.js";
import { MiotSynthesizer, preferredMiotServices, primaryMiotService } from "../src/core/miCloud/synthesis.js";

// Same classification as Tests/MijiaCoreTests/MiotCoverageBaselineTests.swift, pinned to the shared golden (#295).
type Category =
  | "matched"
  | "misclaimed"
  | "genericControllable"
  | "genericReadOnly"
  | "droppedControllable"
  | "droppedReadOnly"
  | "noUsableProperties";
interface Entry {
  deviceType: string;
  category: Category;
  service?: string;
  unexposedWritable?: number;
}
interface Golden {
  summary: { specs: number; deviceTypes: number; categories: Record<Category, number>; unexposedWritableTotal: number };
  models: Record<string, Entry>;
  windowsDivergences: Record<string, Entry>;
}

const NOISE_SERVICES = new Set(["device-information", "exception-card", "device-custom"]);

const isControl = (p: MiotProperty) =>
  p.access.includes("write") && (p.format === "bool" || (p.valueList?.length ?? 0) > 0 || p.valueRange !== undefined);

function classify(spec: MiotDeviceSpec): Entry {
  const deviceType = miotShortName(spec.type);
  const usable = spec.services.filter((s) => {
    const short = miotShortName(s.type);
    return !NOISE_SERVICES.has(short) && !short.endsWith("-custom");
  });
  const primary = primaryMiotService(spec);
  if (primary) {
    const service = miotShortName(primary.type);
    // A pair the fallback table allows (#296) is the device's own kind, not a misclaim.
    const ownType =
      preferredMiotServices(deviceType).includes(service) ||
      (deviceType.endsWith("sensor") && service === "environment") ||
      miotFallbackAllows(deviceType, service);
    if (!ownType) return { deviceType, category: "misclaimed", service };
    const record = { did: "coverage", name: "", model: "", isOnline: true, specType: null };
    const polled = new Set(MiotSynthesizer.requiredProps(record, spec).map((k) => `${k.siid}.${k.piid}`));
    const unexposedWritable = usable.reduce(
      (n, s) => n + s.properties.filter((p) => isControl(p) && !polled.has(`${s.iid}.${p.iid}`)).length,
      0
    );
    return { deviceType, category: "matched", service, unexposedWritable };
  }
  // The generic facet layer is the last rung (#6). `controls()` on macOS drops action facets
  // that carry arguments because this drawer has no generic editor for them.
  const controls = buildMiotFacets(spec).filter((facet) => (facet.arguments?.length ?? 0) === 0);
  if (controls.length > 0) {
    return {
      deviceType,
      category: controls.some((facet) => facet.isWritable) ? "genericControllable" : "genericReadOnly"
    };
  }
  if (usable.some((s) => s.actions.length > 0 || s.properties.some(isControl))) {
    return { deviceType, category: "droppedControllable" };
  }
  const readable = usable.some((s) => s.properties.some((p) => p.access.includes("read") && p.format !== "string"));
  return { deviceType, category: readable ? "droppedReadOnly" : "noUsableProperties" };
}

describe("MIoT coverage baseline (shared golden)", () => {
  const fixtures = resolve(process.cwd(), "../Tests/Fixtures");
  const rawSpecs = JSON.parse(readFileSync(resolve(fixtures, "miot_coverage_specs.json"), "utf8")) as Record<string, unknown>;
  const golden = JSON.parse(readFileSync(resolve(fixtures, "miot_coverage_golden.json"), "utf8")) as Golden;

  it("classifies every fixture spec as macOS does, except the pinned divergences", () => {
    const mismatches: string[] = [];
    for (const [model, raw] of Object.entries(rawSpecs)) {
      const spec = parseMiotDeviceSpec(raw);
      assert.ok(spec, `${model} parses`);
      const measured = classify(spec);
      const expected = golden.windowsDivergences[model] ?? golden.models[model];
      if (!isDeepStrictEqual(measured, expected)) {
        mismatches.push(`${model}: baseline ${JSON.stringify(expected)}, now ${JSON.stringify(measured)}`);
      }
    }
    assert.deepEqual(Object.keys(rawSpecs).sort(), Object.keys(golden.models).sort());
    assert.deepEqual(mismatches, []);
  });
});
