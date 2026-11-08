import MijiaCore
import SwiftUI

/// Target temperature, mode, status, fault, remaining time and start/pause for the shared
/// appliance card. Every row renders only when the spec published that control (#300).
struct ApplianceDetailDrawer: View {
    let device: SmartDevice
    let spec: ApplianceSpec
    let store: HomeStore
    let accent: Color
    let statusColor: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let fault = spec.faultText, !fault.isEmpty {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill")
                    Text(fault)
                        .lineLimit(2)
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

            if let target = spec.targetTemperature, spec.temperatureWritable {
                temperatureStepper(target)
            }

            if !spec.availableModes.isEmpty, spec.modeWritable {
                ScrollView(.horizontal, showsIndicators: false) {
                    AdaptiveGlassContainer(spacing: 6) {
                        HStack(spacing: 6) {
                            ForEach(spec.availableModes, id: \.self) { mode in
                                Button(SmartDevice.localizeApplianceMode(mode)) {
                                    store.setApplianceMode(device.id, mode: mode)
                                }
                                .buttonStyle(PillActionButtonStyle(
                                    isSelected: spec.currentMode == mode,
                                    tintColor: accent
                                ))
                                .disabled(!device.isOnline || !spec.modeWritable)
                            }
                        }
                    }
                    .padding(6)
                }
                .padding(-6)
            }

            if let fan = spec.fanLevel, fan.writable, !fan.options.isEmpty {
                fanLevelPicker(fan)
            }

            if let light = spec.light, light.writable {
                HStack(spacing: 8) {
                    Image(systemName: light.isOn ? "lightbulb.fill" : "lightbulb")
                        .foregroundStyle(light.isOn ? accent : statusColor)
                    Text(L10n.tr("灯光", "Light"))
                        .font(.system(size: DesignTokens.TypeSize.caption, weight: .medium))
                    Spacer(minLength: 8)
                    Toggle("", isOn: Binding(
                        get: { light.isOn },
                        set: { store.setApplianceLightPower(device.id, isOn: $0) }
                    ))
                    .labelsHidden()
                    .toggleStyle(.switch)
                    .controlSize(.small)
                    .tint(accent)
                    .disabled(!device.isOnline)
                }
                .padding(.horizontal, 8)
            }

            if spec.roomReading, let humidity = spec.currentHumidity {
                statusRow(
                    title: L10n.tr("湿度", "Humidity"),
                    value: "\(Int(humidity.rounded()))%"
                )
            }

            if let status = spec.statusText, !status.isEmpty {
                statusRow(title: L10n.tr("状态", "Status"), value: status)
            }
            if let remaining = spec.remainingMinutes {
                statusRow(
                    title: L10n.tr("剩余时间", "Remaining"),
                    value: L10n.tr("\(remaining) 分钟", "\(remaining) min")
                )
            }

            if spec.startAction != nil || spec.pauseAction != nil || spec.stopAction != nil {
                HStack(spacing: 8) {
                    if let start = spec.startAction {
                        actionButton(start)
                    }
                    if let pause = spec.pauseAction {
                        actionButton(pause)
                    }
                    if let stop = spec.stopAction {
                        actionButton(stop)
                    }
                    Spacer(minLength: 0)
                }
            }
        }
    }

    private func temperatureStepper(_ target: Double) -> some View {
        HStack {
            Button {
                store.stepApplianceTemperature(device.id, delta: -L10n.celsiusStep(spec.step))
            } label: {
                Image(systemName: "minus")
                    .font(.system(size: DesignTokens.TypeSize.body, weight: .bold))
                    .frame(width: 32, height: 28)
                    .adaptiveGlass(in: Capsule(style: .continuous), interactive: true)
            }
            .buttonStyle(PressableCardButtonStyle())
            .accessibilityLabel(Text(L10n.tr("降低目标温度", "Lower target temperature")))
            .disabled(!device.isOnline || !spec.temperatureWritable)

            Spacer()

            VStack(spacing: 1) {
                Text(L10n.temperature(target))
                    .font(.system(size: DesignTokens.TypeSize.reading, weight: .bold, design: .rounded))
                    .monospacedDigit()
                if let current = spec.currentTemperature {
                    Text(L10n.format("当前 %@", "Now %@", L10n.temperature(current, fractionDigits: 1)))
                        .font(.system(size: DesignTokens.TypeSize.caption, weight: .medium))
                        .foregroundStyle(.secondary)
                } else {
                    Text(L10n.tr("目标温度", "Target Temp"))
                        .font(.system(size: DesignTokens.TypeSize.caption, weight: .medium))
                        .foregroundStyle(.secondary)
                }
            }

            Spacer()

            Button {
                store.stepApplianceTemperature(device.id, delta: L10n.celsiusStep(spec.step))
            } label: {
                Image(systemName: "plus")
                    .font(.system(size: DesignTokens.TypeSize.body, weight: .bold))
                    .frame(width: 32, height: 28)
                    .adaptiveGlass(in: Capsule(style: .continuous), interactive: true)
            }
            .buttonStyle(PressableCardButtonStyle())
            .accessibilityLabel(Text(L10n.tr("升高目标温度", "Raise target temperature")))
            .disabled(!device.isOnline || !spec.temperatureWritable)
        }
        .padding(.horizontal, 8)
    }

    private func fanLevelPicker(_ fan: ApplianceFanLevel) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            AdaptiveGlassContainer(spacing: 6) {
                HStack(spacing: 6) {
                    ForEach(fan.options, id: \.self) { level in
                        Button(SmartDevice.localizeApplianceFanLevel(level)) {
                            store.setApplianceFanLevel(device.id, level: level)
                        }
                        .buttonStyle(PillActionButtonStyle(
                            isSelected: fan.current == level,
                            tintColor: accent
                        ))
                        .disabled(!device.isOnline || !fan.writable)
                    }
                }
            }
            .padding(6)
        }
        .padding(-6)
    }

    private func statusRow(title: String, value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(title)
                .font(.system(size: DesignTokens.TypeSize.caption, weight: .medium))
                .foregroundStyle(.secondary)
            Spacer(minLength: 8)
            Text(value)
                .font(.system(size: DesignTokens.TypeSize.caption, weight: .semibold))
                .foregroundStyle(statusColor)
                .monospacedDigit()
                .lineLimit(1)
        }
    }

    private func actionButton(_ action: ApplianceAction) -> some View {
        Button(action.title) {
            store.runApplianceAction(device.id, action: action)
        }
        .buttonStyle(PillActionButtonStyle(tintColor: accent))
        .disabled(!device.isOnline)
    }
}
