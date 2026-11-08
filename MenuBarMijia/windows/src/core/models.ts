import { t } from "./i18n.js";
import { formatTemperature } from "./temperature.js";

export type HomeBackend = "xiaomiCloud" | "homeAssistant";

export type ConnectionStatus =
  | { readonly kind: "connected" }
  | { readonly kind: "connecting" }
  | { readonly kind: "disconnected"; readonly reason: string };

export interface LightSpec {
  readonly kind: "light";
  readonly entityId: string;
  isOn: boolean;
  brightnessPercent: number | null;
  colorTempKelvin: number | null;
  readonly minKelvin: number;
  readonly maxKelvin: number;
  currentEffect: string | null;
  readonly availableEffects: readonly string[];
  /** Absent means the control is writable. False follows a read-only MIoT property (#154). */
  readonly powerWritable?: boolean;
  readonly brightnessWritable?: boolean;
  readonly colorWritable?: boolean;
}

export interface ClimateSpec {
  readonly kind: "climate";
  readonly entityId: string;
  isOn: boolean;
  hvacMode: string;
  readonly availableModes: readonly string[];
  targetTemperature: number;
  readonly currentTemperature: number | null;
  readonly minTemp: number;
  readonly maxTemp: number;
  readonly step: number;
  fanMode: string | null;
  readonly availableFanModes: readonly string[];
  /** Absent means the control is writable. False follows a read-only MIoT property (#154). */
  readonly powerWritable?: boolean;
  readonly temperatureWritable?: boolean;
}

export interface FanSpec {
  readonly kind: "fan";
  readonly entityId: string;
  isOn: boolean;
  percentage: number | null;
  presetMode: string | null;
  readonly availablePresetModes: readonly string[];
  isOscillating: boolean | null;
  readonly pm25Density?: number | null;
  readonly airQuality?: string | null;
  readonly temperature?: number | null;
  readonly humidity?: number | null;
  readonly filterLifePercent?: number | null;
  /** Absent means the control is writable. False follows a read-only MIoT property (#154). */
  readonly powerWritable?: boolean;
  readonly speedWritable?: boolean;
  readonly swingWritable?: boolean;
}

export interface PlugSpec {
  readonly kind: "plug";
  readonly switchEntityId: string;
  isOn: boolean;
  readonly powerWatts: number | null;
  readonly energyKwh?: number | null;
  readonly childLockEntityId: string | null;
  isChildLocked: boolean;
  readonly powerWritable?: boolean;
  /** False for a mains breaker (#265): it meters the downstream plugs, so the total would double count. */
  readonly countsInTotalPower?: boolean;
}

export interface FridgeSpec {
  readonly kind: "fridge";
  readonly modeEntityId: string | null;
  currentMode: string;
  readonly availableModes: readonly string[];
  readonly fridgeCurrentTemp: number | null;
  fridgeTargetTemp: number;
  readonly fridgeTargetEntityId: string | null;
  readonly freezerCurrentTemp: number | null;
  freezerTargetTemp: number;
  readonly freezerTargetEntityId: string | null;
}

export interface VacuumSpec {
  readonly kind: "vacuum";
  readonly entityId: string;
  status: string;
  readonly battery: number | null;
  readonly modeEntityId: string | null;
  currentMode: string | null;
  readonly availableModes: readonly string[];
  readonly actionsWritable?: boolean;
}

export interface CameraSpec {
  readonly kind: "camera";
  readonly entityId: string;
  isOn: boolean;
  nightShot: string | null;
  readonly availableNightShots: readonly string[];
  readonly motionDetectionEntityId: string | null;
  isMotionDetectionOn: boolean | null;
  readonly streamEntityId: string | null;
  /** HA-only still frame URL; always absent on the MIoT backend (model parity with macOS). */
  readonly snapshotPath?: string | null;
  readonly powerWritable?: boolean;
}

export interface SensorSpec {
  readonly kind: "sensor";
  readonly temperature: number | null;
  readonly humidity: number | null;
  readonly battery: number | null;
  readonly statusText?: string | null;
}

export interface CoverSpec {
  readonly kind: "cover";
  readonly entityId: string;
  readonly subtype: "curtain" | "window-opener" | "garage-door" | "clothes-airer";
  positionPercent: number | null;
  readonly positionWritable: boolean;
  readonly openDescription: string | null;
  readonly closeDescription: string | null;
  readonly pauseDescription: string | null;
  readonly batteryPercent: number | null;
  readonly faultText: string | null;
  motion: "stopped" | "opening" | "closing";
}

/** Media card for a MIoT `speaker` service; TTS and play-music actions stay macOS-only. */
export interface SpeakerSpec {
  readonly kind: "speaker";
  readonly mediaEntityId: string;
  state: "idle" | "playing" | "paused";
  volumePercent: number;
  isMuted: boolean;
  readonly muteSwitchEntityId: string | null;
  readonly sleepModeEntityId: string | null;
  isSleepMode: boolean;
  readonly volumeWritable: boolean;
}

export type ApplianceActionKind = "start" | "pause" | "stop";

/** One no-argument appliance action addressed on the MIoT service that declares it (#300). */
export interface ApplianceAction {
  readonly kind: ApplianceActionKind;
  readonly entityId: string;
}

/** A hood's or bath heater's light service, merged into the appliance card as a secondary switch (#4). */
export interface ApplianceLight {
  readonly entityId: string;
  isOn: boolean;
  readonly writable: boolean;
}

/** A hood's exhaust speed (`fan-level`), merged into the appliance card as a second select (#4). */
export interface ApplianceFanLevel {
  readonly entityId: string;
  current: string | null;
  readonly options: readonly string[];
  readonly writable: boolean;
}

/**
 * The shared appliance card (GLOSSARY `ApplianceDevice`): water heater, bath heater, hood, washer,
 * dryer and dishwasher. Every control is optional and driven by what the spec publishes.
 */
export interface ApplianceSpec {
  readonly kind: "appliance";
  readonly entityId: string;
  isOn: boolean;
  readonly powerPresent: boolean;
  readonly powerWritable: boolean;
  currentMode: string | null;
  readonly availableModes: readonly string[];
  readonly modeWritable: boolean;
  targetTemperature: number | null;
  readonly currentTemperature: number | null;
  readonly minTemp: number;
  readonly maxTemp: number;
  readonly step: number;
  readonly temperatureWritable: boolean;
  readonly statusText: string | null;
  readonly faultText: string | null;
  readonly remainingMinutes: number | null;
  readonly startAction: ApplianceAction | null;
  readonly pauseAction: ApplianceAction | null;
  readonly stopAction: ApplianceAction | null;
  /** A companion service may own a control while `entityId` stays the main function's service. */
  readonly powerEntityId: string;
  readonly modeEntityId: string;
  readonly temperatureEntityId: string;
  readonly light: ApplianceLight | null;
  readonly fanLevel: ApplianceFanLevel | null;
  readonly currentHumidity: number | null;
  readonly symbolName: string | null;
  /** The current temperature is the room's, not the appliance's own tank or cavity. */
  readonly roomReading: boolean;
  /** The card can start a heater; high-risk confirmation applies to power-on and warmer targets. */
  readonly heats: boolean;
}

export type FacetKind = "toggle" | "select" | "slider" | "reading" | "button";

export interface FacetOption {
  readonly value: number;
  readonly label: string;
}

export interface FacetRange {
  readonly min: number;
  readonly max: number;
  readonly step: number;
}

/** Where a facet write goes: a MIoT property with the new value, or a no-argument action. */
export type FacetTarget =
  | { readonly kind: "property"; readonly entityId: string; readonly piid: number }
  | { readonly kind: "action"; readonly entityId: string };

/** One row under 更多设置 (#5): a generic facet plus the value the last snapshot carried. */
export interface FacetControl {
  /** Stable facet id inside one device (`siid.piid` for properties, `siid.aAiid` for actions). */
  readonly id: string;
  readonly kind: FacetKind;
  /** MIoT short name, kept for localization and for tests. */
  readonly name: string;
  /** Spec description; often Chinese or English depending on the vendor, so the UI may override. */
  readonly title: string | null;
  /** Service short name, used to tell same-named facets apart. */
  readonly serviceName: string;
  readonly unit: string | null;
  /** Display scale from a spec comment (`0.01平方米`); readings multiply the raw value by it. */
  readonly scale: number | null;
  value: unknown;
  readonly options: readonly FacetOption[];
  readonly range: FacetRange | null;
  readonly isWritable: boolean;
  /** False when the facet writes outside the low-risk whitelist in `deviceProfiles.json`. */
  readonly writesByDefault: boolean;
  readonly isHighValue: boolean;
  readonly target: FacetTarget | null;
}

/**
 * The fallback card for a controllable MIoT device whose type has no dedicated builder (#6).
 * It keeps the few readings the card can show and one low-risk main switch; everything else
 * lives in the 更多设置 drawer.
 */
export interface GenericSpec {
  readonly kind: "generic";
  /** MIoT device type short name, e.g. `fish-tank`. */
  readonly deviceType: string;
  /** Device-type label used while there is no reading to show. */
  readonly typeTitle: string;
  isOn: boolean;
  /** A low-risk writable `on` facet; null when the card has no safe main switch. */
  readonly powerControl: FacetControl | null;
  /** No facet accepts any write, so the card is a status surface and says 只读. */
  readonly isReadOnly: boolean;
  /** At most two readings on the card, family preferences before high-value facets. */
  readonly readouts: readonly string[];
  readonly alerts?: readonly string[];
  readonly symbolName: string | null;
}

export type DeviceKind =
  | LightSpec
  | ClimateSpec
  | FanSpec
  | PlugSpec
  | FridgeSpec
  | VacuumSpec
  | CameraSpec
  | SensorSpec
  | CoverSpec
  | SpeakerSpec
  | ApplianceSpec
  | GenericSpec;
export interface SmartDevice {
  readonly id: string;
  readonly name: string;
  readonly roomId: string;
  readonly roomName: string;
  readonly model: string;
  readonly isOnline: boolean;
  kind: DeviceKind;
  readonly isSharedHome?: boolean;
}

export interface RoomCategory {
  readonly id: string;
  readonly name: string;
  readonly deviceCount: number;
}

export interface EnvironmentReading {
  readonly id: string;
  readonly label: string;
  readonly roomName: string;
  readonly temperature: number | null;
  readonly humidity: number | null;
  readonly battery: number | null;
}

export interface HomeScene {
  readonly id: string;
  readonly name: string;
  readonly entityId: string;
}

export interface SynthesizedHome {
  readonly devices: SmartDevice[];
  readonly rooms: RoomCategory[];
  readonly environmentReadings: EnvironmentReading[];
  readonly scenes: HomeScene[];
  /** Device id → 更多设置 rows (#5). Absent means the card has no auxiliary controls. */
  readonly deviceFacets?: Readonly<Record<string, readonly FacetControl[]>>;
}

export type VacuumAction = "start" | "pause" | "returnToBase";

export type DeviceCommand =
  | { readonly kind: "setLightPower"; readonly entityId: string; readonly isOn: boolean }
  | { readonly kind: "setLightBrightness"; readonly entityId: string; readonly percent: number }
  | { readonly kind: "setLightColorTemp"; readonly entityId: string; readonly kelvin: number }
  | { readonly kind: "setLightEffect"; readonly entityId: string; readonly effect: string }
  | { readonly kind: "setClimatePower"; readonly entityId: string; readonly isOn: boolean; readonly resumeMode: string }
  | { readonly kind: "setClimateMode"; readonly entityId: string; readonly hvacMode: string }
  | { readonly kind: "setClimateTemperature"; readonly entityId: string; readonly temperature: number }
  | { readonly kind: "setClimateFanMode"; readonly entityId: string; readonly fanMode: string }
  | { readonly kind: "setFanPower"; readonly entityId: string; readonly isOn: boolean }
  | { readonly kind: "setFanPercentage"; readonly entityId: string; readonly percent: number }
  | { readonly kind: "setFanPresetMode"; readonly entityId: string; readonly presetMode: string }
  | { readonly kind: "setFanOscillating"; readonly entityId: string; readonly oscillating: boolean }
  | { readonly kind: "setSwitch"; readonly entityId: string; readonly isOn: boolean }
  | { readonly kind: "setSelectOption"; readonly entityId: string; readonly option: string }
  | { readonly kind: "setNumberValue"; readonly entityId: string; readonly value: number }
  /** `level` is 0…1, scaled onto the volume property's range (macOS `mediaSetVolume`). */
  | { readonly kind: "mediaSetVolume"; readonly entityId: string; readonly level: number }
  | { readonly kind: "vacuumAction"; readonly entityId: string; readonly action: VacuumAction }
  /** A no-argument MIoT action: an appliance start/pause/stop or a 更多设置 button (#300, #5). */
  | { readonly kind: "pressButton"; readonly entityId: string }
  /** A generic 更多设置 write (#5); `entityId` is the service address and `piid` its property. */
  | { readonly kind: "setMiotProperty"; readonly entityId: string; readonly piid: number; readonly value: unknown }
  | { readonly kind: "activateScene"; readonly entityId: string };

export type IntentOperation =
  | { readonly kind: "power"; readonly isOn: boolean }
  | { readonly kind: "brightness"; readonly percent: number }
  | { readonly kind: "brightnessStep"; readonly delta: number }
  | { readonly kind: "colorTemperature"; readonly kelvin: number }
  | { readonly kind: "lightEffect"; readonly effect: string }
  | { readonly kind: "hvacMode"; readonly mode: string }
  | { readonly kind: "targetTemperature"; readonly celsius: number }
  | { readonly kind: "temperatureStep"; readonly delta: number }
  | { readonly kind: "fanSpeed"; readonly speed: string }
  | { readonly kind: "oscillation"; readonly enabled: boolean }
  | { readonly kind: "activateScene" };

export interface DeviceIntent {
  readonly targetId: string;
  readonly operation: IntentOperation;
}

export interface AssistantSuggestion {
  readonly id: string;
  readonly title: string;
  readonly confidencePercent: number | null;
  readonly intents: readonly DeviceIntent[];
}

export interface IntentPlan {
  readonly intents: readonly DeviceIntent[];
  readonly reply: string;
  readonly source: "local" | "model";
  readonly suggestions: readonly AssistantSuggestion[];
}

export interface ScheduledCommand {
  readonly id: string;
  readonly command: string;
  readonly delaySeconds: number;
  readonly fireTimestampMs: number;
  readonly reply: string;
  /** Resolved when the timer was set, so 「10 分钟后关灯」 keeps the devices chosen then (#273). */
  readonly intents?: readonly DeviceIntent[];
}

/** Plugs often power a router, fridge or NAS, and 全关 should not send a cleaning vacuum home (#23). */
export function deviceCanTogglePower(device: SmartDevice): boolean {
  const kind = device.kind;
  switch (kind.kind) {
    case "light":
    case "climate":
    case "fan":
    case "plug":
    case "camera":
      return kind.powerWritable !== false;
    case "vacuum":
      return kind.actionsWritable !== false;
    case "cover":
      return kind.openDescription !== null && kind.closeDescription !== null;
    case "appliance":
      return kind.powerPresent && kind.powerWritable;
    case "generic":
      return kind.powerControl !== null;
    case "speaker":
    case "fridge":
    case "sensor":
      return false;
    default: {
      const _exhaustive: never = kind;
      return _exhaustive;
    }
  }
}

export function deviceAcceptsControlWrites(device: SmartDevice): boolean {
  const kind = device.kind;
  switch (kind.kind) {
    case "light":
      return (
        kind.powerWritable !== false ||
        kind.brightnessWritable !== false ||
        kind.colorWritable !== false ||
        kind.availableEffects.length > 0
      );
    case "climate":
      return (
        kind.powerWritable !== false ||
        kind.temperatureWritable !== false ||
        kind.availableModes.length > 0 ||
        kind.availableFanModes.length > 0
      );
    case "fan":
      return (
        kind.powerWritable !== false ||
        (kind.speedWritable !== false && kind.percentage !== null) ||
        (kind.swingWritable !== false && kind.isOscillating !== null) ||
        kind.availablePresetModes.length > 0
      );
    case "plug":
      return kind.powerWritable !== false || kind.childLockEntityId !== null;
    case "camera":
      return kind.powerWritable !== false || kind.availableNightShots.length > 0 || kind.motionDetectionEntityId !== null;
    case "vacuum":
      return kind.actionsWritable !== false;
    case "cover":
      return (kind.openDescription !== null && kind.closeDescription !== null) || kind.positionWritable;
    case "fridge":
      return kind.modeEntityId !== null || kind.fridgeTargetEntityId !== null || kind.freezerTargetEntityId !== null;
    case "speaker":
      return kind.volumeWritable || kind.muteSwitchEntityId !== null || kind.sleepModeEntityId !== null;
    case "appliance":
      return (
        kind.powerWritable ||
        kind.modeWritable ||
        kind.temperatureWritable ||
        kind.fanLevel?.writable === true ||
        kind.light?.writable === true ||
        kind.startAction !== null ||
        kind.pauseAction !== null ||
        kind.stopAction !== null
      );
    case "generic":
      return !kind.isReadOnly;
    case "sensor":
      return false;
    default: {
      const _exhaustive: never = kind;
      return _exhaustive;
    }
  }
}

export function joinsAllOffByDefault(device: SmartDevice): boolean {
  return (
    device.kind.kind !== "plug" &&
    device.kind.kind !== "fridge" &&
    device.kind.kind !== "vacuum" &&
    device.kind.kind !== "camera" &&
    device.kind.kind !== "cover" &&
    device.kind.kind !== "appliance" &&
    device.kind.kind !== "generic"
  );
}

export function joinsAllOff(device: SmartDevice, overrides: Readonly<Record<string, boolean>>): boolean {
  return overrides[device.id] ?? joinsAllOffByDefault(device);
}

/** What 全关 turns off: the devices on screen that are on and take part in 全关. */
export function allOffTargets(
  visible: readonly SmartDevice[],
  overrides: Readonly<Record<string, boolean>>
): SmartDevice[] {
  return visible.filter((d) => isDeviceActiveOn(d) && joinsAllOff(d, overrides) && deviceCanTogglePower(d));
}

/** A timer that comes due more than this late (the PC slept, the app was frozen) waits for the user (#22). */
export const LATE_TIMER_GRACE_MS = 60_000;

/** Splits timers into ones to run now, ones that were missed, and ones still counting down. */
export function partitionDueTimers(
  items: readonly ScheduledCommand[],
  nowMs: number,
  graceMs = LATE_TIMER_GRACE_MS
): { run: ScheduledCommand[]; missed: ScheduledCommand[]; remaining: ScheduledCommand[] } {
  const run: ScheduledCommand[] = [];
  const missed: ScheduledCommand[] = [];
  const remaining: ScheduledCommand[] = [];
  for (const item of items) {
    if (item.fireTimestampMs > nowMs) remaining.push(item);
    else if (nowMs - item.fireTimestampMs > graceMs) missed.push(item);
    else run.push(item);
  }
  return { run, missed, remaining };
}

/** Xiaomi cloud refresh while the panel is open (#26). Matches macOS PollingPolicy.panelOpenInterval. */
export const CLOUD_REFRESH_VISIBLE_MS = 6_000;
export const CLOUD_REFRESH_HIDDEN_MS = 60_000;

/** True when the last cloud refresh is older than the interval for the panel's visibility. */
export function cloudRefreshDue(nowMs: number, lastRefreshMs: number | null, panelVisible: boolean): boolean {
  if (lastRefreshMs === null) return true;
  return nowMs - lastRefreshMs >= (panelVisible ? CLOUD_REFRESH_VISIBLE_MS : CLOUD_REFRESH_HIDDEN_MS);
}

export function isDeviceActiveOn(device: SmartDevice): boolean {
  switch (device.kind.kind) {
    case "light":
    case "climate":
    case "fan":
    case "plug":
    case "camera":
      return device.kind.isOn;
    case "vacuum":
      return device.kind.status === "cleaning";
    case "cover":
      return (device.kind.positionPercent ?? 0) > 0;
    case "speaker":
      return device.kind.state === "playing";
    case "appliance":
    case "generic":
      return device.kind.isOn;
    case "fridge":
    case "sensor":
      return false;
    default: {
      const _exhaustive: never = device.kind;
      return _exhaustive;
    }
  }
}

/** GLOSSARY ActiveDevice: an open cover is not switched on, so it stays out of counts and summaries. */
export function isActiveDevice(device: SmartDevice): boolean {
  return device.kind.kind !== "cover" && isDeviceActiveOn(device);
}

/** Dedicated sensors sort ahead of air purifiers and AC return-air thermistors (#103). */
export function environmentReadingPriority(kind: DeviceKind): number {
  switch (kind.kind) {
    case "sensor":
      return 0;
    case "fan":
      return 1;
    case "climate":
      return 2;
    default:
      return 3;
  }
}

/**
 * Ambient temperature / humidity reading exposed by this device for the status strip and menu bar (#103).
 * Offline devices or devices without a live reading are excluded.
 */
export function deviceEnvironmentReading(device: SmartDevice): EnvironmentReading | null {
  if (!device.isOnline) return null;
  switch (device.kind.kind) {
    case "sensor": {
      if (device.kind.temperature === null && device.kind.humidity === null) return null;
      const stripped = device.name.replace("温湿度计", "").replace(t("温湿度计"), "").trim();
      return {
        id: device.id,
        label: stripped || device.roomName,
        roomName: device.roomName,
        temperature: device.kind.temperature,
        humidity: device.kind.humidity,
        battery: device.kind.battery,
      };
    }
    case "fan": {
      const temperature = device.kind.temperature ?? null;
      const humidity = device.kind.humidity ?? null;
      if (temperature === null && humidity === null) return null;
      const trimmed = device.name.trim();
      return {
        id: device.id,
        label: trimmed || device.roomName,
        roomName: device.roomName,
        temperature,
        humidity,
        battery: null,
      };
    }
    case "climate": {
      if (device.kind.currentTemperature === null) return null;
      const trimmed = device.name.trim();
      return {
        id: device.id,
        label: trimmed || device.roomName,
        roomName: device.roomName,
        temperature: device.kind.currentTemperature,
        humidity: null,
        battery: null,
      };
    }
    case "appliance": {
      const { roomReading, currentTemperature, currentHumidity } = device.kind;
      if (!roomReading || (currentTemperature === null && currentHumidity === null)) return null;
      const trimmed = device.name.trim();
      return {
        id: device.id,
        label: trimmed || device.roomName,
        roomName: device.roomName,
        temperature: currentTemperature,
        humidity: currentHumidity,
        battery: null,
      };
    }
    default:
      return null;
  }
}

export function localizeNightShot(mode: string): string {
  switch (mode.trim().toLowerCase()) {
    case "off":
    case "close":
      return t("夜视关");
    case "on":
    case "open":
      return t("夜视开");
    case "auto":
    case "automatic":
      return t("自动夜视");
    case "auto full color":
    case "smart full color":
    case "auto smart full color":
      return t("智能全彩");
    case "on full color":
    case "full color":
      return t("全彩夜视");
    default:
      return mode;
  }
}

export function localizeFanPreset(mode: string): string {
  switch (mode.trim().toLowerCase()) {
    case "auto":
    case "automatic":
      return t("自动");
    case "sleep":
      return t("睡眠");
    case "smart":
      return t("智能");
    case "clothes drying":
      return t("干衣");
    case "favorite":
    case "favourite":
      return t("最爱");
    case "manual":
    case "none":
      return t("手动");
    case "silent":
      return t("静音");
    default:
      return mode;
  }
}

export function localizeFridgeMode(mode: string): string {
  switch (mode.trim().toLowerCase()) {
    case "none":
    case "manual":
      return t("手动");
    case "smart":
    case "auto":
      return t("智能");
    case "holiday":
      return t("假日");
    case "quick cooling":
    case "super cool":
      return t("速冷");
    case "quick frozen":
    case "super freeze":
      return t("速冻");
    default:
      return mode;
  }
}

/** One air-quality grade; mirrors macOS `AirQualityLevel`, pinned by Tests/Fixtures/air_quality_golden.json. */
export type AirQualityLevel = "excellent" | "good" | "moderate" | "poor" | "heavy" | "hazardous";

const AIR_QUALITY_LABEL: Record<AirQualityLevel, string> = {
  excellent: "优",
  good: "良",
  moderate: "轻度污染",
  poor: "中度污染",
  heavy: "重度污染",
  hazardous: "严重污染",
};

function airQualityFromRaw(raw: string): AirQualityLevel | null {
  switch (raw.trim().toLowerCase()) {
    case "excellent":
    case "空气优":
    case "优":
      return "excellent";
    case "good":
    case "空气良":
    case "良":
      return "good";
    case "moderate":
    case "轻度污染":
      return "moderate";
    case "poor":
    case "中度污染":
      return "poor";
    case "heavy pollution":
    case "heavy":
    case "重度污染":
      return "heavy";
    case "hazardous":
    case "严重污染":
      return "hazardous";
    default:
      return null;
  }
}

function airQualityFromPm25(pm25: number): AirQualityLevel {
  if (pm25 <= 35) return "excellent";
  if (pm25 <= 75) return "good";
  if (pm25 <= 115) return "moderate";
  if (pm25 <= 150) return "poor";
  if (pm25 <= 250) return "heavy";
  return "hazardous";
}

/** A known raw grade wins over PM2.5; an unknown raw grade has no level. */
export function airQualityLevel(raw: string | null | undefined, pm25?: number | null): AirQualityLevel | null {
  if (raw && raw.trim() !== "") return airQualityFromRaw(raw);
  if (pm25 === undefined || pm25 === null) return null;
  return airQualityFromPm25(pm25);
}

export function localizeAirQuality(raw: string | null | undefined, pm25?: number | null): string | null {
  if (raw && raw.trim() !== "") {
    const level = airQualityFromRaw(raw);
    return level ? t(AIR_QUALITY_LABEL[level]) : raw;
  }
  if (pm25 === undefined || pm25 === null) return null;
  return t(AIR_QUALITY_LABEL[airQualityFromPm25(pm25)]);
}

export type DeviceReadout = "live" | "waiting" | "possiblyStale";

export function deviceReadout(isWaiting: boolean, valuesAreStale: boolean): DeviceReadout {
  if (isWaiting) return "waiting";
  if (valuesAreStale) return "possiblyStale";
  return "live";
}

/** MIoT `on` is a bool on some devices and 0/1 on others. Anything else is not a power reading. */
export function readMiotPower(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (typeof value === "number" && (value === 0 || value === 1)) return value === 1;
  return null;
}

export function deviceStatusSummary(device: SmartDevice): string {
  if (!device.isOnline) return t("离线");
  const k = device.kind;
  switch (k.kind) {
    case "light": {
      if (!k.isOn) return t("已关闭");
      const parts: string[] = [t("已开启")];
      if (k.currentEffect) {
        const effect = k.currentEffect.toLowerCase();
        parts.push(
          effect === "night" ? t("月光") : effect === "day" ? t("日光") : k.currentEffect
        );
      }
      if (k.brightnessPercent !== null) parts.push(`${k.brightnessPercent}%`);
      if (k.colorTempKelvin !== null) parts.push(`${k.colorTempKelvin}K`);
      return parts.join(" · ");
    }
    case "climate": {
      const modeMap: Record<string, string> = {
        cool: t("制冷"),
        heat: t("制热"),
        dry: t("除湿"),
        fan_only: t("送风"),
        auto: t("自动"),
        off: t("已关闭"),
      };
      if (!k.isOn || k.hvacMode === "off") {
        return k.currentTemperature !== null
          ? t("已关闭 · 室温 {temp}", { temp: formatTemperature(k.currentTemperature) })
          : t("已关闭");
      }
      const modeLabel = modeMap[k.hvacMode] ?? k.hvacMode;
      const roomPart = k.currentTemperature !== null
        ? ` · ${t("室温 {temp}", { temp: formatTemperature(k.currentTemperature) })}`
        : "";
      return `${modeLabel} ${formatTemperature(k.targetTemperature)}${roomPart}`;
    }
    case "fan": {
      const envParts: string[] = [];
      if (k.pm25Density !== undefined && k.pm25Density !== null) {
        const aq = localizeAirQuality(k.airQuality, k.pm25Density);
        envParts.push(aq ? `PM2.5 ${k.pm25Density} ${aq}` : `PM2.5 ${k.pm25Density}`);
      } else if (k.airQuality) {
        const aq = localizeAirQuality(k.airQuality, null);
        if (aq) envParts.push(aq);
      }
      if (k.temperature !== undefined && k.temperature !== null && k.humidity !== undefined && k.humidity !== null) {
        envParts.push(`${formatTemperature(k.temperature, undefined, 0)} ${Math.round(k.humidity)}%`);
      } else if (k.temperature !== undefined && k.temperature !== null) {
        envParts.push(formatTemperature(k.temperature, undefined, 1));
      } else if (k.humidity !== undefined && k.humidity !== null) {
        envParts.push(`${Math.round(k.humidity)}%`);
      }
      if (!k.isOn) {
        return envParts.length > 0 ? [t("已关闭"), ...envParts].join(" · ") : t("已关闭");
      }
      const parts: string[] = [t("运行中")];
      if (k.presetMode) parts.push(localizeFanPreset(k.presetMode));
      if (k.percentage !== null) parts.push(`${k.percentage}%`);
      if (k.isOscillating) parts.push(t("摇头"));
      parts.push(...envParts);
      return parts.join(" · ");
    }
    case "plug": {
      if (!k.isOn) return t("已断电");
      return k.powerWatts !== null ? t("供电中 · {watts}W", { watts: k.powerWatts.toFixed(1) }) : t("供电中");
    }
    case "fridge": {
      if (k.fridgeCurrentTemp === null && k.freezerCurrentTemp === null) {
        const cStr = `${t("冷藏")} ${formatTemperature(k.fridgeTargetTemp)}`;
        const fStr = `${t("冷冻")} ${formatTemperature(k.freezerTargetTemp)}`;
        const mStr = localizeFridgeMode(k.currentMode);
        return mStr ? `${mStr} · ${cStr} · ${fStr}` : `${cStr} · ${fStr}`;
      }
      const cStr =
        k.fridgeCurrentTemp !== null
          ? `${t("冷藏")} ${formatTemperature(k.fridgeCurrentTemp)}`
          : t("冷藏 --");
      const fStr =
        k.freezerCurrentTemp !== null
          ? `${t("冷冻")} ${formatTemperature(k.freezerCurrentTemp)}`
          : t("冷冻 --");
      return `${cStr} · ${fStr}`;
    }
    case "vacuum": {
      const map: Record<string, string> = {
        cleaning: t("清扫中"),
        paused: t("已暂停"),
        returning: t("回充中"),
        docked: t("充电座待命"),
        idle: t("待命"),
      };
      const base = map[k.status] ?? k.status;
      return k.battery !== null ? `${base} · ${k.battery}%` : base;
    }
    case "camera": {
      if (!k.isOn) return t("休眠中 · 点击唤醒");
      const parts: string[] = [t("监控中")];
      if (k.nightShot) parts.push(localizeNightShot(k.nightShot));
      if (k.isMotionDetectionOn) parts.push(t("侦测开"));
      parts.push(t("点击看画面"));
      return parts.join(" · ");
    }
    case "sensor": {
      const parts: string[] = [];
      if (k.temperature !== null) parts.push(formatTemperature(k.temperature, undefined, 1));
      if (k.humidity !== null) parts.push(`${Math.round(k.humidity)}%`);
      if (parts.length > 0) return parts.join(" · ");
      if (k.statusText) {
        const label = t(k.statusText);
        return k.battery !== null ? `${label} · ${t("电量")} ${k.battery}%` : label;
      }
      return t("在线");
    }
    case "cover": {
      if (k.faultText) return k.faultText;
      if (k.positionPercent === null) return t("可控制");
      if (k.positionPercent <= 0) return t("已关上");
      if (k.positionPercent >= 100) return t("已打开");
      return t("开 {percent}%", { percent: k.positionPercent });
    }
    case "speaker": {
      const play = k.state === "playing" ? t("播放中") : t("待机");
      const volume = k.isMuted ? t("已静音") : t("音量 {percent}%", { percent: k.volumePercent });
      return `${play} · ${volume}`;
    }
    case "appliance": {
      const parts: string[] = [];
      if (k.currentTemperature !== null) parts.push(formatTemperature(k.currentTemperature, undefined, 1));
      if (k.targetTemperature !== null) {
        parts.push(t("目标 {temp}", { temp: formatTemperature(k.targetTemperature) }));
      }
      if (k.currentMode) parts.push(localizeApplianceMode(k.currentMode));
      if (k.fanLevel?.current) parts.push(localizeApplianceFanLevel(k.fanLevel.current));
      if (k.statusText) parts.push(k.statusText);
      if (k.remainingMinutes !== null) parts.push(t("剩余 {minutes} 分", { minutes: k.remainingMinutes }));
      if (k.faultText) parts.push(t("故障 {fault}", { fault: k.faultText }));
      if (parts.length > 0) return parts.join(" · ");
      return k.isOn ? t("运行中") : t("已关闭");
    }
    case "generic": {
      const readings = [...(k.alerts ?? []), ...k.readouts].join(" · ");
      if (readings) return readings;
      if (k.isReadOnly) return t("只读 · {type}", { type: k.typeTitle });
      return k.typeTitle;
    }
    default: {
      const _exhaustive: never = k;
      return _exhaustive;
    }
  }
}

/** An appliance programme word; a word this table does not know is shown as the spec wrote it. */
export function localizeApplianceMode(mode: string): string {
  switch (mode.trim().toLowerCase()) {
    case "standard":
    case "normal":
    case "标准":
      return t("标准");
    case "energy1":
    case "eco":
    case "节能":
      return t("节能①");
    case "energy2":
      return t("节能②");
    case "quick":
    case "fast":
    case "快速":
      return t("快速");
    case "electricheating":
    case "electric heating":
    case "电加热":
      return t("电加热");
    case "auto":
    case "automatic":
    case "自动":
      return t("自动");
    case "sleep":
    case "night":
    case "睡眠":
      return t("睡眠");
    case "strong":
    case "high":
    case "强力":
    case "强":
      return t("强力");
    case "baby":
    case "婴儿":
      return t("婴儿");
    case "manual":
    case "手动":
      return t("手动");
    case "off":
    case "关闭":
      return t("关闭");
    case "idle":
    case "待机":
      return t("待机");
    case "working":
    case "工作中":
      return t("工作中");
    case "shower":
    case "bath":
    case "洗浴":
    case "沐浴":
      return t("洗浴");
    case "dry":
    case "dryer":
    case "干燥":
      return t("干燥");
    case "defog":
    case "除雾":
      return t("除雾");
    case "quick defog":
    case "快速除雾":
      return t("快速除雾");
    case "heat":
      return t("弱暖");
    case "quick heat":
      return t("强暖");
    case "ventilate":
    case "ventilation":
    case "换气":
      return t("换气");
    case "fan":
    case "blow":
    case "吹风":
      return t("吹风");
    case "cool":
    case "凉风":
      return t("凉风");
    case "deodorize":
    case "除味":
      return t("除味");
    case "other":
    case "其他":
      return t("其他");
    default:
      return mode;
  }
}

/** A hood's `fan-level` list uses `Level1…Level10` or `Low`/`High`/`Stir Fry`. */
export function localizeApplianceFanLevel(level: string): string {
  const trimmed = level.trim();
  switch (trimmed.toLowerCase()) {
    case "low":
    case "低速":
      return t("低速");
    case "mid":
    case "middle":
    case "中速":
      return t("中速");
    case "high":
    case "高速":
      return t("高速");
    case "stir fry":
    case "stir-fry":
    case "爆炒":
      return t("爆炒");
    default: {
      const match = /^level\s*([0-9]+)$/i.exec(trimmed);
      return match?.[1] ? t("{level}档", { level: match[1] }) : trimmed;
    }
  }
}

/**
 * Whether choosing this mode starts heating from standby (macOS `ApplianceSpec.modeStartsHeating`).
 * Idle/off/standby modes do not, so picking one from standby needs no confirmation (#4).
 */
export function applianceModeStartsHeating(mode: string): boolean {
  const normalized = mode.trim().toLowerCase();
  const idleWords = [
    "idle",
    "off",
    "standby",
    "close",
    "closed",
    "none",
    "fan",
    "ventilate",
    "ventilation",
    "blow",
    "cool",
    "吹风",
    "换气",
    "凉风",
    "待机",
    "关闭",
    "无",
  ];
  return !idleWords.some((word) => normalized.includes(word));
}
