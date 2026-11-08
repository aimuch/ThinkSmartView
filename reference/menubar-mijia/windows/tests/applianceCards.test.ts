import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  allOffTargets,
  joinsAllOffByDefault,
  type DeviceKind,
  type FacetControl,
  type GenericSpec,
  type SmartDevice,
} from "../src/core/models.js";
import { IntentValidator } from "../src/core/assistant.js";
import { applyCommandToDevices } from "../src/core/commandEffects.js";
import { deviceOwningEntityId } from "../src/ui/commandDispatch.js";
import { parseMiotDeviceSpec } from "../src/core/miCloud/spec.js";
import { MiotSynthesizer } from "../src/core/miCloud/synthesis.js";
import { facetWriteEnabled, facetWriteIsHazardous } from "../src/ui/facetViews.js";

const U = (kind: string, name: string, code: string, vendor: string) =>
  `urn:miot-spec-v2:${kind}:${name}:${code}:${vendor}:1`;

/** An airer whose `current-position` is the distance lowered, and a status list without travel words. */
const airerSpec = parseMiotDeviceSpec({
  type: U("device", "airer", "0000A00D", "airer-test"),
  services: [
    {
      iid: 2,
      type: U("service", "airer", "0000A00D", "airer-test"),
      properties: [
        {
          iid: 1,
          type: U("property", "motor-control", "00000038", "airer-test"),
          format: "uint8",
          access: ["write"],
          "value-list": [
            { value: 0, description: "Pause" },
            { value: 1, description: "Up" },
            { value: 2, description: "Down" },
          ],
        },
        {
          iid: 2,
          type: U("property", "current-position", "00000039", "airer-test"),
          format: "uint8",
          access: ["read", "notify"],
          "value-range": [0, 100, 1],
        },
        {
          iid: 3,
          type: U("property", "target-position", "0000003A", "airer-test"),
          format: "uint8",
          access: ["read", "write", "notify"],
          "value-range": [0, 100, 1],
        },
      ],
      actions: [],
    },
  ],
});
assert.ok(airerSpec);

/** A device type with no dedicated builder, one low-risk switch and a high-risk child lock. */
const tankSpec = parseMiotDeviceSpec({
  type: U("device", "fish-tank", "0000A02B", "tank-test"),
  services: [
    {
      iid: 2,
      type: U("service", "fish-tank", "0000A02B", "tank-test"),
      properties: [
        { iid: 1, type: U("property", "on", "00000006", "tank-test"), format: "bool", access: ["read", "write"] },
        {
          iid: 2,
          type: U("property", "mode", "00000008", "tank-test"),
          format: "uint8",
          access: ["read", "write"],
          "value-list": [
            { value: 0, description: "Auto" },
            { value: 1, description: "Manual" },
          ],
        },
        { iid: 3, type: U("property", "battery-level", "00000024", "tank-test"), format: "uint8", access: ["read", "notify"], unit: "percentage" },
        { iid: 4, type: U("property", "child-lock", "00000015", "tank-test"), format: "bool", access: ["read", "write"] },
      ],
      actions: [
        { iid: 1, type: U("action", "reset-filter", "00002810", "tank-test"), in: [] },
        {
          iid: 2,
          type: U("action", "set-level", "00002811", "tank-test"),
          in: [5],
        },
      ],
    },
  ],
});
assert.ok(tankSpec);

function device(kind: DeviceKind, id = "dev"): SmartDevice {
  return { id, name: "设备", roomId: "room", roomName: "客厅", model: "fixture", isOnline: true, kind };
}

describe("cover cards (#9)", () => {
  const build = (values: ReadonlyMap<string, unknown>) =>
    MiotSynthesizer.buildDeviceKind("did_airer", airerSpec!, values);

  it("reports height, not the distance an airer has lowered", () => {
    const kind = build(new Map([["did_airer.2.2", 30], ["did_airer.2.3", 30]]));
    assert.equal(kind?.kind, "cover");
    if (kind?.kind !== "cover") return;
    assert.equal(kind.subtype, "clothes-airer");
    assert.equal(kind.positionPercent, 70, "30% lowered is 70% raised");
    assert.equal(kind.openDescription, "Up");
    assert.equal(kind.closeDescription, "Down");
    assert.equal(kind.motion, "stopped");
  });

  it("infers travel from the target when the status list names no travel", () => {
    // Target 0 means fully raised, i.e. the card's 100%.
    const opening = build(new Map([["did_airer.2.2", 30], ["did_airer.2.3", 0]]));
    assert.equal(opening?.kind === "cover" ? opening.motion : null, "opening");
    assert.equal(opening?.kind === "cover" ? opening.positionPercent : null, 100, "the target is the card's position while it travels");

    const closing = build(new Map([["did_airer.2.2", 30], ["did_airer.2.3", 60]]));
    assert.equal(closing?.kind === "cover" ? closing.motion : null, "closing");
    assert.equal(closing?.kind === "cover" ? closing.positionPercent : null, 40);
  });

  it("snaps a parked position to the end it is within two points of", () => {
    const kind = build(new Map([["did_airer.2.2", 99], ["did_airer.2.3", 99]]));
    assert.equal(kind?.kind === "cover" ? kind.positionPercent : null, 0, "99% lowered is fully lowered");
  });
});

describe("generic fallback card (#6)", () => {
  const build = () => {
    const home = MiotSynthesizer.synthesize(
      [{ did: "did_tank", name: "鱼缸", model: "fixture.tank", isOnline: true, specType: null }],
      [],
      { "fixture.tank": tankSpec! },
      new Map<string, unknown>([
        ["did_tank.2.1", true],
        ["did_tank.2.2", 0],
        ["did_tank.2.3", 88],
        ["did_tank.2.4", false],
      ]),
    );
    const kind = home.devices[0]?.kind;
    assert.equal(kind?.kind, "generic");
    return { kind: kind as GenericSpec, facets: home.deviceFacets?.did_tank ?? [] };
  };

  it("reads a MIoT numeric boolean as the card's power state", () => {
    const build = (raw: number) => {
      const home = MiotSynthesizer.synthesize(
        [{ did: "did_tank", name: "鱼缸", model: "fixture.tank", isOnline: true, specType: null }],
        [],
        { "fixture.tank": tankSpec! },
        new Map<string, unknown>([["did_tank.2.1", raw], ["did_tank.2.2", 0], ["did_tank.2.3", 88]]),
      );
      const kind = home.devices[0]?.kind;
      assert.equal(kind?.kind, "generic");
      return kind?.kind === "generic" ? kind : null;
    };
    assert.equal(build(1)?.isOn, true, "1 means on");
    assert.equal(build(0)?.isOn, false, "0 means off");
  });

  it("keeps one low-risk switch and the readings, and puts the rest in the drawer", () => {
    const { kind, facets } = build();
    assert.equal(kind.deviceType, "fish-tank");
    assert.equal(kind.isOn, true);
    assert.equal(kind.isReadOnly, false);
    assert.deepEqual(kind.readouts, ["88%"]);
    assert.equal(kind.powerControl?.id, "2.1");
    assert.deepEqual(
      facets.map((control) => control.name),
      ["mode", "battery-level", "child-lock", "reset-filter"],
      "the tile's own switch is not repeated under 更多设置",
    );
  });

  it("offers no drawer row for an action that takes arguments", () => {
    const { facets } = build();
    assert.deepEqual(
      facets.filter((control) => control.kind === "button").map((control) => control.name),
      ["reset-filter"],
      "set-level needs an editor this drawer does not have",
    );
  });

  it("stays out of 全关 and assistant batches", () => {
    const { kind } = build();
    const generic = device(kind, "did_tank");
    assert.equal(joinsAllOffByDefault(generic), false);
    assert.deepEqual(allOffTargets([generic], {}), []);
    assert.deepEqual(
      IntentValidator.commandsForIntent({ targetId: generic.id, operation: { kind: "power", isOn: false } }, generic),
      [{ kind: "setMiotProperty", entityId: "miot:did_tank:2", piid: 1, value: false }],
    );
  });
});

describe("更多设置 writes (#5)", () => {
  const powerFacet = (overrides: Partial<FacetControl> = {}): FacetControl => ({
    id: "2.1",
    kind: "toggle",
    name: "on",
    title: null,
    serviceName: "fish-tank",
    unit: null,
    scale: null,
    value: true,
    options: [],
    range: null,
    isWritable: true,
    writesByDefault: true,
    isHighValue: false,
    target: { kind: "property", entityId: "miot:did_tank:2", piid: 1 },
    ...overrides,
  });

  const modeFacet = (overrides: Partial<FacetControl> = {}): FacetControl =>
    powerFacet({
      id: "2.2",
      kind: "select",
      name: "mode",
      options: [
        { value: 0, label: "Auto" },
        { value: 1, label: "Manual" },
      ],
      value: 0,
      target: { kind: "property", entityId: "miot:did_tank:2", piid: 2 },
      ...overrides,
    });

  it("leaves the card's power alone when a sibling property on the same service is written", () => {
    const card = device(
      {
        kind: "generic",
        deviceType: "fish-tank",
        typeTitle: "鱼缸",
        isOn: true,
        powerControl: powerFacet(),
        isReadOnly: false,
        readouts: [],
        symbolName: "fish.fill",
      },
      "did_tank",
    );
    const facets = { did_tank: [powerFacet(), modeFacet()] };
    applyCommandToDevices([card], { kind: "setMiotProperty", entityId: "miot:did_tank:2", piid: 2, value: 1 }, "demo", facets);
    assert.equal(card.kind.kind === "generic" ? card.kind.isOn : null, true, "a mode write is not a power write");
    assert.equal(facets.did_tank[1]?.value, 1, "the row still shows the value the write asked for");

    applyCommandToDevices([card], { kind: "setMiotProperty", entityId: "miot:did_tank:2", piid: 1, value: 0 }, "demo", facets);
    assert.equal(card.kind.kind === "generic" ? card.kind.isOn : null, false);
    assert.equal(facets.did_tank[0]?.value, 0, "the switch row follows the write");
  });

  it("resolves a drawer row on a sibling service to its own card", () => {
    const plug = device(
      {
        kind: "plug",
        switchEntityId: "miot:plug1:2",
        isOn: true,
        powerWatts: null,
        childLockEntityId: null,
        isChildLocked: false,
      },
      "plug1",
    );
    const facets = {
      plug1: [
        powerFacet({
          id: "3.1",
          name: "indicator-light",
          target: { kind: "property", entityId: "miot:plug1:3", piid: 1 },
        }),
      ],
    };
    assert.equal(deviceOwningEntityId([plug], facets, "miot:plug1:2"), "plug1", "the main switch");
    assert.equal(deviceOwningEntityId([plug], facets, "miot:plug1:3"), "plug1", "the drawer row");
    assert.equal(deviceOwningEntityId([plug], facets, "miot:plug2:3"), null);
  });

  const facet = (overrides: Partial<FacetControl>): FacetControl => ({
    id: "2.4",
    kind: "toggle",
    name: "child-lock",
    title: null,
    serviceName: "fish-tank",
    unit: null,
    scale: null,
    value: false,
    options: [],
    range: null,
    isWritable: true,
    writesByDefault: false,
    isHighValue: false,
    target: { kind: "property", entityId: "miot:did_tank:2", piid: 4 },
    ...overrides,
  });

  it("keeps a row outside the low-risk whitelist disabled until advanced writes are allowed", () => {
    const control = facet({});
    assert.equal(facetWriteEnabled(control, false), false);
    assert.equal(facetWriteEnabled(control, true), true);
    assert.equal(facetWriteEnabled(facet({ writesByDefault: true }), false), true);
    assert.equal(facetWriteEnabled(facet({ isWritable: false }), true), false);
  });

  it("asks once more for the hazardous direction only", () => {
    const control = facet({});
    assert.equal(facetWriteIsHazardous(control, true), true, "turning a gated switch on asks");
    assert.equal(facetWriteIsHazardous(control, false), false, "turning it off only makes the home safer");
    assert.equal(facetWriteIsHazardous(facet({ writesByDefault: true }), true), false, "a whitelisted write stays direct");
    const mode = facet({
      id: "2.2",
      kind: "select",
      name: "mode",
      options: [
        { value: 0, label: "Off" },
        { value: 1, label: "Heat" },
      ],
    });
    assert.equal(facetWriteIsHazardous(mode, 0), false);
    assert.equal(facetWriteIsHazardous(mode, 1), true);
  });
});

describe("appliance power routing (#3, #4)", () => {
  it("addresses the service that carries power, not the card's main function", () => {
    const appliance = device({
      kind: "appliance",
      entityId: "miot:did_heater:2",
      isOn: false,
      powerPresent: true,
      powerWritable: true,
      currentMode: null,
      availableModes: [],
      modeWritable: false,
      targetTemperature: null,
      currentTemperature: null,
      minTemp: 0,
      maxTemp: 100,
      step: 1,
      temperatureWritable: false,
      statusText: null,
      faultText: null,
      remainingMinutes: null,
      startAction: null,
      pauseAction: null,
      stopAction: null,
      powerEntityId: "miot:did_heater:9",
      modeEntityId: "miot:did_heater:2",
      temperatureEntityId: "miot:did_heater:2",
      light: null,
      fanLevel: null,
      currentHumidity: null,
      symbolName: null,
      roomReading: false,
      heats: true,
    });
    assert.deepEqual(
      IntentValidator.commandsForIntent({ targetId: appliance.id, operation: { kind: "power", isOn: true } }, appliance),
      [{ kind: "setSwitch", entityId: "miot:did_heater:9", isOn: true }],
    );
    assert.equal(joinsAllOffByDefault(appliance), false);
  });
});
