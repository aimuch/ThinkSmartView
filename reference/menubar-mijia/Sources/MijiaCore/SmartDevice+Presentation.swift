import Foundation

// MARK: - SmartDevice presentation
//
// Icons, subtitles and localized labels the card shows. Kept apart from the domain model in
// Models.swift so state and the words for it change independently.

extension SmartDevice {
    public var sfSymbolName: String {
        switch kind {
        case .light(let s):
            if model.contains("strip") {
                return s.isOn ? "light.strip.2.fill" : "light.strip.2"
            }
            return s.isOn ? "lightbulb.fill" : "lightbulb"
        case .climate(let s):
            if model.contains("blanket") { return "bed.double.fill" }
            if model.contains("heater") {
                return s.isOn ? "heater.vertical.fill" : "heater.vertical"
            }
            return s.isOn ? "snowflake" : "air.conditioner.horizontal"
        case .fan(let s):
            if model.contains(".derh.") { return "humidity.fill" }
            if model.contains("humidifier") || name.contains("加湿") { return "air.humidifier.fill" }
            if model.contains("airfresh") || name.contains("新风") { return "wind" }
            if model.contains("airp") || model.contains("purifier") || name.contains("净化")
                || s.pm25Density != nil || s.airQuality != nil || s.filterLifePercent != nil {
                return "aqi.medium"
            }
            return "fan.floor.fill"
        case .plug(let s):
            if model.contains(".diffuser.") { return "air.humidifier.fill" }
            if s.switchEntityId.hasPrefix("button.") {
                if s.switchEntityId.contains("wake_on_lan") || name.contains("电脑")
                    || name.localizedCaseInsensitiveContains("pc") || name.localizedCaseInsensitiveContains("mac") {
                    return "desktopcomputer"
                }
                return "power.circle.fill"
            }
            if name.contains("咖啡") {
                return s.isOn ? "cup.and.heat.waves.fill" : "cup.and.saucer.fill"
            }
            if name.contains("灯") {
                return s.isOn ? "lightbulb.fill" : "lightbulb"
            }
            if model.contains(".switch.") {
                return s.isOn ? "lightswitch.on.fill" : "lightswitch.off"
            }
            return s.isOn ? "powerplug.fill" : "powerplug"
        case .fridge:
            return "refrigerator.fill"
        case .vacuum:
            return "humidifier.and.droplets.fill" // clean appliance icon fallback handled in view
        case .speaker(let s):
            return s.isMuted ? "hifispeaker.slash.fill" : "hifispeaker.fill"
        case .tv:
            return "tv.fill"
        case .camera(let s):
            return s.isOn ? "web.camera.fill" : "web.camera"
        case .sensor(let s):
            if name.contains("门锁") || model.contains(".lock.") {
                return "lock.shield.fill"
            }
            if model.contains("kettle") || model.contains("waterdispenser") || name.contains("水壶") || name.contains("饮水") {
                return "mug.fill"
            }
            if model.contains("fryer") || model.contains("oven") || name.contains("炸锅") || name.contains("烤箱") {
                return "frying.pan.fill"
            }
            if model.contains("cooker") || name.contains("饭煲") || name.contains("电锅") {
                return "flame.fill"
            }
            if model.contains("motion") || model.contains("occupancy") || name.contains("人体") || name.contains("存在") {
                return "figure.walk.motion"
            }
            if s.temperature != nil {
                return "thermometer.medium"
            }
            return "sensor.fill"
        case .cover(let s):
            if s.subtype == .clothesAirer { return "hanger" }
            return (s.positionPercent ?? 0) > 0 ? "window.vertical.open" : "window.vertical.closed"
        case .appliance(let s):
            return s.symbolName ?? "water.waves"
        case .generic(let s):
            return s.symbolName ?? "square.grid.2x2"
        case .humidifier(let s):
            return s.isOn ? "humidity.fill" : "humidity"
        case .valve(let s):
            if s.deviceClass?.lowercased() == "gas" {
                return s.isOpen ? "flame.fill" : "flame"
            }
            return s.isOpen ? "drop.fill" : "drop"
        }
    }

    public var statusSubtitle: String {
        guard isOnline else { return L10n.tr("离线 · 未通电", "Offline · Unplugged") }
        switch kind {
        case .light(let s):
            if !s.isOn { return L10n.tr("已关闭", "Off") }
            var parts: [String] = []
            if let b = s.brightnessPercent, b > 0 {
                parts.append("\(b)%")
            } else {
                parts.append(L10n.tr("已开启", "On"))
            }
            if let effect = s.currentEffect, !effect.isEmpty {
                parts.append(Self.localizeEffect(effect))
            } else if let k = s.colorTempKelvin {
                parts.append("\(k)K")
            }
            return parts.joined(separator: " · ")

        case .climate(let s):
            if !s.isOn || s.hvacMode == "off" { return L10n.tr("已关闭", "Off") }
            let modeLabel = Self.localizeHVACMode(s.hvacMode)
            // Stored and stepped in Celsius; only the digits shown follow the display unit (#52).
            let tempStr = L10n.temperature(s.targetTemperature)
            if let cur = s.currentTemperature {
                return L10n.tr(
                    "\(modeLabel) \(tempStr) · 室温 \(L10n.temperature(cur))",
                    "\(modeLabel) \(tempStr) · Room \(L10n.temperature(cur))"
                )
            }
            return "\(modeLabel) · \(tempStr)"

        case .fan(let s):
            var envParts: [String] = []
            if let pm25 = s.pm25Density {
                if let aq = Self.localizeAirQuality(s.airQuality, pm25: pm25) {
                    envParts.append("PM2.5 \(pm25) \(aq)")
                } else {
                    envParts.append("PM2.5 \(pm25)")
                }
            } else if let aq = Self.localizeAirQuality(s.airQuality, pm25: nil) {
                envParts.append(aq)
            }
            if let t = s.temperature, let h = s.humidity {
                envParts.append("\(L10n.temperature(t, fractionDigits: 0)) \(Int(h.rounded()))%")
            } else if let t = s.temperature {
                envParts.append(L10n.temperature(t, fractionDigits: 1))
            } else if let h = s.humidity {
                envParts.append("\(Int(h.rounded()))%")
            }
            if !s.isOn {
                if envParts.isEmpty { return L10n.tr("已关闭", "Off") }
                return ([L10n.tr("已关闭", "Off")] + envParts).joined(separator: " · ")
            }
            var parts: [String] = []
            if let mode = s.presetMode { parts.append(Self.localizeFanPreset(mode)) }
            if let pct = s.percentage { parts.append("\(pct)%") }
            if parts.isEmpty { parts.append(L10n.tr("运行中", "Running")) }
            parts.append(contentsOf: envParts)
            return parts.joined(separator: " · ")

        case .plug(let s):
            if s.switchEntityId.hasPrefix("button.") {
                return L10n.tr("点击触发", "Tap to trigger")
            }
            if !s.isOn { return L10n.tr("已关闭", "Off") }
            if let w = s.powerWatts {
                if w >= 1 {
                    return L10n.tr(
                        String(format: "开启 · %.0fW", w),
                        String(format: "On · %.0fW", w)
                    )
                } else {
                    return L10n.tr("开启 · 待机 0W", "On · Standby 0W")
                }
            }
            return L10n.tr("已开启", "On")

        case .fridge(let s):
            if s.fridgeCurrentTemp == nil && s.freezerCurrentTemp == nil {
                let cStr = L10n.format("冷藏 %@", "Fridge %@", L10n.temperature(s.fridgeTargetTemp))
                let fStr = L10n.format("冷冻 %@", "Freezer %@", L10n.temperature(s.freezerTargetTemp))
                let mStr = Self.localizeFridgeMode(s.currentMode)
                return mStr.isEmpty ? "\(cStr) · \(fStr)" : "\(mStr) · \(cStr) · \(fStr)"
            }
            let cStr = s.fridgeCurrentTemp.map { L10n.format("冷藏 %@", "Fridge %@", L10n.temperature($0)) }
                ?? L10n.tr("冷藏 --", "Fridge --")
            let fStr = s.freezerCurrentTemp.map { L10n.format("冷冻 %@", "Freezer %@", L10n.temperature($0)) }
                ?? L10n.tr("冷冻 --", "Freezer --")
            return "\(cStr) · \(fStr)"

        case .vacuum(let s):
            let st: String = switch s.status {
            case "cleaning", "on": L10n.tr("清扫中", "Cleaning")
            case "returning": L10n.tr("回充途中", "Returning")
            case "paused": L10n.tr("已暂停", "Paused")
            case "docked": L10n.tr("充电待机", "Docked")
            default: L10n.tr("待机", "Standby")
            }
            if let bat = s.battery {
                return "\(st) · \(bat)%"
            }
            return st

        case .speaker(let s):
            let playStr = s.state == "playing" ? L10n.tr("播放中", "Playing") : L10n.tr("待机", "Standby")
            let volStr = s.isMuted ? L10n.tr("已静音", "Muted") : L10n.tr("音量 \(s.volumePercent)%", "Vol \(s.volumePercent)%")
            return "\(playStr) · \(volStr)"

        case .tv(let s):
            if s.isPairingPromptActive {
                return L10n.tr("请在电视上点击「允许」配对", "Accept pairing prompt on TV")
            }
            if s.state == "off" || s.state == "standby" {
                return L10n.tr("已关机 · 可唤醒", "Standby · Ready to Wake")
            }
            var parts: [String] = []
            if let src = s.currentSource, !src.isEmpty {
                parts.append(src)
            } else {
                parts.append(L10n.tr("在线", "Online"))
            }
            if s.isScreenOff {
                parts.append(L10n.tr("已熄屏", "Screen Off"))
            }
            parts.append(s.isMuted ? L10n.tr("已静音", "Muted") : L10n.tr("音量 \(s.volumePercent)%", "Vol \(s.volumePercent)%"))
            return parts.joined(separator: " · ")

        case .camera(let s):
            if !s.isOn { return L10n.tr("休眠中 · 点击唤醒", "Sleeping · Click to Wake") }
            var parts: [String] = [L10n.tr("监控中", "Monitoring")]
            if let night = s.nightShot, !night.isEmpty {
                parts.append(Self.localizeNightShot(night))
            }
            if s.isMotionDetectionOn == true {
                parts.append(L10n.tr("侦测开", "Motion On"))
            }
            parts.append(L10n.tr("点击看画面", "Live View"))
            return parts.joined(separator: " · ")

        case .sensor(let s):
            if let t = s.temperature, let h = s.humidity {
                return "\(L10n.temperature(t, fractionDigits: 1)) · \(String(format: "%.0f%%", h))"
            }
            if let text = s.statusText {
                if let bat = s.battery {
                    return L10n.tr("\(text) · 电量 \(bat)%", "\(text) · Battery \(bat)%")
                }
                return text
            }
            return L10n.tr("在线", "Online")
        case .cover(let s):
            if let fault = s.faultText, !fault.isEmpty { return fault }
            if let motion = s.motion.localizedLabel(for: s.subtype) { return motion }
            if s.subtype == .clothesAirer, let pos = s.positionPercent {
                if pos <= 0 { return L10n.tr("已降到底", "Fully lowered") }
                if pos >= 100 { return L10n.tr("已升到顶", "Fully raised") }
                return L10n.tr("高度 \(pos)%", "Height \(pos)%")
            }
            if let pos = s.positionPercent {
                if pos <= 0 { return L10n.tr("已关上", "Closed") }
                if pos >= 100 { return L10n.tr("已打开", "Open") }
                return L10n.tr("开 \(pos)%", "Open \(pos)%")
            }
            return L10n.tr("可控制", "Ready")

        case .appliance(let s):
            var parts: [String] = []
            if let current = s.currentTemperature {
                parts.append(L10n.temperature(current, fractionDigits: 1))
            }
            if let target = s.targetTemperature {
                parts.append(L10n.format("目标 %@", "Target %@", L10n.temperature(target)))
            }
            if let mode = s.currentMode, !mode.isEmpty {
                parts.append(Self.localizeApplianceMode(mode))
            }
            if let level = s.fanLevel?.current, !level.isEmpty {
                parts.append(Self.localizeApplianceFanLevel(level))
            }
            if let status = s.statusText, !status.isEmpty {
                parts.append(status)
            }
            if let remaining = s.remainingMinutes {
                parts.append(L10n.tr("剩余 \(remaining) 分", "\(remaining) min left"))
            }
            if let fault = s.faultText, !fault.isEmpty {
                parts.append(L10n.format("故障 %@", "Fault %@", fault))
            }
            return parts.isEmpty ? (s.isOn ? L10n.tr("运行中", "Running") : L10n.tr("已关闭", "Off"))
                : parts.joined(separator: " · ")

        case .generic(let s):
            var parts = s.alerts
            parts.append(contentsOf: s.readouts.map(\.value))
            let readings = parts.joined(separator: " · ")
            if !readings.isEmpty { return readings }
            if s.isReadOnly {
                return L10n.format("只读 · %@", "Read only · %@", s.typeTitle)
            }
            return s.typeTitle
        case .humidifier(let s):
            guard s.isOn else { return L10n.tr("已关闭", "Off") }
            var parts: [String] = []
            if let mode = s.currentMode, !mode.isEmpty {
                parts.append(Self.localizeApplianceMode(mode))
            }
            if let target = s.targetHumidity {
                parts.append(L10n.tr("目标 \(target)%", "Target \(target)%"))
            }
            if let current = s.currentHumidity {
                parts.append(L10n.tr("当前 \(Int(current.rounded()))%", "Now \(Int(current.rounded()))%"))
            }
            return parts.isEmpty ? L10n.tr("运行中", "Running") : parts.joined(separator: " · ")

        case .valve(let s):
            if let motion = s.motion {
                switch motion {
                case "opening": return L10n.tr("正在打开", "Opening")
                case "closing": return L10n.tr("正在关闭", "Closing")
                default: break
                }
            }
            return s.isOpen ? L10n.tr("已打开", "Open") : L10n.tr("已关闭", "Closed")
        }
    }

    public static func localizeEffect(_ effect: String) -> String {
        switch effect {
        case "Day": return L10n.tr("日光", "Daylight")
        case "Night": return L10n.tr("月光", "Moonlight")
        case "None": return L10n.tr("无", "None")
        case "Warmth": return L10n.tr("温馨", "Warmth")
        case "Tv", "TV": return L10n.tr("观影", "TV")
        case "Reading": return L10n.tr("阅读", "Reading")
        case "Computer": return L10n.tr("电脑", "Computer")
        case "Hospitality": return L10n.tr("会客", "Hospitality")
        case "Entertainment": return L10n.tr("娱乐", "Entertainment")
        case "Wakeup": return L10n.tr("起床", "Wake Up")
        case "Dusk": return L10n.tr("黄昏", "Dusk")
        case "Sleep": return L10n.tr("睡眠", "Sleep")
        default: return effect
        }
    }

    public static func localizeHVACMode(_ mode: String) -> String {
        switch mode {
        case "cool": return L10n.tr("制冷", "Cool")
        case "heat": return L10n.tr("制热", "Heat")
        case "dry": return L10n.tr("除湿", "Dry")
        case "fan_only": return L10n.tr("送风", "Fan")
        case "auto", "heat_cool": return L10n.tr("自动", "Auto")
        case "off": return L10n.tr("关闭", "Off")
        default: return mode
        }
    }

    public static func localizeFanPreset(_ mode: String) -> String {
        switch mode.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "auto", "automatic": return L10n.tr("自动", "Auto")
        case "sleep": return L10n.tr("睡眠", "Sleep")
        case "smart": return L10n.tr("智能", "Smart")
        case "clothes drying": return L10n.tr("干衣", "Clothes Drying")
        case "favorite", "favourite": return L10n.tr("最爱", "Favorite")
        case "manual", "none": return L10n.tr("手动", "Manual")
        case "silent": return L10n.tr("静音", "Silent")
        default: return mode
        }
    }

    public static func localizeApplianceMode(_ mode: String) -> String {
        switch mode.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "standard", "normal", "标准": return L10n.tr("标准", "Standard")
        case "energy1", "eco", "节能": return L10n.tr("节能①", "Energy 1")
        case "energy2": return L10n.tr("节能②", "Energy 2")
        case "quick", "fast", "快速": return L10n.tr("快速", "Quick")
        case "electricheating", "electric heating", "电加热": return L10n.tr("电加热", "Electric Heating")
        case "auto", "automatic", "自动": return L10n.tr("自动", "Auto")
        case "sleep", "night", "睡眠": return L10n.tr("睡眠", "Sleep")
        case "strong", "high", "强力", "强": return L10n.tr("强力", "Strong")
        case "baby", "婴儿": return L10n.tr("婴儿", "Baby")
        case "manual", "手动": return L10n.tr("手动", "Manual")
        case "off", "关闭": return L10n.tr("关闭", "Off")
        case "idle", "待机": return L10n.tr("待机", "Idle")
        case "working", "工作中": return L10n.tr("工作中", "Working")
        case "shower", "bath", "洗浴", "沐浴": return L10n.tr("洗浴", "Shower")
        case "dry", "dryer", "干燥": return L10n.tr("干燥", "Dry")
        case "defog", "除雾": return L10n.tr("除雾", "Defog")
        case "quick defog", "快速除雾": return L10n.tr("快速除雾", "Quick Defog")
        case "heat": return L10n.tr("弱暖", "Heat")
        case "quick heat": return L10n.tr("强暖", "Quick Heat")
        case "ventilate", "ventilation", "换气": return L10n.tr("换气", "Ventilate")
        case "fan", "blow", "吹风": return L10n.tr("吹风", "Fan")
        case "cool", "凉风": return L10n.tr("凉风", "Cool")
        case "deodorize", "除味": return L10n.tr("除味", "Deodorize")
        case "other", "其他": return L10n.tr("其他", "Other")
        default: return mode
        }
    }

    /// A hood's `fan-level` list uses `Level1…Level10` or `Low`/`High`/`Stir Fry`.
    public static func localizeApplianceFanLevel(_ level: String) -> String {
        let trimmed = level.trimmingCharacters(in: .whitespacesAndNewlines)
        switch trimmed.lowercased() {
        case "low", "低速": return L10n.tr("低速", "Low")
        case "mid", "middle", "中速": return L10n.tr("中速", "Mid")
        case "high", "高速": return L10n.tr("高速", "High")
        case "stir fry", "stir-fry", "爆炒": return L10n.tr("爆炒", "Stir Fry")
        default:
            let lower = trimmed.lowercased()
            guard lower.hasPrefix("level"), let value = Int(lower.dropFirst("level".count)) else {
                return trimmed
            }
            return L10n.tr("\(value)档", "Level \(value)")
        }
    }

    public static func localizeFridgeMode(_ mode: String) -> String {
        switch mode.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "none", "manual": return L10n.tr("手动", "Manual")
        case "smart", "auto": return L10n.tr("智能", "Smart")
        case "holiday": return L10n.tr("假日", "Holiday")
        case "quick cooling", "super cool": return L10n.tr("速冷", "Super Cool")
        case "quick frozen", "super freeze": return L10n.tr("速冻", "Super Freeze")
        default: return mode
        }
    }

    /// The label for a purifier's air quality: a known raw grade wins, then the PM2.5 band.
    /// An unknown raw grade is shown as reported.
    public static func localizeAirQuality(_ raw: String?, pm25: Int?) -> String? {
        if let raw, !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return AirQualityLevel(raw: raw)?.label ?? raw
        }
        return pm25.map { AirQualityLevel(pm25: $0).label }
    }

    public static func localizeNightShot(_ mode: String) -> String {
        switch mode.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "off", "close": return L10n.tr("夜视关", "Night Off")
        case "on", "open": return L10n.tr("夜视开", "Night On")
        case "auto", "automatic": return L10n.tr("自动夜视", "Auto Night")
        case "auto full color", "smart full color", "auto smart full color":
            return L10n.tr("智能全彩", "Smart Color")
        case "on full color", "full color":
            return L10n.tr("全彩夜视", "Full Color")
        default: return mode
        }
    }
}

/// One air-quality grade, read from a device's raw grade word or from PM2.5 (µg/m³).
/// The label and the card's colour both come from here, so a raw "优" can no longer be labelled
/// excellent and coloured poor. Mirrored by `airQualityLevel` in windows/src/core/models.ts and
/// pinned by Tests/Fixtures/air_quality_golden.json.
public enum AirQualityLevel: String, CaseIterable, Sendable {
    case excellent, good, moderate, poor, heavy, hazardous

    /// The colour family a card paints the grade in.
    public enum Tone: Sendable { case good, moderate, poor }

    public init?(raw: String) {
        switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "excellent", "空气优", "优": self = .excellent
        case "good", "空气良", "良": self = .good
        case "moderate", "轻度污染": self = .moderate
        case "poor", "中度污染": self = .poor
        case "heavy pollution", "heavy", "重度污染": self = .heavy
        case "hazardous", "严重污染": self = .hazardous
        default: return nil
        }
    }

    public init(pm25: Int) {
        switch pm25 {
        case ...35: self = .excellent
        case ...75: self = .good
        case ...115: self = .moderate
        case ...150: self = .poor
        case ...250: self = .heavy
        default: self = .hazardous
        }
    }

    /// A known raw grade wins over PM2.5, the same precedence as the label.
    public init?(raw: String?, pm25: Int?) {
        if let raw, !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            guard let level = AirQualityLevel(raw: raw) else { return nil }
            self = level
        } else if let pm25 {
            self.init(pm25: pm25)
        } else {
            return nil
        }
    }

    public var label: String {
        switch self {
        case .excellent: return L10n.tr("优", "Excellent")
        case .good: return L10n.tr("良", "Good")
        case .moderate: return L10n.tr("轻度污染", "Moderate")
        case .poor: return L10n.tr("中度污染", "Poor")
        case .heavy: return L10n.tr("重度污染", "Heavy")
        case .hazardous: return L10n.tr("严重污染", "Hazardous")
        }
    }

    public var tone: Tone {
        switch self {
        case .excellent, .good: return .good
        case .moderate: return .moderate
        case .poor, .heavy, .hazardous: return .poor
        }
    }
}
