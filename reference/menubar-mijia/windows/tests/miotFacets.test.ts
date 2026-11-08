import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it } from "node:test";
import { isDeepStrictEqual } from "node:util";
import { DEVICE_PROFILES, buildMiotFacets, isMiotAlertLabel, matchesMiotFacetPattern } from "../src/core/miCloud/facets.js";
import { MiotSynthesizer } from "../src/core/miCloud/synthesis.js";
import { facetAlertText, facetTitle } from "../src/core/miotFacetText.js";
import { setLanguage } from "../src/core/i18n.js";
import type { MiotFacet } from "../src/core/miCloud/facets.js";
import { parseMiotDeviceSpec } from "../src/core/miCloud/spec.js";
import type { MiotDeviceSpec } from "../src/core/miCloud/spec.js";

// Same rule cases, fish tank, and coverage golden as Tests/MijiaCoreTests/MiotFacetsTests.swift (#303).
interface RuleCase {
  id: string;
  spec: unknown;
  expected: MiotFacet[];
}
interface ModelCase {
  model: string;
  expected: MiotFacet[];
}
interface Fixture {
  version: number;
  cases: RuleCase[];
  models: ModelCase[];
}
interface CoverageEntry {
  ids: string[];
  highValue: string[];
}
interface CoverageGolden {
  models: Record<string, CoverageEntry>;
}

const fixtures = resolve(process.cwd(), "../Tests/Fixtures");
const fixture = JSON.parse(readFileSync(resolve(fixtures, "miot_facets.json"), "utf8")) as Fixture;
const golden = JSON.parse(
  readFileSync(resolve(fixtures, "miot_facets_coverage_golden.json"), "utf8"),
) as CoverageGolden;
const rawSpecs = JSON.parse(readFileSync(resolve(fixtures, "miot_coverage_specs.json"), "utf8")) as Record<
  string,
  unknown
>;

function parseSpec(raw: unknown): MiotDeviceSpec {
  const spec = parseMiotDeviceSpec(raw);
  assert.ok(spec, "fixture spec parses");
  return spec;
}

function coverageSpecs(): [string, MiotDeviceSpec][] {
  return Object.entries(rawSpecs).map(([model, raw]) => [model, parseSpec(raw)]);
}

describe("MIoT facet engine (shared fixture)", () => {
  it("recognizes alert words without treating healthy labels or word fragments as faults", () => {
    for (const label of ["Low", "Low1", "Bockage", "Stuck Food", "Pump Fault", "水泵缺水"]) {
      assert.equal(isMiotAlertLabel(label), true, label);
    }
    for (const label of ["Flow", "No Faults", "Normal", "Allowed", "low2", "水Low水"]) {
      assert.equal(isMiotAlertLabel(label), false, label);
    }
  });

  it("ignores invalid alert values and keeps the existing English titles", () => {
    const spec = parseSpec({
      type: "urn:miot-spec-v2:device:fish-tank:00000000:fixture:1",
      services: [{
        iid: 2,
        type: "urn:miot-spec-v2:service:fish-tank:00000000:fixture:1",
        properties: [{
          iid: 1,
          type: "urn:miot-spec-v2:property:pump-alarm:00000000:fixture:1",
          description: "Pump alarm",
          format: "uint8",
          access: ["read"],
          "value-list": [{ value: 1, description: "Low" }],
        }],
      }],
    });
    const record = { did: "tank", name: "tank", model: "fixture.tank", isOnline: true, specType: null };
    for (const raw of [NaN, Infinity, -Infinity, Number.MAX_VALUE, "bad", "", null, true]) {
      const card = MiotSynthesizer.genericCard(record, spec, new Map([["tank.2.1", raw]]));
      assert.deepEqual(card?.kind.alerts, [], String(raw));
    }
    const card = MiotSynthesizer.genericCard(record, spec, new Map([["tank.2.1", 1]]));
    const control = card?.facets[0];
    assert.ok(control);
    setLanguage("en");
    try {
      assert.equal(facetTitle({ ...control, name: "brightness", title: "亮度" }), "Brightness");
      assert.equal(facetAlertText(control, "Low"), "Pump alarm：Low");
      assert.equal(facetAlertText({ ...control, title: null }, "Low"), "Pump alarm：Low");
    } finally {
      setLanguage("zh-Hans");
    }
  });

  it("matches every hand-written rule case", () => {
    for (const ruleCase of fixture.cases) {
      const actual = buildMiotFacets(parseSpec(ruleCase.spec));
      assert.ok(isDeepStrictEqual(actual, ruleCase.expected), `${ruleCase.id}: ${JSON.stringify(actual)}`);
    }
  });

  it("matches the fish tank facets and keeps high value small", () => {
    for (const modelCase of fixture.models) {
      const spec = parseSpec(rawSpecs[modelCase.model]);
      const actual = buildMiotFacets(spec);
      assert.ok(isDeepStrictEqual(actual, modelCase.expected), `${modelCase.model}: ${JSON.stringify(actual)}`);
      const highValue = actual.filter((facet) => facet.highValue);
      assert.ok(highValue.length <= 8, `${modelCase.model}: ${highValue.length}`);
      assert.ok(highValue.length < actual.length, `${modelCase.model}`);
    }
  });

  it("keeps the new high-risk services and device types closed", () => {
    const makeSpec = (deviceType: string, serviceName: string) =>
      parseSpec({
        type: `urn:miot-spec-v2:device:${deviceType}:00000000:fixture:1`,
        services: [
          {
            iid: 2,
            type: `urn:miot-spec-v2:service:${serviceName}:00000000:fixture:1`,
            properties: [
              {
                iid: 1,
                type: "urn:miot-spec-v2:property:on:00000000:fixture:1",
                format: "bool",
                access: ["read", "write"],
              },
            ],
          },
        ],
      });

    for (const serviceName of [
      "garbage-disposal",
      "disposal-service",
      "alertor",
      "alarm",
      "sterilizer",
      "baby-bottle-warmer",
      "multifunction-cooking-pot",
    ]) {
      const facet = buildMiotFacets(makeSpec("vendor", serviceName))[0];
      assert.ok(facet);
      assert.equal(facet.risk, "high", serviceName);
      assert.equal(facet.writesByDefault, false, serviceName);
    }

    for (const deviceType of ["germicidal-lamp", "alertor", "garbage-disposal"]) {
      const facet = buildMiotFacets(makeSpec(deviceType, "switch"))[0];
      assert.ok(facet);
      assert.equal(facet.risk, "high", deviceType);
      assert.equal(facet.writesByDefault, false, deviceType);
    }
  });

  it("keeps high-risk writes closed until the data whitelist opens them", () => {
    const ruleCase = fixture.cases.find((candidate) => candidate.id === "low-risk-writes-are-open-by-default");
    assert.ok(ruleCase);
    const facets = buildMiotFacets(parseSpec(ruleCase.spec));
    const on = facets.find((facet) => facet.name === "on");
    assert.ok(on);
    assert.equal(on.risk, "low");
    assert.equal(on.writesByDefault, true);
    for (const name of ["mode", "brightness", "fan-level"]) {
      const facet = facets.find((candidate) => candidate.name === name);
      assert.ok(facet, name);
      assert.equal(facet.risk, "low", name);
      assert.equal(facet.writesByDefault, true, name);
    }
    const hazardous = fixture.cases.find(
      (candidate) => candidate.id === "hazardous-device-type-keeps-whitelisted-writes-closed",
    );
    assert.ok(hazardous);
    const heaterMode = buildMiotFacets(parseSpec(hazardous.spec)).find((facet) => facet.name === "mode");
    assert.ok(heaterMode);
    assert.equal(heaterMode.risk, "high");
    assert.equal(heaterMode.writesByDefault, false);
  });

  it("drops every noise service and property across the coverage fixture", () => {
    for (const [model, spec] of coverageSpecs()) {
      for (const facet of buildMiotFacets(spec, DEVICE_PROFILES, model)) {
        if (matchesMiotFacetPattern(facet.serviceName, DEVICE_PROFILES.noise.services)) {
          // A noisy vendor service only survives through the named exceptions (#7).
          assert.ok(
            matchesMiotFacetPattern(facet.name, DEVICE_PROFILES.noise.keepInNoisyServices),
            `${model}: service ${facet.serviceName} property ${facet.name}`,
          );
        }
        assert.ok(
          !matchesMiotFacetPattern(facet.name, DEVICE_PROFILES.noise.properties),
          `${model}: property ${facet.name}`,
        );
      }
    }
  });

  it("gives every facet a stable id and constraints that match its kind", () => {
    for (const [model, spec] of coverageSpecs()) {
      const ids = new Set<string>();
      for (const facet of buildMiotFacets(spec, DEVICE_PROFILES, model)) {
        assert.ok(!ids.has(facet.id), `${model}: duplicate ${facet.id}`);
        ids.add(facet.id);
        switch (facet.kind) {
          case "toggle":
            assert.ok(facet.isWritable, `${model}: ${facet.id}`);
            assert.equal(facet.range, undefined, `${model}: ${facet.id}`);
            assert.equal(facet.options, undefined, `${model}: ${facet.id}`);
            break;
          case "select":
            assert.ok(facet.isWritable, `${model}: ${facet.id}`);
            assert.ok((facet.options?.length ?? 0) > 0, `${model}: ${facet.id}`);
            break;
          case "slider":
            assert.ok(facet.isWritable, `${model}: ${facet.id}`);
            assert.ok(facet.range, `${model}: ${facet.id}`);
            break;
          case "reading":
            assert.equal(facet.isWritable, false, `${model}: ${facet.id}`);
            break;
          case "button":
            assert.ok(facet.isWritable, `${model}: ${facet.id}`);
            assert.ok(facet.actionId !== undefined && facet.propertyId === undefined, `${model}: ${facet.id}`);
            break;
        }
        if (facet.propertyId !== undefined) {
          assert.equal(facet.id, `${facet.serviceId}.${facet.propertyId}`, `${model}: ${facet.id}`);
          assert.equal(facet.actionId, undefined, `${model}: ${facet.id}`);
        }
        if (facet.actionId !== undefined) {
          assert.equal(facet.id, `${facet.serviceId}.a${facet.actionId}`, `${model}: ${facet.id}`);
          assert.equal(facet.propertyId, undefined, `${model}: ${facet.id}`);
        }
        assert.ok(facet.propertyId !== undefined || facet.actionId !== undefined, `${model}: ${facet.id}`);
      }
    }
  });

  it("matches the shared coverage golden", () => {
    const measured: Record<string, CoverageEntry> = {};
    for (const [model, spec] of coverageSpecs()) {
      const facets = buildMiotFacets(spec, DEVICE_PROFILES, model);
      measured[model] = {
        ids: facets.map((facet) => facet.id),
        highValue: facets.filter((facet) => facet.highValue).map((facet) => facet.id),
      };
    }
    assert.deepEqual(Object.keys(measured).sort(), Object.keys(golden.models).sort());
    for (const model of Object.keys(golden.models).sort()) {
      assert.deepEqual(measured[model], golden.models[model], model);
    }
  });

  it("ships the same deviceProfiles.json as macOS", () => {
    const windows = readFileSync(resolve(process.cwd(), "src/core/miCloud/deviceProfiles.json"));
    const macos = readFileSync(resolve(fixtures, "..", "..", "Sources/MijiaCore/Resources/deviceProfiles.json"));
    assert.ok(windows.equals(macos));
  });
});
