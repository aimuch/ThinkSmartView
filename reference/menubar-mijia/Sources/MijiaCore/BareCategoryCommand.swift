import Foundation

/// GLOSSARY `BareCategoryCommand`: a command that names only a device category ("关灯",
/// "turn on the lights") with no room, device name, or scope word. Closing looks at what is on
/// right now and opening at what is off: nothing to change gets an honest answer, one device runs
/// directly, and several use the room the panel shows or wait for a tap on a candidate.
public enum BareCategoryCommand {
    /// The direction is part of the domain, not a parser flag. Closing answers with the devices that
    /// are on, opening with the ones that are off, and opening offers one fewer candidate because
    /// widening what runs costs more when it guesses wrong.
    public enum Direction: Equatable, Sendable {
        case close
        case open

        var operation: IntentOperation { .power(self == .open) }

        var candidateLimit: Int { self == .open ? 3 : 4 }

        var suggestionPrefix: String { self == .open ? "bare:on" : "bare:off" }
    }

    /// The room the panel is showing. Identity, not the display name, decides which devices are on
    /// the page: two accounts can each have a 客厅 (ADR-0010), so `关灯` must only touch the room
    /// whose id the panel selected.
    public struct Room: Equatable, Sendable {
        public let id: String
        public let name: String

        public init(id: String, name: String) {
            self.id = id
            self.name = name
        }
    }

    public enum Resolution: Equatable, Sendable {
        /// Nothing of the category would change; `room` is set when the panel's room had none.
        case none(room: String?)
        /// Run these now: the only device that would change, or every such device in the panel's room.
        case run([SmartDevice])
        /// Several would change and no room decides: offer these, most used first.
        case candidates([SmartDevice])
    }

    public static func resolve(
        candidates: [SmartDevice],
        panelRoom: Room?,
        usageOrder: [String] = []
    ) -> Resolution {
        guard !candidates.isEmpty else { return .none(room: nil) }
        if candidates.count == 1 { return .run(candidates) }
        if let room = panelRoom, !room.id.isEmpty {
            let inRoom = candidates.filter { $0.roomId == room.id }
            return inRoom.isEmpty ? .none(room: room.name) : .run(inRoom)
        }
        return .candidates(ranked(candidates, by: usageOrder))
    }

    /// Habit order first, catalog order for everything the user has not used yet.
    static func ranked(_ devices: [SmartDevice], by usageOrder: [String]) -> [SmartDevice] {
        guard !usageOrder.isEmpty else { return devices }
        let rank = Dictionary(usageOrder.enumerated().map { ($1, $0) }, uniquingKeysWith: min)
        return devices.enumerated()
            .sorted { left, right in
                let leftRank = rank[left.element.id] ?? Int.max
                let rightRank = rank[right.element.id] ?? Int.max
                return leftRank == rightRank ? left.offset < right.offset : leftRank < rightRank
            }
            .map(\.element)
    }

    /// What the command says when every device of the category is already in the requested state.
    public static func answerReply(
        direction: Direction,
        category: IntentDeviceCategory,
        room: String?
    ) -> String {
        let noun = category.spokenNoun
        switch direction {
        case .close:
            guard let room else {
                return L10n.format("%@都关着", "The %@ are all off", noun)
            }
            return L10n.format(
                "%@的%@都关着",
                "The %@ %@ are all off",
                L10n.displayRoomName(room),
                noun
            )
        case .open:
            guard let room else {
                return L10n.format("%@都开着", "The %@ are all on", noun)
            }
            return L10n.format(
                "%@的%@都开着",
                "The %@ %@ are all on",
                L10n.displayRoomName(room),
                noun
            )
        }
    }

    /// The device is in the state the command would change, but nothing here can change it (a
    /// read-only camera, a light without a writable power property). Claiming it is already done
    /// would be a false reassurance.
    public static func blockedReply(direction: Direction, category: IntentDeviceCategory) -> String {
        switch direction {
        case .close:
            return L10n.format(
                "%@开着，但无法关闭",
                "The %@ are on but cannot be switched off",
                category.spokenNoun
            )
        case .open:
            return L10n.format(
                "%@关着，但无法打开",
                "The %@ are off but cannot be switched on",
                category.spokenNoun
            )
        }
    }

    /// What a headless entry (Shortcut, URL) can say instead of running something ambiguous.
    public static func choiceReply(direction: Direction, _ devices: [SmartDevice]) -> String {
        let separator = L10n.isEnglish ? ", " : "、"
        let list = devices.map(\.name).joined(separator: separator)
        switch direction {
        case .close:
            return L10n.format(
                "开着：%@。请说出房间或设备名",
                "On: %@. Say a room or device name",
                list
            )
        case .open:
            return L10n.format(
                "关着：%@。请说出房间或设备名",
                "Off: %@. Say a room or device name",
                list
            )
        }
    }

    /// Up to the direction's candidate limit plus one chip that changes every candidate.
    public static func suggestions(
        direction: Direction,
        candidates: [SmartDevice],
        devices: [SmartDevice],
        scenes: [HomeScene]
    ) -> [AssistantSuggestion] {
        var chips: [AssistantSuggestion] = []
        for device in candidates.prefix(direction.candidateLimit) {
            let intent = DeviceIntent(targetId: device.id, operation: direction.operation)
            guard let title = UserHabitMemory.suggestionTitle(for: intent, devices: devices, scenes: scenes) else {
                continue
            }
            chips.append(AssistantSuggestion(
                id: "\(direction.suggestionPrefix):\(device.id)",
                title: title,
                intents: [intent]
            ))
        }
        let all = candidates.map { DeviceIntent(targetId: $0.id, operation: direction.operation) }
        chips.append(AssistantSuggestion(
            id: "\(direction.suggestionPrefix):all",
            title: direction == .open
                ? L10n.tr("全部打开", "Turn All On")
                : L10n.tr("全部关掉", "Turn All Off"),
            intents: all
        ))
        return chips
    }
}

extension IntentDeviceCategory {
    /// Noun used when a bare-category answer says what is already off ("灯都关着") or on ("灯都开着").
    public var spokenNoun: String {
        switch self {
        case .light: return L10n.tr("灯", "lights")
        case .climate: return L10n.tr("空调", "air conditioners")
        case .heater: return L10n.tr("电暖器", "heaters")
        case .fan: return L10n.tr("风扇", "fans")
        case .airPurifier: return L10n.tr("空气净化器", "air purifiers")
        case .dehumidifier: return L10n.tr("除湿机", "dehumidifiers")
        case .diffuser: return L10n.tr("香氛机", "diffusers")
        case .plug: return L10n.tr("插座", "plugs")
        case .vacuum: return L10n.tr("扫地机", "robot vacuums")
        case .speaker: return L10n.tr("音箱", "speakers")
        case .tv: return L10n.tr("电视", "TVs")
        case .fridge: return L10n.tr("冰箱", "fridges")
        case .camera: return L10n.tr("摄像机", "cameras")
        case .sensor: return L10n.tr("传感器", "sensors")
        case .cover: return L10n.tr("窗帘", "covers")
        case .appliance: return L10n.tr("家电", "appliances")
        case .generic: return L10n.tr("其他设备", "other devices")
        case .valve: return L10n.tr("阀门", "valves")
        }
    }
}
