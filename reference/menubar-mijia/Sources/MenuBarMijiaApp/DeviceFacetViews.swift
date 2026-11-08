import Foundation
import MijiaCore
import SwiftUI

// MARK: - Shared Detail Controls (#5)

/// A reading value in the drawer's right column. Shared by the cover battery and generic facets.
struct DetailValueText: View {
    let text: String
    var color: Color = .secondary
    var size: CGFloat = DesignTokens.TypeSize.caption
    var weight: Font.Weight = .medium

    var body: some View {
        Text(text)
            .font(.system(size: size, weight: weight))
            .foregroundStyle(color)
            .monospacedDigit()
            .lineLimit(1)
    }
}

/// A pill button in a detail drawer. `tint` nil keeps the shared accent colour.
struct DetailPillButton: View {
    let title: String
    var tint: Color?
    let action: () -> Void

    var body: some View {
        Button(title, action: action)
            .buttonStyle(PillActionButtonStyle(tintColor: tint ?? .accentColor))
    }
}

/// The glass slider shared by the cover position row and generic sliders.
struct DetailSliderControl: View {
    let value: Double
    let range: ClosedRange<Double>
    var step: Double?
    let iconName: String
    var iconColor: Color = .secondary
    var trackColors: [Color]
    let formattedValue: String
    let onChange: (Double) -> Void

    var body: some View {
        LiquidGlassSlider(
            value: Binding(get: { value }, set: onChange),
            in: range,
            step: step,
            iconName: iconName,
            iconColor: iconColor,
            trackFill: LinearGradient(
                colors: trackColors,
                startPoint: .leading,
                endPoint: .trailing
            ),
            formattedValue: formattedValue
        )
    }
}

// MARK: - Generic fallback drawer (#6)

/// The drawer above 更多设置 for a card with no dedicated type. A read-only device says so in
/// words instead of showing controls it cannot offer; a writable one explains why the main switch
/// is missing when no low-risk `on` exists.
struct GenericDetailDrawer: View {
    let spec: GenericSpec
    let statusColor: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(spec.alerts, id: \.self) { alert in
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill")
                    Text(alert)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .font(.system(size: DesignTokens.TypeSize.caption, weight: .semibold))
                .foregroundStyle(.red)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 8)
                .padding(.vertical, 6)
                .background(
                    RoundedRectangle(cornerRadius: DesignTokens.Radius.inset, style: .continuous)
                        .fill(Color.red.opacity(0.10))
                )
            }
            if spec.isReadOnly {
                explanation(
                    L10n.tr(
                        "该设备只向云端开放读数，不能从这里控制。",
                        "This device only exposes readings and cannot be controlled here."
                    ),
                    systemImage: "lock.fill"
                )
            } else if spec.powerControl == nil {
                explanation(
                    L10n.tr(
                        "没有低风险的主开关；其余控件在下方「更多设置」，高风险写入默认保持只读。",
                        "No low-risk main switch. The remaining controls are under More Settings; high-risk writes stay read-only by default."
                    ),
                    systemImage: "lock.shield"
                )
            }
        }
    }

    private func explanation(_ text: String, systemImage: String) -> some View {
        Label(text, systemImage: systemImage)
            .font(.system(size: DesignTokens.TypeSize.caption, weight: .medium))
            .foregroundStyle(statusColor)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - 更多设置

/// Generic drawer rows synthesized from the MIoT spec (#5). The section starts folded, keeps the
/// busiest rows behind a second fold, and explains every write the whitelist keeps closed.
struct DeviceFacetSection: View {
    let device: SmartDevice
    let store: HomeStore
    let accent: Color
    let statusColor: Color

    @State private var isExpanded: Bool
    @State private var showsRest: Bool
    /// Portion picked per feeding action; the spec's minimum until the user changes it (#7).
    @State private var portionSelections: [String: Int] = [:]

    init(
        device: SmartDevice,
        store: HomeStore,
        accent: Color,
        statusColor: Color,
        initiallyExpanded: Bool = false,
        initiallyShowsRest: Bool = false
    ) {
        self.device = device
        self.store = store
        self.accent = accent
        self.statusColor = statusColor
        _isExpanded = State(initialValue: initiallyExpanded)
        _showsRest = State(initialValue: initiallyShowsRest)
    }

    private var controls: [FacetControl] {
        store.deviceFacets[device.id] ?? []
    }

    /// High-value facets first, then the rest in spec order. `sorted` is not stable.
    private var ordered: [FacetControl] {
        let all = controls
        return all.filter(\.isHighValue) + all.filter { !$0.isHighValue }
    }

    private var visibleLimit: Int {
        max(1, DeviceProfiles.current.facets.maxVisible)
    }

    private var visible: [FacetControl] {
        Array(ordered.prefix(visibleLimit))
    }

    private var folded: [FacetControl] {
        Array(ordered.dropFirst(visibleLimit))
    }

    private var duplicateTitles: Set<String> {
        var seen: Set<String> = []
        var repeated: Set<String> = []
        for control in ordered {
            let title = FacetLabel.title(control)
            if !seen.insert(title).inserted { repeated.insert(title) }
        }
        return repeated
    }

    var body: some View {
        if controls.isEmpty {
            EmptyView()
        } else {
            VStack(alignment: .leading, spacing: 8) {
                header
                if isExpanded {
                    ForEach(visible) { control in
                        row(control)
                    }
                    if !folded.isEmpty {
                        restToggle
                        if showsRest {
                            ForEach(folded) { control in
                                row(control)
                            }
                        }
                    }
                }
            }
        }
    }

    private var header: some View {
        Button {
            isExpanded.toggle()
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "slider.horizontal.3")
                    .font(.system(size: DesignTokens.TypeSize.caption, weight: .semibold))
                    .foregroundStyle(statusColor)
                Text(L10n.tr("更多设置", "More Settings"))
                    .font(.system(size: DesignTokens.TypeSize.caption, weight: .semibold))
                Text("\(controls.count)")
                    .font(.system(size: DesignTokens.TypeSize.control, weight: .medium))
                    .foregroundStyle(.secondary)
                    .monospacedDigit()
                Spacer(minLength: 8)
                Image(systemName: "chevron.right")
                    .font(.system(size: DesignTokens.TypeSize.control, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .rotationEffect(.degrees(isExpanded ? 90 : 0))
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(Text(L10n.tr("更多设置", "More Settings")))
        .accessibilityValue(Text(isExpanded
            ? L10n.tr("已展开", "Expanded")
            : L10n.tr("已收起", "Collapsed")))
        .panelHelp(L10n.tr("显示辅助控件", "Show auxiliary controls"))
    }

    private var restToggle: some View {
        Button {
            showsRest.toggle()
        } label: {
            HStack(spacing: 4) {
                Text(L10n.tr(
                    "还有 \(folded.count) 项次要设置",
                    "\(folded.count) more settings"
                ))
                .font(.system(size: DesignTokens.TypeSize.control, weight: .medium))
                .foregroundStyle(.secondary)
                Spacer(minLength: 0)
                Image(systemName: "chevron.down")
                    .font(.system(size: DesignTokens.TypeSize.micro, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .rotationEffect(.degrees(showsRest ? 180 : 0))
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private func row(_ control: FacetControl) -> some View {
        let title = displayTitle(control)
        let enabled = isEnabled(control)
        let reason = disabledReason(control)
        switch control.kind {
        case .toggle:
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.system(size: DesignTokens.TypeSize.caption, weight: .medium))
                    if let reason { reasonText(reason) }
                }
                Spacer(minLength: 8)
                Toggle("", isOn: Binding(
                    get: { control.isOn ?? false },
                    set: { store.setFacetValue(device.id, controlId: control.id, value: .bool($0)) }
                ))
                .labelsHidden()
                .accessibilityLabel(Text(title))
                .toggleStyle(.glass)
                .controlSize(.mini)
                .disabled(!enabled)
            }

        case .select:
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(title)
                        .font(.system(size: DesignTokens.TypeSize.caption, weight: .medium))
                    Spacer(minLength: 8)
                    Picker("", selection: Binding(
                        get: { control.value?.intValue ?? control.options.first?.value ?? 0 },
                        set: { store.setFacetValue(device.id, controlId: control.id, value: .number(Double($0))) }
                    )) {
                        ForEach(control.options, id: \.value) { option in
                            Text(option.label).tag(option.value)
                        }
                    }
                    .labelsHidden()
                    .accessibilityLabel(Text(title))
                    .pickerStyle(.menu)
                    .fixedSize()
                    .disabled(!enabled)
                }
                if let reason { reasonText(reason) }
            }

        case .slider:
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.system(size: DesignTokens.TypeSize.caption, weight: .medium))
                if let range = control.range {
                    DetailSliderControl(
                        value: control.numberValue ?? range.min,
                        range: range.min...range.max,
                        step: range.step > 0 ? range.step : nil,
                        iconName: FacetLabel.icon(control.name),
                        iconColor: statusColor,
                        trackColors: [accent.opacity(0.55), accent.opacity(0.92)],
                        formattedValue: FacetLabel.value(control)
                    ) { newValue in
                        store.setFacetValue(device.id, controlId: control.id, value: .number(newValue))
                    }
                    .disabled(!enabled)
                    .accessibilityLabel(Text(title))
                }
                if let reason { reasonText(reason) }
            }

        case .reading:
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(title)
                    .font(.system(size: DesignTokens.TypeSize.caption, weight: .medium))
                    .foregroundStyle(.secondary)
                Spacer(minLength: 8)
                DetailValueText(text: FacetLabel.value(control), color: statusColor)
            }

        case .button:
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    DetailPillButton(title: title, tint: accent) {
                        store.runFacetAction(
                            device.id,
                            controlId: control.id,
                            portion: portion(for: control)
                        )
                    }
                    .disabled(!enabled)
                    if let values = portionValues(control) {
                        Picker("", selection: Binding(
                            get: { portion(for: control) },
                            set: { portionSelections[control.id] = $0 }
                        )) {
                            ForEach(values, id: \.self) { value in
                                Text(L10n.format("%@ 份", "%@ portion(s)", "\(value)")).tag(value)
                            }
                        }
                        .labelsHidden()
                        .pickerStyle(.menu)
                        .fixedSize()
                        .disabled(!enabled)
                        .accessibilityLabel(Text(L10n.tr("投喂份数", "Portions")))
                    }
                    Spacer(minLength: 0)
                }
                if let fedToday = control.fedToday {
                    Text(L10n.format("今日已投喂 %@", "Fed today %@", fedToday))
                        .font(.system(size: DesignTokens.TypeSize.micro, weight: .medium))
                        .foregroundStyle(.secondary)
                }
                if let reason { reasonText(reason) }
            }
        }
    }

    /// The portions the spec's feeding input allows, or nil for an ordinary button.
    private func portionValues(_ control: FacetControl) -> [Int]? {
        let values = control.portionOptions
        return values.isEmpty ? nil : values
    }

    private func portion(for control: FacetControl) -> Int {
        if let chosen = portionSelections[control.id] { return chosen }
        return portionValues(control)?.first ?? 1
    }

    private func displayTitle(_ control: FacetControl) -> String {
        let title = FacetLabel.title(control)
        guard duplicateTitles.contains(title) else { return title }
        return "\(title) · \(FacetLabel.service(control.serviceName))"
    }

    private func isEnabled(_ control: FacetControl) -> Bool {
        guard control.isWritable else { return false }
        return device.isOnline
            && (control.writesByDefault || control.isConfirmedWrite || store.allowAdvancedFacetWrites)
    }

    private func disabledReason(_ control: FacetControl) -> String? {
        guard control.isWritable,
              !control.isConfirmedWrite,
              !control.writesByDefault,
              !store.allowAdvancedFacetWrites else {
            return nil
        }
        return L10n.tr(
            "不在默认写入白名单，可在设置中允许写入",
            "Outside the write whitelist; allow writes in Settings"
        )
    }

    private func reasonText(_ reason: String) -> some View {
        Label(reason, systemImage: "lock.fill")
            .font(.system(size: DesignTokens.TypeSize.micro, weight: .medium))
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
    }
}

// MARK: - Facet labels

enum FacetLabel {
    private static let chineseServices: [String: String] = [
        "fan-control": "风扇",
        "environment": "环境",
        "fresh-air": "新风",
        "floor-heat": "地暖",
        "other": "其他",
        "setting": "设置",
        "setmode": "遥控设置",
        "light": "灯光",
        "indicator-light": "指示灯",
        "physical-controls-locked": "按键锁",
        "memory-card-management": "存储卡",
        "motion-detection": "移动侦测",
        "p2p-stream": "视频流",
        "speaker": "扬声器",
        "cockpit-service": "车载服务",
        "air-purifier": "净化器",
        "air-fresh": "新风",
        "custom": "自定义",
        "battery": "电池",
        "vacuum": "扫地机",
        "zone-state-one": "播放分区",
        "device-state": "设备状态",
        "device-control": "按键",
        "play-control": "播放",
        "television": "电视",
        "message-router": "消息",
        "power-consumption": "功耗",
        "ptz-control": "云台",
    ]

    static func title(_ control: FacetControl) -> String {
        MiotFacetText.title(name: control.name, title: control.title)
    }

    static func service(_ name: String) -> String {
        if !L10n.isEnglish, let chinese = chineseServices[name] {
            return chinese
        }
        return humanized(name)
    }

    static func humanized(_ raw: String) -> String {
        MiotFacetText.humanized(raw)
    }

    static func value(_ control: FacetControl) -> String {
        MiotFacetText.value(control)
    }

    static func icon(_ name: String) -> String {
        switch name {
        case "brightness": return "sun.max.fill"
        case "color-temperature": return "thermometer.sun.fill"
        case "target-temperature", "target-temp", "temperature": return "thermometer.medium"
        case "humidity", "target-humidity", "current-humidity", "relative-humidity": return "humidity.fill"
        case "fan-level", "fan-speed": return "wind"
        case "volume": return "speaker.wave.2.fill"
        case "target-position", "current-position": return "window.vertical.open"
        default: return "slider.horizontal.3"
        }
    }
}
