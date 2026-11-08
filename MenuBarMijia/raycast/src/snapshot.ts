import { getPreferenceValues } from "@raycast/api";
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import { validateSnapshot } from "./snapshot-validation";

/**
 * The contract MenuBarMijia writes to `raycast-snapshot.json` (schema version 1).
 * Keep the field names identical to `Sources/MijiaCore/RaycastSnapshot.swift`; a rename there is a
 * breaking change here and the schema version must move with it. `validateSnapshot` checks the
 * fields this extension dereferences before any command renders them.
 */
export const SNAPSHOT_ENV = "MENUBARMIJIA_SNAPSHOT_PATH";

export interface RaycastCapabilities {
  canTogglePower: boolean;
  brightnessWritable: boolean;
  brightnessPercent?: number;
  colorWritable: boolean;
  colorTempKelvin?: number;
  minKelvin?: number;
  maxKelvin?: number;
  supportsMoonlight: boolean;
  availableEffects: string[];
  temperatureWritable: boolean;
  targetTemperature?: number;
  currentTemperature?: number;
  minTemp?: number;
  maxTemp?: number;
  step?: number;
  hvacMode?: string;
  availableModes: string[];
  fanSpeedWritable: boolean;
  fanPercentage?: number;
  availablePresetModes: string[];
  coverSubtype?: string;
  positionWritable: boolean;
  positionPercent?: number;
  canMoveCover: boolean;
  isHighRisk: boolean;
}

export interface RaycastDevice {
  id: string;
  name: string;
  roomId: string;
  roomName: string;
  roomSortOrder: number;
  category: string;
  categoryTitle: string;
  model: string;
  stateText: string;
  isOnline: boolean;
  isActiveOn: boolean;
  isPrimaryToggleable: boolean;
  isFavorite: boolean;
  favoriteRank?: number;
  pinyinInitials: string;
  pinyinFull: string;
  roomPinyinInitials: string;
  capabilities: RaycastCapabilities;
}

export interface RaycastQuickAction {
  id: string;
  name: string;
  subtitle: string;
  icon: string;
  isEnabled: boolean;
  affectedCount: number;
  pinyinInitials: string;
}

export interface RaycastScene {
  id: string;
  name: string;
  entityId: string;
  roomName?: string;
  icon: string;
  visualCategory: string;
  pinyinInitials: string;
  pinyinFull: string;
}

export interface RaycastReading {
  id: string;
  label: string;
  roomName: string;
  temperature?: number;
  humidity?: number;
  battery?: number;
  updatedAt: string;
  pinyinInitials: string;
}

export interface RaycastFeedback {
  requestId?: string;
  command: string;
  message: string;
  isError: boolean;
  completedAt: string;
}

export interface RaycastSnapshot {
  schemaVersion: number;
  updatedAt: string;
  bridgeToken: string;
  connectionSummary: string;
  isStale: boolean;
  devices: RaycastDevice[];
  quickActions: RaycastQuickAction[];
  scenes: RaycastScene[];
  climate: RaycastReading[];
  environmentReadings: RaycastReading[];
  lastCommandFeedback?: RaycastFeedback;
}

interface Preferences {
  snapshotPath?: string;
  waitTimeoutSeconds?: string;
}

export function snapshotPath(): string {
  const preferences = getPreferenceValues<Preferences>();
  const override = (process.env[SNAPSHOT_ENV] ?? preferences.snapshotPath ?? "").trim();
  if (override) {
    return override.startsWith("~/") ? join(homedir(), override.slice(2)) : override;
  }
  return join(homedir(), "Library", "Application Support", "MenuBarMijia", "raycast-snapshot.json");
}

export function resultTimeoutSeconds(): number {
  const raw = Number.parseFloat(getPreferenceValues<Preferences>().waitTimeoutSeconds ?? "");
  return Number.isFinite(raw) && raw > 0 ? raw : 12;
}

export async function loadSnapshot(): Promise<RaycastSnapshot> {
  const path = snapshotPath();
  let raw: string;
  try {
    raw = await readFile(path, "utf8");
  } catch {
    throw new Error(
      `Cannot read ${path}. Turn on Settings → 自动化与外部控制 → Raycast 集成 in MenuBarMijia.`,
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error(`${path} is not valid JSON.`);
  }
  // JSON.parse's `any` ends at validateSnapshot; below it the snapshot type is earned, not assumed.
  return validateSnapshot(parsed, path);
}
