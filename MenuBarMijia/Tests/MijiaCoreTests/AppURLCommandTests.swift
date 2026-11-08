import Foundation
import Testing
@testable import MijiaCore

/// #57: URL Scheme parsing and the confirmation gate are pure logic, so a web page cannot smuggle
/// an unparsed parameter or skip the sheet by default.
struct AppURLCommandTests {
    @Test("Toggle link parses the device id")
    func parsesToggle() throws {
        let url = try #require(URL(string: "menumijia://toggle?device=dev_study_ceiling"))
        #expect(AppURLParser.parse(url) == .toggle(deviceId: "dev_study_ceiling"))
    }

    @Test("Scene link percent-decodes the scene name")
    func parsesScene() throws {
        let url = try #require(URL(string: "menumijia://scene?name=%E5%9B%9E%E5%AE%B6%E6%A8%A1%E5%BC%8F"))
        #expect(AppURLParser.parse(url) == .scene(name: "回家模式"))
    }

    @Test("Ask link keeps the sentence and trims surrounding whitespace")
    func parsesAsk() throws {
        let url = try #require(URL(string: "menumijia://ask?text=%20%E6%89%93%E5%BC%80%E4%B9%A6%E6%88%BF%E7%81%AF%20"))
        #expect(AppURLParser.parse(url) == .ask(text: "打开书房灯"))
    }

    @Test("Path-style links are accepted too")
    func parsesPathStyle() throws {
        let url = try #require(URL(string: "menumijia:/toggle?device=light_bed"))
        #expect(AppURLParser.parse(url) == .toggle(deviceId: "light_bed"))
    }

    @Test("Unknown, incomplete, empty, and oversized links are rejected")
    func rejectsMalformedLinks() throws {
        let bad = [
            "https://toggle?device=light_bed",
            "menumijia://toggle",
            "menumijia://toggle?device=",
            "menumijia://toggle?device=%20",
            "menumijia://scene?name=",
            "menumijia://ask?text=",
            "menumijia://unknown?device=light_bed",
            "menumijia://ask?text=" + String(repeating: "a", count: AppURLParser.maximumAskLength + 1)
        ]
        for raw in bad {
            let url = try #require(URL(string: raw), "invalid test URL \(raw)")
            #expect(AppURLParser.parse(url) == nil, "\(raw) should not parse")
        }
    }

    @Test("URL control asks for confirmation by default and only skips it after opt-in")
    func confirmationGate() throws {
        let suite = "test.url.gate.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }

        #expect(URLDirectControl.requiresConfirmation(in: defaults))
        defaults.set(true, forKey: URLDirectControl.allowDirectControlKey)
        #expect(!URLDirectControl.requiresConfirmation(in: defaults))
        #expect(URLDirectControl.requiresConfirmation(allowDirectControl: false))
        #expect(!URLDirectControl.requiresConfirmation(allowDirectControl: true))
    }

    @Test("Envelope parser extracts bridge token and requestId while rejecting oversized metadata")
    func parsesEnvelopeAndBridgeToken() throws {
        let validURL = try #require(
            URL(string: "menumijia://toggle?device=dev_study_ceiling&token=0123456789abcdef0123456789abcdef&id=req-42")
        )
        let envelope = try #require(AppURLParser.parseEnvelope(validURL))
        #expect(envelope.command == .toggle(deviceId: "dev_study_ceiling"))
        #expect(envelope.token == "0123456789abcdef0123456789abcdef")
        #expect(envelope.requestId == "req-42")

        let expectedToken = "0123456789abcdef0123456789abcdef"
        #expect(URLDirectControl.isValidBridgeToken(envelope.token, expectedToken: expectedToken))
        #expect(!URLDirectControl.isValidBridgeToken("wrong-token-12345678", expectedToken: expectedToken))
        #expect(!URLDirectControl.isValidBridgeToken("short", expectedToken: "short"))
        #expect(
            !URLDirectControl.requiresConfirmation(
                allowDirectControl: false,
                providedToken: envelope.token,
                expectedToken: expectedToken
            )
        )
        #expect(
            URLDirectControl.requiresConfirmation(
                allowDirectControl: false,
                providedToken: "invalid-token-0000000000000000",
                expectedToken: expectedToken
            )
        )

        let oversizedToken = String(repeating: "x", count: AppURLParser.maximumTokenLength + 1)
        let badTokenURL = try #require(URL(string: "menumijia://toggle?device=dev_1&token=\(oversizedToken)"))
        #expect(AppURLParser.parseEnvelope(badTokenURL) == nil)
    }

    @Test("BuiltInURLScene resolves quick actions, allOff, and undo identifiers")
    func resolvesBuiltInScenes() {
        #expect(BuiltInURLScene.resolve("lightsOff") == .lightsOff)
        #expect(BuiltInURLScene.resolve("quick:lightsOff") == .lightsOff)
        #expect(BuiltInURLScene.resolve("全屋关灯") == .lightsOff)
        #expect(BuiltInURLScene.resolve("softNightLight") == .softNightLight)
        #expect(BuiltInURLScene.resolve("柔和夜灯") == .softNightLight)
        #expect(BuiltInURLScene.resolve("climateSleepOff") == .climateSleepOff)
        #expect(BuiltInURLScene.resolve("关温控风扇") == .climateSleepOff)
        #expect(BuiltInURLScene.resolve("allOff") == .allOff)
        #expect(BuiltInURLScene.resolve("一键全关") == .allOff)
        #expect(BuiltInURLScene.resolve("undo") == .undo)
        #expect(BuiltInURLScene.resolve("撤销上一步") == .undo)
        #expect(BuiltInURLScene.resolve("unknown_custom_scene") == nil)
    }

    @Test("Raycast integration is off by default; on, exports coalesce, skip unchanged snapshots and write feedback at once; off revokes the token")
    @MainActor
    func raycastSnapshotExportIsCoalescedAndSkipsUnchangedWrites() async throws {
        let suite = "test.raycast.coalesce.\(UUID().uuidString)"
        let tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("raycast-coalesce-\(UUID().uuidString)", isDirectory: true)
        let snapshotFile = tempDir.appendingPathComponent("raycast-snapshot.json", isDirectory: false)
        defer {
            UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
            try? FileManager.default.removeItem(at: tempDir)
        }
        let lamp = SmartDevice(
            id: "dev_lamp", name: "台灯", roomId: "study", roomName: "书房",
            model: "yeelink.light.lamp4", isOnline: true,
            kind: .light(LightSpec(entityId: "light.lamp", isOn: false))
        )
        let store = HomeStore(
            provider: MockHomeProvider(snapshot: SynthesizedHome(devices: [lamp])),
            defaultsSuiteName: suite
        )
        try await store.waitForTestSnapshot()
        store.raycastSnapshotTestURL = snapshotFile

        // Off by default: nothing is written and there is no token.
        #expect(!store.raycastIntegrationEnabled)
        #expect(store.raycastBridgeToken == nil)
        store.scheduleRaycastSnapshotExport()
        store.exportRaycastSnapshotIfNeeded()
        store.recordRaycastCommandFeedback(requestId: "ignored", command: "toggle", message: "x")
        try await Task.sleep(for: .milliseconds(500))
        #expect(!FileManager.default.fileExists(atPath: snapshotFile.path))

        store.setRaycastIntegration(enabled: true)
        let firstToken = try #require(store.raycastBridgeToken)
        try FileManager.default.removeItem(at: snapshotFile)
        store.lastExportedRaycastSnapshot = nil

        // A burst of changes schedules one export that lands after the quiet period.
        for _ in 0..<5 { store.scheduleRaycastSnapshotExport() }
        #expect(!FileManager.default.fileExists(atPath: snapshotFile.path))
        for _ in 0..<250 where !FileManager.default.fileExists(atPath: snapshotFile.path) {
            try await Task.sleep(for: .milliseconds(20))
        }
        let first = try Data(contentsOf: snapshotFile)

        // Nothing changed except the clock: the file is left alone.
        store.exportRaycastSnapshotIfNeeded(now: Date().addingTimeInterval(60))
        #expect(try Data(contentsOf: snapshotFile) == first)

        // Command feedback is written immediately because Raycast is waiting for it.
        store.recordRaycastCommandFeedback(requestId: "req-1", command: "toggle", message: "已打开 台灯")
        let decoded = try JSONDecoder().decode(RaycastSnapshot.self, from: Data(contentsOf: snapshotFile))
        #expect(decoded.lastCommandFeedback?.requestId == "req-1")
        #expect(decoded.bridgeToken == firstToken)

        // Turning it off deletes the file and revokes the token; turning it back on issues a new one.
        store.setRaycastIntegration(enabled: false)
        #expect(!FileManager.default.fileExists(atPath: snapshotFile.path))
        #expect(store.raycastBridgeToken == nil)
        #expect(!URLDirectControl.isValidBridgeToken(firstToken, expectedToken: store.raycastBridgeToken))
        store.setRaycastIntegration(enabled: true)
        #expect(try #require(store.raycastBridgeToken) != firstToken)
    }

    @Test("RaycastSnapshot exports 0600 JSON with capabilities, pinyin, multi-sensor readings, and command feedback")
    @MainActor
    func exportsRaycastSnapshotAndExecutesBuiltInScenes() async throws {
        let suite = "test.raycast.snapshot.\(UUID().uuidString)"
        let tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("raycast-snapshot-test-\(UUID().uuidString)", isDirectory: true)
        let snapshotFile = tempDir.appendingPathComponent("raycast-snapshot.json", isDirectory: false)
        defer {
            UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
            try? FileManager.default.removeItem(at: tempDir)
        }

        let rooms = [
            RoomArea(id: "living", name: "客厅", sortOrder: 0),
            RoomArea(id: "study", name: "书房", sortOrder: 1),
        ]
        let devices = [
            SmartDevice(
                id: "dev_living_light",
                name: "客厅吸顶灯",
                roomId: "living",
                roomName: "客厅",
                model: "yeelink.light.ceiling",
                isOnline: true,
                kind: .light(LightSpec(
                    entityId: "light.living",
                    isOn: true,
                    brightnessPercent: 80,
                    colorTempKelvin: 4000,
                    minKelvin: 2700,
                    maxKelvin: 6500
                ))
            ),
            SmartDevice(
                id: "dev_study_ac",
                name: "书房空调",
                roomId: "study",
                roomName: "书房",
                model: "xiaomi.aircondition.mc5",
                isOnline: true,
                kind: .climate(ClimateSpec(
                    entityId: "climate.study",
                    isOn: true,
                    hvacMode: "cool",
                    availableModes: ["off", "cool", "heat"],
                    targetTemperature: 25,
                    currentTemperature: 27
                ))
            ),
        ]
        let readings = [
            EnvironmentReading(
                id: "sensor_living_1",
                label: "沙发温湿度计",
                roomName: "客厅",
                temperature: 24.5,
                humidity: 52,
                battery: 90
            ),
            EnvironmentReading(
                id: "sensor_living_2",
                label: "阳台侧温湿度计",
                roomName: "客厅",
                temperature: 26.1,
                humidity: 48,
                battery: 82
            ),
        ]
        let scenes = [
            HomeScene(id: "scene_movie", name: "观影模式", entityId: "scene.movie", iconName: "film.fill")
        ]

        let provider = MockHomeProvider(
            snapshot: SynthesizedHome(
                rooms: rooms,
                devices: devices,
                environmentReadings: readings,
                scenes: scenes
            )
        )
        let store = HomeStore(provider: provider, defaultsSuiteName: suite)
        store.raycastSnapshotTestURL = snapshotFile
        try await store.waitForTestSnapshot()
        store.setRaycastIntegration(enabled: true)
        store.exportRaycastSnapshotIfNeeded()

        // Selecting a single room in the panel must NOT hide other rooms in the Raycast snapshot.
        store.selectRoom("study")
        store.exportRaycastSnapshotIfNeeded()

        let data = try Data(contentsOf: snapshotFile)
        let decoded = try JSONDecoder().decode(RaycastSnapshot.self, from: data)
        #expect(decoded.schemaVersion == 1)
        #expect(decoded.bridgeToken.count >= 32)
        #expect(decoded.devices.count == 2)

        let lightItem = try #require(decoded.devices.first(where: { $0.id == "dev_living_light" }))
        let acItem = try #require(decoded.devices.first(where: { $0.id == "dev_study_ac" }))
        #expect(lightItem.capabilities.supportsMoonlight == true)
        #expect(acItem.capabilities.supportsMoonlight == false)
        #expect(acItem.capabilities.temperatureWritable == true)
        #expect(lightItem.pinyinInitials == "ktxdd")

        // Both sensors in the same room keep their distinct IDs.
        #expect(decoded.environmentReadings.map(\.id) == ["sensor_living_1", "sensor_living_2"])
        #expect(decoded.quickActions.map(\.id) == ["lightsOff", "softNightLight", "climateSleepOff", "allOff", "undo"])

        // File permissions must be 0600.
        let attrs = try FileManager.default.attributesOfItem(atPath: snapshotFile.path)
        let perms = (attrs[.posixPermissions] as? NSNumber)?.intValue ?? 0
        #expect((perms & 0o777) == 0o600)

        // Built-in quick action + undo execution and command feedback persistence.
        let lightsOffOutcome = await store.executeBuiltInURLScene(.lightsOff)
        #expect(!lightsOffOutcome.isError)
        #expect(store.devices.first(where: { $0.id == "dev_living_light" })?.isActiveOn == false)

        let undoOutcome = await store.executeBuiltInURLScene(.undo)
        #expect(!undoOutcome.isError)
        #expect(store.devices.first(where: { $0.id == "dev_living_light" })?.isActiveOn == true)

        store.recordRaycastCommandFeedback(
            requestId: "req-99",
            command: "ask:打开客厅灯",
            message: "已打开 客厅吸顶灯",
            isError: false
        )
        let updatedData = try Data(contentsOf: snapshotFile)
        let updatedSnapshot = try JSONDecoder().decode(RaycastSnapshot.self, from: updatedData)
        #expect(updatedSnapshot.lastCommandFeedback?.requestId == "req-99")
        #expect(updatedSnapshot.lastCommandFeedback?.message == "已打开 客厅吸顶灯")
        #expect(updatedSnapshot.lastCommandFeedback?.isError == false)
    }

    // MARK: - Parameterized Companion Controls (#20)

    @Test("Parameterized companion links parse into exact-value commands")
    func parsesParameterizedControls() throws {
        let power = try #require(URL(string: "menumijia://power?device=dev_light&on=false"))
        #expect(AppURLParser.parse(power) == .power(deviceId: "dev_light", isOn: false))
        let powerState = try #require(URL(string: "menumijia://power?device=dev_light&state=ON"))
        #expect(AppURLParser.parse(powerState) == .power(deviceId: "dev_light", isOn: true))

        let brightness = try #require(URL(string: "menumijia://brightness?device=dev_light&percent=40"))
        #expect(AppURLParser.parse(brightness) == .brightness(deviceId: "dev_light", percent: 40))

        for route in ["color-temp", "color_temp", "colortemp"] {
            let url = try #require(URL(string: "menumijia://\(route)?device=dev_light&kelvin=2700"))
            #expect(AppURLParser.parse(url) == .colorTemp(deviceId: "dev_light", kelvin: 2700))
        }

        let temperature = try #require(URL(string: "menumijia://temperature?device=dev_ac&celsius=25.5"))
        #expect(AppURLParser.parse(temperature) == .temperature(deviceId: "dev_ac", celsius: 25.5))

        let fanPercent = try #require(URL(string: "menumijia://fan?device=dev_fan&percent=60"))
        #expect(AppURLParser.parse(fanPercent) == .fan(deviceId: "dev_fan", percent: 60, preset: nil))
        let fanPreset = try #require(URL(string: "menumijia://fan?device=dev_fan&preset=Auto"))
        #expect(AppURLParser.parse(fanPreset) == .fan(deviceId: "dev_fan", percent: nil, preset: "Auto"))

        let focus = try #require(URL(string: "menumijia://focus?device=dev_fan"))
        #expect(AppURLParser.parse(focus) == .focus(deviceId: "dev_fan"))

        // A slider that overshoots must not be clamped at the edge of the process: reject the link.
        let rejected = [
            "menumijia://power?device=dev_light",
            "menumijia://power?device=dev_light&on=maybe",
            "menumijia://brightness?device=dev_light",
            "menumijia://brightness?device=dev_light&percent=101",
            "menumijia://brightness?device=dev_light&percent=-1",
            "menumijia://color-temp?device=dev_light&kelvin=999",
            "menumijia://temperature?device=dev_ac&celsius=nan",
            "menumijia://temperature?device=dev_ac&celsius=80",
            "menumijia://fan?device=dev_fan",
            "menumijia://fan?device=dev_fan&percent=250",
            "menumijia://fan?device=dev_fan&percent=60&preset=Auto",
            "menumijia://focus?device=",
        ]
        for raw in rejected {
            let url = try #require(URL(string: raw))
            #expect(AppURLParser.parse(url) == nil, "\(raw) should not parse")
        }
    }

    @Test("Parameterized links write exact values, clamp to the device's range, and report the result")
    @MainActor
    func parameterizedControlsWriteExactValues() async throws {
        let suite = "test.raycast.controls.\(UUID().uuidString)"
        defer { UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite) }

        let rooms = [
            RoomArea(id: "living", name: "客厅", sortOrder: 0),
            RoomArea(id: "study", name: "书房", sortOrder: 1),
            RoomArea(id: "bedroom", name: "卧室", sortOrder: 2),
        ]
        let light = SmartDevice(
            id: "dev_living_light",
            name: "客厅吸顶灯",
            roomId: "living",
            roomName: "客厅",
            model: "yeelink.light.ceiling",
            isOnline: true,
            kind: .light(LightSpec(
                entityId: "light.living",
                isOn: true,
                brightnessPercent: 80,
                colorTempKelvin: 4000,
                minKelvin: 2700,
                maxKelvin: 6500
            ))
        )
        let ac = SmartDevice(
            id: "dev_study_ac",
            name: "书房空调",
            roomId: "study",
            roomName: "书房",
            model: "xiaomi.aircondition.mc5",
            isOnline: true,
            kind: .climate(ClimateSpec(
                entityId: "climate.study",
                isOn: true,
                hvacMode: "cool",
                availableModes: ["off", "cool", "heat"],
                targetTemperature: 25,
                minTemp: 16,
                maxTemp: 30,
                step: 0.5
            ))
        )
        let fan = SmartDevice(
            id: "dev_bedroom_fan",
            name: "卧室风扇",
            roomId: "bedroom",
            roomName: "卧室",
            model: "zhimi.fan.za4",
            isOnline: true,
            kind: .fan(FanSpec(
                entityId: "fan.bedroom",
                isOn: false,
                percentage: 40,
                presetMode: "Auto",
                availablePresetModes: ["Auto", "Sleep", "Favorite"]
            ))
        )
        let offlineLamp = SmartDevice(
            id: "dev_offline_lamp",
            name: "阳台灯",
            roomId: "living",
            roomName: "客厅",
            model: "yeelink.light.color1",
            isOnline: false,
            kind: .light(LightSpec(entityId: "light.balcony", isOn: false))
        )

        let provider = MockHomeProvider(snapshot: SynthesizedHome(rooms: rooms, devices: [light, ac, fan, offlineLamp]))
        let store = HomeStore(provider: provider, defaultsSuiteName: suite)
        try await store.waitForTestSnapshot()

        func device(_ id: String) -> SmartDevice? {
            store.devices.first { $0.id == id }
        }

        // Brightness writes the exact percent; zero turns the light off instead of asking for 0%.
        let dimmed = await store.executeRaycastBrightness(deviceId: "dev_living_light", percent: 40)
        #expect(!dimmed.isError)
        guard case .light(let dimmedSpec) = try #require(device("dev_living_light")?.kind) else {
            Issue.record("expected a light")
            return
        }
        #expect(dimmedSpec.brightnessPercent == 40)
        #expect(dimmedSpec.isOn)
        var commands = await provider.executedCommands
        #expect(commands.last == .setLightBrightness(entityId: "light.living", percent: 40))

        let off = await store.executeRaycastBrightness(deviceId: "dev_living_light", percent: 0)
        #expect(!off.isError)
        guard case .light(let offSpec) = try #require(device("dev_living_light")?.kind) else {
            Issue.record("expected a light")
            return
        }
        #expect(offSpec.isOn == false)
        commands = await provider.executedCommands
        #expect(commands.last == .setLightPower(entityId: "light.living", isOn: false))

        // Colour temperature is clamped to the light's own 2700…6500 K window.
        let warm = await store.executeRaycastColorTemp(deviceId: "dev_living_light", kelvin: 9000)
        #expect(!warm.isError)
        guard case .light(let warmSpec) = try #require(device("dev_living_light")?.kind) else {
            Issue.record("expected a light")
            return
        }
        #expect(warmSpec.colorTempKelvin == 6500)
        #expect(warmSpec.isOn)
        commands = await provider.executedCommands
        #expect(commands.last == .setLightColorTemp(entityId: "light.living", kelvin: 6500))

        // 25.3 °C snaps to the thermostat's 0.5 °C step instead of reaching the device as 25.3.
        let warmer = await store.executeRaycastTemperature(deviceId: "dev_study_ac", celsius: 25.3)
        #expect(!warmer.isError)
        guard case .climate(let acSpec) = try #require(device("dev_study_ac")?.kind) else {
            Issue.record("expected a climate device")
            return
        }
        #expect(acSpec.targetTemperature == 25.5)
        commands = await provider.executedCommands
        #expect(commands.last == .setClimateTemperature(entityId: "climate.study", temperature: 25.5))

        // Setting the temperature it already holds is a no-op, not another cloud round trip.
        let unchanged = await store.executeRaycastTemperature(deviceId: "dev_study_ac", celsius: 25.5)
        #expect(!unchanged.isError)
        #expect(unchanged.message.contains("25.5"))
        commands = await provider.executedCommands
        #expect(commands.count == 4)

        // Fan percentage powers on and switches to Favorite, the mode purifiers honour a speed in.
        let faster = await store.executeRaycastFan(deviceId: "dev_bedroom_fan", percent: 70, preset: nil)
        #expect(!faster.isError)
        guard case .fan(let fanSpec) = try #require(device("dev_bedroom_fan")?.kind) else {
            Issue.record("expected a fan")
            return
        }
        #expect(fanSpec.percentage == 70)
        #expect(fanSpec.isOn)
        #expect(fanSpec.presetMode == "Favorite")
        commands = await provider.executedCommands
        #expect(commands.last == .setFanPercentage(entityId: "fan.bedroom", percent: 70))

        // A preset is matched against the modes the device publishes.
        let quiet = await store.executeRaycastFan(deviceId: "dev_bedroom_fan", percent: nil, preset: "sleep")
        #expect(!quiet.isError)
        guard case .fan(let quietSpec) = try #require(device("dev_bedroom_fan")?.kind) else {
            Issue.record("expected a fan")
            return
        }
        #expect(quietSpec.presetMode == "Sleep")
        commands = await provider.executedCommands
        #expect(commands.last == .setFanPresetMode(entityId: "fan.bedroom", presetMode: "Sleep"))

        // An unpublished mode is refused with the available list instead of being sent anyway.
        let unknown = await store.executeRaycastFan(deviceId: "dev_bedroom_fan", percent: nil, preset: "Turbo")
        #expect(unknown.isError)
        #expect(unknown.message.contains("Auto"))
        commands = await provider.executedCommands
        #expect(commands.count == 6)

        // Explicit power: on/off set the state, and asking for the state it already holds is a no-op.
        let turnedOn = await store.executeRaycastPower(deviceId: "dev_bedroom_fan", isOn: true)
        #expect(!turnedOn.isError)
        let alreadyOn = await store.executeRaycastPower(deviceId: "dev_bedroom_fan", isOn: true)
        #expect(!alreadyOn.isError)
        #expect(alreadyOn.message.contains("已经"))
        commands = await provider.executedCommands
        #expect(commands.count == 6, "an already-satisfied power link sends nothing")

        let turnedOff = await store.executeRaycastPower(deviceId: "dev_bedroom_fan", isOn: false)
        #expect(!turnedOff.isError)
        #expect(device("dev_bedroom_fan")?.isActiveOn == false)

        // An offline device is refused before any command is queued.
        let offline = await store.executeRaycastBrightness(deviceId: "dev_offline_lamp", percent: 50)
        #expect(offline.isError)

        // Focus shows the device's room and drawer without changing any device state.
        let focused = store.focusRaycastDevice(deviceId: "dev_study_ac")
        #expect(!focused.isError)
        #expect(store.selectedRoomId == "study")
        #expect(store.expandedDeviceId == "dev_study_ac")

        store.hideDevice("dev_study_ac")
        let hidden = store.focusRaycastDevice(deviceId: "dev_study_ac")
        #expect(hidden.isError)
    }

    @Test("A power link opens or closes a travelling cover instead of pausing it")
    @MainActor
    func powerLinkDrivesCoverInTheRequestedDirection() async throws {
        let suite = "test.raycast.cover.\(UUID().uuidString)"
        defer { UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite) }

        let openingCurtain = SmartDevice(
            id: "dev_opening_curtain",
            name: "客厅窗帘",
            roomId: "living",
            roomName: "客厅",
            model: "lumi.curtain.hagl05",
            isOnline: true,
            kind: .cover(CoverSpec(
                entityId: "cover.opening",
                subtype: .curtain,
                positionPercent: 40,
                openDescription: "Open",
                closeDescription: "Close",
                pauseDescription: "Pause",
                motion: .opening
            ))
        )
        let closingWindow = SmartDevice(
            id: "dev_closing_window",
            name: "卧室窗户",
            roomId: "bedroom",
            roomName: "卧室",
            model: "linp.wopener.wd1lb",
            isOnline: true,
            kind: .cover(CoverSpec(
                entityId: "cover.window",
                subtype: .windowOpener,
                positionPercent: 60,
                openDescription: "Open",
                closeDescription: "Close",
                pauseDescription: "Pause",
                motion: .closing
            ))
        )
        let provider = MockHomeProvider(snapshot: SynthesizedHome(
            rooms: [
                RoomArea(id: "living", name: "客厅", sortOrder: 0),
                RoomArea(id: "bedroom", name: "卧室", sortOrder: 1),
            ],
            devices: [openingCurtain, closingWindow]
        ))
        let store = HomeStore(provider: provider, defaultsSuiteName: suite)
        try await store.waitForTestSnapshot()

        // Off while the curtain is opening closes it: the three-way tile toggle would pause it.
        let closed = await store.executeRaycastPower(deviceId: "dev_opening_curtain", isOn: false)
        #expect(!closed.isError)
        var commands = await provider.executedCommands
        #expect(commands.last == .setSelectOption(entityId: "cover.opening", option: "Close"))

        // On while the window is closing opens it, but only after the high-risk gate approves.
        store.manualControlConfirmationHandler = { _ in false }
        let refused = await store.executeRaycastPower(deviceId: "dev_closing_window", isOn: true)
        #expect(refused.isError)
        #expect(await provider.executedCommands.count == commands.count)

        store.manualControlConfirmationHandler = { _ in true }
        let opened = await store.executeRaycastPower(deviceId: "dev_closing_window", isOn: true)
        #expect(!opened.isError)
        commands = await provider.executedCommands
        #expect(commands.last == .setSelectOption(entityId: "cover.window", option: "Open"))
    }

    @Test("A power link keeps the high-risk confirmation gate and refuses an unconfirmed heater")
    @MainActor
    func powerLinkKeepsHazardConfirmation() async throws {
        let suite = "test.raycast.hazard.\(UUID().uuidString)"
        defer { UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite) }

        let heater = SmartDevice(
            id: "dev_water_heater",
            name: "热水器",
            roomId: "bathroom",
            roomName: "卫生间",
            model: "xiaomi.waterheater.ymj",
            isOnline: true,
            kind: .appliance(ApplianceSpec(
                entityId: "switch.water_heater",
                isOn: false,
                powerPresent: true,
                powerWritable: true,
                heats: true
            ))
        )
        let provider = MockHomeProvider(snapshot: SynthesizedHome(
            rooms: [RoomArea(id: "bathroom", name: "卫生间", sortOrder: 0)],
            devices: [heater]
        ))
        let store = HomeStore(provider: provider, defaultsSuiteName: suite)
        try await store.waitForTestSnapshot()

        store.manualControlConfirmationHandler = { _ in false }
        let refused = await store.executeRaycastPower(deviceId: "dev_water_heater", isOn: true)
        #expect(refused.isError)
        #expect(store.devices.first { $0.id == "dev_water_heater" }?.isActiveOn == false)
        #expect(await provider.executedCommands.isEmpty)

        store.manualControlConfirmationHandler = { _ in true }
        let approved = await store.executeRaycastPower(deviceId: "dev_water_heater", isOn: true)
        #expect(!approved.isError)
        #expect(store.devices.first { $0.id == "dev_water_heater" }?.isActiveOn == true)
    }
}
