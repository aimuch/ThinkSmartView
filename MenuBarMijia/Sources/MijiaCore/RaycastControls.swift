import Foundation

// MARK: - Raycast Companion Command Execution (#20, #176, ADR-0013)

/// Runs the commands a `menumijia://` link asks for: the natural-language presets `ask` resolves
/// without the cloud assistant, and the exact-value controls the Raycast extension and
/// `scripts/mijia` send. Every path shares one lock-screen check, one hazard confirmation, and one
/// optimistic write, so a companion cannot reach a device by a route the panel would refuse.
extension HomeStore {
    // MARK: - Deterministic Preset & Single-Device Natural Command Execution

    /// Deterministically executes single-device preset and natural commands sent via `menumijia://ask?text=...`
    /// (including light moonlight, cover position/open/close, fan percentage/mode, and vacuum start/pause/dock)
    /// without requiring cloud AI. Returns `handled == false` when the text should fall through to `runAssistantCommand`.
    public func executeRaycastDirectCommand(_ text: String) async -> (handled: Bool, message: String, isError: Bool) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return (false, "", false) }

        // 1. Light Moonlight: "把<light>调到15%亮度2700K" or "把<light>设为柔和月光模式"
        if let rawTarget = Self.firstCapture(
            in: trimmed,
            patterns: [
                #"^(?:请|帮我|麻烦)?(?:把|将)?\s*(.+?)\s*(?:调到|设为|调成|变成)\s*15%\s*亮度\s*2700\s*[kK]$"#,
                #"^(?:请|帮我|麻烦)?(?:把|将)?\s*(.+?)\s*(?:设为|调到|调成|开启)\s*(?:柔和)?(?:月光|夜灯)(?:模式)?$"#
            ]
        ), let idx = findUniqueDeviceIndex(matching: rawTarget, where: {
            if case .light = $0.kind { return true }
            return false
        }) {
            return await executeDirectLightMoonlight(at: idx)
        }

        // 2. Cover / Curtain Open / Close / Position
        if let match = Self.twoCaptures(
            in: trimmed,
            pattern: #"^(?:请|帮我|麻烦)?(?:把|将)?\s*(.+?)\s*(?:开到|调到|设为|拉到)\s*(\d{1,3})\s*%$"#
        ), let percent = Int(match.second),
           let idx = findUniqueDeviceIndex(matching: match.first, where: { $0.kind.isCover }) {
            return await executeDirectCoverPosition(at: idx, percent: percent)
        }
        if let rawTarget = Self.firstCapture(
            in: trimmed,
            patterns: [#"^(?:请|帮我|麻烦)?(?:打开|开启|拉开|全开)\s*(.+)$"#]
        ), let idx = findUniqueDeviceIndex(matching: rawTarget, where: { $0.kind.isCover }) {
            return await executeDirectCoverPosition(at: idx, percent: 100)
        }
        if let rawTarget = Self.firstCapture(
            in: trimmed,
            patterns: [#"^(?:请|帮我|麻烦)?(?:关闭|关上|拉上|合上|全关)\s*(.+)$"#]
        ), let idx = findUniqueDeviceIndex(matching: rawTarget, where: { $0.kind.isCover }) {
            return await executeDirectCoverPosition(at: idx, percent: 0)
        }

        // 3. Fan Speed Percentage & Preset Mode
        if let match = Self.twoCaptures(
            in: trimmed,
            pattern: #"^(?:请|帮我|麻烦)?(?:把|将)?\s*(.+?)\s*(?:风速|风量|转速)\s*(?:调到|设为|调成)\s*(\d{1,3})\s*%$"#
        ), let percent = Int(match.second),
           let idx = findUniqueDeviceIndex(matching: match.first, where: {
               if case .fan = $0.kind { return true }
               return false
           }) {
            return await executeDirectFanPercentage(at: idx, percent: percent)
        }
        if let match = Self.twoCaptures(
            in: trimmed,
            pattern: #"^(?:请|帮我|麻烦)?(?:把|将)?\s*(.+?)\s*(?:设为|调到|调成|切换为)\s*(自动|睡眠|静音|最爱)\s*(?:模式|档)?$"#
        ), let idx = findUniqueDeviceIndex(matching: match.first, where: {
            if case .fan = $0.kind { return true }
            return false
        }) {
            return await executeDirectFanPreset(at: idx, modeWord: match.second)
        }

        // 4. Vacuum Start / Pause / Dock
        let isVacuum: (SmartDevice) -> Bool = {
            if case .vacuum = $0.kind { return true }
            return false
        }
        if let rawTarget = Self.firstCapture(
            in: trimmed,
            patterns: [
                #"^(?:请|帮我|麻烦)?(?:暂停|暂停清扫)\s*(.+)$"#,
                #"^(?:请|帮我|麻烦)?(?:把|将|让)?\s*(.+?)\s*(?:暂停清扫|暂停)$"#
            ]
        ), let idx = findUniqueDeviceIndex(matching: rawTarget, where: isVacuum) {
            return await executeDirectVacuumAction(at: idx, action: .pause)
        }
        if let rawTarget = Self.firstCapture(
            in: trimmed,
            patterns: [
                #"^(?:请|帮我|麻烦)?(?:让|把|将)?\s*(.+?)\s*(?:返回基站回充|返回基站|回充|回座|回去充电)$"#,
                #"^(?:请|帮我|麻烦)?(?:回充|返回基站)\s*(.+)$"#
            ]
        ), let idx = findUniqueDeviceIndex(matching: rawTarget, where: isVacuum) {
            return await executeDirectVacuumAction(at: idx, action: .returnToBase)
        }
        if let rawTarget = Self.firstCapture(
            in: trimmed,
            patterns: [
                #"^(?:请|帮我|麻烦)?(?:打开|开启|启动|开始清扫)\s*(.+)$"#,
                #"^(?:请|帮我|麻烦)?(?:让|把|将)?\s*(.+?)\s*(?:开始清扫|启动清扫)$"#
            ]
        ), let idx = findUniqueDeviceIndex(matching: rawTarget, where: isVacuum) {
            return await executeDirectVacuumAction(at: idx, action: .start)
        }

        return (false, "", false)
    }

    private func findUniqueDeviceIndex(
        matching rawQuery: String,
        where predicate: (SmartDevice) -> Bool
    ) -> Int? {
        var clean = rawQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        for prefix in ["把", "将", "让", "给", "的"] where clean.hasPrefix(prefix) {
            clean = String(clean.dropFirst(prefix.count)).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        if clean.hasSuffix("的") {
            clean = String(clean.dropLast()).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        guard !clean.isEmpty else { return nil }

        let matchingIndices = devices.indices.filter { predicate(devices[$0]) }
        guard !matchingIndices.isEmpty else { return nil }

        let exact = matchingIndices.filter { idx in
            let dev = devices[idx]
            let combined = "\(dev.roomName)\(dev.name)"
            let spaced = "\(dev.roomName) \(dev.name)"
            return dev.name.caseInsensitiveCompare(clean) == .orderedSame
                || combined.caseInsensitiveCompare(clean) == .orderedSame
                || spaced.caseInsensitiveCompare(clean) == .orderedSame
        }
        if exact.count == 1 { return exact[0] }

        let substring = matchingIndices.filter { idx in
            let dev = devices[idx]
            guard !dev.name.isEmpty else { return false }
            let combined = "\(dev.roomName)\(dev.name)"
            return dev.name.localizedCaseInsensitiveContains(clean)
                || clean.localizedCaseInsensitiveContains(dev.name)
                || combined.localizedCaseInsensitiveContains(clean)
        }
        if substring.count == 1 { return substring[0] }
        return nil
    }

    public typealias RaycastDirectResult = (handled: Bool, message: String, isError: Bool)

    /// The lock-screen and offline checks every direct Raycast command runs first, with the
    /// message Raycast shows when one refuses. Returns the device when control may proceed.
    private func raycastControllable(at idx: Int) -> (device: SmartDevice?, refusal: RaycastDirectResult?) {
        guard devices.indices.contains(idx) else { return (nil, nil) }
        return raycastControllable(devices[idx])
    }

    private func raycastControllable(_ device: SmartDevice) -> (device: SmartDevice?, refusal: RaycastDirectResult?) {
        let previousToastId = toastNotice?.id
        guard !rejectControlWhileLocked() else {
            let msg = (toastNotice?.id != previousToastId ? toastNotice?.message : nil)
                ?? L10n.tr("屏幕已锁定，已拒绝控制", "Screen is locked; control rejected")
            return (nil, (true, msg, true))
        }
        guard device.isOnline else {
            return (nil, (true, L10n.format("%@ 当前离线", "%@ is offline", device.name), true))
        }
        return (device, nil)
    }

    /// Resolves a parameterized companion command's device id, then applies the same lock and
    /// online checks as a natural-language preset.
    private func raycastControllable(deviceId: String) -> (device: SmartDevice?, refusal: RaycastDirectResult?) {
        guard let device = devices.first(where: { $0.id == deviceId }) else {
            return (nil, (true, L10n.tr("设备已不在面板中", "The device is no longer available"), true))
        }
        return raycastControllable(device)
    }

    /// The hazard confirmation, reporting a refusal the way Raycast shows it.
    private func raycastConfirm(_ operation: IntentOperation, for device: SmartDevice) -> RaycastDirectResult? {
        let preConfirmToastId = toastNotice?.id
        guard allowManualControl(operation, for: device) else {
            let msg = (toastNotice?.id != preConfirmToastId ? toastNotice?.message : nil)
                ?? L10n.format("已取消「%@」的操作", "Cancelled the action for \"%@\"", device.name)
            return (true, msg, true)
        }
        return nil
    }

    /// Shows `updated`, sends `commands`, and reports the outcome as a toast and a Raycast reply.
    /// A failure rolls the row back through the control-write module.
    private func raycastWrite(
        from device: SmartDevice,
        to updated: SmartDevice,
        commands: [DeviceCommand],
        lock: Duration,
        habitIntents: [DeviceIntent] = [],
        success: String
    ) async -> RaycastDirectResult {
        let write = beginOptimisticWrite(from: device, to: updated, lock: lock)
        do {
            try await send(commands, for: write)
            if !habitIntents.isEmpty { confirmManualControl(intents: habitIntents) }
            showToast(success, isError: false)
            return (true, success, false)
        } catch {
            let msg = L10n.format("%@ 响应失败", "%@ failed to respond", device.name)
            showToast(msg, isError: true)
            return (true, msg, true)
        }
    }

    private func executeDirectLightMoonlight(at idx: Int) async -> RaycastDirectResult {
        let check = raycastControllable(at: idx)
        guard let device = check.device else { return check.refusal ?? (false, "", false) }
        guard case .light(var spec) = device.kind else {
            return (false, "", false)
        }
        var commands: [DeviceCommand] = []
        spec.isOn = true
        if spec.brightnessWritable {
            spec.brightnessPercent = 15
            commands.append(.setLightBrightness(entityId: spec.entityId, percent: 15))
        }
        if spec.colorWritable {
            let kelvin = max(spec.minKelvin, min(spec.maxKelvin, 2700))
            spec.colorTempKelvin = kelvin
            spec.currentEffect = nil
            commands.append(.setLightColorTemp(entityId: spec.entityId, kelvin: kelvin))
        } else if let nightEffect = spec.availableEffects.first(where: {
            $0.caseInsensitiveCompare("Night") == .orderedSame
                || $0.localizedCaseInsensitiveContains("moon")
                || $0.contains("夜")
        }) {
            spec.currentEffect = nightEffect
            commands.append(.setLightEffect(entityId: spec.entityId, effect: nightEffect))
        }
        if commands.isEmpty && spec.powerWritable {
            commands.append(.setLightPower(entityId: spec.entityId, isOn: true))
        }
        guard !commands.isEmpty else {
            return (true, L10n.format("%@ 不支持柔和月光调节", "%@ does not support Moonlight mode", device.name), true)
        }
        var updated = device
        updated.kind = .light(spec)
        recordManualLightUse(device.id)
        return await raycastWrite(
            from: device, to: updated, commands: commands, lock: .seconds(2.5),
            habitIntents: [.init(targetId: device.id, operation: .brightness(15))],
            success: L10n.tr("已将\(device.name)设为柔和月光（15% · 2700K）", "Set \(device.name) to Moonlight (15% · 2700K)")
        )
    }

    private func executeDirectCoverPosition(at idx: Int, percent: Int) async -> RaycastDirectResult {
        let check = raycastControllable(at: idx)
        guard let device = check.device else { return check.refusal ?? (false, "", false) }
        return await executeCoverPosition(device, percent: percent)
    }

    /// Drives a cover to `percent` with the command its spec supports: a position write when it has
    /// one, otherwise the open/close action word. An explicit power link therefore reaches the
    /// motor as open/close even while it is travelling; `raycastConfirm` keeps the hazard gate.
    private func executeCoverPosition(_ device: SmartDevice, percent: Int) async -> RaycastDirectResult {
        guard case .cover(var spec) = device.kind else {
            return (false, "", false)
        }
        let clamped = max(0, min(100, percent))
        if let refusal = raycastConfirm(clamped > 0 ? .power(true) : .power(false), for: device) { return refusal }
        let command: DeviceCommand?
        if clamped == 100 {
            if !spec.positionWritable, let open = spec.openDescription {
                command = .setSelectOption(entityId: spec.entityId, option: open)
            } else if spec.positionWritable {
                command = .setNumberValue(entityId: spec.entityId, value: 100)
            } else if let open = spec.openDescription {
                command = .setSelectOption(entityId: spec.entityId, option: open)
            } else {
                command = nil
            }
        } else if clamped == 0 {
            if !spec.positionWritable, let close = spec.closeDescription {
                command = .setSelectOption(entityId: spec.entityId, option: close)
            } else if spec.positionWritable {
                command = .setNumberValue(entityId: spec.entityId, value: 0)
            } else if let close = spec.closeDescription {
                command = .setSelectOption(entityId: spec.entityId, option: close)
            } else {
                command = nil
            }
        } else if spec.positionWritable {
            command = .setNumberValue(entityId: spec.entityId, value: Double(clamped))
        } else {
            command = nil
        }
        guard let cmd = command else {
            return (true, L10n.format("%@ 不支持该位置调节", "%@ does not support that position", device.name), true)
        }
        spec.positionPercent = clamped
        var updated = device
        updated.kind = .cover(spec)
        let success: String
        if clamped == 100 {
            success = L10n.tr("已打开\(device.name)", "Opened \(device.name)")
        } else if clamped == 0 {
            success = L10n.tr("已关闭\(device.name)", "Closed \(device.name)")
        } else {
            success = L10n.tr("已将\(device.name)开到 \(clamped)%", "Set \(device.name) to \(clamped)%")
        }
        return await raycastWrite(from: device, to: updated, commands: [cmd], lock: .seconds(8), success: success)
    }

    private func executeDirectFanPercentage(at idx: Int, percent: Int) async -> RaycastDirectResult {
        let check = raycastControllable(at: idx)
        guard let device = check.device else { return check.refusal ?? (false, "", false) }
        guard case .fan(let spec) = device.kind, spec.speedWritable,
              let change = Self.fanPercentageChange(device, percent: percent) else {
            return (true, L10n.format("%@ 不支持风速百分比调节", "%@ does not support fan speed percentage", device.name), true)
        }
        let clamped = max(1, min(100, percent))
        return await raycastWrite(
            from: device, to: change.updated, commands: [change.command], lock: .seconds(2.5),
            success: L10n.tr("已将\(device.name)风速调到 \(clamped)%", "Set \(device.name) fan speed to \(clamped)%")
        )
    }

    private func executeDirectFanPreset(at idx: Int, modeWord: String) async -> RaycastDirectResult {
        let check = raycastControllable(at: idx)
        guard let device = check.device else { return check.refusal ?? (false, "", false) }
        guard case .fan(var spec) = device.kind else {
            return (false, "", false)
        }
        let canonical: String
        switch modeWord {
        case "自动": canonical = "auto"
        case "睡眠", "静音": canonical = "sleep"
        case "最爱": canonical = "favorite"
        default: canonical = modeWord.lowercased()
        }
        let matchedPreset = FanSpeedMatcher.match(canonical, in: spec.availablePresetModes)
            ?? (canonical == "sleep" ? FanSpeedMatcher.match("silent", in: spec.availablePresetModes) : nil)
            ?? spec.availablePresetModes.first(where: { $0.localizedCaseInsensitiveContains(canonical) })
            ?? (canonical == "auto" ? "Auto" : canonical == "sleep" ? "Sleep" : "Favorite")
        spec.isOn = true
        spec.presetMode = matchedPreset
        var updated = device
        updated.kind = .fan(spec)
        return await raycastWrite(
            from: device, to: updated,
            commands: [.setFanPresetMode(entityId: spec.entityId, presetMode: matchedPreset)],
            lock: .seconds(2.2),
            habitIntents: [.init(targetId: device.id, operation: .fanSpeed(matchedPreset))],
            success: L10n.tr("已将\(device.name)设为\(modeWord)模式", "Set \(device.name) to \(matchedPreset) mode")
        )
    }

    private func executeDirectVacuumAction(at idx: Int, action: VacuumAction) async -> RaycastDirectResult {
        let check = raycastControllable(at: idx)
        guard let device = check.device else { return check.refusal ?? (false, "", false) }
        guard case .vacuum(let spec) = device.kind, spec.actionsWritable,
              let change = Self.vacuumActionChange(device, action: action) else {
            return (true, L10n.format("%@ 不支持该清扫动作", "%@ does not support that vacuum action", device.name), true)
        }
        if let refusal = raycastConfirm(action == .start ? .power(true) : .power(false), for: device) { return refusal }
        let success: String
        switch action {
        case .start:
            success = L10n.tr("已启动\(device.name)清扫", "Started \(device.name)")
        case .pause:
            success = L10n.tr("已暂停\(device.name)", "Paused \(device.name)")
        case .returnToBase:
            success = L10n.tr("已让\(device.name)返回基站回充", "Sent \(device.name) back to dock")
        }
        return await raycastWrite(from: device, to: change.updated, commands: [change.command], lock: .seconds(2.5), success: success)
    }

    // MARK: - Parameterized Companion Controls

    /// `menumijia://power?device=<id>&on=true|false`. An explicit state never double-flips a device
    /// whose current state the client already read. A cover is driven to the requested end instead
    /// of going through `togglePrimary`'s three-way tap, which pauses a travelling cover: `off` on
    /// an opening curtain must close it, not stop it half-way (#20). Hazard confirmation, valves
    /// and momentary buttons stay in `togglePrimary`, so one policy owns every power path.
    public func executeRaycastPower(deviceId: String, isOn: Bool) async -> RaycastDirectResult {
        let check = raycastControllable(deviceId: deviceId)
        guard let device = check.device else { return check.refusal ?? (false, "", false) }
        guard device.isPrimaryToggleable else {
            return (true, L10n.format("%@ 没有可直接开关的功能", "%@ has no direct power switch", device.name), true)
        }
        if device.isMomentaryButton, !isOn {
            return (true, L10n.format("%@ 是按钮类设备，只能触发", "%@ is a button device and can only be triggered", device.name), true)
        }
        if case .cover(let spec) = device.kind {
            // Already travelling the way the link asks for is satisfied; any other motion is a
            // direction change the motor has to be told explicitly.
            if (isOn && spec.motion == .opening) || (!isOn && spec.motion == .closing) {
                let already = isOn
                    ? L10n.format("%@ 正在打开", "%@ is already opening", device.name)
                    : L10n.format("%@ 正在关闭", "%@ is already closing", device.name)
                return (true, already, false)
            }
            if !spec.isMoving, let position = spec.positionPercent,
               (isOn && position >= 100) || (!isOn && position <= 0) {
                let already = isOn
                    ? L10n.format("%@ 已经是打开状态", "%@ is already open", device.name)
                    : L10n.format("%@ 已经是关闭状态", "%@ is already closed", device.name)
                return (true, already, false)
            }
            return await executeCoverPosition(device, percent: isOn ? 100 : 0)
        }
        if !device.isMomentaryButton, device.isActiveOn == isOn {
            let already = isOn
                ? L10n.format("%@ 已经是打开状态", "%@ is already on", device.name)
                : L10n.format("%@ 已经是关闭状态", "%@ is already off", device.name)
            return (true, already, false)
        }
        let previousToastId = toastNotice?.id
        guard let task = togglePrimary(device) else {
            let refusal = (toastNotice?.id != previousToastId ? toastNotice?.message : nil)
                ?? L10n.format("%@ 响应失败", "%@ failed to respond", device.name)
            return (true, refusal, true)
        }
        guard await task.value else {
            return (true, L10n.format("%@ 响应失败", "%@ failed to respond", device.name), true)
        }
        if device.isMomentaryButton {
            return (true, L10n.format("已触发 %@", "Triggered %@", device.name), false)
        }
        let isOnNow = devices.first(where: { $0.id == device.id })?.isActiveOn ?? isOn
        let message = isOnNow
            ? L10n.format("已打开 %@", "Turned on %@", device.name)
            : L10n.format("已关闭 %@", "Turned off %@", device.name)
        return (true, message, false)
    }

    /// `menumijia://brightness?device=<id>&percent=...`. Zero turns the light off: Home Assistant
    /// clamps `brightness_pct` to 1, so a brightness write of zero would turn the light on instead.
    public func executeRaycastBrightness(deviceId: String, percent: Int) async -> RaycastDirectResult {
        let check = raycastControllable(deviceId: deviceId)
        guard let device = check.device else { return check.refusal ?? (false, "", false) }
        guard case .light(var spec) = device.kind else {
            return (true, L10n.format("%@ 不是灯具", "%@ is not a light", device.name), true)
        }
        let clamped = max(0, min(100, percent))
        if clamped > 0, !spec.brightnessWritable {
            return (true, L10n.format("%@ 不支持调光", "%@ does not support dimming", device.name), true)
        }
        if clamped == 0, !spec.powerWritable {
            return (true, L10n.format("%@ 不支持关闭", "%@ cannot be turned off", device.name), true)
        }
        if let refusal = raycastConfirm(clamped > 0 ? .brightness(clamped) : .power(false), for: device) { return refusal }
        spec.isOn = clamped > 0
        spec.brightnessPercent = clamped
        var updated = device
        updated.kind = .light(spec)
        if clamped > 0 { recordManualLightUse(device.id) }
        let commands: [DeviceCommand] = clamped > 0
            ? [.setLightBrightness(entityId: spec.entityId, percent: clamped)]
            : [.setLightPower(entityId: spec.entityId, isOn: false)]
        let habitIntents: [DeviceIntent] = clamped > 0
            ? [.init(targetId: device.id, operation: .brightness(clamped))]
            : []
        let success = clamped > 0
            ? L10n.tr("已将\(device.name)亮度调到 \(clamped)%", "Set \(device.name) brightness to \(clamped)%")
            : L10n.tr("已关闭\(device.name)", "Turned off \(device.name)")
        return await raycastWrite(
            from: device, to: updated, commands: commands, lock: .seconds(2.5),
            habitIntents: habitIntents, success: success
        )
    }

    /// `menumijia://color-temp?device=<id>&kelvin=...`, clamped to the light's own range.
    public func executeRaycastColorTemp(deviceId: String, kelvin: Int) async -> RaycastDirectResult {
        let check = raycastControllable(deviceId: deviceId)
        guard let device = check.device else { return check.refusal ?? (false, "", false) }
        guard case .light(var spec) = device.kind else {
            return (true, L10n.format("%@ 不是灯具", "%@ is not a light", device.name), true)
        }
        guard spec.colorWritable else {
            return (true, L10n.format("%@ 不支持调节色温", "%@ does not support colour temperature", device.name), true)
        }
        let clamped = max(spec.minKelvin, min(spec.maxKelvin, kelvin))
        if let refusal = raycastConfirm(.colorTemperature(clamped), for: device) { return refusal }
        spec.isOn = true
        spec.colorTempKelvin = clamped
        spec.currentEffect = nil
        var updated = device
        updated.kind = .light(spec)
        recordManualLightUse(device.id)
        return await raycastWrite(
            from: device, to: updated,
            commands: [.setLightColorTemp(entityId: spec.entityId, kelvin: clamped)],
            lock: .seconds(2.5),
            habitIntents: [.init(targetId: device.id, operation: .colorTemperature(clamped))],
            success: L10n.tr("已将\(device.name)色温调到 \(clamped)K", "Set \(device.name) colour temperature to \(clamped)K")
        )
    }

    /// `menumijia://temperature?device=<id>&celsius=...`, clamped to the spec's range and snapped to
    /// its step, so a 0.5 °C thermostat never receives 25.3 °C.
    public func executeRaycastTemperature(deviceId: String, celsius: Double) async -> RaycastDirectResult {
        let check = raycastControllable(deviceId: deviceId)
        guard let device = check.device else { return check.refusal ?? (false, "", false) }
        guard case .climate(var spec) = device.kind else {
            return (true, L10n.format("%@ 不是温控设备", "%@ is not a climate device", device.name), true)
        }
        guard spec.temperatureWritable else {
            return (true, L10n.format("%@ 不支持设定温度", "%@ does not accept a target temperature", device.name), true)
        }
        let target = Self.snappedTemperature(celsius, lowerBound: spec.minTemp, upperBound: spec.maxTemp, step: spec.step)
        let targetText = IntentReply.formatTemperature(target)
        if target == spec.targetTemperature {
            return (true, L10n.tr("\(device.name) 的目标温度已经是 \(targetText)°C", "\(device.name) is already set to \(targetText) °C"), false)
        }
        if let refusal = raycastConfirm(.targetTemperature(target), for: device) { return refusal }
        spec.targetTemperature = target
        var updated = device
        updated.kind = .climate(spec)
        return await raycastWrite(
            from: device, to: updated,
            commands: [.setClimateTemperature(entityId: spec.entityId, temperature: target)],
            lock: .seconds(2.5),
            habitIntents: [.init(targetId: device.id, operation: .targetTemperature(target))],
            success: L10n.tr("已将\(device.name)设定为 \(targetText)°C", "Set \(device.name) to \(targetText) °C")
        )
    }

    /// `menumijia://fan?device=<id>&percent=...` or `&preset=...`. A preset must be one the device
    /// publishes; guessing a mode the firmware lacks would make the fixture and the device disagree.
    public func executeRaycastFan(deviceId: String, percent: Int?, preset: String?) async -> RaycastDirectResult {
        let check = raycastControllable(deviceId: deviceId)
        guard let device = check.device else { return check.refusal ?? (false, "", false) }
        guard case .fan(var spec) = device.kind else {
            return (true, L10n.format("%@ 不是风扇或空气净化器", "%@ is not a fan or purifier", device.name), true)
        }
        guard spec.speedWritable else {
            return (true, L10n.format("%@ 不支持调节风速", "%@ does not support fan speed", device.name), true)
        }
        if let percent {
            guard let change = Self.fanPercentageChange(device, percent: percent) else {
                return (true, L10n.format("%@ 不支持调节风速", "%@ does not support fan speed", device.name), true)
            }
            if let refusal = raycastConfirm(.power(true), for: device) { return refusal }
            let clamped = max(1, min(100, percent))
            let habitIntents = change.favoritePreset.map {
                [DeviceIntent(targetId: device.id, operation: .fanSpeed($0))]
            } ?? []
            return await raycastWrite(
                from: device, to: change.updated, commands: [change.command], lock: .seconds(2.5),
                habitIntents: habitIntents,
                success: L10n.tr("已将\(device.name)风速调到 \(clamped)%", "Set \(device.name) fan speed to \(clamped)%")
            )
        }
        guard let wanted = preset else {
            return (true, L10n.tr("缺少风速百分比或档位", "A fan speed or preset is required"), true)
        }
        let matched = FanSpeedMatcher.match(wanted, in: spec.availablePresetModes)
            ?? spec.availablePresetModes.first {
                $0.caseInsensitiveCompare(wanted) == .orderedSame
                    || $0.localizedCaseInsensitiveContains(wanted)
            }
        guard let matched else {
            let options = spec.availablePresetModes.prefix(6).joined(separator: ", ")
            let message = L10n.tr(
                "\(device.name) 没有「\(wanted)」档位，可选：\(options)",
                "\(device.name) has no \"\(wanted)\" mode; available: \(options)"
            )
            return (true, message, true)
        }
        spec.isOn = true
        spec.presetMode = matched
        var updated = device
        updated.kind = .fan(spec)
        return await raycastWrite(
            from: device, to: updated,
            commands: [.setFanPresetMode(entityId: spec.entityId, presetMode: matched)],
            lock: .seconds(2.2),
            habitIntents: [.init(targetId: device.id, operation: .fanSpeed(matched))],
            success: L10n.tr("已将\(device.name)设为\(matched)档", "Set \(device.name) to \(matched)")
        )
    }

    /// `menumijia://focus?device=<id>`: shows that device's drawer. Navigation only, so it runs
    /// without the confirmation sheet; the list cut is replaced so the card is actually on screen.
    public func focusRaycastDevice(deviceId: String) -> RaycastDirectResult {
        guard let device = devices.first(where: { $0.id == deviceId }) else {
            return (true, L10n.tr("设备已不在面板中", "The device is no longer available"), true)
        }
        guard !rejectControlWhileLocked() else {
            let message = toastNotice?.message ?? screenLockBlockedMessage
            return (true, message, true)
        }
        guard raycastVisibleDevices.contains(where: { $0.id == device.id }) else {
            return (true, L10n.tr("\(device.name) 在面板里被隐藏了", "\(device.name) is hidden in the panel"), true)
        }
        let roomId = device.roomId.trimmingCharacters(in: .whitespacesAndNewlines)
        selectRoom(roomId.isEmpty ? nil : roomId)
        focusedDeviceId = device.id
        expandedDeviceId = hasDetailDrawer(for: device) ? device.id : nil
        return (true, L10n.tr("已定位到 \(device.name)", "Focused \(device.name)"), false)
    }

    nonisolated static func snappedTemperature(
        _ value: Double,
        lowerBound: Double,
        upperBound: Double,
        step: Double
    ) -> Double {
        let clamped = Swift.min(Swift.max(value, lowerBound), upperBound)
        guard step > 0 else { return clamped }
        let snapped = ((clamped - lowerBound) / step).rounded() * step + lowerBound
        return Swift.min(Swift.max(snapped, lowerBound), upperBound)
    }

    private static func firstCapture(in text: String, patterns: [String]) -> String? {
        for pattern in patterns {
            guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else { continue }
            let range = NSRange(text.startIndex..<text.endIndex, in: text)
            guard let match = regex.firstMatch(in: text, options: [], range: range),
                  match.numberOfRanges >= 2,
                  let captureRange = Range(match.range(at: 1), in: text) else { continue }
            return String(text[captureRange]).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return nil
    }

    private static func twoCaptures(in text: String, pattern: String) -> (first: String, second: String)? {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else { return nil }
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        guard let match = regex.firstMatch(in: text, options: [], range: range),
              match.numberOfRanges >= 3,
              let r1 = Range(match.range(at: 1), in: text),
              let r2 = Range(match.range(at: 2), in: text) else { return nil }
        return (
            String(text[r1]).trimmingCharacters(in: .whitespacesAndNewlines),
            String(text[r2]).trimmingCharacters(in: .whitespacesAndNewlines)
        )
    }
}
