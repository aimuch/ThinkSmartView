import MijiaCore
import SwiftUI

/// Position, open/stop/close, battery and fault for a cover (window opener, curtain or airer). An
/// airer reads as height with raise/stop/lower (#299). Kept out of `DeviceCardView` so that file
/// stays under the line-count gate (#184). Its slider and buttons render through the shared detail
/// controls, the same ones 更多设置 uses (#5).
struct CoverDetailDrawer: View {
    let device: SmartDevice
    let spec: CoverSpec
    let store: HomeStore
    let accent: Color
    let statusColor: Color

    var body: some View {
        let position = Double(spec.positionPercent ?? 0)
        VStack(alignment: .leading, spacing: 8) {
            if let motion = spec.motion.localizedLabel(for: spec.subtype) {
                Text(motion)
                    .font(.system(size: DesignTokens.TypeSize.caption, weight: .medium))
                    .foregroundStyle(statusColor)
            }
            if let fault = spec.faultText, !fault.isEmpty {
                Text(fault)
                    .font(.system(size: DesignTokens.TypeSize.caption, weight: .medium))
                    .foregroundStyle(.secondary)
            }
            if spec.positionWritable {
                DetailSliderControl(
                    value: position,
                    range: 0...100,
                    step: 1,
                    iconName: spec.subtype == .clothesAirer ? "hanger" : "window.vertical.open",
                    iconColor: statusColor,
                    trackColors: [accent.opacity(0.55), accent.opacity(0.92)],
                    formattedValue: "\(Int(position))%"
                ) { percent in
                    store.setCoverPosition(device.id, percent: Int(percent.rounded()))
                }
                .disabled(!device.isOnline)
            }
            if spec.canMove || spec.batteryPercent != nil {
                HStack(spacing: 8) {
                    if let open = spec.openDescription {
                        motorButton(open)
                    }
                    if let pause = spec.pauseDescription {
                        motorButton(pause)
                    }
                    if let close = spec.closeDescription {
                        motorButton(close)
                    }
                    if let battery = spec.batteryPercent {
                        Spacer(minLength: 0)
                        DetailValueText(
                            text: L10n.tr("电量 \(battery)%", "Battery \(battery)%"),
                            color: statusColor
                        )
                    }
                }
            }
        }
    }

    private func motorButton(_ description: String) -> some View {
        DetailPillButton(title: Self.actionTitle(description, subtype: spec.subtype)) {
            store.setCoverMotor(device.id, description: description)
        }
        .disabled(!device.isOnline)
    }

    static func actionTitle(_ description: String, subtype: CoverSubtype) -> String {
        let airer = subtype == .clothesAirer
        switch description.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "open", "打开": return L10n.tr("打开", "Open")
        case "close", "关闭": return L10n.tr("关闭", "Close")
        case "up", "raise": return airer ? L10n.tr("升", "Raise") : L10n.tr("升起", "Up")
        case "down", "lower": return airer ? L10n.tr("降", "Lower") : L10n.tr("降下", "Down")
        case "pause", "stop", "暂停": return airer ? L10n.tr("停", "Stop") : L10n.tr("暂停", "Pause")
        default: return description
        }
    }
}
