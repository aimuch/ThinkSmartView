import assert from "node:assert/strict";
import { test } from "node:test";
import { SNAPSHOT_SCHEMA_VERSION, validateSnapshot } from "../src/snapshot-validation.ts";

const path = "/tmp/raycast-snapshot.json";

const capabilities = {
  canTogglePower: true,
  brightnessWritable: false,
  colorWritable: false,
  supportsMoonlight: false,
  availableEffects: [],
  temperatureWritable: false,
  availableModes: [],
  fanSpeedWritable: false,
  availablePresetModes: [],
  positionWritable: false,
  canMoveCover: false,
  isHighRisk: false,
};

function snapshotWith(devices: unknown[]): unknown {
  return {
    schemaVersion: SNAPSHOT_SCHEMA_VERSION,
    updatedAt: "2026-11-06T00:00:00Z",
    bridgeToken: "token",
    connectionSummary: "1 device",
    isStale: false,
    devices,
    quickActions: [],
    scenes: [],
    climate: [],
    environmentReadings: [],
  };
}

test("a well-formed snapshot passes through unchanged", () => {
  const snapshot = validateSnapshot(snapshotWith([{ id: "dev_1", name: "客厅灯", capabilities }]), path);
  assert.equal(snapshot.devices.length, 1);
  assert.equal(snapshot.devices[0].id, "dev_1");
});

test("a device without capabilities is a readable error, not a render crash", () => {
  assert.throws(
    () => validateSnapshot(snapshotWith([{ id: "dev_1", name: "客厅灯" }]), path),
    /device "dev_1" has no capabilities object/,
  );
});

test("a device missing a capability list is rejected", () => {
  assert.throws(
    () =>
      validateSnapshot(
        snapshotWith([{ id: "dev_1", name: "客厅灯", capabilities: { ...capabilities, availablePresetModes: undefined } }]),
        path,
      ),
    /capabilities\.availablePresetModes/,
  );
});

test("a device without an id or a name is rejected", () => {
  assert.throws(() => validateSnapshot(snapshotWith([{ name: "客厅灯", capabilities }]), path), /has no id/);
  assert.throws(() => validateSnapshot(snapshotWith([{ id: "dev_1", capabilities }]), path), /has no name/);
});

test("a foreign schema version or a missing top-level list keeps its own message", () => {
  assert.throws(
    () => validateSnapshot({ ...(snapshotWith([]) as Record<string, unknown>), schemaVersion: 99 }, path),
    /is not supported/,
  );
  assert.throws(() => validateSnapshot({ schemaVersion: SNAPSHOT_SCHEMA_VERSION, devices: [] }, path), /missing its device/);
});
