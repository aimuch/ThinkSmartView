import type { RaycastCapabilities, RaycastDevice, RaycastSnapshot } from "./snapshot";

/**
 * The contract MenuBarMijia writes to `raycast-snapshot.json` (schema version 1).
 * Keep the field names identical to `Sources/MijiaCore/RaycastSnapshot.swift`; a rename there is a
 * breaking change here and the schema version must move with it.
 */
export const SNAPSHOT_SCHEMA_VERSION = 1;

/** Lists the extension iterates while rendering; a missing one crashes the submenu, not just looks odd. */
const DEVICE_LISTS = ["availableEffects", "availableModes", "availablePresetModes"] as const;

/**
 * Validates the fields this extension dereferences. `JSON.parse`'s `any` ends here: a device
 * without `capabilities` would throw while rendering (`device.capabilities.minKelvin`), so it
 * becomes a readable error instead. Returns the same object, narrowed.
 */
export function validateSnapshot(parsed: unknown, path: string): RaycastSnapshot {
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`${path} is not a MenuBarMijia snapshot object.`);
  }
  const snapshot = parsed as Partial<RaycastSnapshot>;
  if (snapshot.schemaVersion !== SNAPSHOT_SCHEMA_VERSION) {
    throw new Error(
      `Snapshot schema ${String(snapshot.schemaVersion)} is not supported (expected ${SNAPSHOT_SCHEMA_VERSION}). Update MenuBarMijia or this extension.`,
    );
  }
  if (!Array.isArray(snapshot.devices) || !Array.isArray(snapshot.scenes) || !Array.isArray(snapshot.quickActions)) {
    throw new Error(`${path} is missing its device, scene or quick-action list.`);
  }
  snapshot.devices.forEach((device, index) => validateDevice(device, index, path));
  return snapshot as RaycastSnapshot;
}

function validateDevice(value: unknown, index: number, path: string): RaycastDevice {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${path} device #${index + 1} is not an object.`);
  }
  const device = value as Partial<RaycastDevice>;
  if (typeof device.id !== "string" || device.id.trim() === "") {
    throw new Error(`${path} device #${index + 1} has no id.`);
  }
  if (typeof device.name !== "string") {
    throw new Error(`${path} device "${device.id}" has no name.`);
  }
  const capabilities = device.capabilities;
  if (typeof capabilities !== "object" || capabilities === null) {
    throw new Error(`${path} device "${device.id}" has no capabilities object.`);
  }
  for (const key of DEVICE_LISTS) {
    if (!Array.isArray((capabilities as Partial<RaycastCapabilities>)[key])) {
      throw new Error(`${path} device "${device.id}" has no capabilities.${key} list.`);
    }
  }
  return device as RaycastDevice;
}
