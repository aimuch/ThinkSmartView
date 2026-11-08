import Foundation
import Testing
@testable import MijiaCore

/// #307: Home Assistant `humidifier`, `water_heater`, `valve` and the domain-level 更多设置 rows.
struct HADomainCardsTests {
    private struct Fixture: Decodable {
        struct RawState: Decodable {
            let entity_id: String
            let state: String
            let attributes: [String: JSONValue]
        }
        let areas: [HAAreaEntry]
        let devices: [HADeviceEntry]
        let entities: [HAEntityEntry]
        let states: [RawState]
    }

    private func fixtureURL() throws -> URL {
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            let candidate = directory.appendingPathComponent("Tests/Fixtures/ha_domain_cards.json")
            if FileManager.default.fileExists(atPath: candidate.path) { return candidate }
            directory.deleteLastPathComponent()
        }
        throw CocoaError(.fileNoSuchFile)
    }

    private func load() throws -> (home: SynthesizedHome, fixture: Fixture) {
        let raw = try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: try fixtureURL()))
        let states = Dictionary(
            uniqueKeysWithValues: raw.states.map {
                ($0.entity_id, HAEntityState(entityId: $0.entity_id, state: $0.state, attributes: $0.attributes))
            }
        )
        let home = DeviceSynthesizer.synthesize(
            areas: raw.areas,
            devices: raw.devices,
            entities: raw.entities,
            states: states
        )
        return (home, raw)
    }

    private func devices(_ home: SynthesizedHome) -> [String: SmartDevice] {
        Dictionary(uniqueKeysWithValues: home.devices.map { ($0.id, $0) })
    }

    // MARK: - Humidifier

    @Test("A humidifier card carries power, mode, target humidity and its existing humidity reading")
    func humidifierCard() throws {
        let (home, _) = try load()
        let device = try #require(devices(home)["humidifier-full"])
        guard case .humidifier(let spec) = device.kind else {
            Issue.record("humidifier was not synthesized as .humidifier")
            return
        }
        #expect(device.name == "客厅加湿器")
        #expect(device.roomName == "客厅")
        #expect(device.isOnline)
        #expect(spec.entityId == "humidifier.living")
        #expect(spec.isOn)
        #expect(spec.powerWritable)
        #expect(spec.currentMode == "auto")
        #expect(spec.availableModes == ["auto", "sleep", "strong"])
        #expect(spec.modeWritable)
        #expect(spec.targetHumidity == 55)
        #expect(spec.minHumidity == 30)
        #expect(spec.maxHumidity == 80)
        #expect(spec.step == 5)
        #expect(spec.humidityWritable)
        #expect(spec.currentHumidity == 48, "the entity's own current_humidity wins over the sensor")
        #expect(device.hasDetailDrawer)
        #expect(device.sfSymbolName == "humidity.fill")
        #expect(device.statusSubtitle.contains("55"))
        #expect(device.statusSubtitle.contains("48"))
        #expect(home.environmentReadings.first { $0.id == device.id }?.humidity == 48)

        let facetIDs = Set(home.deviceFacets[device.id, default: []].map(\.id))
        #expect(facetIDs == Set([
            "switch.living_led", "select.living_fan_mode", "number.target_level",
            "button.living_self_clean", "sensor.living_battery",
            "binary_sensor.living_water_shortage",
        ]))

        let facets = home.deviceFacets[device.id, default: []]
        let toggle = try #require(facets.first { $0.id == "switch.living_led" })
        #expect(toggle.kind == .toggle)
        #expect(toggle.value == .bool(true))
        #expect(toggle.target == .haProperty(entityId: "switch.living_led"))
        #expect(toggle.writesByDefault)

        let select = try #require(facets.first { $0.id == "select.living_fan_mode" })
        #expect(select.kind == .select)
        #expect(select.value == .number(1))
        #expect(select.options.map(\.label) == ["low", "medium", "high"])
        #expect(select.writesByDefault, "a mode select follows the #5 low-risk whitelist")
        #expect(select.target == .haProperty(entityId: "select.living_fan_mode"))

        let slider = try #require(facets.first { $0.id == "number.target_level" })
        #expect(slider.kind == .slider)
        #expect(slider.value == .number(40))
        #expect(slider.range == MiotFacetRange(min: 0, max: 100, step: 10))
        #expect(slider.isHighValue)
        #expect(!slider.writesByDefault, "target-level is outside the #5 low-risk whitelist")

        let button = try #require(facets.first { $0.id == "button.living_self_clean" })
        #expect(button.kind == .button)
        #expect(!button.writesByDefault, "an arbitrary action is not on the #5 low-risk whitelist")
        #expect(button.target == .haAction(entityId: "button.living_self_clean"))

        let battery = try #require(facets.first { $0.id == "sensor.living_battery" })
        #expect(battery.kind == .reading)
        #expect(battery.value == .number(88))
        #expect(battery.unit == "%")

        let shortage = try #require(facets.first { $0.id == "binary_sensor.living_water_shortage" })
        #expect(shortage.kind == .reading)
        #expect(shortage.value == .bool(false))
    }

    @Test("A humidifier without modes or target humidity keeps its card and hides those controls")
    func humidifierMissingFields() throws {
        let (home, _) = try load()
        let device = try #require(devices(home)["humidifier-minimal"])
        guard case .humidifier(let spec) = device.kind else {
            Issue.record("minimal humidifier was not synthesized")
            return
        }
        #expect(!spec.isOn)
        #expect(spec.availableModes.isEmpty)
        #expect(!spec.modeWritable)
        #expect(spec.targetHumidity == nil)
        #expect(spec.minHumidity == 0)
        #expect(spec.maxHumidity == 100)
        #expect(spec.step == 1)
        #expect(!spec.humidityWritable)
        #expect(spec.currentHumidity == nil)
        #expect(!device.hasDetailDrawer)
        #expect(device.isPrimaryToggleable)
    }

    // MARK: - Water heater

    @Test("A Home Assistant water heater reuses the appliance card and confirms heat")
    func waterHeaterCard() throws {
        let (home, _) = try load()
        let device = try #require(devices(home)["water-heater-full"])
        guard case .appliance(let spec) = device.kind else {
            Issue.record("water_heater was not synthesized as .appliance")
            return
        }
        #expect(spec.entityId == "water_heater.boiler")
        #expect(spec.powerEntityId == "water_heater.boiler")
        #expect(spec.modeEntityId == "water_heater.boiler")
        #expect(spec.temperatureEntityId == "water_heater.boiler")
        #expect(spec.isOn)
        #expect(spec.powerPresent)
        #expect(spec.powerWritable)
        #expect(spec.currentMode == "eco")
        #expect(spec.availableModes == ["eco", "performance", "electric"])
        #expect(spec.modeWritable)
        #expect(spec.targetTemperature == 55)
        #expect(spec.currentTemperature == 48)
        #expect(spec.minTemp == 35)
        #expect(spec.maxTemp == 75)
        #expect(spec.step == 1)
        #expect(spec.temperatureWritable)
        #expect(spec.heats)
        #expect(device.hasDetailDrawer)
        #expect(device.sfSymbolName == "water.waves")

        #expect(ManualControlPolicy.requiresConfirmation(.power(true), for: device))
        #expect(!ManualControlPolicy.requiresConfirmation(.power(false), for: device))
        #expect(ManualControlPolicy.requiresConfirmation(.targetTemperature(56), for: device))
        #expect(!ManualControlPolicy.requiresConfirmation(.targetTemperature(54), for: device))

        let powerOff = HAWebSocketProvider.mapCommandToService(.setSwitch(entityId: spec.entityId, isOn: false))
        #expect(powerOff.domain == "water_heater")
        #expect(powerOff.service == "turn_off")
        let temperature = HAWebSocketProvider.mapCommandToService(
            .setClimateTemperature(entityId: spec.temperatureEntityId, temperature: 56)
        )
        #expect(temperature.domain == "water_heater")
        #expect(temperature.service == "set_temperature")
        #expect(temperature.payload["temperature"] as? Double == 56)
        let mode = HAWebSocketProvider.mapCommandToService(.setSelectOption(entityId: spec.modeEntityId, option: "eco"))
        #expect(mode.domain == "water_heater")
        #expect(mode.service == "set_operation_mode")
        #expect(mode.payload["operation_mode"] as? String == "eco")
    }

    @Test("A water heater with settings missing still allows power and keeps the heat confirmation")
    func waterHeaterMissingFields() throws {
        let (home, _) = try load()
        let device = try #require(devices(home)["water-heater-minimal"])
        guard case .appliance(let spec) = device.kind else {
            Issue.record("minimal water heater was not synthesized")
            return
        }
        #expect(!spec.isOn)
        #expect(spec.powerPresent)
        #expect(spec.availableModes.isEmpty)
        #expect(!spec.modeWritable)
        #expect(spec.targetTemperature == nil)
        #expect(spec.currentTemperature == nil)
        #expect(!spec.temperatureWritable)
        #expect(spec.heats)
        #expect(!device.hasDetailDrawer)
        #expect(ManualControlPolicy.requiresConfirmation(.power(true), for: device))
        #expect(!ManualControlPolicy.requiresConfirmation(.power(false), for: device))
    }

    // MARK: - Valve

    @Test("Water and gas valves synthesize open/close cards and confirm only opening")
    func valveCards() throws {
        let (home, _) = try load()
        let water = try #require(devices(home)["valve-water"])
        guard case .valve(let waterSpec) = water.kind else {
            Issue.record("water valve was not synthesized")
            return
        }
        #expect(waterSpec.entityId == "valve.water_main")
        #expect(waterSpec.isOpen)
        #expect(waterSpec.deviceClass == "water")
        #expect(waterSpec.motion == nil)
        #expect(waterSpec.openWritable)
        #expect(waterSpec.closeWritable)
        #expect(water.isActiveOn)
        #expect(!water.hasDetailDrawer)
        #expect(!ManualControlPolicy.requiresConfirmation(.power(false), for: water))
        #expect(ManualControlPolicy.requiresConfirmation(.power(true), for: water))
        #expect(water.sfSymbolName == "drop.fill")

        let gas = try #require(devices(home)["valve-gas"])
        guard case .valve(let gasSpec) = gas.kind else {
            Issue.record("gas valve was not synthesized")
            return
        }
        #expect(!gasSpec.isOpen)
        #expect(gasSpec.deviceClass == "gas")
        #expect(ManualControlPolicy.requiresConfirmation(.power(true), for: gas))
        #expect(!ManualControlPolicy.requiresConfirmation(.power(false), for: gas))
        #expect(gas.sfSymbolName == "flame")

        let minimal = try #require(devices(home)["valve-minimal"])
        guard case .valve(let minimalSpec) = minimal.kind else {
            Issue.record("valve without attributes was not synthesized")
            return
        }
        #expect(minimalSpec.deviceClass == nil)
        #expect(minimalSpec.openWritable)
        #expect(minimalSpec.closeWritable)
        #expect(!minimalSpec.isOpen)

        let open = HAWebSocketProvider.mapCommandToService(.setSwitch(entityId: "valve.water_main", isOn: true))
        #expect(open.domain == "valve")
        #expect(open.service == "open_valve")
        let close = HAWebSocketProvider.mapCommandToService(.setSwitch(entityId: "valve.water_main", isOn: false))
        #expect(close.domain == "valve")
        #expect(close.service == "close_valve")
    }

    @Test("The unsupported-device scan no longer lists the three new domains")
    func unsupportedScanExcludesNewDomains() throws {
        let (home, fixture) = try load()
        let states = Dictionary(
            uniqueKeysWithValues: fixture.states.map {
                ($0.entity_id, HAEntityState(entityId: $0.entity_id, state: $0.state, attributes: $0.attributes))
            }
        )
        let skipped = UnsupportedDeviceCollector.homeAssistant(
            devices: fixture.devices,
            entities: fixture.entities,
            states: states,
            synthesized: home
        )
        #expect(skipped.isEmpty)
    }

    // MARK: - Metadata-first matching

    @Test("Plug power and energy come from device_class even when the entity id does not say so")
    func plugPowerAndEnergyUseDeviceClass() {
        let home = DeviceSynthesizer.synthesize(
            areas: [],
            devices: [HADeviceEntry(id: "plug", name: "书桌插座", model: "cuco.plug.v3")],
            entities: [
                HAEntityEntry(entity_id: "switch.load", device_id: "plug"),
                HAEntityEntry(entity_id: "sensor.watts", device_id: "plug"),
                HAEntityEntry(entity_id: "sensor.total_energy", device_id: "plug"),
            ],
            states: [
                "switch.load": HAEntityState(
                    entityId: "switch.load", state: "on",
                    attributes: ["device_class": .string("outlet")]
                ),
                "sensor.watts": HAEntityState(
                    entityId: "sensor.watts", state: "12.5",
                    attributes: ["device_class": .string("power")]
                ),
                "sensor.total_energy": HAEntityState(
                    entityId: "sensor.total_energy", state: "3.2",
                    attributes: ["device_class": .string("energy")]
                ),
            ]
        )
        let device = home.devices.first
        guard case .plug(let spec) = device?.kind else {
            Issue.record("plug card not synthesized")
            return
        }
        #expect(spec.switchEntityId == "switch.load")
        #expect(spec.powerWatts == 12.5)
        #expect(spec.energyKwh == 3.2)
    }

    @Test("Fridge zones follow translation_key instead of the legacy temperature_p_3 substring")
    func fridgeZonesUseTranslationKey() {
        let home = DeviceSynthesizer.synthesize(
            areas: [],
            devices: [HADeviceEntry(id: "fridge", name: "冰箱", model: "midjd6.fridge.test")],
            entities: [
                HAEntityEntry(entity_id: "sensor.cold", device_id: "fridge", translation_key: "temperature_p_3"),
                HAEntityEntry(entity_id: "sensor.freezer", device_id: "fridge", translation_key: "temperature_p_4"),
                HAEntityEntry(entity_id: "number.cold_target", device_id: "fridge", translation_key: "target_temperature_p_3"),
                HAEntityEntry(entity_id: "number.freezer_target", device_id: "fridge", translation_key: "target_temperature_p_4"),
            ],
            states: [
                "sensor.cold": HAEntityState(entityId: "sensor.cold", state: "5"),
                "sensor.freezer": HAEntityState(entityId: "sensor.freezer", state: "-18"),
                "number.cold_target": HAEntityState(entityId: "number.cold_target", state: "4"),
                "number.freezer_target": HAEntityState(entityId: "number.freezer_target", state: "-18"),
            ]
        )
        let device = home.devices.first
        guard case .fridge(let spec) = device?.kind else {
            Issue.record("fridge card not synthesized")
            return
        }
        #expect(spec.fridgeCurrentTemp == 5)
        #expect(spec.freezerCurrentTemp == -18)
        #expect(spec.fridgeTargetTemp == 4)
        #expect(spec.freezerTargetTemp == -18)
        #expect(spec.fridgeTargetEntityId == "number.cold_target")
        #expect(spec.freezerTargetEntityId == "number.freezer_target")
    }

    @Test("A purifier filter follows translation_key instead of the legacy filter_life substring")
    func filterLifeUsesTranslationKey() {
        let home = DeviceSynthesizer.synthesize(
            areas: [],
            devices: [HADeviceEntry(id: "purifier", name: "净化器", model: "zhimi.airp.test")],
            entities: [
                HAEntityEntry(entity_id: "fan.purifier", device_id: "purifier"),
                HAEntityEntry(entity_id: "sensor.cartridge", device_id: "purifier", translation_key: "filter_life_remaining"),
            ],
            states: [
                "fan.purifier": HAEntityState(entityId: "fan.purifier", state: "on"),
                "sensor.cartridge": HAEntityState(entityId: "sensor.cartridge", state: "12"),
            ]
        )
        let device = home.devices.first
        guard case .fan(let spec) = device?.kind else {
            Issue.record("fan card not synthesized")
            return
        }
        #expect(spec.filterLifePercent == 12)
    }

    @Test("HA auxiliary switches follow the power hazard table by device and low-risk tokens")
    func facetConfirmationCoversHADomains() throws {
        let (home, _) = try load()
        let waterHeater = try #require(devices(home)["water-heater-full"])
        let valve = try #require(devices(home)["valve-water"])

        let plug = SmartDevice(
            id: "plug", name: "书桌插座", roomId: "study", roomName: "书房", model: "cuco.plug.v3",
            isOnline: true, kind: .plug(PlugSpec(switchEntityId: "switch.plug", isOn: true))
        )
        let heater = SmartDevice(
            id: "heater", name: "电暖器", roomId: "study", roomName: "书房", model: "xiaomi.heater.v1",
            isOnline: true,
            kind: .climate(ClimateSpec(
                entityId: "climate.heater", isOn: true, hvacMode: "heat",
                availableModes: ["heat", "off"], targetTemperature: 22
            ))
        )
        let window = SmartDevice(
            id: "window", name: "推窗器", roomId: "study", roomName: "书房", model: "linp.wopener.wd1lb",
            isOnline: true,
            kind: .cover(CoverSpec(entityId: "cover.window", subtype: .windowOpener))
        )
        let garage = SmartDevice(
            id: "garage", name: "车库门", roomId: "garage", roomName: "车库", model: "meross.garage.msg100",
            isOnline: true,
            kind: .cover(CoverSpec(entityId: "cover.garage", subtype: .garageDoor))
        )

        func facet(
            _ name: String,
            service: String = "switch",
            kind: MiotFacetKind = .toggle,
            value: JSONValue? = .bool(true),
            options: [MiotFacetOption] = [],
            current: JSONValue? = nil
        ) -> FacetControl {
            FacetControl(
                id: name, kind: kind, name: name, title: name, serviceName: service,
                value: current ?? value, options: options,
                isWritable: true, writesByDefault: false, isHighValue: false,
                target: .haProperty(entityId: name)
            )
        }
        func needs(_ facet: FacetControl, _ value: JSONValue, _ device: SmartDevice) -> Bool {
            ManualControlPolicy.confirmation(facet: facet, value: value, for: device) != nil
        }

        let plugSwitch = facet("dual-plug-socket-2")
        #expect(needs(plugSwitch, .bool(true), plug))
        #expect(!needs(plugSwitch, .bool(false), plug))

        let heaterPower = facet("heater-power")
        #expect(needs(heaterPower, .bool(true), heater))
        #expect(!needs(heaterPower, .bool(false), heater), "turning a heater off stays direct")
        #expect(needs(facet("heater-ptc"), .bool(true), heater))
        #expect(!needs(facet("heater-ptc"), .bool(false), heater), "a device-specific heater switch stays direct off")
        #expect(!needs(facet("heater-led"), .bool(true), heater), "an indicator light is low risk")

        let heaterPtc = facet("bath-heater-ptc")
        #expect(needs(heaterPtc, .bool(true), waterHeater))
        #expect(!needs(heaterPtc, .bool(false), waterHeater))

        let valveSwitch = facet("valve-water")
        #expect(needs(valveSwitch, .bool(true), valve))
        #expect(!needs(valveSwitch, .bool(false), valve))

        #expect(needs(facet("window-open"), .bool(true), window))
        #expect(!needs(facet("window-open"), .bool(false), window))
        #expect(needs(facet("garage-door"), .bool(true), garage))
        #expect(needs(facet("garage-door"), .bool(false), garage), "a garage door moves hazardously both ways")

        let modes = [MiotFacetOption(value: 0, label: "off"), MiotFacetOption(value: 1, label: "eco")]
        let mode = facet("boiler-mode", service: "select", kind: .select, value: .number(1), options: modes)
        #expect(needs(mode, .number(1), waterHeater))
        #expect(!needs(mode, .number(0), waterHeater), "switching to off stays direct")
        #expect(needs(mode, .number(1), plug) == false, "a plug has no water-heater mode hazard")

        let target = facet(
            "boiler-target-temperature", service: "number", kind: .slider,
            value: .number(60), current: .number(55)
        )
        #expect(needs(target, .number(60), waterHeater))
        #expect(!needs(target, .number(50), waterHeater))
    }

    @Test("HA facets prefer translation_key and skip noise, unavailable and maintenance controls")
    func facetFilteringNamingAndMaintenance() {
        let home = DeviceSynthesizer.synthesize(
            areas: [],
            devices: [HADeviceEntry(id: "aux", name: "加湿器", model: "deerma.humidifier.jsq5")],
            entities: [
                HAEntityEntry(entity_id: "humidifier.aux", device_id: "aux"),
                HAEntityEntry(entity_id: "select.aux_control", device_id: "aux", translation_key: "mode"),
                HAEntityEntry(entity_id: "sensor.aux_timestamp", device_id: "aux"),
                HAEntityEntry(entity_id: "button.aux_restart", device_id: "aux"),
                HAEntityEntry(entity_id: "button.aux_offline", device_id: "aux"),
                HAEntityEntry(entity_id: "button.aux_action", device_id: "aux"),
            ],
            states: [
                "humidifier.aux": HAEntityState(entityId: "humidifier.aux", state: "on"),
                "select.aux_control": HAEntityState(
                    entityId: "select.aux_control", state: "auto",
                    attributes: ["options": .array([.string("auto"), .string("sleep")])]
                ),
                "sensor.aux_timestamp": HAEntityState(entityId: "sensor.aux_timestamp", state: "2026-11-01T08:00:00+00:00"),
                "button.aux_restart": HAEntityState(
                    entityId: "button.aux_restart", state: "unknown",
                    attributes: ["device_class": .string("restart")]
                ),
                "button.aux_offline": HAEntityState(entityId: "button.aux_offline", state: "unavailable"),
                "button.aux_action": HAEntityState(entityId: "button.aux_action", state: "unknown"),
            ]
        )
        let facets = home.deviceFacets["aux", default: []]
        #expect(facets.map(\.id) == ["select.aux_control", "button.aux_action"])
        let select = facets.first { $0.id == "select.aux_control" }
        #expect(select?.name == "mode", "translation_key wins over the object id")
        #expect(select?.writesByDefault == true)
        #expect(select?.isHighValue == true)
        let button = facets.first { $0.id == "button.aux_action" }
        #expect(button?.writesByDefault == false)
    }

    @Test("A standalone button card and an orphan safety sensor do not repeat in the facet drawer")
    func claimedStandaloneCards() throws {
        let buttonHome = DeviceSynthesizer.synthesize(
            areas: [],
            devices: [HADeviceEntry(id: "wol", name: "书房台式机", model: "Wake on LAN")],
            entities: [
                HAEntityEntry(entity_id: "button.wake_on_lan_pc", device_id: "wol"),
                HAEntityEntry(entity_id: "button.aux_action", device_id: "wol"),
            ],
            states: [
                "button.wake_on_lan_pc": HAEntityState(entityId: "button.wake_on_lan_pc", state: "unknown"),
                "button.aux_action": HAEntityState(entityId: "button.aux_action", state: "unknown"),
            ]
        )
        #expect(buttonHome.deviceFacets["wol"]?.map(\.id) == ["button.aux_action"])

        let safetyHome = DeviceSynthesizer.synthesize(
            areas: [],
            devices: [],
            entities: [],
            states: [
                "binary_sensor.smoke": HAEntityState(
                    entityId: "binary_sensor.smoke", state: "off",
                    attributes: ["device_class": .string("smoke")]
                ),
            ]
        )
        let sensor = try #require(safetyHome.devices.first)
        guard case .sensor = sensor.kind else {
            Issue.record("smoke binary_sensor did not synthesize a safety sensor card")
            return
        }
        #expect(safetyHome.deviceFacets[sensor.id] == nil)
    }

    // MARK: - Store writes and undo

    @MainActor
    private func makeStore() async throws -> (HomeStore, MockHomeProvider) {
        let (home, _) = try load()
        let provider = MockHomeProvider(snapshot: home)
        let store = HomeStore(provider: provider, defaultsSuiteName: "test.ha.domains.\(UUID().uuidString)")
        store.controlDebounceInterval = .milliseconds(10)
        try await store.waitForTestSnapshot()
        return (store, provider)
    }

    @MainActor
    private func waitUntil(_ condition: @MainActor () async -> Bool) async {
        for _ in 0..<150 {
            if await condition() { return }
            try? await Task.sleep(for: .milliseconds(20))
        }
        Issue.record("condition was not met in time")
    }

    @MainActor
    @Test("HA facet toggles, selects, sliders and buttons dispatch their own services and undo")
    func facetWritesAndUndo() async throws {
        let (store, provider) = try await makeStore()
        store.pauseUndoBannerCountdown()

        func waitForNext(_ command: DeviceCommand) async {
            await waitUntil { await provider.executedCommands.last == command }
        }

        store.setFacetValue("humidifier-full", controlId: "switch.living_led", value: .bool(false))
        await waitForNext(.setSwitch(entityId: "switch.living_led", isOn: false))
        #expect(store.deviceFacets["humidifier-full"]?.first { $0.id == "switch.living_led" }?.value == .bool(false))
        #expect(store.undoBanner?.restoreCommands == [.setSwitch(entityId: "switch.living_led", isOn: true)])

        let undo = try #require(store.undoTurnAllOff())
        await undo.value
        await waitForNext(.setSwitch(entityId: "switch.living_led", isOn: true))
        #expect(store.deviceFacets["humidifier-full"]?.first { $0.id == "switch.living_led" }?.value == .bool(true))

        store.setFacetValue("humidifier-full", controlId: "select.living_fan_mode", value: .number(2))
        await waitForNext(.setSelectOption(entityId: "select.living_fan_mode", option: "high"))

        store.allowAdvancedFacetWrites = true
        store.setFacetValue("humidifier-full", controlId: "number.target_level", value: .number(70))
        await waitForNext(.setNumberValue(entityId: "number.target_level", value: 70))

        var prompts: [ManualControlConfirmation] = []
        store.manualControlConfirmationHandler = { prompt in
            prompts.append(prompt)
            return true
        }
        store.runFacetAction("humidifier-full", controlId: "button.living_self_clean")
        await waitForNext(.pressButton(entityId: "button.living_self_clean"))
        #expect(prompts.count == 1)
    }

    @MainActor
    @Test("Humidifier mode and target humidity write to humidifier.set_mode and set_humidity")
    func humidifierControls() async throws {
        let (store, provider) = try await makeStore()
        store.setHumidifierMode("humidifier-full", mode: "sleep")
        await waitUntil { await provider.executedCommands.count == 1 }
        #expect(await provider.executedCommands.last == .setSelectOption(entityId: "humidifier.living", option: "sleep"))

        store.setHumidifierHumidity("humidifier-full", percent: 60)
        await waitUntil { await provider.executedCommands.count == 2 }
        #expect(await provider.executedCommands.last == .setHumidity(entityId: "humidifier.living", percent: 60))
        #expect(store.devices.first { $0.id == "humidifier-full" }?.humidifierCard?.targetHumidity == 60)
    }

    @MainActor
    @Test("Water heater mode and warmer target keep the manual confirmation gate")
    func waterHeaterControls() async throws {
        let (store, provider) = try await makeStore()
        var prompts: [ManualControlConfirmation] = []
        store.manualControlConfirmationHandler = { prompt in
            prompts.append(prompt)
            return true
        }

        store.setApplianceMode("water-heater-full", mode: "performance")
        await waitUntil { await provider.executedCommands.count == 1 }
        #expect(await provider.executedCommands.last == .setSelectOption(entityId: "water_heater.boiler", option: "performance"))
        #expect(prompts.isEmpty, "an already-running water heater changes mode directly")

        store.stepApplianceTemperature("water-heater-full", delta: 1)
        await waitUntil { await provider.executedCommands.count == 2 }
        #expect(prompts.count == 1)
        #expect(await provider.executedCommands.last == .setClimateTemperature(entityId: "water_heater.boiler", temperature: 56))
    }

    @MainActor
    @Test("Opening a valve asks for confirmation; closing it does not")
    func valveControls() async throws {
        let (store, provider) = try await makeStore()
        var prompts: [ManualControlConfirmation] = []
        store.manualControlConfirmationHandler = { prompt in
            prompts.append(prompt)
            return true
        }

        let gas = try #require(store.devices.first { $0.id == "valve-gas" })
        let openTask = try #require(store.togglePrimary(gas))
        _ = await openTask.value
        await waitUntil { await provider.executedCommands.count >= 1 }
        #expect(prompts.count == 1, "opening a closed gas valve is high risk")
        #expect(await provider.executedCommands.contains(.setSwitch(entityId: "valve.gas_main", isOn: true)))

        let water = try #require(store.devices.first { $0.id == "valve-water" })
        let closeTask = try #require(store.togglePrimary(water))
        _ = await closeTask.value
        await waitUntil { await provider.executedCommands.count >= 2 }
        #expect(prompts.count == 1, "closing an open valve stays direct")
        #expect(await provider.executedCommands.contains(.setSwitch(entityId: "valve.water_main", isOn: false)))
    }
}

private extension SmartDevice {
    var humidifierCard: HumidifierSpec? {
        guard case .humidifier(let spec) = kind else { return nil }
        return spec
    }
}
