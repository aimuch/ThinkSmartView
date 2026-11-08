import Foundation

/// Display text for a generic facet, shared by the card's readouts and the 更多设置 drawer (#5, #6).
/// Keeping it in `MijiaCore` means the synthesized card and the drawer cannot format the same
/// value differently.
public enum MiotFacetText {
    /// Common MIoT short names. A localized title here beats the spec's vendor description.
    private static let chineseNames: [String: String] = [
        "on": "开关",
        "mode": "模式",
        "fan-level": "风速",
        "fan-speed": "风速",
        "brightness": "亮度",
        "color-temperature": "色温",
        "target-temperature": "目标温度",
        "target-temp": "目标温度",
        "temperature": "温度",
        "relative-humidity": "湿度",
        "humidity": "湿度",
        "target-humidity": "目标湿度",
        "current-humidity": "当前湿度",
        "pm2.5-density": "PM2.5",
        "battery-level": "电量",
        "fault": "故障",
        "status": "状态",
        "volume": "音量",
        "mute": "静音",
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
        "alarm": "告警",
        "working-time": "运行时长",
    ]

    public static func title(name: String, title: String?) -> String {
        if !L10n.isEnglish, let chinese = chineseNames[name] {
            return chinese
        }
        let raw = title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return raw.isEmpty ? humanized(name) : raw
    }

    public static func humanized(_ raw: String) -> String {
        let words = raw.replacingOccurrences(of: "-", with: " ")
        guard let first = words.first else { return raw }
        return first.uppercased() + String(words.dropFirst())
    }

    public static func value(_ control: FacetControl) -> String {
        value(
            kind: control.kind,
            value: control.value,
            unit: control.unit,
            scale: control.scale,
            options: control.options
        )
    }

    public static func value(
        kind: MiotFacetKind,
        value: JSONValue?,
        unit: String?,
        scale: Double?,
        options: [MiotFacetOption]
    ) -> String {
        if let raw = value?.intValue, let option = options.first(where: { $0.value == raw }) {
            return option.label
        }
        if kind == .toggle {
            return value?.miotBool == true ? L10n.tr("开", "On") : L10n.tr("关", "Off")
        }
        guard let raw = value?.doubleValue else {
            if let isOn = value?.miotBool {
                return isOn ? L10n.tr("开", "On") : L10n.tr("关", "Off")
            }
            if let text = value?.stringValue { return text }
            return "—"
        }
        let scaled = raw * (scale ?? 1)
        if kind == .reading, unit?.lowercased() == "celsius" {
            return L10n.temperature(scaled, fractionDigits: 1)
        }
        return number(scaled) + unitSuffix(unit)
    }

    private static func number(_ value: Double) -> String {
        if value.rounded() == value, let exact = Int(exactly: value) { return "\(exact)" }
        return String(format: "%.1f", value)
    }

    private static func unitSuffix(_ raw: String?) -> String {
        let unit = raw?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        guard !unit.isEmpty, unit.lowercased() != "none" else { return "" }
        switch unit.lowercased() {
        case "celsius": return " °C"
        case "percentage", "percent": return "%"
        case "ppm": return " ppm"
        case "lux": return " lx"
        case "minutes": return " min"
        case "seconds": return " s"
        case "hour", "hours": return " h"
        default: return " \(unit)"
        }
    }
}
