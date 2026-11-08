import Foundation

public enum DeviceKindTag: String, Equatable, Sendable {
    case climate, light, plug, fan, vacuum, fridge, speaker, tv, camera, sensor, cover, appliance, generic

    public var rank: Int {
        switch self {
        case .climate: return 1
        case .light: return 2
        case .plug: return 3
        case .fan: return 4
        case .vacuum: return 5
        case .fridge: return 6
        case .speaker: return 7
        case .tv: return 8
        case .camera: return 9
        case .sensor: return 10
        case .cover: return 11
        case .appliance: return 12
        case .generic: return 13
        }
    }

    public var chipTitle: String {
        switch self {
        case .light: return L10n.tr("灯", "Lights")
        case .climate: return L10n.tr("空调", "Climate")
        case .fan: return L10n.tr("风扇", "Fan")
        case .plug: return L10n.tr("插座", "Plugs")
        case .fridge: return L10n.tr("冰箱", "Fridge")
        case .vacuum: return L10n.tr("扫地机", "Vacuum")
        case .speaker: return L10n.tr("音箱", "Speaker")
        case .tv: return L10n.tr("电视", "TV")
        case .camera: return L10n.tr("摄像机", "Camera")
        case .sensor: return L10n.tr("传感器", "Sensors")
        case .cover: return L10n.tr("门窗", "Windows")
        case .appliance: return L10n.tr("家电", "Appliances")
        case .generic: return L10n.tr("其他", "Other")
        }
    }

    public var chipHint: String? {
        switch self {
        case .climate:
            return L10n.tr(
                "也包括电暖器、温控器和电热毯",
                "Also heaters, thermostats, and electric blankets"
            )
        case .fan:
            return L10n.tr(
                "也包括净化器、新风、加湿器和除湿器",
                "Also purifiers, fresh-air units, humidifiers, and dehumidifiers"
            )
        case .plug:
            return L10n.tr(
                "也包括开关、香薰，以及带开关的小家电",
                "Also switches, diffusers, and small appliances with a power switch"
            )
        case .sensor:
            return L10n.tr(
                "也包括没有开关的小家电",
                "Also small appliances that have no power switch"
            )
        case .appliance:
            return L10n.tr(
                "目前包括热水器、洗衣机、烘干机与洗碗机",
                "Currently includes water heaters, washers, dryers, and dishwashers"
            )
        case .generic:
            return L10n.tr(
                "规格里没有专用类型、但仍有可控项的设备",
                "Devices with no dedicated type but at least one usable control"
            )
        case .light, .fridge, .vacuum, .speaker, .tv, .camera, .cover:
            return nil
        }
    }
}

extension DeviceKind {
    public var tag: DeviceKindTag {
        switch self {
        case .light: return .light
        case .climate: return .climate
        case .fan: return .fan
        case .plug: return .plug
        case .fridge: return .fridge
        case .vacuum: return .vacuum
        case .speaker: return .speaker
        case .tv: return .tv
        case .camera: return .camera
        case .sensor: return .sensor
        case .cover: return .cover
        case .appliance: return .appliance
        case .generic: return .generic
        case .humidifier: return .fan
        case .valve: return .plug
        }
    }
}

public enum DeviceListCut: Equatable, Sendable {
    case all
    case room(String)
    case kind(DeviceKindTag)

    func matches(_ device: SmartDevice) -> Bool {
        switch self {
        case .all:
            return true
        case .room(let id):
            return device.roomId == id
        case .kind(let tag):
            return device.kind.tag == tag
        }
    }
}
