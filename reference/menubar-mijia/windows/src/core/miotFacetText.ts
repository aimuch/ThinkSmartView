import { speechLocale, t } from "./i18n.js";
import { formatTemperature } from "./temperature.js";
import type { FacetControl } from "./models.js";

/**
 * Display text for a generic facet, shared by the generic card's readouts and the 更多设置 drawer
 * (#5, #6), so the card and the drawer cannot format the same value differently. Mirrors macOS
 * `MiotFacetText`.
 */

/** Common MIoT short names; a localized title here beats the spec's vendor description. */
const FACET_TITLES: Readonly<Record<string, string>> = {
  on: "开关",
  mode: "模式",
  "fan-level": "风速",
  "fan-speed": "风速",
  brightness: "亮度",
  "color-temperature": "色温",
  "target-temperature": "目标温度",
  "target-temp": "目标温度",
  temperature: "温度",
  "relative-humidity": "湿度",
  humidity: "湿度",
  "target-humidity": "目标湿度",
  "current-humidity": "当前湿度",
  "pm2.5-density": "PM2.5",
  "battery-level": "电量",
  fault: "故障",
  status: "状态",
  volume: "音量",
  mute: "静音",
  "motion-detection": "移动侦测",
  "detection-sensitivity": "侦测灵敏度",
  "alarm-interval": "报警间隔",
  "night-shot": "夜视",
  "current-position": "当前位置",
  "target-position": "目标位置",
  "motor-control": "电机控制",
  "motor-reverse": "电机方向",
  "physical-controls-locked": "按键锁",
  "indicator-light": "指示灯",
  "default-power-on-state": "通电默认状态",
  "soft-wind": "柔风",
  "horizontal-swing": "左右摆风",
  "vertical-swing": "上下摆风",
  "heat-level": "加热档位",
  "storage-free-space": "剩余空间",
  "storage-total-space": "总空间",
  "storage-used-space": "已用空间",
  "left-time": "剩余时间",
  "delay-off-time": "延时关闭",
  "work-mode": "工作模式",
  "privacy-mode": "隐私模式",
};

const FAMILY_FACET_TITLES: Readonly<Record<string, string>> = {
  "water-pump": "水泵",
  "pump-flux": "水泵档位",
  "water-pump-status": "水泵状态",
  "pump-alarm": "水泵告警",
  "pump-fault-detection": "水泵故障检测",
  "pet-food-out": "投喂",
  "feeding-measure": "投喂份数",
  "today-feeded-num": "今日已投喂",
  "sum-fed-measure": "今日已投喂",
  "feeding-quantity": "今日已投喂",
  "eaten-food-measure": "已吃份数",
  "feeding-cycle": "投喂周期",
  "feeding-status": "投喂状态",
  "feeding-alarm": "投喂告警",
  "feeding-protection": "投喂保护",
  "feeding-type": "投喂方式",
  "pet-food-left-level": "余粮",
  "target-feeding-measure": "目标份数",
  "food-heap-value": "粮仓余量",
  "post-feed-pump-pause": "投喂后停泵",
  "post-feed-pump-state": "投喂后水泵",
  "filter-life-level": "滤芯寿命",
  "filter-left-time": "滤芯剩余",
  "reset-filter-life": "重置滤芯",
  "filter-alarm": "滤芯告警",
  "water-alarm": "换水提醒",
  "flow-speed-level": "流速",
  "temp-warn-switch": "水温告警",
  "temp-warn-min": "水温下限",
  "temp-warn-max": "水温上限",
  "temperature-warn-on": "水温告警",
  "temperature-warn-min": "水温下限",
  "temperature-warn-max": "水温上限",
  "upper-temperature": "水温上限",
  "lower-temperature": "水温下限",
  "limit-alarm": "水温告警",
  "desiccant-left-level": "干燥剂余量",
  "desiccant-left-time": "干燥剂剩余",
  "reset-desiccant-life": "重置干燥剂",
  "no-disturb": "勿扰",
  alarm: "告警",
  "working-time": "运行时长",
};

/** Device-type labels for the generic card; an unlisted type is humanized instead. */
const DEVICE_TYPE_TITLES: Readonly<Record<string, string>> = {
  "fish-tank": "鱼缸",
  "pet-feeder": "宠物喂食器",
  router: "路由器",
  gateway: "网关",
  repeater: "信号放大器",
  lock: "门锁",
  scale: "体重秤",
  "body-fat-scale": "体脂秤",
  bracelet: "手环",
  watch: "手表",
  toothbrush: "牙刷",
  treadmill: "跑步机",
  massage: "按摩设备",
  bed: "床",
  mattress: "床垫",
  pillow: "枕头",
  sofa: "沙发",
  mirror: "镜子",
  shower: "淋浴",
  toilet: "马桶",
  "towel-rack": "毛巾架",
  "car-seat": "儿童安全座椅",
  tracker: "定位器",
  projector: "投影仪",
  television: "电视",
  stb: "机顶盒",
  stove: "燃气灶",
  boiler: "壁挂炉",
  clock: "时钟",
  "coffee-machine": "咖啡机",
};

export function humanizedFacetName(raw: string): string {
  const words = raw.replaceAll("-", " ");
  const first = words.slice(0, 1);
  return first.toUpperCase() + words.slice(1);
}

/** Row title: a known MIoT name, else the spec's own description, else the humanized name. */
export function facetTitle(control: FacetControl): string {
  const known = FACET_TITLES[control.name];
  if (known) return t(known);
  const family = FAMILY_FACET_TITLES[control.name];
  if (family && speechLocale() !== "en-US") return family;
  const raw = (control.title ?? "").trim();
  return raw.length > 0 ? raw : humanizedFacetName(control.name);
}

export function facetAlertText(control: FacetControl, label: string): string {
  const title = facetTitle(control);
  if (speechLocale() === "en-US") return `${title}：${label}`;
  const key = label.toLowerCase();
  let condition: string | undefined;
  if (key.includes("upper limit")) condition = "高于上限";
  else if (key.includes("lower limit")) condition = "低于下限";
  else if (control.name.includes("pump")) {
    condition = {
      "not inserted": "未插入", low: "低水位", low1: "低水位",
      blocked: "堵塞", blockage: "堵塞", bockage: "堵塞", fault: "故障",
    }[key];
  } else if (["food", "feed", "left-level"].some((word) => control.name.includes(word))) {
    condition = { empty: "已空", low: "余粮不足", "stuck food": "卡粮", timeout: "超时" }[key];
  }
  condition ??= {
    fault: "故障", error: "错误", exception: "异常", alarm: "告警",
    change: "需更换", timeout: "超时", empty: "已空", offline: "离线",
    "not inserted": "未插入", blocked: "堵塞", blockage: "堵塞", bockage: "堵塞",
  }[key];
  return `${title}：${condition ?? label}`;
}

function unitSuffix(unit: string | null): string {
  const trimmed = (unit ?? "").trim();
  if (!trimmed || trimmed.toLowerCase() === "none") return "";
  switch (trimmed.toLowerCase()) {
    case "celsius":
      return " °C";
    case "percentage":
    case "percent":
      return "%";
    case "ppm":
      return " ppm";
    case "lux":
      return " lx";
    case "minutes":
      return " min";
    case "seconds":
      return " s";
    case "hour":
    case "hours":
      return " h";
    default:
      return ` ${trimmed}`;
  }
}

/** A whole number reads as an integer; a fractional step keeps one decimal. */
export function formatReading(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/** The value a row shows: an option label, 开/关, a scaled reading, or the raw value. */
export function facetValue(control: FacetControl): string {
  const raw = control.value;
  if (typeof raw === "number") {
    const option = control.options.find((item) => item.value === Math.trunc(raw));
    if (option) return option.label;
  }
  if (control.kind === "toggle") return raw === true || raw === 1 ? t("开") : t("关");
  if (typeof raw === "number") {
    const scaled = raw * (control.scale ?? 1);
    if (control.kind === "reading" && control.unit?.toLowerCase() === "celsius") {
      return formatTemperature(scaled, undefined, 1);
    }
    return formatReading(scaled) + unitSuffix(control.unit);
  }
  if (typeof raw === "boolean") return raw ? t("开") : t("关");
  if (typeof raw === "string" && raw.length > 0) return raw;
  return "—";
}

/** Device-type label for a generic card. */
export function genericTypeTitle(deviceType: string): string {
  const known = DEVICE_TYPE_TITLES[deviceType];
  return known ? t(known) : humanizedFacetName(deviceType);
}

/** A symbol name in the macOS vocabulary, so both cards pick the same glyph. */
export function genericSymbolName(deviceType: string): string {
  switch (deviceType) {
    case "fish-tank":
      return "fish.fill";
    case "pet-feeder":
      return "pawprint.fill";
    case "router":
    case "gateway":
    case "repeater":
      return "wifi";
    case "lock":
      return "lock.fill";
    case "scale":
    case "body-fat-scale":
      return "scalemass.fill";
    case "bracelet":
    case "watch":
      return "applewatch";
    case "toothbrush":
      return "mouth.fill";
    case "treadmill":
      return "figure.run";
    case "bed":
    case "mattress":
    case "pillow":
      return "bed.double.fill";
    case "sofa":
    case "chair":
    case "massage":
      return "chair.lounge.fill";
    case "projector":
      return "projector.fill";
    case "television":
    case "stb":
      return "tv";
    case "boiler":
      return "water.waves";
    case "shower":
      return "drop.fill";
    case "mirror":
      return "rectangle.portrait.fill";
    case "car-seat":
      return "car.fill";
    case "tracker":
      return "location.fill";
    case "bike":
      return "bicycle";
    case "clock":
      return "clock.fill";
    case "coffee-machine":
      return "cup.and.saucer.fill";
    default:
      return "square.grid.2x2";
  }
}
