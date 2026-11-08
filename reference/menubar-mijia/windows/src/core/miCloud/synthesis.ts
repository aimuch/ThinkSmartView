import { t } from "../i18n.js";
import {
  deviceEnvironmentReading,
  environmentReadingPriority,
  type ApplianceActionKind,
  type DeviceKind,
  type EnvironmentReading,
  type FacetControl,
  type GenericSpec,
  type RoomCategory,
  type SmartDevice,
  type SynthesizedHome,
} from "../models.js";
import {
  miotDid,
  propKeyString,
  specForRecord,
  type MiDeviceRecord,
  type MiRoom,
  type MiotPropKey,
} from "./client.js";
import {
  hvacModeFromDescription,
  inferFallbackSpec,
  isMainsBreaker,
  makeMiotActionAddress,
  makeMiotAddress,
  miotShortName,
  type MiotDeviceSpec,
  type MiotProperty,
  type MiotService,
  type MiotValueListItem,
} from "./spec.js";
import {
  DEVICE_PROFILES,
  buildMiotFacets,
  facetControls,
  isMiotAlertLabel,
  matchesMiotFacetPattern,
  miotFallbackAllows,
  profileOverride,
} from "./facets.js";
import { facetAlertText, facetValue, genericSymbolName, genericTypeTitle } from "../miotFacetText.js";
import { percentFromRaw } from "./percentScale.js";

export function exposesWrite(access: readonly string[] | undefined): boolean {
  return Boolean(access?.includes("write"));
}

export function readOnlyFlags(flags: Record<string, boolean>): Record<string, false> {
  const out: Record<string, false> = {};
  for (const [key, writable] of Object.entries(flags)) {
    if (!writable) out[key] = false;
  }
  return out;
}

/** A read-only property map: synthesis also runs to discover which keys it polls. */
const EMPTY_VALUES: ReadonlyMap<string, unknown> = new Map<string, unknown>();

export function miotBool(val: unknown): boolean | null {
  if (typeof val === "boolean") return val;
  if (typeof val === "number") return val !== 0;
  return null;
}

interface MiotBuildPlan {
  readonly id: string;
  readonly name: string;
  readonly spec: MiotDeviceSpec;
}

type CoverSubtype = "curtain" | "window-opener" | "garage-door" | "clothes-airer";
type CoverMotion = "stopped" | "opening" | "closing";

/** MIoT service short name → subtype, matching macOS `CoverSubtype(miotServiceShortName:)`. */
export function coverSubtype(serviceName: string): CoverSubtype {
  switch (serviceName) {
    case "curtain":
      return "curtain";
    case "airer":
      return "clothes-airer";
    default:
      // An unlisted cover service keeps the riskier class so a new cover never lands in a
      // safer one by omission.
      return "window-opener";
  }
}

/** An airer reports how far it has lowered (0 at the top); the card shows height (#299). */
function coverInvertsPosition(subtype: CoverSubtype): boolean {
  return subtype === "clothes-airer";
}

/** A position within two points of either end is parked on that limit (#298). */
function snapCoverPosition(percent: number): number {
  if (percent <= 2) return 0;
  if (percent >= 98) return 100;
  return percent;
}

/** One MIoT `status` value-list word, classified by its letters only (macOS `CoverStatusWord`). */
type CoverStatusWord = "opening" | "closing" | "open" | "closed" | "stopped";

function coverStatusWord(description: string | null | undefined): CoverStatusWord | null {
  const trimmed = (description ?? "").trim();
  switch (trimmed) {
    case "打开中":
    case "开启中":
      return "opening";
    case "关闭中":
      return "closing";
    case "已开":
    case "完全打开":
      return "open";
    case "已关":
    case "完全关闭":
      return "closed";
    case "停止":
    case "已停止":
    case "暂停":
    case "空闲":
      return "stopped";
    default:
      break;
  }
  const letters = Array.from(trimmed.toLowerCase())
    .filter((c) => c >= "a" && c <= "z")
    .join("");
  switch (letters) {
    case "opening":
    case "up":
    case "uping":
    case "rise":
    case "rising":
      return "opening";
    case "closing":
    case "down":
    case "dowm":
    case "falling":
    case "fallin":
    case "dropping":
    case "downing":
    case "lower":
      return "closing";
    case "open":
    case "opened":
    case "fullyopen":
    case "stopathighest":
    case "stopatupperlimit":
    case "stopupperlimit":
    case "upperlimitstop":
    case "ceiling":
      return "open";
    case "close":
    case "closed":
    case "closeover":
    case "stopatlowest":
    case "stoplowerlimit":
    case "lowerlimitstop":
    case "floor":
    case "lowerlimit":
      return "closed";
    case "stop":
    case "stopped":
    case "pause":
    case "paused":
    case "idle":
    case "calibration":
    case "calibrating":
    case "partialopen":
    case "partialclosed":
    case "manual":
      return "stopped";
    default:
      return null;
  }
}

/** A status value whose list names travel is the authoritative motion signal. */
function coverStatusDefinesTravel(status: MiotProperty | null): boolean {
  return (
    status?.valueList?.some((item) => {
      const word = coverStatusWord(item.description);
      return word === "opening" || word === "closing";
    }) ?? false
  );
}

/** The only motion signal for a spec without travel words; snap before comparing (#298). */
function inferredCoverMotion(current: number | null, target: number | null): CoverMotion {
  const snappedCurrent = current === null ? null : snapCoverPosition(current);
  const snappedTarget = target === null ? null : snapCoverPosition(target);
  if (snappedCurrent === null || snappedTarget === null || snappedCurrent === snappedTarget) return "stopped";
  return snappedTarget > snappedCurrent ? "opening" : "closing";
}

/**
 * A dedicated travel word (`Opening`, `Rising`, `Closing`, `Falling`) is authoritative. Bare
 * `Open`/`Close` are terminal end words in the released library, so they only become travel when
 * the status list has no dedicated travel word and the position still differs from the target.
 */
function coverMotion(
  statusDefinesTravel: boolean,
  word: CoverStatusWord | null,
  current: number | null,
  target: number | null,
): CoverMotion {
  if (word === "opening" || word === "closing") return word;
  if (word === "stopped") return "stopped";
  if (word === "open" || word === "closed") {
    return statusDefinesTravel ? "stopped" : inferredCoverMotion(current, target);
  }
  if (statusDefinesTravel) return "stopped";
  return inferredCoverMotion(current, target);
}

/**
 * The target is the card's position while the motor travels, so intermediate current-position
 * reports cannot pull the card back. At rest the real current position wins.
 */
function coverDisplayPosition(
  current: number | null,
  target: number | null,
  motion: CoverMotion,
): number | null {
  const snappedCurrent = current === null ? null : snapCoverPosition(current);
  const snappedTarget = target === null ? null : snapCoverPosition(target);
  const resting = snappedCurrent ?? snappedTarget;
  if (motion === "stopped") return resting;
  if (snappedCurrent === null || snappedTarget === null) return resting;
  return motion === "opening" ? Math.max(snappedCurrent, snappedTarget) : Math.min(snappedCurrent, snappedTarget);
}

function coverStatusFallbackPosition(word: CoverStatusWord | null): number | null {
  if (word === "open") return 100;
  if (word === "closed") return 0;
  return null;
}

/**
 * Exact motor value-list words only: a substring match could turn "Not Open" into an open
 * command. Airers and roller blinds say `Up` / `Down` (#299).
 */
function coverMotorDescription(
  items: readonly MiotValueListItem[],
  word: "open" | "close" | "pause",
): string | null {
  const aliases =
    word === "open"
      ? ["open", "打开", "up", "raise"]
      : word === "close"
        ? ["close", "关闭", "down", "lower"]
        : ["pause", "stop", "暂停"];
  return items.find((item) => aliases.includes(item.description.trim().toLowerCase()))?.description ?? null;
}

/** Some specs use `fault`, and some publish a healthy code of 1 named "No Fault". */
function faultDescription(code: number | null, description: string | null): string | null {
  if (code === null || code === 0) return null;
  if (description?.toLowerCase().includes("no fault")) return null;
  return description ?? String(code);
}

/** A track card takes its service description, localized like macOS `trackName`. */
function curtainTrackName(description: string | undefined, fallback: string): string {
  const value = (description ?? "").trim();
  switch (value.toLowerCase()) {
    case "drape curtain":
      return "布帘";
    case "lace curtain":
      return "纱帘";
    case "left curtain":
      return "左帘";
    case "right curtain":
      return "右帘";
    case "curtain":
      return "窗帘";
    case "":
      return fallback;
    default:
      return value;
  }
}

/** Services the shared appliance card reads by default, matching macOS `applianceServiceNames`. */
export const APPLIANCE_SERVICE_NAMES: readonly string[] = [
  "water-heater",
  "boiler",
  "washer",
  "dryer",
  "clothes-dryer",
  "cleanfly-dryer",
  "dishwasher",
];

/** Families whose `status` and `run-status` words belong to the shared activity vocabulary. */
const ACTIVITY_SERVICES = new Set(["washer", "dryer", "clothes-dryer", "cleanfly-dryer", "dishwasher"]);

/**
 * Whether a service can carry the generic tile's main switch. The device's own service is the
 * only candidate; light-like types may also use their `light` service. Companion services
 * (`indicator-light`, `night-light`, `ambient-light`, `screen`, `led`) never qualify (#6).
 */
function isMainPowerService(serviceName: string, deviceType: string): boolean {
  if (serviceName === deviceType) return true;
  if (deviceType.startsWith(`${serviceName}-`) || deviceType.endsWith(`-${serviceName}`)) return true;
  if (deviceType.includes("light") || deviceType.includes("lamp")) return serviceName === "light";
  return false;
}

/** The no-argument actions the shared appliance card can run, with the names each may use. */
const APPLIANCE_ACTION_ALIASES: Readonly<Record<ApplianceActionKind, readonly string[]>> = {
  start: ["start", "start-heating", "start-wash", "start-dry", "start-drying", "开始"],
  pause: ["pause", "暂停"],
  stop: ["stop", "stop-washing", "stop-dry", "stop-drying", "停止"],
};

interface ApplianceActivityWord {
  readonly label: string;
  readonly isStage: boolean;
  readonly isTerminal: boolean;
}

/**
 * One MIoT `status` or `run-status` word, classified by its letters only. Vendors spell the same
 * phase differently (`Rinsing` / `Rinsh`), so punctuation, spaces and digits are dropped (#3).
 */
function applianceActivity(description: string | null | undefined): ApplianceActivityWord | null {
  const trimmed = (description ?? "").trim();
  const word = (label: string, isStage: boolean, isTerminal: boolean): ApplianceActivityWord => ({
    label,
    isStage,
    isTerminal,
  });
  switch (trimmed) {
    case "待机":
    case "空闲":
    case "关机":
      return word(t("待机"), false, true);
    case "预约中":
      return word(t("预约中"), false, true);
    case "称重中":
      return word(t("称重中"), true, false);
    case "预洗中":
      return word(t("预洗中"), true, false);
    case "洗涤中":
    case "洗护中":
      return word(t("洗涤中"), true, false);
    case "漂洗中":
    case "漂洗":
      return word(t("漂洗中"), true, false);
    case "脱水中":
    case "脱水":
      return word(t("脱水中"), true, false);
    case "排水中":
    case "排水":
      return word(t("排水中"), true, false);
    case "浸泡中":
      return word(t("浸泡中"), true, false);
    case "烘干中":
      return word(t("烘干中"), true, false);
    case "护衣中":
      return word(t("护衣中"), true, false);
    case "除菌中":
      return word(t("除菌中"), true, false);
    case "保管中":
      return word(t("保管中"), false, true);
    case "运行中":
    case "工作中":
      return word(t("运行中"), false, false);
    case "已暂停":
    case "暂停中":
      return word(t("已暂停"), false, true);
    case "已完成":
    case "完成":
      return word(t("已完成"), false, true);
    case "中止中":
      return word(t("正在中止"), false, true);
    case "故障":
    case "报警":
      return word(t("故障"), false, true);
    default:
      break;
  }
  const letters = Array.from(trimmed.toLowerCase())
    .filter((c) => c >= "a" && c <= "z")
    .join("");
  if (!letters) return null;
  // A terminal word wins over the phase it names: `Washing Completed` is not washing.
  if (letters.includes("paus")) return word(t("已暂停"), false, true);
  if (letters.includes("fault") || letters.includes("error")) {
    // `No Faults` is the healthy code several specs publish, not a fault state.
    return letters.includes("nofault") ? null : word(t("故障"), false, true);
  }
  if (letters.includes("abort") || letters.includes("cancel")) return word(t("正在中止"), false, true);
  if (letters.includes("complete") || letters.includes("finish") || letters === "done") {
    return word(t("已完成"), false, true);
  }
  // Before the scheduled words: `Preservation` also contains `reserv`.
  if (letters.includes("preserv")) return word(t("保管中"), false, true);
  if (
    letters.includes("reserv") ||
    letters.includes("delay") ||
    letters.includes("appoint") ||
    letters.includes("schedul")
  ) {
    return word(t("预约中"), false, true);
  }
  if (["idle", "inactive", "off", "notrun", "ready", "standby", "shutdown", "standbymode"].includes(letters)) {
    return word(t("待机"), false, true);
  }
  if (
    letters.includes("anticrease") ||
    letters.includes("blow") ||
    letters.includes("shake") ||
    letters.includes("care")
  ) {
    return word(t("护衣中"), true, false);
  }
  if (letters.includes("prewash")) return word(t("预洗中"), true, false);
  // `Rinse`, `Rinsing`, and the released spelling `Rinsh`.
  if (letters.includes("rins")) return word(t("漂洗中"), true, false);
  if (letters.includes("drain")) return word(t("排水中"), true, false);
  if (letters.includes("spin")) return word(t("脱水中"), true, false);
  if (letters.includes("steril")) return word(t("除菌中"), true, false);
  if (letters.includes("weigh")) return word(t("称重中"), true, false);
  if (letters.includes("dry")) return word(t("烘干中"), true, false);
  if (letters.includes("soak")) return word(t("浸泡中"), true, false);
  if (letters.includes("wash")) return word(t("洗涤中"), true, false);
  if (["busy", "running", "working", "on", "run"].includes(letters)) return word(t("运行中"), false, false);
  return null;
}

/**
 * One activity line. The coarse `status` owns a terminal word, because a washer keeps naming the
 * phase it was paused in; while the machine works, a `run-status` phase is more specific (#3).
 * A word this vocabulary does not know is shown as the spec wrote it.
 */
function applianceActivityText(status: string | null, runStatus: string | null): string | null {
  const coarse = applianceActivity(status);
  if (coarse?.isTerminal) return coarse.label;
  const stage = applianceActivity(runStatus);
  if (stage?.isStage) return stage.label;
  if (coarse) return coarse.label;
  if (stage) return stage.label;
  return status ?? runStatus;
}

/** Shared appliance-card statuses: `Close` means standby and `Wash` means bathing (#300, #4). */
function applianceStatusDescription(description: string): string {
  switch (description.trim().toLowerCase()) {
    case "close":
    case "closed":
    case "off":
    case "shutdown":
      return t("待机");
    case "open":
    case "on":
      return t("已开启");
    case "wash":
    case "washing":
      return t("洗浴中");
    default:
      break;
  }
  const lower = description.trim().toLowerCase();
  if (lower.includes("idle") || lower.includes("standby") || lower.includes("off") || lower.includes("shutdown")) {
    return t("待机");
  }
  if (
    lower.includes("keep warm") ||
    lower.includes("keep-warm") ||
    lower.includes("warm") ||
    lower.includes("insulation")
  ) {
    return t("保温中");
  }
  if (
    lower.includes("cook") ||
    lower.includes("work") ||
    lower.includes("busy") ||
    lower.includes("run") ||
    lower.includes("boil") ||
    lower.includes("heat")
  ) {
    return t("运行中");
  }
  if (lower.includes("preheat")) return t("预热中");
  if (lower.includes("delayed") || lower.includes("delay-off") || lower.includes("延时")) return t("延时关机");
  if (lower.includes("delay") || lower.includes("appoint") || lower.includes("reservation")) return t("预约中");
  if (lower.includes("clean") || lower.includes("清洗")) return t("清洗中");
  if (lower.includes("paus")) return t("已暂停");
  if (lower.includes("finish") || lower.includes("complete") || lower.includes("cooked")) return t("已完成");
  if (lower.includes("error") || lower.includes("fault")) return t("故障");
  return description;
}

function applianceStatusText(service: string, status: string | null, runStatus: string | null): string | null {
  if (ACTIVITY_SERVICES.has(service)) return applianceActivityText(status, runStatus);
  return status === null ? null : applianceStatusDescription(status);
}

/** `left-time` is minutes in most specs; a few released models publish seconds or hours (#3). */
function remainingMinutes(ref: { readonly prop: MiotProperty; readonly val: unknown } | null): number | null {
  if (!ref || typeof ref.val !== "number" || !Number.isFinite(ref.val) || ref.val <= 0) return null;
  const unit = (ref.prop.unit ?? "").trim().toLowerCase();
  const minutes =
    unit === "seconds" || unit === "second" || unit === "sec" || unit === "s"
      ? Math.ceil(ref.val / 60)
      : unit === "hours" || unit === "hour" || unit === "h"
        ? ref.val * 60
        : Math.round(ref.val);
  return Number.isFinite(minutes) && minutes > 0 ? minutes : null;
}

export function hasScaleToken(comment: string, token: string): boolean {
  let idx = comment.indexOf(token);
  while (idx !== -1) {
    const prev = idx > 0 ? comment[idx - 1]! : "";
    if (!((prev >= "0" && prev <= "9") || prev === ".")) {
      return true;
    }
    idx = comment.indexOf(token, idx + token.length);
  }
  return false;
}

/** Explicit power metadata wins over magnitude, range-step, and misleading watt labels (#129). */
export function powerScale(prop: MiotProperty): number | null {
  const unit = (prop.unit ?? "").toLowerCase().replaceAll(" ", "");
  const comment = (prop.comment ?? "").toLowerCase().replaceAll(",", ".").replaceAll(" ", "");
  const scales: readonly (readonly [string, number])[] = [["0.001", 0.001], ["0.01", 0.01], ["0.1", 0.1]];
  for (const [token, scale] of scales) {
    const pattern = new RegExp("(?<![0-9.])" + token.replaceAll(".", "\\.") + "(?:watt|w)(?![a-z])");
    if (pattern.test(comment) || unit === token + "w" || unit === token + "watt") return scale;
  }
  switch (unit) {
    case "": case "none": case "w": case "watt": return 1;
    case "kw": case "kilowatt": return 1000;
    case "mw": case "milliwatt": return 0.001;
    default: return null;
  }
}

/**
 * Scale factor that converts a MIoT `power-consumption` reading into kWh (#62).
 * Returns `null` when an integer property has neither a recognized `unit` nor an explicit
 * scale in `comment` (e.g. "0.01kwh"), so ambiguous raw integers are never guessed.
 */
export function energyScale(prop: MiotProperty): number | null {
  const unit = (prop.unit ?? "")
    .trim()
    .toLowerCase()
    .replaceAll(",", ".")
    .replaceAll(" ", "");
  const comment = (prop.comment ?? "")
    .toLowerCase()
    .replaceAll(",", ".")
    .replaceAll(" ", "");
  if (unit === "wh" || unit === "watt-hour" || unit === "0.001kwh" || hasScaleToken(comment, "0.001kwh")) {
    return 0.001;
  }
  if (unit === "0.01kwh" || hasScaleToken(comment, "0.01kwh")) {
    return 0.01;
  }
  if (unit === "kwh" || unit === "kilowatt-hour" || unit === "1kwh" || hasScaleToken(comment, "1kwh")) {
    return 1.0;
  }
  if (prop.format === "float" && (unit === "" || unit === "none")) {
    return 1.0;
  }
  return null;
}

/** Descriptions that claim a door security state. Offline devices must not keep one of these. */
export const AFFIRMATIVE_LOCK_STATUS = new Set([
  "已上锁",
  "已开锁",
  "门虚掩",
  "门未关",
  "门锁卡住",
  "正在上锁",
  "正在开锁",
  "守护中",
]);

/**
 * Map a MIoT `door-state` value-list description to a lock status.
 * Child-lock / anti-lock entries and unrecognized text return null so the card stays unknown (#149).
 */
export function interpretLockStatus(description: string): string | null {
  const lower = description.toLowerCase();
  if (
    lower.includes("child") ||
    lower.includes("童锁") ||
    lower.includes("反锁") ||
    lower.includes("anti-lock") ||
    lower.includes("antilock")
  ) {
    return null;
  }
  if (
    lower.includes("unlock") ||
    lower.includes("已开锁") ||
    lower.includes("未上锁") ||
    lower.includes("not locked") ||
    lower.includes("未锁")
  ) {
    return "已开锁";
  }
  if (
    (lower.includes("ajar") || lower.includes("虚掩")) &&
    !lower.includes("解除") &&
    !lower.includes("clear") &&
    !lower.includes("release")
  ) {
    return "门虚掩";
  }
  if (lower.includes("not closed") || lower.includes("not close") || lower.includes("未关") || lower.includes("没关")) {
    return "门未关";
  }
  if (lower.includes("jam") || lower.includes("卡住")) {
    return "门锁卡住";
  }
  if (lower.includes("locked") || lower.includes("已上锁") || lower.includes("已锁")) {
    return "已上锁";
  }
  return null;
}

/** Services that count as this device type's own primary service. */
export function preferredMiotServices(deviceType: string): string[] {
  const appliance = DEVICE_PROFILES.appliances[deviceType];
  if (appliance) return [...appliance.primary];
  if (deviceType === "outlet") return ["outlet", "switch"];
  if (deviceType === "camera" || deviceType === "cateye" || deviceType === "video-doorbell") {
    return ["camera-control", "video-doorbell"];
  }
  if (deviceType === "kettle" || deviceType === "ble-kettle") return ["kettle", "ble-kettle", "water-dispenser"];
  return [deviceType];
}

/** The service a card is built from, or undefined when the device is unsupported. */
export function primaryMiotService(spec: MiotDeviceSpec): MiotService | undefined {
  const priority = [
    "air-conditioner",
    "heater",
    "thermostat",
    "electric-blanket",
    "dehumidifier",
    "diffuser",
    "speaker",
    "light",
    "fan",
    "air-purifier",
    "air-fresh",
    "humidifier",
    "video-doorbell",
    "vacuum",
    "curtain",
    "window-opener",
    "airer",
    "water-heater",
    "washer",
    "dryer",
    "clothes-dryer",
    "dishwasher",
    "fridge",
    "camera-control",
    "outlet",
    "switch",
    "electronic-valve",
    "kettle",
    "ble-kettle",
    "water-dispenser",
    "cooker",
    "air-fryer",
    "oven",
    "microwave-oven",
    "health-pot",
    "lock",
    "motion-sensor",
    "occupancy-sensor",
    "magnet-sensor",
    "submersion-sensor",
    "illumination-sensor",
    "air-monitor",
    "temperature-humidity-sensor",
  ];
  const devShort = miotShortName(spec.type);
  // A device type the profile table lists as an appliance takes the first service the table
  // names, even though the type itself has no same-name service (#4).
  const appliance = DEVICE_PROFILES.appliances[devShort];
  if (appliance) {
    for (const serviceName of appliance.primary) {
      const service = spec.services.find((s) => miotShortName(s.type) === serviceName);
      if (service) return service;
    }
    return undefined;
  }
  const preferredServices = preferredMiotServices(devShort);
  // A shut-off valve without a power meter is not a breaker and stays unsupported (#265), and a
  // `water-heater` service belongs to a water heater rather than claiming another device (#300).
  const claims = (short: string) =>
    (short !== "electronic-valve" || isMainsBreaker(spec)) &&
    (short !== "water-heater" || devShort === "water-heater");
  let primaryService: MiotService | undefined = spec.services.find((s) => {
    const sShort = miotShortName(s.type);
    return preferredServices.includes(sShort) && priority.includes(sShort) && claims(sShort);
  });
  if (!primaryService) {
    for (const p of priority) {
      if (!miotFallbackAllows(devShort, p) || !claims(p)) continue;
      const found = spec.services.find((s) => miotShortName(s.type) === p);
      if (found) {
        primaryService = found;
        break;
      }
    }
  }
  if (!primaryService && devShort.endsWith("sensor")) {
    primaryService = spec.services.find((s) => miotShortName(s.type) === "environment");
  }
  if (
    !primaryService &&
    // If the spec names a service of its own device type we simply do not support that
    // category yet — claiming the first writable `on` as a plug would fake a device
    // (hood/airer would show up as an outlet).
    !spec.services.some((s) => miotShortName(s.type) === devShort)
  ) {
    // Only genuine plug-like services may fall back to a plug card. macOS has no
    // generic-on fallback at all, so accepting e.g. a bath-heater's `ptc-bath-heater`
    // or `light` service here would fake a device the other platform drops (#98).
    // The plug fallback obeys the same table (#296): a motor controller's switch is not a plug.
    primaryService = spec.services.find(
      (s) =>
        ["outlet", "switch"].includes(miotShortName(s.type)) &&
        miotFallbackAllows(devShort, miotShortName(s.type)) &&
        s.properties.some((p) => miotShortName(p.type) === "on" && p.access.includes("write"))
    );
  }
  return primaryService;
}

export const MiotSynthesizer = {
  /**
   * The cards a record produces: normally one. A dual-track curtain motor publishes two
   * `curtain` services and gets one card per track, matching the macOS split.
   */
  buildPlans(record: MiDeviceRecord, spec: MiotDeviceSpec): MiotBuildPlan[] {
    const scoped = specForRecord(record, spec);
    const tracks =
      !record.splitParentDid && miotShortName(spec.type) === "curtain"
        ? scoped.services.filter(
            (service) =>
              miotShortName(service.type) === "curtain" &&
              service.properties.some((p) =>
                ["motor-control", "current-position", "target-position"].includes(miotShortName(p.type)),
              ),
          )
        : [];
    if (tracks.length > 1) {
      return tracks.map((service) => ({
        id: `${record.did}.s${service.iid}`,
        name: curtainTrackName(service.description, record.name),
        spec: { type: scoped.type, services: [service] },
      }));
    }
    return [{ id: record.did, name: record.name, spec: scoped }];
  },

  synthesize(
    devices: readonly MiDeviceRecord[],
    rooms: readonly MiRoom[],
    specs: Readonly<Record<string, MiotDeviceSpec>>,
    values: ReadonlyMap<string, unknown>
  ): SynthesizedHome {
    const roomByDid = new Map<string, MiRoom>();
    for (const room of rooms) {
      for (const did of room.dids) {
        if (!roomByDid.has(did)) roomByDid.set(did, room);
      }
    }

    // A panel whose keys are listed as split devices is shown through those keys only.
    const splitParents = new Set(devices.map((d) => d.splitParentDid).filter((d): d is string => !!d));

    const smartDevices: SmartDevice[] = [];
    const readings: EnvironmentReading[] = [];
    const facetsByDevice: Record<string, readonly FacetControl[]> = {};

    for (const record of devices) {
      if (splitParents.has(record.did)) continue;
      // A real spec that yields no builder means the category is unsupported — drop the
      // device (macOS parity). The offline fallback spec applies only when no real spec
      // was fetched at all; re-falling back here would re-admit devices the guard rejected.
      const spec = specs[record.model] ?? inferFallbackSpec(record);
      const room =
        roomByDid.get(record.did) ??
        (record.splitParentDid ? roomByDid.get(record.splitParentDid) : undefined);
      const roomName = room?.name.trim() || "全屋";
      const roomId = room ? `room_${room.id}` : "other";

      const plans = this.buildPlans(record, spec);
      for (const plan of plans) {
        const touched = new Set<string>();
        const consumed = new Set<string>();
        const built = this.buildDeviceKind(miotDid(record), plan.spec, values, touched, consumed);
        if (!built) {
          // The last rung of the resolution ladder (#6): no dedicated builder, but the generic
          // facet layer still finds a control or a reading. A mains breaker stays out.
          if (plans.length > 1 || isMainsBreaker(spec)) continue;
          const generic = this.genericCard(record, plan.spec, values);
          if (!generic) continue;
          smartDevices.push({
            id: plan.id,
            name: plan.name,
            roomId,
            roomName,
            model: record.model,
            isOnline: record.isOnline,
            kind: generic.kind,
            ...(room?.isSharedHome === true ? { isSharedHome: true } : {}),
          });
          if (generic.facets.length > 0) facetsByDevice[plan.id] = generic.facets;
          continue;
        }

        const kind =
          record.isOnline || built.kind !== "sensor" || !built.statusText || !AFFIRMATIVE_LOCK_STATUS.has(built.statusText)
            ? built
            : { ...built, statusText: "锁状态未知" };
        const smartDevice: SmartDevice = {
          id: plan.id,
          name: plan.name,
          roomId,
          roomName,
          model: record.model,
          isOnline: record.isOnline,
          kind,
          ...(room?.isSharedHome === true ? { isSharedHome: true } : {}),
        };
        smartDevices.push(smartDevice);

        const reading = deviceEnvironmentReading(smartDevice);
        if (reading) {
          readings.push(reading);
        }

        // A mains breaker offers no 更多设置 writes even with advanced writes on (#265).
        if (!isMainsBreaker(spec)) {
          const facets = facetControls(
            plan.spec,
            miotDid(record),
            values,
            primaryMiotService(plan.spec)?.iid ?? null,
            new Set([...touched, ...consumed]),
            record.model,
          );
          if (facets.length > 0) facetsByDevice[plan.id] = facets;
        }
      }
    }

    const roomOrderByName = new Map<string, number>();
    rooms.forEach((r, idx) => {
      const name = r.name.trim() || "全屋";
      if (!roomOrderByName.has(name)) roomOrderByName.set(name, idx);
    });
    const firstSeenRoom = new Map<string, number>();
    for (const d of smartDevices) {
      if (!firstSeenRoom.has(d.roomName)) firstSeenRoom.set(d.roomName, firstSeenRoom.size);
    }
    const fallbackBase = roomOrderByName.size + 1;
    const roomOrder = (name: string): number =>
      roomOrderByName.get(name) ?? fallbackBase + (firstSeenRoom.get(name) ?? 999);
    const priorityById = new Map<string, number>(
      smartDevices.map((d) => [d.id, environmentReadingPriority(d.kind)])
    );
    readings.sort((a, b) => {
      const pa = priorityById.get(a.id) ?? 0;
      const pb = priorityById.get(b.id) ?? 0;
      if (pa !== pb) return pa - pb;
      const ra = roomOrder(a.roomName);
      const rb = roomOrder(b.roomName);
      if (ra !== rb) return ra - rb;
      return a.label < b.label ? -1 : a.label > b.label ? 1 : 0;
    });

    const roomCounts = new Map<string, { name: string; count: number }>();
    for (const dev of smartDevices) {
      const prev = roomCounts.get(dev.roomId);
      roomCounts.set(dev.roomId, {
        name: dev.roomName,
        count: (prev?.count ?? 0) + 1,
      });
    }
    const roomCategories: RoomCategory[] = Array.from(roomCounts.entries()).map(([id, info]) => ({
      id,
      name: info.name,
      deviceCount: info.count,
    }));

    return {
      devices: smartDevices,
      rooms: roomCategories,
      environmentReadings: readings,
      scenes: [],
      deviceFacets: facetsByDevice,
    };
  },

  /**
   * The fallback card content (#6): every generic facet, the first low-risk `on` as the main
   * switch, and up to two high-value readings. Null when the facet layer finds nothing.
   */
  genericCard(
    record: MiDeviceRecord,
    spec: MiotDeviceSpec,
    values: ReadonlyMap<string, unknown>
  ): { kind: GenericSpec; facets: FacetControl[] } | null {
    const deviceType = miotShortName(spec.type);
    const controls = facetControls(spec, miotDid(record), values, null, new Set(), record.model);
    if (controls.length === 0) return null;
    const power =
      controls
        .map((control, index) => ({ control, index }))
        .filter(
          ({ control }) =>
            control.kind === "toggle" &&
            control.name === "on" &&
            control.isWritable &&
            control.writesByDefault &&
            control.target !== null &&
            // Only the device's own main service may supply the tile's switch. A companion
            // indicator, night light or ambient light must never impersonate device power.
            isMainPowerService(control.serviceName, deviceType),
        )
        .sort((lhs, rhs) => {
          const lhsOwn = lhs.control.serviceName === deviceType;
          const rhsOwn = rhs.control.serviceName === deviceType;
          if (lhsOwn !== rhsOwn) return lhsOwn ? -1 : 1;
          return lhs.index - rhs.index;
        })[0]?.control ?? null;
    const preferred = profileOverride(spec, record.model)?.readouts ?? [];
    const picked: FacetControl[] = [];
    for (const pattern of preferred) {
      if (picked.length >= 2) break;
      const match = controls.find((control) =>
        control.kind === "reading" && control.value !== null &&
        matchesMiotFacetPattern(control.name, [pattern]) &&
        !picked.some((chosen) => chosen.id === control.id),
      );
      if (match) picked.push(match);
    }
    if (picked.length === 0) {
      picked.push(...controls
        .map((control, index) => ({ control, index }))
        .filter(({ control }) => control.kind === "reading" && control.value !== null)
        .sort((lhs, rhs) => {
          if (lhs.control.isHighValue !== rhs.control.isHighValue) return lhs.control.isHighValue ? -1 : 1;
          return lhs.index - rhs.index;
        })
        .slice(0, 2)
        .map(({ control }) => control));
    }
    const alerts = new Set<string>();
    for (const control of controls) {
      const raw = control.value;
      if (control.kind !== "reading") continue;
      const numeric = typeof raw === "number" ? raw : typeof raw === "string" && raw.trim() ? Number(raw) : NaN;
      const code = Math.sign(numeric) * Math.round(Math.abs(numeric));
      if (!Number.isSafeInteger(code)) continue;
      const option = control.options.find((item) => item.value === code);
      if (option && isMiotAlertLabel(option.label)) alerts.add(facetAlertText(control, option.label));
    }
    return {
      kind: {
        kind: "generic",
        deviceType,
        typeTitle: genericTypeTitle(deviceType),
        // MIoT booleans arrive as `true`/`false` or as `1`/`0`, and both mean on.
        isOn: miotBool(power?.value) === true,
        powerControl: power,
        isReadOnly: !controls.some((control) => control.isWritable),
        readouts: picked.map(facetValue),
        alerts: [...alerts],
        symbolName: genericSymbolName(deviceType),
      },
      // The tile's main switch is not repeated under 更多设置.
      facets: controls.filter((control) => control.id !== power?.id),
    };
  },

  /**
   * The (did, siid, piid) triples the synthesizer reads for this device; poll exactly these.
   * A builder records every property it reads, so `facetProps` can poll the rest (#5).
   */
  requiredProps(device: MiDeviceRecord, spec: MiotDeviceSpec): MiotPropKey[] {
    const did = miotDid(device);
    const touched: MiotPropKey[] = [];
    const seen = new Set<string>();
    for (const plan of this.buildPlans(device, spec)) {
      const keys = new Set<string>();
      this.buildDeviceKind(did, plan.spec, EMPTY_VALUES, keys);
      for (const key of keys) {
        if (seen.has(key)) continue;
        seen.add(key);
        const parts = key.split(".");
        touched.push({ did, siid: Number(parts[0]), piid: Number(parts[1]) });
      }
    }
    return touched;
  },

  /**
   * Readable properties behind the 更多设置 rows (#5), polled so the drawer's values arrive with
   * the snapshot instead of showing empty rows until a write. Card properties are excluded here
   * because `requiredProps` already covers them.
   */
  facetProps(device: MiDeviceRecord, spec: MiotDeviceSpec): MiotPropKey[] {
    if (isMainsBreaker(spec)) return [];
    const did = miotDid(device);
    const scoped = specForRecord(device, spec);
    const plans = this.buildPlans(device, spec);
    const keys: MiotPropKey[] = [];
    const seen = new Set<string>();
    const addReadable = (facetSpec: MiotDeviceSpec, excluding: ReadonlySet<string>) => {
      for (const facet of buildMiotFacets(facetSpec)) {
        if (facet.propertyId === undefined) continue;
        const service = facetSpec.services.find((s) => s.iid === facet.serviceId);
        const property = service?.properties.find((p) => p.iid === facet.propertyId);
        if (!property?.access.includes("read")) continue;
        const key = `${facet.serviceId}.${facet.propertyId}`;
        if (excluding.has(key) || seen.has(key)) continue;
        seen.add(key);
        keys.push({ did, siid: facet.serviceId, piid: facet.propertyId });
      }
    };
    // A generic card has no dedicated builder, so every readable facet property is its state.
    if (plans.length === 0) {
      addReadable(scoped, new Set());
      return keys;
    }
    for (const plan of plans) {
      const touched = new Set<string>();
      const consumed = new Set<string>();
      this.buildDeviceKind(did, plan.spec, EMPTY_VALUES, touched, consumed);
      addReadable(plan.spec, new Set([...touched, ...consumed]));
    }
    return keys;
  },

  buildDeviceKind(
    did: string,
    spec: MiotDeviceSpec,
    values: ReadonlyMap<string, unknown>,
    /** Readable properties the builder read; they are polled and hidden from 更多设置. */
    touched?: Set<string>,
    /** Properties the builder owns even without reading them, e.g. a write-only motor control. */
    consumed?: Set<string>
  ): DeviceKind | null {
    const primaryService = primaryMiotService(spec);
    if (!primaryService) return null;

    const readProp = (s: MiotService, short: string, predicate?: (p: MiotProperty) => boolean) => {
      const prop = s.properties.find(
        (p) => miotShortName(p.type) === short && p.access.includes("read") && (!predicate || predicate(p))
      );
      if (!prop) return null;
      touched?.add(`${s.iid}.${prop.iid}`);
      const val = values.get(propKeyString({ did, siid: s.iid, piid: prop.iid }));
      return { service: s, prop, val };
    };

    const readProps = (s: MiotService, short: string) =>
      s.properties
        .filter((p) => miotShortName(p.type) === short && p.access.includes("read"))
        .map((prop) => {
          touched?.add(`${s.iid}.${prop.iid}`);
          return {
            service: s,
            prop,
            val: values.get(propKeyString({ did, siid: s.iid, piid: prop.iid })),
          };
        });

    const readFirst = (services: readonly MiotService[], short: string, predicate?: (p: MiotProperty) => boolean) => {
      const seen = new Set<number>();
      for (const s of services) {
        if (seen.has(s.iid)) continue;
        seen.add(s.iid);
        const found = readProp(s, short, predicate);
        if (found) return found;
      }
      return null;
    };

    const sName = miotShortName(primaryService.type);
    if (sName === "light") {
      const on = readProp(primaryService, "on");
      const bri = readProp(primaryService, "brightness");
      const ct = readProp(primaryService, "color-temperature");
      const mode = readProp(primaryService, "mode");
      const modeVal = typeof mode?.val === "number" ? Math.trunc(mode.val) : null;
      const currentEffect =
        modeVal !== null
          ? (mode?.prop.valueList?.find((i) => i.value === modeVal)?.description ?? null)
          : null;
      const briNum = typeof bri?.val === "number" ? bri.val : null;
      const briPct = bri && briNum !== null ? percentFromRaw(briNum, bri.prop, "proportional") : null;

      const isLightGroup = did.startsWith("group.");
      const onWritable = exposesWrite(on?.prop.access);
      return {
        kind: "light",
        entityId: makeMiotAddress(did, primaryService.iid),
        isOn: miotBool(on?.val) ?? false,
        brightnessPercent: briPct,
        colorTempKelvin: typeof ct?.val === "number" ? Math.round(ct.val) : null,
        minKelvin: Math.round(ct?.prop.valueRange?.[0] ?? 2700),
        maxKelvin: Math.round(ct?.prop.valueRange?.[1] ?? 6500),
        currentEffect: isLightGroup ? null : currentEffect,
        availableEffects:
          isLightGroup || !exposesWrite(mode?.prop.access)
            ? []
            : (mode?.prop.valueList?.map((i) => i.description) ?? []),
        ...readOnlyFlags({
          powerWritable: onWritable,
          brightnessWritable: bri ? exposesWrite(bri.prop.access) : true,
          colorWritable: ct ? exposesWrite(ct.prop.access) : true,
        }),
      };
    }

    if (sName === "air-conditioner" || sName === "heater" || sName === "thermostat" || sName === "electric-blanket") {
      const on = readProp(primaryService, "on");
      const mode = sName === "electric-blanket" ? null : readProp(primaryService, "mode");
      const target = readProp(primaryService, "target-temperature");
      const current = sName === "electric-blanket" ? null : readFirst(
        [
          primaryService,
          ...spec.services.filter((s) => miotShortName(s.type) === "environment"),
          ...spec.services.filter((s) => miotShortName(s.type) === "temperature-humidity-sensor"),
        ],
        "temperature"
      );
      const fanLevel = readFirst(
        [...spec.services.filter((s) => miotShortName(s.type) === "fan-control"), primaryService],
        "fan-level"
      );

      const isOn = miotBool(on?.val) ?? false;
      const onWritable = exposesWrite(on?.prop.access);
      const modeWritable = exposesWrite(mode?.prop.access);
      const haModes: string[] = [];
      for (const item of mode?.prop.valueList ?? []) {
        const mapped = hvacModeFromDescription(item.description);
        if (mapped && !haModes.includes(mapped)) haModes.push(mapped);
      }
      if (haModes.length === 0 && (modeWritable || (!mode && onWritable))) {
        haModes.push(sName === "air-conditioner" ? "cool" : "heat");
      }
      const modeVal = typeof mode?.val === "number" ? Math.trunc(mode.val) : null;
      const rawDesc = modeVal !== null ? mode?.prop.valueList?.find((i) => i.value === modeVal)?.description : null;
      const activeMode = (rawDesc ? hvacModeFromDescription(rawDesc) : null) ?? haModes[0] ?? (sName === "air-conditioner" ? "cool" : "heat");
      const fanVal = typeof fanLevel?.val === "number" ? Math.trunc(fanLevel.val) : null;
      const fanDesc =
        fanVal !== null ? (fanLevel?.prop.valueList?.find((i) => i.value === fanVal)?.description ?? null) : null;
      const exposeModes = modeWritable || (!mode && onWritable);

      return {
        kind: "climate",
        entityId: makeMiotAddress(did, primaryService.iid),
        isOn,
        hvacMode: isOn ? activeMode : "off",
        availableModes: exposeModes ? [...haModes, "off"] : [],
        targetTemperature: typeof target?.val === "number" ? target.val : 26,
        currentTemperature: typeof current?.val === "number" ? current.val : null,
        minTemp: target?.prop.valueRange?.[0] ?? 16,
        maxTemp: target?.prop.valueRange?.[1] ?? 30,
        step: target?.prop.valueRange?.[2] ?? 1,
        fanMode: fanDesc,
        availableFanModes: exposesWrite(fanLevel?.prop.access)
          ? (fanLevel?.prop.valueList?.map((i) => i.description) ?? [])
          : [],
        ...readOnlyFlags({
          powerWritable: onWritable,
          temperatureWritable: target ? exposesWrite(target.prop.access) : true,
        }),
      };
    }

    if (sName === "fan") {
      const on = readProp(primaryService, "on");
      const mode = readProp(primaryService, "mode");
      const levels = readProps(primaryService, "fan-level");
      const swing = readProp(primaryService, "horizontal-swing");
      const percentRef = levels.find((r) => r.prop.valueRange !== undefined);
      const presetRef =
        (mode?.prop.valueList?.length ?? 0) > 0
          ? mode
          : levels.find((r) => (r.prop.valueList?.length ?? 0) > 0);
      const pctNum = typeof percentRef?.val === "number" ? percentRef.val : null;
      // The writer scales fan speed with the speed curve, so the reader must too.
      const percentage = percentRef && pctNum !== null ? percentFromRaw(pctNum, percentRef.prop, "speed") : null;
      const presetVal = typeof presetRef?.val === "number" ? Math.trunc(presetRef.val) : null;
      const presetDesc =
        presetVal !== null
          ? (presetRef?.prop.valueList?.find((i) => i.value === presetVal)?.description ?? null)
          : null;

      const onWritable = exposesWrite(on?.prop.access);
      return {
        kind: "fan",
        entityId: makeMiotAddress(did, primaryService.iid),
        isOn: miotBool(on?.val) ?? false,
        percentage,
        presetMode: presetDesc,
        availablePresetModes: exposesWrite(presetRef?.prop.access)
          ? (presetRef?.prop.valueList?.map((i) => i.description) ?? [])
          : [],
        isOscillating: miotBool(swing?.val),
        ...readOnlyFlags({
          powerWritable: onWritable,
          speedWritable: percentRef ? exposesWrite(percentRef.prop.access) : true,
          swingWritable: swing ? exposesWrite(swing.prop.access) : true,
        }),
      };
    }

    if (sName === "air-purifier" || sName === "air-fresh" || sName === "humidifier") {
      const on = readProp(primaryService, "on");
      const mode = readProp(primaryService, "mode");
      const levels = readProps(primaryService, "fan-level");
      const favServices = [
        ...spec.services.filter((s) => miotShortName(s.type) === "air-purifier-favorite"),
        ...spec.services.filter((s) => miotShortName(s.type) === "air-fresh-favorite"),
        ...spec.services.filter((s) => miotShortName(s.type) === "motor-speed"),
        ...spec.services.filter((s) => miotShortName(s.type) === "custom-service"),
        ...spec.services.filter((s) => miotShortName(s.type) === "fan-control"),
      ];
      const favSpeed =
        readFirst(favServices, "fan-level") ??
        readFirst(favServices, "favorite-fan-level") ??
        readFirst(favServices, "favorite-level") ??
        readFirst(favServices, "favorite-speed");
      const speedRef =
        favSpeed ??
        levels.find((r) => r.prop.valueRange !== undefined) ??
        levels.find((r) => (r.prop.valueList?.length ?? 0) > 0);
      const presetRef =
        (mode?.prop.valueList?.length ?? 0) > 0
          ? mode
          : levels.find((r) => (r.prop.valueList?.length ?? 0) > 0);

      const percentage =
        speedRef && typeof speedRef.val === "number" ? percentFromRaw(speedRef.val, speedRef.prop, "speed") : null;

      const presetVal = typeof presetRef?.val === "number" ? Math.trunc(presetRef.val) : null;
      const presetDesc =
        presetVal !== null
          ? (presetRef?.prop.valueList?.find((i) => i.value === presetVal)?.description ?? null)
          : null;

      const envServices = [
        ...spec.services.filter((s) => miotShortName(s.type) === "environment"),
        ...spec.services.filter((s) => miotShortName(s.type) === "temperature-humidity-sensor"),
        primaryService,
      ];
      const pm25 = readFirst(envServices, "pm2.5-density");
      const aq = readFirst(envServices, "air-quality");
      const temp = readFirst(envServices, "temperature");
      const hum = readFirst(envServices, "relative-humidity");
      const filterServices = [
        ...spec.services.filter((s) => miotShortName(s.type) === "filter"),
        primaryService,
      ];
      const filterLife = readFirst(filterServices, "filter-life-level");

      const pm25Density = typeof pm25?.val === "number" ? Math.round(pm25.val) : null;
      const aqVal = typeof aq?.val === "number" ? Math.trunc(aq.val) : null;
      const airQuality =
        aqVal !== null
          ? (aq?.prop.valueList?.find((i) => i.value === aqVal)?.description ?? null)
          : null;
      const temperature = typeof temp?.val === "number" ? temp.val : null;
      const humidity = typeof hum?.val === "number" ? hum.val : null;
      const filterLifePercent = typeof filterLife?.val === "number" ? Math.round(filterLife.val) : null;

      const onWritable = exposesWrite(on?.prop.access);
      return {
        kind: "fan",
        entityId: makeMiotAddress(did, primaryService.iid),
        isOn: miotBool(on?.val) ?? false,
        percentage,
        presetMode: presetDesc,
        availablePresetModes: exposesWrite(presetRef?.prop.access)
          ? (presetRef?.prop.valueList?.map((i) => i.description) ?? [])
          : [],
        isOscillating: null,
        ...(pm25Density !== null ? { pm25Density } : {}),
        ...(airQuality !== null ? { airQuality } : {}),
        ...(temperature !== null ? { temperature } : {}),
        ...(humidity !== null ? { humidity } : {}),
        ...(filterLifePercent !== null ? { filterLifePercent } : {}),
        ...readOnlyFlags({
          powerWritable: onWritable,
          speedWritable: speedRef ? exposesWrite(speedRef.prop.access) : true,
        }),
      };
    }

    if (sName === "vacuum") {
      const status = readProp(primaryService, "status");
      const mode = readProp(primaryService, "mode");
      const battery = readFirst(
        [...spec.services.filter((s) => miotShortName(s.type) === "battery"), primaryService],
        "battery-level"
      );
      const statusVal = typeof status?.val === "number" ? Math.trunc(status.val) : null;
      const statusDesc =
        statusVal !== null
          ? (status?.prop.valueList?.find((i) => i.value === statusVal)?.description?.toLowerCase() ?? "")
          : "";
      let normalizedStatus = "idle";
      if (
        statusDesc.includes("go charging") ||
        statusDesc.includes("returning") ||
        statusDesc.includes("go home") ||
        statusDesc.includes("back to") ||
        statusDesc.includes("recharge")
      ) {
        normalizedStatus = "returning";
      } else if (statusDesc.includes("paus")) {
        normalizedStatus = "paused";
      } else if (
        statusDesc.includes("sweep") ||
        statusDesc.includes("clean") ||
        statusDesc.includes("mop") ||
        statusDesc.includes("working") ||
        statusDesc.includes("busy")
      ) {
        normalizedStatus = "cleaning";
      } else if (statusDesc.includes("charg") || statusDesc.includes("dock") || statusDesc.includes("full")) {
        normalizedStatus = "docked";
      }

      const modeVal = typeof mode?.val === "number" ? Math.trunc(mode.val) : null;
      const currentMode =
        modeVal !== null
          ? (mode?.prop.valueList?.find((i) => i.value === modeVal)?.description ?? null)
          : null;

      return {
        kind: "vacuum",
        entityId: makeMiotAddress(did, primaryService.iid),
        status: normalizedStatus,
        battery: typeof battery?.val === "number" ? Math.round(battery.val) : null,
        modeEntityId: mode ? makeMiotAddress(did, mode.service.iid) : null,
        currentMode,
        availableModes: mode?.prop.valueList?.map((i) => i.description) ?? [],
        ...readOnlyFlags({
          actionsWritable: spec.services.some((service) =>
            service.actions.some((action) =>
              ["start-sweep", "start-clean", "start-sweep-mop", "start-mop"].includes(miotShortName(action.type)),
            ),
          ),
        }),
      };
    }

    if (sName === "speaker") {
      const volume = readProp(primaryService, "volume");
      const mute = readProp(primaryService, "mute");
      const playing = readFirst(
        spec.services.filter((s) => miotShortName(s.type) === "play-control"),
        "playing-state",
      );
      const intelligent = spec.services.find((s) => miotShortName(s.type) === "intelligent-speaker");
      const sleep = intelligent ? readProp(intelligent, "sleep-mode") : null;
      const playingCode = typeof playing?.val === "number" ? Math.trunc(playing.val) : null;
      const playingWord =
        playingCode !== null
          ? (playing?.prop.valueList?.find((i) => i.value === playingCode)?.description.toLowerCase() ?? "")
          : "";
      const state = playingWord === "playing" || playingWord === "play"
        ? "playing"
        : playingWord === "paused" || playingWord === "pause"
          ? "paused"
          : "idle";
      const speakerAddress = makeMiotAddress(did, primaryService.iid);
      return {
        kind: "speaker",
        mediaEntityId: speakerAddress,
        state,
        volumePercent: volume && typeof volume.val === "number" ? percentFromRaw(volume.val, volume.prop, "proportional") : 0,
        isMuted: miotBool(mute?.val) ?? false,
        muteSwitchEntityId: exposesWrite(mute?.prop.access) ? speakerAddress : null,
        sleepModeEntityId:
          intelligent && exposesWrite(sleep?.prop.access) ? makeMiotAddress(did, intelligent.iid) : null,
        isSleepMode: miotBool(sleep?.val) ?? false,
        volumeWritable: exposesWrite(volume?.prop.access),
      };
    }

    if (sName === "curtain" || sName === "window-opener" || sName === "airer") {
      const current = readProp(primaryService, "current-position");
      const target = readProp(primaryService, "target-position");
      const status = readProp(primaryService, "status");
      const fault = readProp(primaryService, "device-fault") ?? readProp(primaryService, "fault");
      const battery = readFirst(
        spec.services.filter((s) => miotShortName(s.type) === "battery"),
        "battery-level",
      );
      const motor = primaryService.properties.find(
        (p) => miotShortName(p.type) === "motor-control" && p.access.includes("write"),
      );
      // The motor is reachable from the card's own open/close/pause taps, so it never repeats
      // under 更多设置 (macOS marks a write-only motor control as consumed, not as polled).
      if (motor) consumed?.add(`${primaryService.iid}.${motor.iid}`);
      const items = motor?.valueList ?? [];
      const targetProperty = primaryService.properties.find((p) => miotShortName(p.type) === "target-position");
      const statusCode = typeof status?.val === "number" ? Math.trunc(status.val) : null;
      const statusDescription =
        statusCode !== null
          ? (status?.prop.valueList?.find((i) => i.value === statusCode)?.description ?? null)
          : null;
      const faultCode = typeof fault?.val === "number" ? Math.trunc(fault.val) : null;
      const faultWord =
        faultCode !== null
          ? (fault?.prop.valueList?.find((i) => i.value === faultCode)?.description ?? null)
          : null;
      const subtype = coverSubtype(sName);
      const inverted = coverInvertsPosition(subtype);
      const coverPercent = (ref: typeof current): number | null =>
        ref && typeof ref.val === "number" ? percentFromRaw(ref.val, ref.prop, "proportional") : null;
      const rawCurrent = coverPercent(current);
      const rawTarget = coverPercent(target);
      const currentPosition = rawCurrent === null ? null : inverted ? 100 - rawCurrent : rawCurrent;
      const targetPosition = rawTarget === null ? null : inverted ? 100 - rawTarget : rawTarget;
      const word = coverStatusWord(statusDescription);
      const motion = coverMotion(
        coverStatusDefinesTravel(status?.prop ?? null),
        word,
        currentPosition,
        targetPosition,
      );
      return {
        kind: "cover",
        entityId: makeMiotAddress(did, primaryService.iid),
        subtype,
        positionPercent:
          coverDisplayPosition(currentPosition, targetPosition, motion) ?? coverStatusFallbackPosition(word),
        positionWritable: exposesWrite(targetProperty?.access),
        openDescription: coverMotorDescription(items, "open"),
        closeDescription: coverMotorDescription(items, "close"),
        pauseDescription: coverMotorDescription(items, "pause"),
        batteryPercent: typeof battery?.val === "number" ? Math.trunc(battery.val) : null,
        faultText: faultDescription(faultCode, faultWord),
        motion,
      };
    }

    const deviceType = miotShortName(spec.type);
    const applianceProfile = DEVICE_PROFILES.appliances[deviceType];
    const isApplianceCard =
      (applianceProfile !== undefined && applianceProfile.primary.includes(sName)) ||
      APPLIANCE_SERVICE_NAMES.includes(sName);
    if (isApplianceCard) {
      // A spec may publish a richer companion service beside the one its device type names, so
      // the card merges the primary with the profile's companions and skips duplicates.
      const services: MiotService[] = [];
      const serviceIds = new Set<number>();
      for (const service of [
        primaryService,
        ...(applianceProfile?.companions ?? APPLIANCE_SERVICE_NAMES).flatMap((name) =>
          spec.services.filter((s) => miotShortName(s.type) === name),
        ),
      ]) {
        if (serviceIds.has(service.iid)) continue;
        serviceIds.add(service.iid);
        services.push(service);
      }
      // A `light` companion contributes its own switch only; its `on`/`mode` must never be
      // mistaken for the appliance's main function.
      const lightService = services.find(
        (service) =>
          miotShortName(service.type) === "light" &&
          service.properties.some((p) => miotShortName(p.type) === "on"),
      );
      const controls = services.filter((service) => service.iid !== lightService?.iid);
      const companionControls = controls.filter((service) => service.iid !== primaryService.iid);
      const writablePower = (group: readonly MiotService[]) =>
        readFirst(group, "on", (p) => exposesWrite(p.access)) ??
        readFirst(group, "heating", (p) => exposesWrite(p.access));
      // The main function's own service owns power whenever it publishes it. A companion can
      // carry the only writable copy while the primary publishes the same name read-only (#300),
      // and this order keeps a bath heater's `heating` from being replaced by a fan switch (#4).
      const power =
        writablePower([primaryService]) ??
        writablePower(companionControls) ??
        readFirst([primaryService], "on") ??
        readFirst([primaryService], "heating") ??
        readFirst(companionControls, "on") ??
        readFirst(companionControls, "heating");
      const mode =
        readFirst(controls, "mode", (p) => exposesWrite(p.access) && (p.valueList?.length ?? 0) > 0) ??
        readFirst(controls, "mode", (p) => (p.valueList?.length ?? 0) > 0);
      const target =
        readFirst(controls, "target-temperature", (p) => exposesWrite(p.access)) ??
        readFirst(controls, "target-temperature");
      const readingServices = [
        ...services,
        ...spec.services.filter((s) => miotShortName(s.type) === "environment"),
        ...spec.services.filter((s) => miotShortName(s.type) === "temperature-humidity-sensor"),
      ];
      const current = readFirst(readingServices, "temperature");
      const humidity =
        readFirst(readingServices, "relative-humidity") ?? readFirst(readingServices, "humidity");
      const status = readFirst(controls, "status");
      const runStatus = readFirst(controls, "run-status");
      const fault = readFirst(controls, "fault") ?? readFirst(controls, "device-fault");
      const remaining = readFirst(controls, "left-time");
      const faultCode = typeof fault?.val === "number" ? Math.trunc(fault.val) : null;
      const faultWord =
        faultCode === null
          ? null
          : (fault?.prop.valueList?.find((i) => i.value === faultCode)?.description ?? null);
      const lightOn = lightService ? readProp(lightService, "on") : null;
      const fan =
        readFirst(controls, "fan-level", (p) => exposesWrite(p.access) && (p.valueList?.length ?? 0) > 0) ??
        readFirst(controls, "fan-level", (p) => (p.valueList?.length ?? 0) > 0);
      const description = (ref: { readonly prop: MiotProperty; readonly val: unknown } | null) => {
        const raw = ref?.val;
        if (ref === null || typeof raw !== "number") return null;
        return ref.prop.valueList?.find((i) => i.value === Math.trunc(raw))?.description ?? null;
      };
      const applianceAction = (kind: ApplianceActionKind) => {
        for (const service of controls) {
          for (const action of service.actions) {
            if ((action.in?.length ?? 0) > 0) continue;
            if (!APPLIANCE_ACTION_ALIASES[kind].includes(miotShortName(action.type).toLowerCase())) continue;
            return { kind, entityId: makeMiotActionAddress(did, service.iid, action.iid) };
          }
        }
        return null;
      };
      const address = (ref: { readonly service: MiotService } | null) =>
        ref === null ? null : makeMiotAddress(did, ref.service.iid);
      return {
        kind: "appliance",
        entityId: makeMiotAddress(did, primaryService.iid),
        isOn: miotBool(power?.val) ?? false,
        powerPresent: power !== null,
        powerWritable: exposesWrite(power?.prop.access),
        currentMode: description(mode),
        availableModes: mode?.prop.valueList?.map((i) => i.description) ?? [],
        modeWritable: exposesWrite(mode?.prop.access),
        targetTemperature: typeof target?.val === "number" ? target.val : null,
        currentTemperature: typeof current?.val === "number" ? current.val : null,
        minTemp: target?.prop.valueRange?.[0] ?? 0,
        maxTemp: target?.prop.valueRange?.[1] ?? 100,
        step: target?.prop.valueRange?.[2] ?? 1,
        temperatureWritable: exposesWrite(target?.prop.access),
        statusText: applianceStatusText(sName, description(status), description(runStatus)),
        faultText: faultDescription(faultCode, faultWord),
        remainingMinutes: remainingMinutes(remaining),
        startAction: applianceAction("start"),
        pauseAction: applianceAction("pause"),
        stopAction: applianceAction("stop"),
        powerEntityId: address(power) ?? makeMiotAddress(did, primaryService.iid),
        modeEntityId: address(mode) ?? makeMiotAddress(did, primaryService.iid),
        temperatureEntityId: address(target) ?? makeMiotAddress(did, primaryService.iid),
        light:
          lightOn === null
            ? null
            : {
                entityId: makeMiotAddress(did, lightOn.service.iid),
                isOn: miotBool(lightOn.val) ?? false,
                writable: exposesWrite(lightOn.prop.access),
              },
        fanLevel:
          fan === null
            ? null
            : {
                entityId: makeMiotAddress(did, fan.service.iid),
                current: description(fan),
                options: fan.prop.valueList?.map((i) => i.description) ?? [],
                writable: exposesWrite(fan.prop.access),
              },
        currentHumidity: typeof humidity?.val === "number" ? humidity.val : null,
        symbolName: applianceProfile?.symbol ?? null,
        roomReading: applianceProfile?.roomReading === true,
        heats: applianceProfile?.heats ?? deviceType === "water-heater",
      };
    }

    if (sName === "fridge") {
      const mode = readProp(primaryService, "mode");
      const chambers = spec.services.filter((s) => miotShortName(s.type) === "fridge-chamber");
      const fridgeChamber =
        chambers.find((s) => {
          const target = s.properties.find((p) => miotShortName(p.type) === "target-temperature");
          return (target?.valueRange?.[0] ?? 0) >= 0;
        }) ?? chambers[0];
      const freezerChamber =
        chambers.find((s) => {
          const target = s.properties.find((p) => miotShortName(p.type) === "target-temperature");
          return (target?.valueRange?.[0] ?? 0) < 0;
        }) ?? (chambers.length > 1 ? chambers[1] : undefined);

      const fridgeTemp = fridgeChamber ? readProp(fridgeChamber, "temperature") : null;
      const fridgeTarget = fridgeChamber ? readProp(fridgeChamber, "target-temperature") : null;
      const freezerTemp = freezerChamber ? readProp(freezerChamber, "temperature") : null;
      const freezerTarget = freezerChamber ? readProp(freezerChamber, "target-temperature") : null;

      const modeVal = typeof mode?.val === "number" ? Math.trunc(mode.val) : null;
      const currentMode =
        modeVal !== null
          ? (mode?.prop.valueList?.find((i) => i.value === modeVal)?.description ?? "None")
          : "None";
      const modeWritable = Boolean(mode?.prop.access.includes("write"));
      const fridgeTargetWritable = Boolean(fridgeTarget?.prop.access.includes("write"));
      const freezerTargetWritable = Boolean(freezerTarget?.prop.access.includes("write"));

      return {
        kind: "fridge",
        modeEntityId: modeWritable ? makeMiotAddress(did, primaryService.iid) : null,
        currentMode,
        availableModes: modeWritable ? (mode?.prop.valueList?.map((i) => i.description) ?? []) : [],
        fridgeCurrentTemp: typeof fridgeTemp?.val === "number" ? fridgeTemp.val : null,
        fridgeTargetTemp: typeof fridgeTarget?.val === "number" ? fridgeTarget.val : 4,
        fridgeTargetEntityId: fridgeTargetWritable && fridgeChamber ? makeMiotAddress(did, fridgeChamber.iid) : null,
        freezerCurrentTemp: typeof freezerTemp?.val === "number" ? freezerTemp.val : null,
        freezerTargetTemp: typeof freezerTarget?.val === "number" ? freezerTarget.val : -18,
        freezerTargetEntityId: freezerTargetWritable && freezerChamber ? makeMiotAddress(did, freezerChamber.iid) : null,
      };
    }

    if (sName === "camera-control" || sName === "video-doorbell") {
      const on = readProp(primaryService, "on");
      const nightShot = readProp(primaryService, "night-shot");
      const motionSvc = spec.services.find((s) => miotShortName(s.type) === "motion-detection");
      const motion = motionSvc ? readProp(motionSvc, "motion-detection") : null;
      const streamSvc = spec.services.find(
        (s) =>
          miotShortName(s.type) === "camera-stream-for-google-home" &&
          s.actions.some((a) => miotShortName(a.type) === "start-hls-stream")
      );
      const nightVal = typeof nightShot?.val === "number" ? Math.trunc(nightShot.val) : null;
      const currentNightShot =
        nightVal !== null
          ? (nightShot?.prop.valueList?.find((i) => i.value === nightVal)?.description ?? null)
          : null;

      const onWritable = exposesWrite(on?.prop.access);
      return {
        kind: "camera",
        entityId: makeMiotAddress(did, primaryService.iid),
        isOn: miotBool(on?.val) ?? false,
        nightShot: currentNightShot,
        availableNightShots: exposesWrite(nightShot?.prop.access)
          ? (nightShot?.prop.valueList?.map((i) => i.description) ?? [])
          : [],
        motionDetectionEntityId:
          motion && exposesWrite(motion.prop.access) ? makeMiotAddress(did, motion.service.iid) : null,
        isMotionDetectionOn: miotBool(motion?.val),
        streamEntityId: streamSvc ? makeMiotAddress(did, streamSvc.iid) : null,
        ...readOnlyFlags({ powerWritable: onWritable }),
      };
    }

    if (
      [
        "kettle",
        "ble-kettle",
        "water-dispenser",
        "cooker",
        "air-fryer",
        "oven",
        "microwave-oven",
        "health-pot",
      ].includes(sName) &&
      !primaryService.properties.some((p) => miotShortName(p.type) === "on" && p.access.includes("write"))
    ) {
      const status = readProp(primaryService, "status");
      const statusVal = typeof status?.val === "number" ? Math.trunc(status.val) : null;
      const statusDesc =
        statusVal !== null
          ? (status?.prop.valueList?.find((i) => i.value === statusVal)?.description ?? null)
          : null;
      const hasActionControl = primaryService.actions.some(
        (a) =>
          ["start-cook", "cancel-cooking", "pause"].includes(miotShortName(a.type)) &&
          (a.in?.length ?? 0) === 0
      );
      if (hasActionControl) {
        const desc = (statusDesc ?? "").toLowerCase();
        const isActive =
          desc.includes("running") ||
          desc.includes("cooking") ||
          desc.includes("heating") ||
          desc.includes("boiling") ||
          desc.includes("preheat") ||
          desc.includes("keep warm") ||
          desc.includes("warming") ||
          desc.includes("busy");
        return {
          kind: "plug",
          switchEntityId: makeMiotAddress(did, primaryService.iid),
          isOn: isActive,
          powerWatts: null,
          childLockEntityId: null,
          isChildLocked: false,
        };
      }
      const temp = readProp(primaryService, "temperature") ?? readProp(primaryService, "target-temperature");
      const leftTime = readProp(primaryService, "left-time");
      readProp(primaryService, "ble-keep-warm-time");
      const parts: string[] = [];
      if (statusDesc) {
        const lower = statusDesc.toLowerCase();
        if (lower.includes("idle") || lower.includes("standby") || lower.includes("off")) parts.push("待机");
        else if (lower.includes("warm") || lower.includes("insulation")) parts.push("保温中");
        else if (lower.includes("cook") || lower.includes("work") || lower.includes("busy") || lower.includes("boil") || lower.includes("heat")) parts.push("运行中");
        else if (lower.includes("preheat")) parts.push("预热中");
        else if (lower.includes("delay") || lower.includes("appoint") || lower.includes("reservation")) parts.push("预约中");
        else if (lower.includes("paus")) parts.push("已暂停");
        else parts.push(statusDesc);
      }
      if (typeof temp?.val === "number" && Number.isFinite(temp.val)) {
        parts.push(`${Math.round(temp.val)}°C`);
      }
      if (typeof leftTime?.val === "number" && leftTime.val > 0) {
        parts.push(`剩余 ${Math.round(leftTime.val)} 分`);
      }
      const fallback = ["kettle", "ble-kettle", "water-dispenser"].includes(sName) ? "保温监控" : "待机";
      return {
        kind: "sensor",
        temperature: null,
        humidity: null,
        battery: null,
        statusText: parts.length > 0 ? parts.join(" · ") : fallback,
      };
    }

    if (
      [
        "temperature-humidity-sensor",
        "environment",
        "lock",
        "motion-sensor",
        "occupancy-sensor",
        "magnet-sensor",
        "submersion-sensor",
        "illumination-sensor",
        "air-monitor",
      ].includes(sName)
    ) {
      const readingServices = [
        primaryService,
        ...spec.services.filter((s) => miotShortName(s.type) === "environment"),
        ...spec.services.filter((s) => miotShortName(s.type) === "temperature-humidity-sensor"),
      ];
      const temp = readFirst(readingServices, "temperature");
      const hum = readFirst(readingServices, "relative-humidity");
      const battery = readFirst(
        [...spec.services.filter((s) => miotShortName(s.type) === "battery"), primaryService],
        "battery-level"
      );
      let statusText: string | null = null;
      if (temp === null && hum === null) {
        if (sName === "lock") {
          const doorServices = [
            primaryService,
            ...spec.services.filter((s) => miotShortName(s.type) === "door"),
          ];
          const door = readFirst(doorServices, "door-state");
          const doorValue = typeof door?.val === "number" ? Math.trunc(door.val) : null;
          const description =
            doorValue !== null
              ? (door?.prop.valueList?.find((item) => item.value === doorValue)?.description ?? null)
              : null;
          statusText = (description && interpretLockStatus(description)) || "锁状态未知";
        } else if (sName === "motion-sensor" || sName === "occupancy-sensor") {
          const motion = readProp(primaryService, "motion-state") ?? readProp(primaryService, "occupancy-status");
          const lux = readFirst(
            [primaryService, ...spec.services.filter((s) => miotShortName(s.type) === "illumination-sensor")],
            "illumination"
          );
          const mBool = miotBool(motion?.val);
          const parts = [mBool === true ? "有人移动" : mBool === false ? "无人移动" : "移动监测"];
          if (typeof lux?.val === "number" && Number.isFinite(lux.val)) {
            parts.push(`${Math.round(lux.val)} lx`);
          }
          statusText = parts.join(" · ");
        } else if (sName === "magnet-sensor") {
          const contact = miotBool(readProp(primaryService, "contact-state")?.val);
          statusText = contact === true ? "已闭合" : contact === false ? "已打开" : "门窗监测";
        } else if (sName === "submersion-sensor") {
          const wet = miotBool(readProp(primaryService, "submersion-state")?.val);
          statusText = wet === true ? "浸水告警" : wet === false ? "正常" : "水浸监测";
        } else if (sName === "illumination-sensor") {
          const lux = readProp(primaryService, "illumination");
          statusText = typeof lux?.val === "number" && Number.isFinite(lux.val) ? `${Math.round(lux.val)} lx` : "光照监测";
        }
      }
      return {
        kind: "sensor",
        temperature: typeof temp?.val === "number" ? temp.val : null,
        humidity: typeof hum?.val === "number" ? hum.val : null,
        battery: typeof battery?.val === "number" ? Math.round(battery.val) : null,
        ...(statusText ? { statusText } : {}),
      };
    }

    if (sName === "dehumidifier") {
      const on = readProp(primaryService, "on");
      const mode = readProp(primaryService, "mode");
      const env = spec.services.filter((s) => miotShortName(s.type) === "environment");
      const temperature = readFirst(env, "temperature");
      const humidity = readFirst(env, "relative-humidity");
      const onWritable = exposesWrite(on?.prop.access);
      return {
        kind: "fan",
        entityId: makeMiotAddress(did, primaryService.iid),
        isOn: miotBool(on?.val) ?? false,
        percentage: null,
        presetMode: mode?.prop.valueList?.find((item) => item.value === mode.val)?.description ?? null,
        availablePresetModes: mode?.prop.access.includes("write") ? mode.prop.valueList?.map((item) => item.description) ?? [] : [],
        isOscillating: null,
        temperature: typeof temperature?.val === "number" ? temperature.val : null,
        humidity: typeof humidity?.val === "number" ? humidity.val : null,
        ...readOnlyFlags({ powerWritable: onWritable }),
      };
    }

    if (
      sName === "outlet" ||
      sName === "diffuser" ||
      sName === "switch" ||
      primaryService.properties.some((p) => miotShortName(p.type) === "on")
    ) {
      const on = readProp(primaryService, "on");
      const consumptionServices = [
        ...spec.services.filter((s) => miotShortName(s.type) === "power-consumption"),
        ...spec.services,
      ];
      const power = readFirst(consumptionServices, "electric-power");
      const powerScaleVal = power ? powerScale(power.prop) : null;
      const watts = typeof power?.val === "number" && power.val >= 0 && powerScaleVal !== null ? power.val * powerScaleVal : NaN;
      const energy = readFirst(consumptionServices, "power-consumption", (p) => energyScale(p) !== null);
      const energyScaleVal = energy ? energyScale(energy.prop) : null;
      const energyKwh =
        energy && typeof energy.val === "number" && Number.isFinite(energy.val) && energy.val >= 0 && energyScaleVal !== null
          ? Math.round(energy.val * energyScaleVal * 1000) / 1000
          : null;
      const lockSvc = spec.services.find((s) => miotShortName(s.type) === "physical-controls-locked");
      const lock = lockSvc ? readProp(lockSvc, "physical-controls-locked") : null;

      // A mains breaker (#265) is a read-only meter: one write could black out the home, and its
      // power already covers the downstream plugs, so it stays out of the home total.
      const isBreaker = sName === "electronic-valve";
      const onWritable = !isBreaker && exposesWrite(on?.prop.access);
      return {
        kind: "plug",
        switchEntityId: makeMiotAddress(did, primaryService.iid),
        isOn: miotBool(on?.val) ?? false,
        powerWatts: Number.isFinite(watts) ? watts : null,
        ...(energyKwh !== null ? { energyKwh } : {}),
        childLockEntityId: lock && !isBreaker ? makeMiotAddress(did, lock.service.iid) : null,
        isChildLocked: isBreaker ? false : miotBool(lock?.val) ?? false,
        ...readOnlyFlags({ powerWritable: onWritable }),
        ...(isBreaker ? { countsInTotalPower: false } : {}),
      };
    }

    return null;
  },
};
