import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it } from "node:test";
import { MiotCommandMapper } from "../src/core/miCloud/command.js";
import { parseMiotDeviceSpec, type MiotDeviceSpec } from "../src/core/miCloud/spec.js";
import { MiotSynthesizer } from "../src/core/miCloud/synthesis.js";

// #314: Windows dropped window openers and drew a speaker as an air conditioner.
const specs = JSON.parse(
  readFileSync(resolve(process.cwd(), "../Tests/Fixtures/miot_coverage_specs.json"), "utf8"),
) as Record<string, unknown>;

function fixture(model: string): MiotDeviceSpec {
  const spec = parseMiotDeviceSpec(specs[model]);
  assert.ok(spec, `${model} parses`);
  return spec;
}

function synthesize(model: string, values: Record<string, unknown>) {
  const record = { did: "did1", name: "Device", model, isOnline: true, specType: null };
  const home = MiotSynthesizer.synthesize([record], [], { [model]: fixture(model) }, new Map(Object.entries(values)));
  assert.equal(home.devices.length, 1);
  return home.devices[0]!.kind;
}

describe("Windows speaker and window-opener cards (#314)", () => {
  it("builds a speaker card, not an air conditioner, for a Wi-Fi speaker", () => {
    const kind = synthesize("090615.wifispeaker.x6bj", { "did1.2.1": 35, "did1.3.1": 1 });
    assert.equal(kind.kind, "speaker");
    if (kind.kind !== "speaker") return;
    assert.equal(kind.mediaEntityId, "miot:did1:2");
    assert.equal(kind.volumePercent, 35);
    assert.equal(kind.state, "playing");
    assert.equal(kind.volumeWritable, true);
    assert.equal(kind.muteSwitchEntityId, null);
  });

  it("writes a volume level onto the speaker's volume range", () => {
    const write = MiotCommandMapper.map(
      { kind: "mediaSetVolume", entityId: "miot:did1:2", level: 0.42 },
      fixture("090615.wifispeaker.x6bj"),
    );
    assert.deepEqual(write, { kind: "setProps", writes: [{ did: "did1", siid: 2, piid: 1, value: 42 }] });
  });

  it("builds a window-opener cover card with its own risk class", () => {
    const kind = synthesize("babai.wopener.82", { "did1.2.7": 30 });
    assert.equal(kind.kind, "cover");
    if (kind.kind !== "cover") return;
    assert.equal(kind.subtype, "window-opener");
    assert.equal(kind.entityId, "miot:did1:2");
    assert.equal(kind.positionPercent, 30);
    assert.equal(kind.positionWritable, true);
  });
});
