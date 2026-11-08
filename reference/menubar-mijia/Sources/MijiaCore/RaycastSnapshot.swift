import Foundation

// MARK: - Raycast Companion Snapshot Contract (ADR-0013, ADR-0004)

/// Execution result written back to `raycast-snapshot.json` after a `menumijia://` command finishes.
public struct RaycastCommandFeedback: Codable, Equatable, Sendable {
    public var requestId: String?
    public var command: String
    public var message: String
    public var isError: Bool
    public var completedAt: String

    public init(
        requestId: String? = nil,
        command: String,
        message: String,
        isError: Bool = false,
        completedAt: String
    ) {
        self.requestId = requestId
        self.command = command
        self.message = message
        self.isError = isError
        self.completedAt = completedAt
    }
}

/// Fine-grained device capabilities exported so Raycast can offer category-accurate preset actions.
public struct RaycastDeviceCapabilities: Codable, Equatable, Sendable {
    public var canTogglePower: Bool
    public var brightnessWritable: Bool
    public var brightnessPercent: Int?
    public var colorWritable: Bool
    public var colorTempKelvin: Int?
    public var minKelvin: Int?
    public var maxKelvin: Int?
    public var supportsMoonlight: Bool
    public var availableEffects: [String]
    public var temperatureWritable: Bool
    public var targetTemperature: Double?
    public var currentTemperature: Double?
    public var minTemp: Double?
    public var maxTemp: Double?
    public var step: Double?
    public var hvacMode: String?
    public var availableModes: [String]
    public var fanSpeedWritable: Bool
    public var fanPercentage: Int?
    public var availablePresetModes: [String]
    public var coverSubtype: String?
    public var positionWritable: Bool
    public var positionPercent: Int?
    public var canMoveCover: Bool
    public var isHighRisk: Bool

    public init(
        canTogglePower: Bool = false,
        brightnessWritable: Bool = false,
        brightnessPercent: Int? = nil,
        colorWritable: Bool = false,
        colorTempKelvin: Int? = nil,
        minKelvin: Int? = nil,
        maxKelvin: Int? = nil,
        supportsMoonlight: Bool = false,
        availableEffects: [String] = [],
        temperatureWritable: Bool = false,
        targetTemperature: Double? = nil,
        currentTemperature: Double? = nil,
        minTemp: Double? = nil,
        maxTemp: Double? = nil,
        step: Double? = nil,
        hvacMode: String? = nil,
        availableModes: [String] = [],
        fanSpeedWritable: Bool = false,
        fanPercentage: Int? = nil,
        availablePresetModes: [String] = [],
        coverSubtype: String? = nil,
        positionWritable: Bool = false,
        positionPercent: Int? = nil,
        canMoveCover: Bool = false,
        isHighRisk: Bool = false
    ) {
        self.canTogglePower = canTogglePower
        self.brightnessWritable = brightnessWritable
        self.brightnessPercent = brightnessPercent
        self.colorWritable = colorWritable
        self.colorTempKelvin = colorTempKelvin
        self.minKelvin = minKelvin
        self.maxKelvin = maxKelvin
        self.supportsMoonlight = supportsMoonlight
        self.availableEffects = availableEffects
        self.temperatureWritable = temperatureWritable
        self.targetTemperature = targetTemperature
        self.currentTemperature = currentTemperature
        self.minTemp = minTemp
        self.maxTemp = maxTemp
        self.step = step
        self.hvacMode = hvacMode
        self.availableModes = availableModes
        self.fanSpeedWritable = fanSpeedWritable
        self.fanPercentage = fanPercentage
        self.availablePresetModes = availablePresetModes
        self.coverSubtype = coverSubtype
        self.positionWritable = positionWritable
        self.positionPercent = positionPercent
        self.canMoveCover = canMoveCover
        self.isHighRisk = isHighRisk
    }
}

/// A visible device exported to `raycast-snapshot.json`.
public struct RaycastDeviceItem: Codable, Equatable, Sendable {
    public var id: String
    public var name: String
    public var roomId: String
    public var roomName: String
    public var roomSortOrder: Int
    public var category: String
    public var categoryTitle: String
    public var model: String
    public var stateText: String
    public var isOnline: Bool
    public var isActiveOn: Bool
    public var isPrimaryToggleable: Bool
    public var isFavorite: Bool
    public var favoriteRank: Int?
    public var pinyinInitials: String
    public var pinyinFull: String
    public var roomPinyinInitials: String
    public var capabilities: RaycastDeviceCapabilities

    public init(
        id: String,
        name: String,
        roomId: String,
        roomName: String,
        roomSortOrder: Int,
        category: String,
        categoryTitle: String,
        model: String,
        stateText: String,
        isOnline: Bool,
        isActiveOn: Bool,
        isPrimaryToggleable: Bool,
        isFavorite: Bool,
        favoriteRank: Int?,
        pinyinInitials: String,
        pinyinFull: String,
        roomPinyinInitials: String,
        capabilities: RaycastDeviceCapabilities
    ) {
        self.id = id
        self.name = name
        self.roomId = roomId
        self.roomName = roomName
        self.roomSortOrder = roomSortOrder
        self.category = category
        self.categoryTitle = categoryTitle
        self.model = model
        self.stateText = stateText
        self.isOnline = isOnline
        self.isActiveOn = isActiveOn
        self.isPrimaryToggleable = isPrimaryToggleable
        self.isFavorite = isFavorite
        self.favoriteRank = favoriteRank
        self.pinyinInitials = pinyinInitials
        self.pinyinFull = pinyinFull
        self.roomPinyinInitials = roomPinyinInitials
        self.capabilities = capabilities
    }
}

/// Built-in quick action (`QuickAction`, `allOff`, `undo`) exported to `raycast-snapshot.json`.
public struct RaycastQuickActionItem: Codable, Equatable, Sendable {
    public var id: String
    public var name: String
    public var subtitle: String
    public var icon: String
    public var isEnabled: Bool
    public var affectedCount: Int
    public var pinyinInitials: String

    public init(
        id: String,
        name: String,
        subtitle: String,
        icon: String,
        isEnabled: Bool,
        affectedCount: Int,
        pinyinInitials: String
    ) {
        self.id = id
        self.name = name
        self.subtitle = subtitle
        self.icon = icon
        self.isEnabled = isEnabled
        self.affectedCount = affectedCount
        self.pinyinInitials = pinyinInitials
    }
}

/// Remote scene (`HomeScene`) exported to `raycast-snapshot.json`.
public struct RaycastSceneItem: Codable, Equatable, Sendable {
    public var id: String
    public var name: String
    public var entityId: String
    public var roomName: String?
    public var icon: String
    public var visualCategory: String
    public var pinyinInitials: String
    public var pinyinFull: String

    public init(
        id: String,
        name: String,
        entityId: String,
        roomName: String? = nil,
        icon: String,
        visualCategory: String,
        pinyinInitials: String,
        pinyinFull: String
    ) {
        self.id = id
        self.name = name
        self.entityId = entityId
        self.roomName = roomName
        self.icon = icon
        self.visualCategory = visualCategory
        self.pinyinInitials = pinyinInitials
        self.pinyinFull = pinyinFull
    }
}

/// Sensor environment reading exported to `raycast-snapshot.json`.
public struct RaycastClimateItem: Codable, Equatable, Sendable {
    public var id: String
    public var label: String
    public var roomName: String
    public var temperature: Double?
    public var humidity: Double?
    public var battery: Int?
    public var updatedAt: String
    public var pinyinInitials: String

    public init(
        id: String,
        label: String,
        roomName: String,
        temperature: Double?,
        humidity: Double?,
        battery: Int?,
        updatedAt: String,
        pinyinInitials: String
    ) {
        self.id = id
        self.label = label
        self.roomName = roomName
        self.temperature = temperature
        self.humidity = humidity
        self.battery = battery
        self.updatedAt = updatedAt
        self.pinyinInitials = pinyinInitials
    }
}

/// Versioned snapshot written by `MenuBarMijia` for `raycast-mijia` to read without touching credentials.
public struct RaycastSnapshot: Codable, Equatable, Sendable {
    public static let currentSchemaVersion = 1

    public var schemaVersion: Int
    public var updatedAt: String
    public var bridgeToken: String
    public var connectionSummary: String
    public var isStale: Bool
    public var devices: [RaycastDeviceItem]
    public var quickActions: [RaycastQuickActionItem]
    public var scenes: [RaycastSceneItem]
    public var climate: [RaycastClimateItem]
    public var environmentReadings: [RaycastClimateItem]
    public var lastCommandFeedback: RaycastCommandFeedback?

    public init(
        schemaVersion: Int = Self.currentSchemaVersion,
        updatedAt: String,
        bridgeToken: String,
        connectionSummary: String,
        isStale: Bool,
        devices: [RaycastDeviceItem],
        quickActions: [RaycastQuickActionItem],
        scenes: [RaycastSceneItem],
        climate: [RaycastClimateItem],
        environmentReadings: [RaycastClimateItem],
        lastCommandFeedback: RaycastCommandFeedback? = nil
    ) {
        self.schemaVersion = schemaVersion
        self.updatedAt = updatedAt
        self.bridgeToken = bridgeToken
        self.connectionSummary = connectionSummary
        self.isStale = isStale
        self.devices = devices
        self.quickActions = quickActions
        self.scenes = scenes
        self.climate = climate
        self.environmentReadings = environmentReadings
        self.lastCommandFeedback = lastCommandFeedback
    }
}

/// Writes `RaycastSnapshot` atomically with `0600` owner-only permissions.
public enum RaycastSnapshotExporter {
    public static let bridgeTokenDefaultsKey = "raycast_bridge_token_v1"
    /// Settings → 自动化与外部控制 → Raycast 集成. Off by default: no snapshot file and no token.
    public static let integrationEnabledKey = "raycast_integration_enabled"

    public static let defaultSnapshotURL: URL = {
        let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support", isDirectory: true)
        return appSupport
            .appendingPathComponent("MenuBarMijia", isDirectory: true)
            .appendingPathComponent("raycast-snapshot.json", isDirectory: false)
    }()

    public static func loadOrCreateBridgeToken(in defaults: UserDefaults = .standard) -> String {
        if let existing = defaults.string(forKey: bridgeTokenDefaultsKey)?
            .trimmingCharacters(in: .whitespacesAndNewlines),
           existing.count >= URLDirectControl.minimumBridgeTokenLength {
            return existing
        }
        let generated = (UUID().uuidString + UUID().uuidString)
            .replacingOccurrences(of: "-", with: "")
            .lowercased()
        defaults.set(generated, forKey: bridgeTokenDefaultsKey)
        return generated
    }

    /// Serial, so a slower older write can never land after a newer one.
    private static let writeQueue = DispatchQueue(label: "MenuBarMijia.RaycastSnapshotExporter", qos: .utility)

    public static func writeInBackground(_ snapshot: RaycastSnapshot, to fileURL: URL = defaultSnapshotURL) {
        writeQueue.async {
            try? write(snapshot, to: fileURL)
        }
    }

    /// Queued behind any pending write, so a late write cannot recreate the file after it is removed.
    public static func removeInBackground(at fileURL: URL = defaultSnapshotURL) {
        writeQueue.async {
            try? FileManager.default.removeItem(at: fileURL)
        }
    }

    public static func write(_ snapshot: RaycastSnapshot, to fileURL: URL = defaultSnapshotURL) throws {
        let fm = FileManager.default
        let dirURL = fileURL.deletingLastPathComponent()
        if !fm.fileExists(atPath: dirURL.path) {
            try fm.createDirectory(
                at: dirURL,
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: NSNumber(value: Int16(0o700))]
            )
        } else {
            try? fm.setAttributes([.posixPermissions: NSNumber(value: Int16(0o700))], ofItemAtPath: dirURL.path)
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(snapshot)
        try data.write(to: fileURL, options: .atomic)
        try fm.setAttributes([.posixPermissions: NSNumber(value: Int16(0o600))], ofItemAtPath: fileURL.path)
    }
}

// MARK: - HomeStore Companion Bridge Integration

extension HomeStore {
    private static let roomAliasesDefaultsKey = "room_aliases"
    private static let roomOrderDefaultsKey = "room_custom_order"

    /// Whether the user turned on the Raycast companion (default off).
    public var raycastIntegrationEnabled: Bool {
        userDefaults.bool(forKey: RaycastSnapshotExporter.integrationEnabledKey)
    }

    /// Local random bridge token used to authenticate `menumijia://...?token=...` requests from
    /// `raycast-mijia`. `nil` while the integration is off, so no link can skip confirmation.
    public var raycastBridgeToken: String? {
        guard raycastIntegrationEnabled else { return nil }
        return RaycastSnapshotExporter.loadOrCreateBridgeToken(in: userDefaults)
    }

    /// Turning the integration on creates a token and exports at once. Turning it off deletes the
    /// snapshot and the token, so links carrying the old token need confirmation again and the next
    /// enable issues a new token.
    public func setRaycastIntegration(enabled: Bool) {
        userDefaults.set(enabled, forKey: RaycastSnapshotExporter.integrationEnabledKey)
        if enabled {
            exportRaycastSnapshotIfNeeded()
            return
        }
        raycastExportTask?.cancel()
        raycastExportTask = nil
        lastExportedRaycastSnapshot = nil
        userDefaults.removeObject(forKey: RaycastSnapshotExporter.bridgeTokenDefaultsKey)
        if let testURL = raycastSnapshotTestURL {
            try? FileManager.default.removeItem(at: testURL)
        } else if !isTestEnvironment {
            RaycastSnapshotExporter.removeInBackground()
        }
    }

    /// All visible devices across all rooms (ignoring the panel's temporary `listCut` filter),
    /// respecting hidden devices, offline hiding, and light-group folding.
    public var raycastVisibleDevices: [SmartDevice] {
        let baseVisible = devices.filter { dev in
            if hiddenDeviceIds.contains(dev.id) { return false }
            if hideOfflineDevices && !dev.isOnline { return false }
            return true
        }
        guard hideGroupedLights, !lightGroupOwners.isEmpty else { return baseVisible }
        let listed = Dictionary(baseVisible.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        var folded: Set<String> = []
        for device in baseVisible {
            guard let groupDid = lightGroupOwners[device.id],
                  let group = listed[groupDid],
                  group.isOnline else { continue }
            folded.insert(device.id)
        }
        return baseVisible.filter { !folded.contains($0.id) }
    }

    /// Records the outcome of a `menumijia://` command and immediately updates `raycast-snapshot.json`.
    public func recordRaycastCommandFeedback(
        requestId: String?,
        command: String,
        message: String,
        isError: Bool = false,
        now: Date = Date()
    ) {
        let iso = Self.raycastISO8601String(from: now)
        lastRaycastCommandFeedback = RaycastCommandFeedback(
            requestId: requestId,
            command: command,
            message: message,
            isError: isError,
            completedAt: iso
        )
        exportRaycastSnapshotIfNeeded(now: now)
    }

    /// Executes a built-in quick action or undo scene requested via `menumijia://scene?name=...`.
    public func executeBuiltInURLScene(_ builtIn: BuiltInURLScene) async -> (message: String, isError: Bool) {
        switch builtIn {
        case .lightsOff, .softNightLight, .climateSleepOff:
            guard let action = builtIn.quickAction else {
                return (L10n.tr("不支持的快捷动作", "Unsupported quick action"), true)
            }
            guard let task = executeQuickAction(action) else {
                let fallback = L10n.tr("当前无需执行「\(action.title)」", "No devices need \"\(action.title)\"")
                return (toastNotice?.message ?? fallback, false)
            }
            let bannerTitle = undoBanner?.title ?? L10n.tr("已执行「\(action.title)」", "Executed \"\(action.title)\"")
            await task.value
            return (bannerTitle, false)

        case .allOff:
            let targetCount = allOffTargets.count
            turnAllOff()
            guard targetCount > 0 else {
                let fallback = L10n.tr("当前没有开启的设备", "No active devices are currently on")
                return (toastNotice?.message ?? fallback, false)
            }
            let bannerTitle = undoBanner?.title ?? L10n.tr("已关闭 \(targetCount) 台设备", "Turned off \(targetCount) device(s)")
            return (bannerTitle, false)

        case .undo:
            if undoBanner != nil {
                guard let task = undoTurnAllOff() else {
                    let msg = toastNotice?.message ?? L10n.tr("设备状态已变化，无法撤销", "Device state changed; cannot undo")
                    return (msg, true)
                }
                await task.value
                let msg = toastNotice?.message ?? L10n.tr("已恢复关闭前的状态", "Restored previous device states")
                let isErr = toastNotice?.isError ?? false
                return (msg, isErr)
            }
            _ = await runAssistantCommand("撤销")
            switch assistantState {
            case .done(let reply), .awaitingChoice(let reply):
                return (reply, false)
            case .failed(let reason):
                return (reason, true)
            case .thinking, .idle:
                return (L10n.tr("没有可以撤销的操作", "Nothing to undo"), true)
            }
        }
    }

    /// Builds a complete `RaycastSnapshot` from the store's current synthesized state and preferences.
    public func buildRaycastSnapshot(now: Date = Date()) -> RaycastSnapshot {
        let isoNow = Self.raycastISO8601String(from: now)
        let roomAliases = loadRoomAliases()
        let roomRanks = computeRoomRanks()
        let visible = raycastVisibleDevices

        let favOrderMap = Dictionary(
            favoriteOrder.enumerated().map { ($1, $0) },
            uniquingKeysWith: { first, _ in first }
        )

        let sortedDevices = visible.enumerated().sorted { lhs, rhs in
            let a = lhs.element
            let b = rhs.element
            let aFav = favoriteDeviceIds.contains(a.id)
            let bFav = favoriteDeviceIds.contains(b.id)
            if aFav != bFav { return aFav && !bFav }
            if aFav && bFav {
                let rankA = favOrderMap[a.id] ?? Int.max
                let rankB = favOrderMap[b.id] ?? Int.max
                if rankA != rankB { return rankA < rankB }
            }
            let roomRankA = roomRanks[a.roomId] ?? roomRanks[a.roomName] ?? 999
            let roomRankB = roomRanks[b.roomId] ?? roomRanks[b.roomName] ?? 999
            if roomRankA != roomRankB { return roomRankA < roomRankB }
            return lhs.offset < rhs.offset
        }.map(\.element)

        var favCounter = 0
        let deviceItems: [RaycastDeviceItem] = sortedDevices.map { dev in
            let isFav = favoriteDeviceIds.contains(dev.id)
            let favRank: Int?
            if isFav {
                favRank = favCounter
                favCounter += 1
            } else {
                favRank = nil
            }
            let displayRoom = resolvedRoomDisplayName(roomId: dev.roomId, rawRoomName: dev.roomName, aliases: roomAliases)
            let roomOrder = roomRanks[dev.roomId] ?? roomRanks[dev.roomName] ?? 999
            let pinyinInitials = Self.pinyinInitials(of: dev.name)
            let pinyinFull = PanelSearch.Pinyin.syllables(dev.name)?.joined() ?? ""
            let roomPinyin = Self.pinyinInitials(of: displayRoom)
            let capabilities = Self.buildCapabilities(for: dev)

            return RaycastDeviceItem(
                id: dev.id,
                name: dev.name,
                roomId: dev.roomId,
                roomName: displayRoom,
                roomSortOrder: roomOrder,
                category: dev.kind.tag.rawValue,
                categoryTitle: dev.kind.tag.chipTitle,
                model: dev.model,
                stateText: dev.statusSubtitle,
                isOnline: dev.isOnline,
                isActiveOn: dev.isActiveOn,
                isPrimaryToggleable: dev.isPrimaryToggleable,
                isFavorite: isFav,
                favoriteRank: favRank,
                pinyinInitials: pinyinInitials,
                pinyinFull: pinyinFull,
                roomPinyinInitials: roomPinyin,
                capabilities: capabilities
            )
        }

        let quickActions = buildQuickActionItems()

        let sceneItems: [RaycastSceneItem] = scenes.map { scene in
            RaycastSceneItem(
                id: scene.id,
                name: scene.name,
                entityId: scene.entityId,
                roomName: nil,
                icon: scene.iconName,
                visualCategory: String(describing: scene.visualCategory),
                pinyinInitials: Self.pinyinInitials(of: scene.name),
                pinyinFull: PanelSearch.Pinyin.syllables(scene.name)?.joined() ?? ""
            )
        }

        let orderedReadings = environmentReadings.enumerated().sorted { lhs, rhs in
            let rankA = roomRanks[lhs.element.roomName] ?? 999
            let rankB = roomRanks[rhs.element.roomName] ?? 999
            if rankA != rankB { return rankA < rankB }
            return lhs.offset < rhs.offset
        }.map(\.element)

        let climateItems: [RaycastClimateItem] = orderedReadings.map { reading in
            let matchedRoom = rooms.first(where: { $0.name == reading.roomName || $0.id == reading.roomName })
            let displayRoom = resolvedRoomDisplayName(
                roomId: matchedRoom?.id ?? reading.roomName,
                rawRoomName: reading.roomName,
                aliases: roomAliases
            )
            let combinedLabel = "\(displayRoom) \(reading.label)"
            return RaycastClimateItem(
                id: reading.id,
                label: reading.label,
                roomName: displayRoom,
                temperature: reading.temperature,
                humidity: reading.humidity,
                battery: reading.battery,
                updatedAt: isoNow,
                pinyinInitials: Self.pinyinInitials(of: combinedLabel)
            )
        }

        return RaycastSnapshot(
            schemaVersion: RaycastSnapshot.currentSchemaVersion,
            updatedAt: isoNow,
            bridgeToken: raycastBridgeToken ?? "",
            connectionSummary: serverHostSummary,
            isStale: deviceValuesAreStale,
            devices: deviceItems,
            quickActions: quickActions,
            scenes: sceneItems,
            climate: climateItems,
            environmentReadings: climateItems,
            lastCommandFeedback: lastRaycastCommandFeedback
        )
    }

    /// Writes `raycast-snapshot.json` when in live app mode or when an explicit test output URL is configured.
    /// Coalesces bursts of state changes (provider snapshots, optimistic updates, layout edits) into
    /// one export 0.3 s after the last change, so the main actor does not encode and write the file
    /// on every `didSet`.
    public func scheduleRaycastSnapshotExport() {
        guard raycastIntegrationEnabled, !isApplyingProviderSnapshot else { return }
        raycastExportTask?.cancel()
        raycastExportTask = Task { @MainActor [weak self] in
            try? await Task.sleep(for: .milliseconds(300))
            guard !Task.isCancelled, let self else { return }
            self.exportRaycastSnapshotIfNeeded()
        }
    }

    /// Writes the snapshot now. Command feedback uses this directly because Raycast is waiting for it;
    /// everything else goes through `scheduleRaycastSnapshotExport()`.
    public func exportRaycastSnapshotIfNeeded(to customURL: URL? = nil, now: Date = Date()) {
        guard raycastIntegrationEnabled || customURL != nil, !isApplyingProviderSnapshot else { return }
        let targetURL: URL
        if let customURL {
            targetURL = customURL
        } else if let testURL = raycastSnapshotTestURL {
            targetURL = testURL
        } else if isTestEnvironment {
            return
        } else {
            targetURL = RaycastSnapshotExporter.defaultSnapshotURL
        }
        guard hasReceivedInitialSnapshot
                || !devices.isEmpty
                || !scenes.isEmpty
                || !environmentReadings.isEmpty
                || lastRaycastCommandFeedback != nil
                || customURL != nil
                || raycastSnapshotTestURL != nil else {
            return
        }
        let snapshot = buildRaycastSnapshot(now: now)
        if customURL == nil, var previous = lastExportedRaycastSnapshot {
            previous.updatedAt = snapshot.updatedAt
            if previous == snapshot { return }
        }
        raycastExportTask?.cancel()
        lastExportedRaycastSnapshot = snapshot
        if customURL != nil || raycastSnapshotTestURL != nil {
            try? RaycastSnapshotExporter.write(snapshot, to: targetURL)
        } else {
            RaycastSnapshotExporter.writeInBackground(snapshot, to: targetURL)
        }
    }

    // MARK: - Private Snapshot Helpers

    private static func pinyinInitials(of text: String) -> String {
        guard let syllables = PanelSearch.Pinyin.syllables(text) else { return "" }
        return PanelSearch.Pinyin.initials(of: syllables)
    }

    private func loadRoomAliases() -> [String: String] {
        guard let raw = userDefaults.string(forKey: Self.roomAliasesDefaultsKey),
              let data = raw.data(using: .utf8),
              let decoded = try? JSONDecoder().decode([String: String].self, from: data) else {
            return [:]
        }
        return decoded
    }

    private func loadRoomCustomOrder() -> [String] {
        guard let raw = userDefaults.string(forKey: Self.roomOrderDefaultsKey),
              let data = raw.data(using: .utf8),
              let decoded = try? JSONDecoder().decode([String].self, from: data) else {
            return []
        }
        return decoded
    }

    private func computeRoomRanks() -> [String: Int] {
        let customOrder = loadRoomCustomOrder()
        let baseOrderedRooms = rooms.enumerated()
            .sorted { ($0.element.sortOrder, $0.offset) < ($1.element.sortOrder, $1.offset) }
            .map(\.element)

        let orderedRooms: [RoomArea]
        if customOrder.isEmpty {
            orderedRooms = baseOrderedRooms
        } else {
            let customRank = Dictionary(
                customOrder.enumerated().map { ($1, $0) },
                uniquingKeysWith: { first, _ in first }
            )
            orderedRooms = baseOrderedRooms.enumerated().sorted { lhs, rhs in
                let rA = customRank[lhs.element.id] ?? (customOrder.count + lhs.offset)
                let rB = customRank[rhs.element.id] ?? (customOrder.count + rhs.offset)
                return rA < rB
            }.map(\.element)
        }

        var ranks: [String: Int] = [:]
        for (idx, room) in orderedRooms.enumerated() {
            if ranks[room.id] == nil { ranks[room.id] = idx }
            if ranks[room.name] == nil { ranks[room.name] = idx }
        }
        return ranks
    }

    private func resolvedRoomDisplayName(
        roomId: String,
        rawRoomName: String,
        aliases: [String: String]
    ) -> String {
        if let alias = aliases[roomId]?.trimmingCharacters(in: .whitespacesAndNewlines), !alias.isEmpty {
            return alias
        }
        return L10n.displayRoomName(rawRoomName)
    }

    private func buildQuickActionItems() -> [RaycastQuickActionItem] {
        let activeLightsCount = devices.filter { dev in
            guard dev.isOnline, case .light(let s) = dev.kind else { return false }
            return s.isOn && s.powerWritable
        }.count

        let nightLightCandidatesCount = devices.filter { dev in
            guard dev.isOnline, case .light(let s) = dev.kind else { return false }
            return s.brightnessWritable && s.colorWritable
        }.count

        let activeClimateFanCount = devices.filter { dev in
            guard dev.isOnline else { return false }
            switch dev.kind {
            case .climate(let s): return s.isOn && s.hvacMode != "off" && s.powerWritable
            case .fan(let s): return s.isOn && s.powerWritable
            default: return false
            }
        }.count

        let allOffCount = allOffTargets.count

        let hasBannerUndo = undoBanner != nil
        let hasAssistantUndo: Bool = {
            guard let last = lastAssistantUndo else { return false }
            return Date().timeIntervalSince(last.at) <= Self.voiceUndoWindow
        }()
        let canUndo = hasBannerUndo || hasAssistantUndo
        let undoSubtitle: String
        if let banner = undoBanner {
            undoSubtitle = L10n.tr("可撤销：\(banner.title)", "Undo: \(banner.title)")
        } else if let last = lastAssistantUndo, hasAssistantUndo {
            undoSubtitle = L10n.tr("可撤销：\(last.reply)", "Undo: \(last.reply)")
        } else {
            undoSubtitle = L10n.tr("当前没有可撤销的操作", "Nothing to undo right now")
        }

        return [
            RaycastQuickActionItem(
                id: BuiltInURLScene.lightsOff.rawValue,
                name: QuickAction.lightsOff.title,
                subtitle: activeLightsCount > 0
                    ? L10n.tr("当前有 \(activeLightsCount) 盏灯开启", "\(activeLightsCount) light(s) currently on")
                    : L10n.tr("全部灯具已关闭", "All lights are already off"),
                icon: QuickAction.lightsOff.sfSymbolName,
                isEnabled: activeLightsCount > 0,
                affectedCount: activeLightsCount,
                pinyinInitials: Self.pinyinInitials(of: QuickAction.lightsOff.title)
            ),
            RaycastQuickActionItem(
                id: BuiltInURLScene.softNightLight.rawValue,
                name: QuickAction.softNightLight.title,
                subtitle: nightLightCandidatesCount > 0
                    ? L10n.tr("调至 15% 亮度与 2700K 暖光", "Dim to 15% at 2700K warm light")
                    : L10n.tr("无支持调光调色温的在线灯具", "No dimmable warm-white lights online"),
                icon: QuickAction.softNightLight.sfSymbolName,
                isEnabled: nightLightCandidatesCount > 0,
                affectedCount: min(max(nightLightCandidatesCount, 0), 1),
                pinyinInitials: Self.pinyinInitials(of: QuickAction.softNightLight.title)
            ),
            RaycastQuickActionItem(
                id: BuiltInURLScene.climateSleepOff.rawValue,
                name: QuickAction.climateSleepOff.title,
                subtitle: activeClimateFanCount > 0
                    ? L10n.tr("当前有 \(activeClimateFanCount) 台温控/风扇开启", "\(activeClimateFanCount) climate/fan device(s) on")
                    : L10n.tr("空调与风扇均已关闭", "All climate & fan devices are off"),
                icon: QuickAction.climateSleepOff.sfSymbolName,
                isEnabled: activeClimateFanCount > 0,
                affectedCount: activeClimateFanCount,
                pinyinInitials: Self.pinyinInitials(of: QuickAction.climateSleepOff.title)
            ),
            RaycastQuickActionItem(
                id: BuiltInURLScene.allOff.rawValue,
                name: BuiltInURLScene.allOff.displayTitle,
                subtitle: allOffCount > 0
                    ? L10n.tr("关闭当前开启的 \(allOffCount) 台设备（支持撤销）", "Turn off \(allOffCount) active device(s) (with undo)")
                    : L10n.tr("当前没有开启的设备", "No active devices are currently on"),
                icon: "power",
                isEnabled: allOffCount > 0,
                affectedCount: allOffCount,
                pinyinInitials: Self.pinyinInitials(of: BuiltInURLScene.allOff.displayTitle)
            ),
            RaycastQuickActionItem(
                id: BuiltInURLScene.undo.rawValue,
                name: BuiltInURLScene.undo.displayTitle,
                subtitle: undoSubtitle,
                icon: "arrow.uturn.backward",
                isEnabled: canUndo,
                affectedCount: undoBanner?.restoreCommands.count ?? lastAssistantUndo?.restore.count ?? 0,
                pinyinInitials: Self.pinyinInitials(of: BuiltInURLScene.undo.displayTitle)
            ),
        ]
    }

    private static func buildCapabilities(for device: SmartDevice) -> RaycastDeviceCapabilities {
        let isHighRisk = ManualControlPolicy.requiresConfirmation(.power(true), for: device)
            || ManualControlPolicy.requiresConfirmation(.power(false), for: device)
        switch device.kind {
        case .light(let s):
            return RaycastDeviceCapabilities(
                canTogglePower: s.powerWritable,
                brightnessWritable: s.brightnessWritable,
                brightnessPercent: s.brightnessPercent,
                colorWritable: s.colorWritable,
                colorTempKelvin: s.colorTempKelvin,
                minKelvin: s.minKelvin,
                maxKelvin: s.maxKelvin,
                supportsMoonlight: s.brightnessWritable || s.colorWritable || !s.availableEffects.isEmpty,
                availableEffects: s.availableEffects,
                isHighRisk: isHighRisk
            )
        case .climate(let s):
            return RaycastDeviceCapabilities(
                canTogglePower: s.powerWritable,
                temperatureWritable: s.temperatureWritable,
                targetTemperature: s.targetTemperature,
                currentTemperature: s.currentTemperature,
                minTemp: s.minTemp,
                maxTemp: s.maxTemp,
                step: s.step,
                hvacMode: s.hvacMode,
                availableModes: s.availableModes,
                isHighRisk: isHighRisk
            )
        case .fan(let s):
            return RaycastDeviceCapabilities(
                canTogglePower: s.powerWritable,
                fanSpeedWritable: s.speedWritable,
                fanPercentage: s.percentage,
                availablePresetModes: s.availablePresetModes,
                isHighRisk: isHighRisk
            )
        case .cover(let s):
            return RaycastDeviceCapabilities(
                canTogglePower: device.kind.canTogglePower,
                coverSubtype: s.subtype.rawValue,
                positionWritable: s.positionWritable,
                positionPercent: s.positionPercent,
                canMoveCover: s.canMove,
                isHighRisk: isHighRisk || s.subtype == .garageDoor || s.subtype == .windowOpener
            )
        default:
            return RaycastDeviceCapabilities(
                canTogglePower: device.kind.canTogglePower,
                isHighRisk: isHighRisk
            )
        }
    }

    private static func raycastISO8601String(from date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }
}
