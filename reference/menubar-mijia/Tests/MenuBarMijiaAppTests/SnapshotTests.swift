import Testing
import Foundation
import SwiftUI
import AppKit
@testable import MijiaCore
@testable import MenuBarMijiaApp

/// UI snapshots for the menu-bar panel and the five device-card families (#46).
///
/// Two-phase baseline: `bash scripts/record_snapshots.sh` runs `SNAPSHOT_RECORD=1 swift test --filter
/// SnapshotTests` on the maintainer's Mac, which is also the GitLab CI runner, and writes PNGs into
/// `Tests/MenuBarMijiaAppTests/__Snapshots__/SnapshotTests/`; those files are then committed.
/// SnapshotAssertion reports "Record mode is on" as a failure for that run, so recording exits
/// non-zero while still producing every baseline. Later runs compare
/// against them; on a mismatch the new bitmap is written under `$SNAPSHOT_ARTIFACTS`/SnapshotTests
/// (or `$TMPDIR/SnapshotTests`) and the diff command is printed in the failure message.
///
/// The renderer is fed fixed demo data and the accessibility "reduce motion" / "reduce transparency"
/// switches, so animations and materials cannot make two runs differ. The suite is serialized
/// because it changes `UserDefaults.standard` (the panel reads skin/appearance through
/// `@AppStorage`) and `L10n.currentLanguage`.
@MainActor
@Suite(.serialized)
struct SnapshotTests {
    private var recordAll: Bool {
        ProcessInfo.processInfo.environment["SNAPSHOT_RECORD"] == "1"
    }

    // MARK: - Panel: one system look (Chinese, light, single column)

    @Test("Panel renders in the system look")
    func panelSnapshot() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }

        UserDefaults.standard.set(AppAppearance.system.rawValue, forKey: AppAppearance.storageKey)

        assertRendered(
            snapshotReady(ControlCenterPanel(store: fixture.store)),
            size: CGSize(width: 376, height: 560),
            named: "panel-zh-light"
        )
    }

    @Test("Panel shows the expired Mi session banner")
    func panelExpiredMiSessionSnapshot() async throws {
        let fixture = try await SnapshotFixture(
            miSession: MiSession(
                userId: "42",
                cUserId: "c",
                ssecurity: "c2VjcmV0",
                serviceToken: "tok",
                expiresAt: Date(timeIntervalSince1970: 1_700_000_000)
            )
        )
        defer { fixture.restore() }

        UserDefaults.standard.set(AppAppearance.system.rawValue, forKey: AppAppearance.storageKey)

        assertRendered(
            snapshotReady(ControlCenterPanel(store: fixture.store)),
            size: CGSize(width: 376, height: 560),
            named: "panel-mi-session-expired-zh-light"
        )
    }

    @Test("Panel asks before sending the HA token over public plain http, and keeps the declined reason", arguments: [
        (HAPlaintextPrompt.asking("http://203.0.113.5:8123"), "panel-ha-plaintext-asking-zh-light"),
        (HAPlaintextPrompt.declined("http://203.0.113.5:8123"), "panel-ha-plaintext-declined-zh-light"),
    ])
    func panelHAPlaintextSnapshot(prompt: HAPlaintextPrompt, name: String) async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }

        UserDefaults.standard.set(AppAppearance.system.rawValue, forKey: AppAppearance.storageKey)
        fixture.store.haPlaintextPrompt = prompt

        assertRendered(
            snapshotReady(ControlCenterPanel(store: fixture.store)),
            size: CGSize(width: 376, height: 560),
            named: name
        )
    }

    // MARK: - Room order (#146)

    @Test("The room-order list renders over the dashboard with 16 rooms")
    func roomOrderSnapshot() async throws {
        let fixture = try await SnapshotFixture(roomCount: 16, editMode: .roomOrder)
        defer { fixture.restore() }

        UserDefaults.standard.set(AppAppearance.system.rawValue, forKey: AppAppearance.storageKey)

        assertRendered(
            snapshotReady(ControlCenterPanel(store: fixture.store)),
            size: CGSize(width: 376, height: 560),
            named: "panel-room-order-zh-light"
        )

        // The drag needs the real backing scroll view: without it auto-scroll never runs and the
        // pointer-to-row mapping loses the scroll offset (found in review, guarded here).
        let scrollView = try #require(
            RoomOrderScrollProbe.lastResolvedScrollView,
            "the room-order list must hand the drag its NSScrollView"
        )
        let document = try #require(scrollView.documentView)
        #expect(
            document.frame.height > scrollView.contentView.bounds.height,
            "16 rooms must overflow the list so the drag can scroll"
        )
    }

    // MARK: - Device cards: light / climate / plug / sensor / TV

    @Test("Five device card families render")
    func deviceCardSnapshots() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }
        let light = try #require(fixture.store.devices.first { device in
            if case .light = device.kind { return true } else { return false }
        })
        let climate = try #require(fixture.store.devices.first { device in
            if case .climate = device.kind { return true } else { return false }
        })
        let plug = try #require(fixture.store.devices.first { device in
            if case .plug = device.kind { return true } else { return false }
        })
        let sensor = try #require(fixture.store.devices.first { device in
            if case .sensor = device.kind { return true } else { return false }
        })
        let tv = try #require(fixture.store.devices.first { device in
            if case .tv = device.kind { return true } else { return false }
        })

        let cards: [(String, SmartDevice)] = [
            ("light", light),
            ("climate", climate),
            ("plug", plug),
            ("sensor", sensor),
            ("tv", tv)
        ]

        for (name, device) in cards {
            let view = snapshotReady(
                DeviceCardView(device: device, store: fixture.store, showRoomBadge: true)
                    .padding(8)
                    .frame(width: 360)
            )
            assertRendered(
                view,
                size: CGSize(width: 376, height: 140),
                named: "card-\(name)-zh-light"
            )
        }
    }

    // MARK: - Generic fallback cards (#6)

    @Test("Generic controllable and read-only cards render")
    func genericCardSnapshots() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }

        for (readOnly, name) in [(false, "generic-card-zh-light"), (true, "generic-card-readonly-zh-light")] {
            let (device, facets) = Self.genericDevice(readOnly: readOnly)
            fixture.store.devices.append(device)
            fixture.store.deviceFacets[device.id] = facets
            assertRendered(
                snapshotReady(
                    DeviceCardView(device: device, store: fixture.store, showRoomBadge: true)
                        .padding(8)
                        .frame(width: 360)
                ),
                size: CGSize(width: 376, height: 140),
                named: name
            )
        }
    }

    @Test("A read-only generic drawer explains why it has no controls")
    func genericReadOnlyDrawerSnapshot() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }

        let (device, facets) = Self.genericDevice(readOnly: true)
        fixture.store.devices.append(device)
        fixture.store.deviceFacets[device.id] = facets
        fixture.store.expandedDeviceId = device.id

        assertRendered(
            snapshotReady(
                DeviceCardView(device: device, store: fixture.store, showRoomBadge: true)
                    .padding(8)
                    .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 260),
            named: "generic-readonly-drawer-zh-light"
        )
    }

    @Test("A generic drawer explains why no low-risk main switch exists")
    func genericHighRiskDrawerSnapshot() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }

        let mode = FacetControl(
            id: "4.2", kind: .select, name: "mode", title: "工作模式", serviceName: "router",
            value: .number(0),
            options: [MiotFacetOption(value: 0, label: "路由模式"), MiotFacetOption(value: 1, label: "中继模式")],
            isWritable: true, writesByDefault: false, isHighValue: false,
            target: .property(entityId: "miot:snapshot-router:4", piid: 2)
        )
        let card = GenericSpec(
            deviceType: "router",
            typeTitle: "路由器",
            isOn: false,
            powerControl: nil,
            isReadOnly: false,
            readouts: [.init(value: "已连接")],
            symbolName: "wifi"
        )
        let device = SmartDevice(
            id: "snapshot-generic-risk", name: "路由器", roomId: "living", roomName: "客厅",
            model: "isa.router.mr01hl", isOnline: true, kind: .generic(card)
        )
        fixture.store.devices.append(device)
        fixture.store.deviceFacets[device.id] = [mode]
        fixture.store.expandedDeviceId = device.id

        assertRendered(
            snapshotReady(
                DeviceCardView(device: device, store: fixture.store, showRoomBadge: true)
                    .padding(8)
                    .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 260),
            named: "generic-high-risk-drawer-zh-light"
        )
    }

    @Test("A fish tank drawer shows the pump alert and the portioned feeding action")
    func fishTankDrawerSnapshot() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }

        let (device, card, facets) = try Self.fishTank()
        fixture.store.devices.append(device)
        fixture.store.deviceFacets[device.id] = facets

        assertRendered(
            snapshotReady(
                VStack(alignment: .leading, spacing: 10) {
                    GenericDetailDrawer(spec: card, statusColor: DesignTokens.active)
                    DeviceFacetSection(
                        device: device,
                        store: fixture.store,
                        accent: DesignTokens.active,
                        statusColor: DesignTokens.active,
                        initiallyExpanded: true
                    )
                }
                .padding(10)
                .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 520),
            named: "fish-tank-drawer-zh-light"
        )
    }

    /// A real `xiaomi.fishbowl.m200` synthesized from the public spec: pump blocked, 24.5 °C water,
    /// 12 portions fed today (#7).
    private static func fishTank() throws -> (SmartDevice, GenericSpec, [FacetControl]) {
        var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        for _ in 0..<8 {
            if FileManager.default.fileExists(atPath: directory.appendingPathComponent("Tests/Fixtures").path) {
                break
            }
            directory.deleteLastPathComponent()
        }
        let model = "xiaomi.fishbowl.m200"
        let data = try Data(contentsOf: directory.appendingPathComponent("Tests/Fixtures/miot_coverage_specs.json"))
        let specs = try JSONDecoder().decode([String: MiotDeviceSpec].self, from: data)
        let spec = try #require(specs[model])
        let did = "snapshot-fish-tank"
        let home = MiotSynthesizer.synthesize(
            devices: [MiDeviceRecord(did: did, name: "鱼缸", model: model, isOnline: true)],
            rooms: [],
            specs: [model: spec],
            values: [
                MiotPropKey(did: did, siid: 2, piid: 1): .bool(true),
                MiotPropKey(did: did, siid: 2, piid: 4): .number(4),
                MiotPropKey(did: did, siid: 2, piid: 6): .number(24.5),
                MiotPropKey(did: did, siid: 3, piid: 1): .bool(true),
                MiotPropKey(did: did, siid: 3, piid: 2): .number(0),
                MiotPropKey(did: did, siid: 5, piid: 2): .number(12),
                MiotPropKey(did: did, siid: 6, piid: 1): .number(64),
                MiotPropKey(did: did, siid: 13, piid: 2): .bool(true),
            ]
        )
        let device = try #require(home.devices.first)
        guard case .generic(let card) = device.kind else {
            throw CocoaError(.featureUnsupported)
        }
        let facets = home.deviceFacets[did] ?? []
        return (device, card, facets)
    }

    private static func genericDevice(readOnly: Bool) -> (SmartDevice, [FacetControl]) {
        let power = FacetControl(
            id: "2.1", kind: .toggle, name: "on", title: "开关", serviceName: "fish-tank",
            value: .bool(true), isWritable: true, writesByDefault: true, isHighValue: false,
            target: .property(entityId: "miot:snapshot-generic:2", piid: 1)
        )
        let temperature = FacetControl(
            id: "3.1", kind: .reading, name: "temperature", title: "温度", serviceName: "temperature",
            unit: "celsius", value: .number(24.5), isWritable: false, writesByDefault: false, isHighValue: true
        )
        let humidity = FacetControl(
            id: "3.2", kind: .reading, name: "relative-humidity", title: "湿度", serviceName: "temperature",
            unit: "percentage", value: .number(52), isWritable: false, writesByDefault: false, isHighValue: false
        )
        let mode = FacetControl(
            id: "4.2", kind: .select, name: "mode", title: "模式", serviceName: "fish-tank",
            value: .number(0),
            options: [MiotFacetOption(value: 0, label: "自动"), MiotFacetOption(value: 1, label: "静音")],
            isWritable: true, writesByDefault: false, isHighValue: false,
            target: .property(entityId: "miot:snapshot-generic:4", piid: 2)
        )
        let card = GenericSpec(
            deviceType: "fish-tank",
            typeTitle: "鱼缸",
            isOn: !readOnly,
            powerControl: readOnly ? nil : power,
            isReadOnly: readOnly,
            readouts: [.init(value: "24.5 °C"), .init(value: "52%")],
            symbolName: "fish.fill"
        )
        let device = SmartDevice(
            id: "snapshot-generic", name: "鱼缸", roomId: "living", roomName: "客厅",
            model: "acme.fishbowl.v1", isOnline: true, kind: .generic(card)
        )
        return (device, readOnly ? [temperature, humidity] : [temperature, humidity, mode])
    }

    @Test("Cover drawer shows travel")
    func coverDrawerMovingSnapshot() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }

        let spec = CoverSpec(
            entityId: "cover.curtain",
            subtype: .curtain,
            positionPercent: 60,
            reportedPositionPercent: 40,
            positionWritable: true,
            openDescription: "Open",
            closeDescription: "Close",
            pauseDescription: "Pause",
            motion: .opening
        )
        let device = SmartDevice(
            id: "snapshot-cover", name: "客厅窗帘", roomId: "living", roomName: "客厅",
            model: "lumi.curtain.hagl05", isOnline: true,
            kind: .cover(spec)
        )

        assertRendered(
            snapshotReady(
                CoverDetailDrawer(
                    device: device,
                    spec: spec,
                    store: fixture.store,
                    accent: DesignTokens.active,
                    statusColor: DesignTokens.active
                )
                .padding(8)
                .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 160),
            named: "cover-drawer-opening-zh-light"
        )
    }

    @Test("Airer drawer reads as height with raise / stop / lower while lowering (#299)")
    func airerDrawerLoweringSnapshot() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }

        let spec = CoverSpec(
            entityId: "miot:airer1:2",
            subtype: .clothesAirer,
            positionPercent: 35,
            reportedPositionPercent: 70,
            positionWritable: true,
            openDescription: "Up",
            closeDescription: "Down",
            pauseDescription: "Pause",
            motion: .closing
        )
        let device = SmartDevice(
            id: "snapshot-airer", name: "阳台晾衣架", roomId: "balcony", roomName: "阳台",
            model: "xiaomi.airer.maxb", isOnline: true,
            kind: .cover(spec)
        )

        assertRendered(
            snapshotReady(
                CoverDetailDrawer(
                    device: device,
                    spec: spec,
                    store: fixture.store,
                    accent: DesignTokens.active,
                    statusColor: DesignTokens.active
                )
                .padding(8)
                .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 160),
            named: "airer-drawer-lowering-zh-light"
        )
    }

    @Test("More Settings starts folded, then shows every facet style with closed writes explained")
    func facetDrawerSnapshots() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }

        let spec = LightSpec(
            entityId: "light.facet",
            isOn: true,
            brightnessPercent: 60,
            colorTempKelvin: 4000
        )
        let device = SmartDevice(
            id: "snapshot-facets", name: "客厅灯", roomId: "living", roomName: "客厅",
            model: "test.facet.light", isOnline: true,
            kind: .light(spec)
        )
        fixture.store.deviceFacets = [device.id: [
            FacetControl(
                id: "3.2", kind: .slider, name: "delaytime", title: "延时关闭",
                serviceName: "setmode", value: .number(3), range: .init(min: 0, max: 20, step: 1),
                isWritable: true, writesByDefault: true, isHighValue: true,
                target: .property(entityId: "miot:snapshot-facets:3", piid: 2)
            ),
            FacetControl(
                id: "2.4", kind: .select, name: "default-power-on-state", title: "通电默认状态",
                serviceName: "light", value: .number(0),
                options: [
                    MiotFacetOption(value: 0, label: "开"),
                    MiotFacetOption(value: 1, label: "记忆"),
                    MiotFacetOption(value: 2, label: "关"),
                ],
                isWritable: true, writesByDefault: false, isHighValue: false,
                target: .property(entityId: "miot:snapshot-facets:2", piid: 4)
            ),
            FacetControl(
                id: "3.1", kind: .toggle, name: "flex-switch", title: "灵动开关",
                serviceName: "setmode", value: .bool(true),
                isWritable: true, writesByDefault: false, isHighValue: false,
                target: .property(entityId: "miot:snapshot-facets:3", piid: 1)
            ),
            FacetControl(
                id: "3.a1", kind: .button, name: "restart-device", title: "重启设备",
                serviceName: "setmode",
                isWritable: true, writesByDefault: false, isHighValue: false,
                target: .action(entityId: "miot-action:snapshot-facets:3:1")
            ),
            FacetControl(
                id: "3.4", kind: .reading, name: "temperature", title: "温度",
                serviceName: "setmode", unit: "celsius", value: .number(23.5),
                isWritable: false, writesByDefault: false, isHighValue: false
            ),
            FacetControl(
                id: "3.5", kind: .reading, name: "relative-humidity", title: "湿度",
                serviceName: "setmode", unit: "percentage", value: .number(52),
                isWritable: false, writesByDefault: false, isHighValue: false
            ),
            FacetControl(
                id: "3.6", kind: .reading, name: "battery-level", title: "电量",
                serviceName: "setmode", unit: "percentage", value: .number(88),
                isWritable: false, writesByDefault: false, isHighValue: false
            ),
            FacetControl(
                id: "3.7", kind: .reading, name: "left-time", title: "剩余时间",
                serviceName: "setmode", unit: "minutes", value: .number(12),
                isWritable: false, writesByDefault: false, isHighValue: false
            ),
            FacetControl(
                id: "3.8", kind: .reading, name: "volume", title: "音量",
                serviceName: "setmode", value: .number(30),
                isWritable: false, writesByDefault: false, isHighValue: false
            ),
        ]]

        assertRendered(
            snapshotReady(
                DeviceFacetSection(
                    device: device,
                    store: fixture.store,
                    accent: DesignTokens.active,
                    statusColor: DesignTokens.active
                )
                .padding(10)
                .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 64),
            named: "facet-drawer-collapsed-zh-light"
        )

        assertRendered(
            snapshotReady(
                DeviceFacetSection(
                    device: device,
                    store: fixture.store,
                    accent: DesignTokens.active,
                    statusColor: DesignTokens.active,
                    initiallyExpanded: true
                )
                .padding(10)
                .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 380),
            named: "facet-drawer-expanded-zh-light"
        )
    }

    @Test("Appliance card shows the water heater controls the spec publishes")
    func applianceCardSnapshots() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }
        let device = Self.waterHeater

        assertRendered(
            snapshotReady(
                DeviceCardView(device: device, store: fixture.store, showRoomBadge: true)
                    .padding(8)
                    .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 140),
            named: "card-appliance-zh-light"
        )

        fixture.store.expandedDeviceId = device.id
        assertRendered(
            snapshotReady(
                DeviceCardView(device: device, store: fixture.store, showRoomBadge: true)
                    .padding(8)
                    .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 320),
            named: "expanded-appliance-zh-light"
        )

        var faulty = device
        if case .appliance(var spec) = faulty.kind {
            spec.faultText = "E3"
            faulty.kind = .appliance(spec)
        }
        assertRendered(
            snapshotReady(
                DeviceCardView(device: faulty, store: fixture.store, showRoomBadge: true)
                    .padding(8)
                    .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 320),
            named: "expanded-appliance-fault-zh-light"
        )
    }

    @Test("Washer card shows the wash stage, remaining time and its long programme row")
    func laundryApplianceCardSnapshots() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }
        let device = Self.washer

        assertRendered(
            snapshotReady(
                DeviceCardView(device: device, store: fixture.store, showRoomBadge: true)
                    .padding(8)
                    .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 140),
            named: "card-appliance-washer-zh-light"
        )

        fixture.store.expandedDeviceId = device.id
        assertRendered(
            snapshotReady(
                DeviceCardView(device: device, store: fixture.store, showRoomBadge: true)
                    .padding(8)
                    .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 320),
            named: "expanded-appliance-washer-zh-light"
        )
    }

    @Test("An expanded air-conditioner card lists its synthesized 更多设置 rows (#30)")
    func expandedClimateFacetSnapshot() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }
        let savedPanel = UserDefaults.standard.object(forKey: PanelPrefs.climatePanelKey)
        UserDefaults.standard.set(true, forKey: PanelPrefs.climatePanelKey)
        defer {
            if let savedPanel {
                UserDefaults.standard.set(savedPanel, forKey: PanelPrefs.climatePanelKey)
            } else {
                UserDefaults.standard.removeObject(forKey: PanelPrefs.climatePanelKey)
            }
        }

        let climate = try #require(fixture.store.devices.first { device in
            if case .climate = device.kind { return true } else { return false }
        })
        // Two synthesized controls the panel card used to drop (#30): the fresh-air switch and the
        // louver direction. Both live in the shared 更多设置 section the card now appends.
        fixture.store.deviceFacets[climate.id] = [
            FacetControl(
                id: "4.1", kind: .toggle, name: "on", title: "新风", serviceName: "fresh-air",
                value: .bool(true), isWritable: true, writesByDefault: true, isHighValue: true,
                target: .property(entityId: "miot:\(climate.id):4", piid: 1)
            ),
            FacetControl(
                id: "3.1", kind: .select, name: "vertical-swing", title: "上下摆风", serviceName: "air-conditioner",
                value: .number(1),
                options: [MiotFacetOption(value: 0, label: "关"), MiotFacetOption(value: 1, label: "开")],
                isWritable: true, writesByDefault: true, isHighValue: true,
                target: .property(entityId: "miot:\(climate.id):3", piid: 1)
            ),
        ]
        fixture.store.expandedDeviceId = climate.id

        assertRendered(
            snapshotReady(
                DeviceCardView(device: climate, store: fixture.store, showRoomBadge: true)
                    .padding(8)
                    .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 460),
            named: "expanded-climate-facets-zh-light"
        )
    }

    /// The program names are the released `mibx5.washer.f27vn` list (#3); 25 entries is what the
    /// drawer's scrolling pill row is for.
    private static var washer: SmartDevice {
        SmartDevice(
            id: "snapshot-washer",
            name: "阳台洗衣机",
            roomId: "balcony",
            roomName: "阳台",
            model: "mibx5.washer.f27vn",
            isOnline: true,
            kind: .appliance(ApplianceSpec(
                entityId: "miot:snapshot-washer:2",
                isOn: true,
                powerPresent: true,
                powerWritable: true,
                currentMode: "Mix",
                availableModes: [
                    "Mix", "Quick Wash", "Delicate Wash", "Heavy Wash", "Rinse Spin", "Spin", "Intensive",
                    "Baby Care", "Drum Clean", "Wool", "Down Coat", "Cotton", "Synthetic", "Underwear",
                    "Jacket", "Smart", "Sportswear", "New-Clothes Wash", "Silk", "Jeans", "Pleased Wash",
                    "Turban", "User Define", "Bedding", "Shirt",
                ],
                modeWritable: true,
                statusText: L10n.tr("洗涤中", "Washing"),
                remainingMinutes: 42,
                startAction: ApplianceAction(kind: .start, entityId: "miot-action:snapshot-washer:2:2"),
                pauseAction: ApplianceAction(kind: .pause, entityId: "miot-action:snapshot-washer:2:3"),
                stopAction: ApplianceAction(kind: .stop, entityId: "miot-action:snapshot-washer:2:1")
            ))
        )
    }

    @Test("A bath heater and a hood render every control merged onto one card")
    func aggregatedApplianceCardSnapshots() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }
        let cases: [(device: SmartDevice, name: String, size: CGSize)] = [
            (Self.bathHeater, "expanded-bath-heater-zh-light", CGSize(width: 376, height: 440)),
            (Self.hood, "expanded-hood-zh-light", CGSize(width: 376, height: 360)),
        ]
        for item in cases {
            fixture.store.expandedDeviceId = item.device.id
            assertRendered(
                snapshotReady(
                    DeviceCardView(device: item.device, store: fixture.store, showRoomBadge: true)
                        .padding(8)
                        .frame(width: 360)
                ),
                size: item.size,
                named: item.name
            )
        }
    }

    private static var bathHeater: SmartDevice {
        SmartDevice(
            id: "snapshot-bath-heater",
            name: "浴室浴霸",
            roomId: "bath",
            roomName: "浴室",
            model: "xiaomi.bhf_light.v1",
            isOnline: true,
            kind: .appliance(ApplianceSpec(
                entityId: "miot:snapshot-bath-heater:3",
                isOn: true,
                powerPresent: true,
                powerWritable: true,
                currentMode: "Quick Heat",
                availableModes: ["Idle", "Dry", "Defog", "Quick Defog", "Quick Heat", "Other"],
                modeWritable: true,
                targetTemperature: 40,
                currentTemperature: 26,
                minTemp: 25,
                maxTemp: 45,
                step: 1,
                temperatureWritable: true,
                statusText: L10n.tr("运行中", "Running"),
                light: ApplianceLight(entityId: "miot:snapshot-bath-heater:2", isOn: true, writable: true),
                fanLevel: ApplianceFanLevel(
                    entityId: "miot:snapshot-bath-heater:5",
                    current: "High",
                    options: ["Low", "High"],
                    writable: true
                ),
                currentHumidity: 58,
                symbolName: "heater.vertical",
                roomReading: true
            ))
        )
    }

    private static var hood: SmartDevice {
        SmartDevice(
            id: "snapshot-hood",
            name: "厨房油烟机",
            roomId: "kitchen",
            roomName: "厨房",
            model: "viomi.hood.c26",
            isOnline: true,
            kind: .appliance(ApplianceSpec(
                entityId: "miot:snapshot-hood:2",
                isOn: true,
                powerPresent: true,
                powerWritable: true,
                statusText: L10n.tr("已开启", "On"),
                light: ApplianceLight(entityId: "miot:snapshot-hood:6", isOn: true, writable: true),
                fanLevel: ApplianceFanLevel(
                    entityId: "miot:snapshot-hood:10",
                    current: "High",
                    options: ["Low", "High", "Stir Fry"],
                    writable: true
                ),
                symbolName: "wind"
            ))
        )
    }

    private static var waterHeater: SmartDevice {
        SmartDevice(
            id: "snapshot-water-heater",
            name: "浴室热水器",
            roomId: "bath",
            roomName: "浴室",
            model: "viomi.waterheater.u3",
            isOnline: true,
            kind: .appliance(ApplianceSpec(
                entityId: "miot:snapshot-water-heater:2",
                isOn: true,
                powerPresent: true,
                powerWritable: true,
                currentMode: "Standard",
                availableModes: ["Standard", "Energy1", "Quick"],
                modeWritable: true,
                targetTemperature: 60,
                currentTemperature: 52,
                minTemp: 35,
                maxTemp: 75,
                step: 0.5,
                temperatureWritable: true,
                statusText: L10n.tr("加热中", "Heating"),
                remainingMinutes: 18,
                startAction: ApplianceAction(kind: .start, entityId: "miot-action:snapshot-water-heater:2:1"),
                pauseAction: ApplianceAction(kind: .pause, entityId: "miot-action:snapshot-water-heater:2:2")
            ))
        )
    }

    private static var humidifier: SmartDevice {
        SmartDevice(
            id: "snapshot-humidifier",
            name: "卧室加湿器",
            roomId: "bedroom",
            roomName: "卧室",
            model: "deerma.humidifier.jsq5",
            isOnline: true,
            kind: .humidifier(HumidifierSpec(
                entityId: "humidifier.bedroom",
                isOn: true,
                currentMode: "auto",
                availableModes: ["auto", "sleep", "strong"],
                modeWritable: true,
                targetHumidity: 55,
                minHumidity: 30,
                maxHumidity: 80,
                step: 5,
                humidityWritable: true,
                currentHumidity: 48,
                powerWritable: true
            ))
        )
    }

    private static var valve: SmartDevice {
        SmartDevice(
            id: "snapshot-valve",
            name: "入户水阀",
            roomId: "kitchen",
            roomName: "厨房",
            model: "tuya.valve",
            isOnline: true,
            kind: .valve(ValveSpec(
                entityId: "valve.water_main",
                isOpen: true,
                deviceClass: "water"
            ))
        )
    }

    @Test("Humidifier and valve cards render their own controls")
    func haDomainCardSnapshots() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }

        assertRendered(
            snapshotReady(
                DeviceCardView(device: Self.humidifier, store: fixture.store, showRoomBadge: true)
                    .padding(8)
                    .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 140),
            named: "card-humidifier-zh-light"
        )

        fixture.store.expandedDeviceId = Self.humidifier.id
        assertRendered(
            snapshotReady(
                DeviceCardView(device: Self.humidifier, store: fixture.store, showRoomBadge: true)
                    .padding(8)
                    .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 280),
            named: "expanded-humidifier-zh-light"
        )

        fixture.store.expandedDeviceId = nil
        assertRendered(
            snapshotReady(
                DeviceCardView(device: Self.valve, store: fixture.store, showRoomBadge: true)
                    .padding(8)
                    .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 140),
            named: "card-valve-zh-light"
        )
    }

    @Test("Home Assistant facets render as toggles, selects, sliders, buttons and readings")
    func haFacetDrawerSnapshot() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }
        let device = Self.humidifier
        fixture.store.deviceFacets[device.id] = [
            FacetControl(
                id: "switch.living_led", kind: .toggle, name: "indicator-light", title: "指示灯",
                serviceName: "switch", value: .bool(true),
                isWritable: true, writesByDefault: true, isHighValue: false,
                target: .haProperty(entityId: "switch.living_led")
            ),
            FacetControl(
                id: "select.living_fan_mode", kind: .select, name: "fan-mode", title: "风速",
                serviceName: "select", value: .number(1),
                options: [
                    MiotFacetOption(value: 0, label: "低"),
                    MiotFacetOption(value: 1, label: "中"),
                    MiotFacetOption(value: 2, label: "高"),
                ],
                isWritable: true, writesByDefault: true, isHighValue: false,
                target: .haProperty(entityId: "select.living_fan_mode")
            ),
            FacetControl(
                id: "number.target_level", kind: .slider, name: "target-level", title: "目标档位",
                serviceName: "number", value: .number(40),
                range: MiotFacetRange(min: 0, max: 100, step: 10),
                isWritable: true, writesByDefault: false, isHighValue: true,
                target: .haProperty(entityId: "number.target_level")
            ),
            FacetControl(
                id: "button.living_self_clean", kind: .button, name: "self-clean", title: "自清洁",
                serviceName: "button",
                isWritable: true, writesByDefault: false, isHighValue: false,
                target: .haAction(entityId: "button.living_self_clean")
            ),
            FacetControl(
                id: "sensor.living_battery", kind: .reading, name: "battery", title: "电量",
                serviceName: "sensor", unit: "percentage", value: .number(88),
                isWritable: false, writesByDefault: false, isHighValue: false
            ),
        ]

        assertRendered(
            snapshotReady(
                DeviceFacetSection(
                    device: device,
                    store: fixture.store,
                    accent: DesignTokens.active,
                    statusColor: DesignTokens.active,
                    initiallyExpanded: true
                )
                .padding(10)
                .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 360),
            named: "ha-facet-drawer-expanded-zh-light"
        )
    }

    @Test("Climate card wears its tinted platter when on")
    func climateOnCardSnapshot() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }
        var climate = try #require(fixture.store.devices.first { device in
            if case .climate = device.kind { return true } else { return false }
        })
        await fixture.store.togglePrimary(climate)?.value
        climate = try #require(fixture.store.devices.first { $0.id == climate.id })
        #expect(climate.isActiveOn, "MockHomeProvider should apply the power toggle")

        assertRendered(
            snapshotReady(
                DeviceCardView(device: climate, store: fixture.store, showRoomBadge: true)
                    .padding(8)
                    .frame(width: 360)
            ),
            size: CGSize(width: 376, height: 140),
            named: "card-climate-on-zh-light"
        )
        assertRendered(
            snapshotReady(
                DeviceCardView(device: climate, store: fixture.store, showRoomBadge: true)
                    .padding(8)
                    .frame(width: 360),
                scheme: .dark
            ),
            size: CGSize(width: 376, height: 140),
            named: "card-climate-on-zh-dark",
            scheme: .dark
        )
    }

    @Test("Settings remain readable in light and dark appearances")
    func settingsSnapshots() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }
        for tab in [SettingsTab.general, .connection, .assistant, .appearance, .privacy, .about] {
            for scheme in [ColorScheme.light, .dark] {
                assertRendered(
                    SettingsWindowView(store: fixture.store, selectedTab: tab)
                        .environment(\.colorScheme, scheme)
                        .environment(\.reduceMotionOverride, true)
                        .environment(\.reduceTransparencyOverride, true),
                    size: CGSize(width: 780, height: 680),
                    named: "settings-\(tab.rawValue)-\(scheme == .dark ? "dark" : "light")",
                    scheme: scheme
                )
            }
        }
    }

    @Test("Settings capsule fits English at minimum window size with glass enabled")
    func settingsCapsuleVariants() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }
        for language in [AppLanguage.zhHans, .en] {
            L10n.currentLanguage = language
            fixture.store.appLanguagePreference = AppLanguagePreference(language)
            for scheme in [ColorScheme.light, .dark] {
                assertRendered(
                    SettingsWindowView(store: fixture.store, selectedTab: .privacy)
                        .environment(\.colorScheme, scheme)
                        .environment(\.reduceMotionOverride, true)
                        .environment(\.reduceTransparencyOverride, false),
                    size: CGSize(width: 740, height: 600),
                    named: "settings-capsule-\(language == .en ? "en" : "zh")-\(scheme == .dark ? "dark" : "light")",
                    scheme: scheme
                )
            }
        }
    }

    @Test("Settings stay proportional in wide windows and with motion enabled")
    func settingsWideWindows() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }
        for width: CGFloat in [900, 1200] {
            for scheme in [ColorScheme.light, .dark] {
                assertRendered(
                    SettingsWindowView(store: fixture.store)
                        .environment(\.colorScheme, scheme)
                        .environment(\.reduceMotionOverride, false)
                        .environment(\.reduceTransparencyOverride, false),
                    size: CGSize(width: width, height: 680),
                    named: "settings-wide-\(Int(width))-\(scheme == .dark ? "dark" : "light")",
                    scheme: scheme
                )
            }
        }
    }

    @Test("Panel supports dark, compact and expanded controls")
    func panelInteractionStates() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }
        for compact in [false, true] {
            fixture.store.isCompactGridMode = compact
            assertRendered(
                snapshotReady(ControlCenterPanel(store: fixture.store), scheme: .dark),
                size: CGSize(width: 376, height: 560),
                named: compact ? "panel-compact-dark" : "panel-dark",
                scheme: .dark
            )
        }
        let light = try #require(fixture.store.devices.first { if case .light = $0.kind { true } else { false } })
        fixture.store.expandedDeviceId = light.id
        for scheme in [ColorScheme.light, .dark] {
            assertRendered(
                snapshotReady(DeviceCardView(device: light, store: fixture.store, showRoomBadge: true).padding(8), scheme: scheme),
                size: CGSize(width: 376, height: 260),
                named: "expanded-light-\(scheme == .dark ? "dark" : "light")",
                scheme: scheme
            )
        }
    }

    // MARK: - Non-visual guards for the appearance work (#51) and tooltip (#54)

    @Test("On and off tiles never share the same ink or fill")
    func platterContrastHoldsInBothSchemes() {
        let lit = TileSurface(isActive: true, scheme: .light)
        let quiet = TileSurface(isActive: false, scheme: .light)
        #expect(lit.ink == PanelLook.platterInk)
        #expect(quiet.ink == .primary)
        #expect(lit.secondaryInk == PanelLook.platterInk.opacity(0.55))
        #expect(quiet.secondaryInk == .secondary)
        // The platter keeps a defined hairline in light mode; wells get the quieter one.
        #expect(lit.hairline != quiet.hairline)

        // A tinted lit surface is dark blue glass in dark mode, so it takes white ink and the
        // family hairline instead of the platter rules.
        let darkTinted = TileSurface(isActive: true, tint: DesignTokens.Orb.climate, scheme: .dark)
        #expect(darkTinted.ink == .white)
        #expect(darkTinted.ink != lit.ink)
        #expect(darkTinted.secondaryInk == .white.opacity(0.62))
        #expect(darkTinted.hairline != lit.hairline)

        // Dark mode never puts a white platter under the window's ink: every lit surface there is
        // dark glass, so it keeps white ink instead of the light-mode platter ink.
        let darkLit = TileSurface(isActive: true, scheme: .dark)
        #expect(darkLit.ink == .white)
        #expect(darkLit.secondaryInk == .white.opacity(0.62))
        #expect(darkLit.ink != lit.ink)

        // The bright platter is the only surface that owns the fixed dark ink, and it exists in
        // light mode only. If a future fill change reintroduces a white tile in dark mode, this
        // pairing breaks before the ink can go white-on-white.
        #expect(lit.isBrightPlatter)
        #expect(!quiet.isBrightPlatter)
        #expect(!darkLit.isBrightPlatter)
        #expect(!darkTinted.isBrightPlatter)
    }

    @Test("Scene icons map to one visual category per family")
    func sceneVisualCategoriesMapFromSymbols() {
        // Quick actions own their category outright; no symbol parsing involved.
        #expect(QuickAction.lightsOff.visualCategory == .light)
        #expect(QuickAction.softNightLight.visualCategory == .night)
        #expect(QuickAction.climateSleepOff.visualCategory == .climate)
        // Externally supplied scene icons resolve through the centralised keyword map.
        #expect(SceneVisualCategory(iconSymbol: "moon.stars.fill") == .night)
        #expect(SceneVisualCategory(iconSymbol: "wind.snow") == .climate)
        #expect(SceneVisualCategory(iconSymbol: "drop.fill") == .water)
        #expect(SceneVisualCategory(iconSymbol: "flame.fill") == .heat)
        #expect(SceneVisualCategory(iconSymbol: "tv.fill") == .media)
        // Unknown symbols land on the warm default instead of vanishing.
        #expect(SceneVisualCategory(iconSymbol: "sparkles") == .light)
    }

    @Test("Menu-bar tooltip summarises devices, temperature and backend")
    func menuBarSummaryDescribesCurrentState() async throws {
        let fixture = try await SnapshotFixture()
        defer { fixture.restore() }

        let text = MenuBarStatusSummary.text(for: fixture.store)
        #expect(text.hasPrefix("MenuBarMijia · "))
        #expect(text.contains("台开启"))
        #expect(text.contains("已连接 Home Assistant"))
    }

    // MARK: - Camera live HUD audio states (#29)

    @Test("Camera HUD labels video-only streams and offers unmute for HLS")
    func cameraAudioHUDStates() async throws {
        let previousLanguage = L10n.currentLanguage
        L10n.currentLanguage = .zhHans
        defer { L10n.currentLanguage = previousLanguage }

        let states: [(CameraLiveAudioStatus, String)] = [
            (.videoOnly, "video-only"),
            (.audio(isMuted: true), "muted"),
            (.audio(isMuted: false), "unmuted"),
        ]
        for (status, name) in states {
            assertRendered(
                snapshotReady(
                    CameraLiveAudioStatusView(status: status) {}
                        .frame(width: 140, height: 44)
                        .background(Color(red: 0.08, green: 0.10, blue: 0.15)),
                    scheme: .dark
                ),
                size: CGSize(width: 140, height: 44),
                named: "camera-audio-\(name)-dark",
                scheme: .dark
            )
        }
    }

    // MARK: - Camera PTZ conditional UI (#97)

    @Test("The compact live panel shows the longest PTZ stop-failure banner")
    func cameraPTZPanelNoticeSnapshot() async throws {
        let fixture = try await SnapshotFixture(includeCamera: true)
        defer { fixture.restore() }
        let camera = try #require(fixture.store.devices.first { device in
            if case .camera = device.kind { return true } else { return false }
        })
        let controller = CameraLivePanelController.shared
        controller.isExpandedSize = false
        controller.configurePTZPreview(
            store: fixture.store,
            deviceId: camera.id,
            binding: CameraPTZBinding(
                capability: CameraPTZCapability(
                    model: "chuangmi.camera.061a01",
                    vendor: "cs2",
                    mode: .unknown,
                    confidence: .pendingRealDevice
                ),
                streamName: "cam_raw_test"
            ),
            stream: .demo(deviceId: camera.id),
            notice: "停止指令写入失败，云台可能仍在转动",
            ready: true
        )
        defer { controller.configurePTZPreview(deviceId: nil, binding: nil, stream: nil, notice: nil, ready: false) }

        assertRendered(
            snapshotReady(CameraLivePanelView(controller: controller, store: fixture.store), scheme: .dark),
            size: CGSize(width: CameraLivePanelController.compactSize.width, height: CameraLivePanelController.compactSize.height),
            named: "camera-ptz-panel-notice-dark",
            scheme: .dark
        )
    }

    // MARK: - Rendering switches

    private func snapshotReady<V: View>(_ view: V, scheme: ColorScheme = .light) -> some View {
        view
            .environment(\.colorScheme, scheme)
            .environment(\.reduceMotionOverride, true)
            .environment(\.reduceTransparencyOverride, true)
            .transaction { transaction in
                transaction.disablesAnimations = true
            }
    }

    /// Renders a SwiftUI view through an `NSHostingView` inside a borderless window and snapshots the NSImage.
    private func assertRendered<V: View>(
        _ view: V,
        size: CGSize,
        named name: String,
        scheme: ColorScheme = .light,
        fileID: StaticString = #fileID,
        file: StaticString = #filePath,
        testName: String = #function,
        line: Int = #line,
        column: Int = #column
    ) {
        // `swift test` does not launch an app; AppKit still needs its shared application object
        // before offscreen windows and hosting views can be created.
        _ = NSApplication.shared

        let speech = SpeechInputController.shared
        let savedListening = speech.isListening
        let savedHoldToTalk = speech.isHoldToTalk
        let savedDraft = speech.draft
        // The shared controller also keeps the last start error; a suite running in parallel that
        // presses the mic (outside a packaged app) would otherwise put its banner into the capture.
        let savedError = speech.errorMessage
        speech.setPreviewState(isListening: false, isHoldToTalk: false, draft: "")
        defer {
            speech.setPreviewState(
                isListening: savedListening,
                isHoldToTalk: savedHoldToTalk,
                draft: savedDraft,
                errorMessage: savedError
            )
        }

        // A control that holds focus must not draw its ring into the bitmap (#323).
        let hosting = NSHostingView(rootView: view.focusEffectDisabled())
        hosting.frame = NSRect(origin: .zero, size: size)

        let window = SnapshotWindow.make(size: size)
        window.appearance = NSAppearance(named: scheme == .dark ? .darkAqua : .aqua)
        window.contentView = hosting

        guard let bitmap = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: Int(size.width),
            pixelsHigh: Int(size.height),
            bitsPerSample: 8,
            samplesPerPixel: 4,
            hasAlpha: true,
            isPlanar: false,
            colorSpaceName: .deviceRGB,
            bytesPerRow: 0,
            bitsPerPixel: 0
        ) else {
            Issue.record("Could not create a 1x snapshot bitmap")
            return
        }
        bitmap.size = size
        SnapshotWindow.prepareForCapture(window)
        hosting.cacheDisplay(in: hosting.bounds, to: bitmap)
        let image = NSImage(size: size)
        image.addRepresentation(bitmap)
        if let failure = SnapshotAssertion.verifyImage(
            image,
            named: name,
            recordAll: recordAll,
            file: file,
            testName: testName
        ) {
            Issue.record(
                Comment(rawValue: failure),
                sourceLocation: SourceLocation(
                    fileID: String(describing: fileID),
                    filePath: String(describing: file),
                    line: line,
                    column: column
                )
            )
        }

        window.contentView = nil
        window.orderOut(nil)
    }
}

/// A loaded store over the built-in virtual home, with a fixed language and the standard-defaults
/// keys the panel reads through `@AppStorage`.
@MainActor
private struct SnapshotFixture {
    struct MissingDefaults: Error {}

    let store: HomeStore
    private let previousAppearance: String?
    private let previousTemperatureUnit: String?
    private let previousLanguage: AppLanguage
    private let previousRoomOrder: String?
    private let previousRoomAliases: String?

    /// - Parameters:
    ///   - roomCount: builds a home with that many rooms instead of the demo's three, so the
    ///     room-order list can be rendered at the scale it was designed for (#146).
    ///   - editMode: opens the panel in that mode, e.g. `.roomOrder` for the order list baseline.
    init(
        miSession: MiSession? = nil,
        roomCount: Int? = nil,
        editMode: PanelEditMode = .none,
        includeCamera: Bool = false
    ) async throws {
        previousLanguage = L10n.currentLanguage
        L10n.currentLanguage = .zhHans
        previousAppearance = UserDefaults.standard.string(forKey: AppAppearance.storageKey)
        previousTemperatureUnit = UserDefaults.standard.string(forKey: TemperatureUnitPreference.storageKey)
        // The panel reads room order/aliases through `@AppStorage` on `.standard`, i.e. the real
        // developer's preferences. Save them and hand the renderer a deterministic empty value —
        // clearing without restoring would wipe a developer's own room layout on every test run.
        previousRoomOrder = UserDefaults.standard.string(forKey: RoomPrefs.orderKey)
        previousRoomAliases = UserDefaults.standard.string(forKey: RoomPrefs.aliasesKey)
        UserDefaults.standard.set(AppAppearance.system.rawValue, forKey: AppAppearance.storageKey)
        UserDefaults.standard.set(TemperatureUnitPreference.celsius.rawValue, forKey: TemperatureUnitPreference.storageKey)
        UserDefaults.standard.removeObject(forKey: RoomPrefs.orderKey)
        UserDefaults.standard.removeObject(forKey: RoomPrefs.aliasesKey)
        // The session state is a process-wide observable: a previous test's draft must not leak in.
        LayoutUIState.shared.resetForTesting()

        let suiteName = "MenuBarMijiaSnapshotTests-\(UUID().uuidString)"
        guard let defaults = UserDefaults(suiteName: suiteName) else {
            throw MissingDefaults()
        }
        defaults.set(true, forKey: "has_completed_onboarding")
        defaults.set(AppLanguagePreference.zhHans.rawValue, forKey: "app_language")
        defaults.set(TemperatureUnitPreference.celsius.rawValue, forKey: TemperatureUnitPreference.storageKey)
        // The panel's `.task` checks for updates; keep the snapshot offline and deterministic.
        defaults.set(false, forKey: "automatically_checks_for_updates")
        // Empty isolated credentials are part of the snapshot fixture, never the developer's vault.
        #expect(KeychainHelper.loadToken(defaults: defaults).isEmpty)
        #expect(AssistantConfig.userStoredAPIKey(forBaseURL: AssistantConfig.defaultBaseURL, defaults: defaults).isEmpty)
        if let miSession {
            defaults.set(HomeBackend.xiaomiCloud.rawValue, forKey: "home_backend")
            guard MiSessionStore.save(miSession, defaults: defaults) else { throw MissingDefaults() }
        }

        store = HomeStore(
            provider: MockHomeProvider(snapshot: Self.home(roomCount: roomCount, includeCamera: includeCamera)),
            defaultsSuiteName: suiteName
        )

        var attempts = 0
        while store.devices.isEmpty && attempts < 200 {
            try await Task.sleep(for: .milliseconds(20))
            attempts += 1
        }
        #expect(!store.devices.isEmpty, "MockHomeProvider should deliver a snapshot right away")

        if editMode == .roomOrder {
            LayoutUIState.shared.beginRoomOrder(
                rooms: store.rooms,
                order: RoomPrefs.storedOrder(),
                aliases: RoomPrefs.decodeAliases(UserDefaults.standard.string(forKey: RoomPrefs.aliasesKey) ?? "{}")
            )
        }
    }

    /// The demo home with its three rooms replaced by `roomCount` rooms; devices are spread over
    /// them so the list shows more than "0 台" on every row.
    private static func home(roomCount: Int?, includeCamera: Bool = false) -> SynthesizedHome {
        var home = DemoHomeProvider.defaultHome(includeCamera: includeCamera)
        guard let roomCount, roomCount > 0 else { return home }
        let names = [
            "客厅", "主卧", "书房", "儿童房", "厨房", "餐厅", "阳台", "玄关",
            "卫生间", "次卧", "客房", "衣帽间", "储物间", "影音室", "健身房", "阁楼"
        ]
        let rooms = (0..<roomCount).map { index in
            RoomArea(
                id: "room_\(index)",
                name: index < names.count ? names[index] : "房间 \(index + 1)",
                sortOrder: index
            )
        }
        home.rooms = rooms
        home.devices = home.devices.enumerated().map { index, device in
            var device = device
            let room = rooms[index % rooms.count]
            device.roomId = room.id
            device.roomName = room.name
            return device
        }
        return home
    }

    func restore() {
        if let previousAppearance {
            UserDefaults.standard.set(previousAppearance, forKey: AppAppearance.storageKey)
        } else {
            UserDefaults.standard.removeObject(forKey: AppAppearance.storageKey)
        }
        L10n.currentLanguage = previousLanguage
        if let previousTemperatureUnit {
            UserDefaults.standard.set(previousTemperatureUnit, forKey: TemperatureUnitPreference.storageKey)
        } else {
            UserDefaults.standard.removeObject(forKey: TemperatureUnitPreference.storageKey)
        }
        // Put the developer's real room preferences back, byte for byte.
        if let previousRoomOrder {
            UserDefaults.standard.set(previousRoomOrder, forKey: RoomPrefs.orderKey)
        } else {
            UserDefaults.standard.removeObject(forKey: RoomPrefs.orderKey)
        }
        if let previousRoomAliases {
            UserDefaults.standard.set(previousRoomAliases, forKey: RoomPrefs.aliasesKey)
        } else {
            UserDefaults.standard.removeObject(forKey: RoomPrefs.aliasesKey)
        }
        LayoutUIState.shared.resetForTesting()
    }
}

/// Brings the offscreen window the snapshot harness captures into its final state.
@MainActor
enum SnapshotWindow {
    /// An offscreen borderless window in which no control can hold keyboard focus (#351). SwiftUI may
    /// hand focus to the first focusable control (the header mic button) on any later run-loop pass,
    /// so clearing focus once before the capture (#323) still let a focus ring into CI baselines.
    static func make(size: CGSize) -> NSWindow {
        pinOverlayScrollers()
        let window = CaptureWindow(
            contentRect: NSRect(origin: .zero, size: size),
            styleMask: [.borderless],
            backing: .buffered,
            defer: false
        )
        window.isReleasedWhenClosed = false
        return window
    }

    /// With "Show scroll bars: automatically", AppKit switches to legacy scrollers once it notices a
    /// mouse, a beat after launch; every scroll view in a capture then gives 17 pt to a scroller and the
    /// layout no longer matches the baselines, which were drawn with overlay scrollers. Pin the setting
    /// in the argument domain, which outranks the user's global one, so attached hardware cannot matter.
    private static func pinOverlayScrollers() {
        let defaults = UserDefaults.standard
        var arguments = defaults.volatileDomain(forName: UserDefaults.argumentDomain)
        guard arguments["AppleShowScrollBars"] as? String != "WhenScrolling" else { return }
        arguments["AppleShowScrollBars"] = "WhenScrolling"
        defaults.setVolatileDomain(arguments, forName: UserDefaults.argumentDomain)
    }

    private final class CaptureWindow: NSWindow {
        override var canBecomeKey: Bool { false }
        override var canBecomeMain: Bool { false }

        override func makeFirstResponder(_ responder: NSResponder?) -> Bool {
            super.makeFirstResponder(nil)
        }
    }

    /// Takes keyboard focus away, then lays the window out and draws it, so a ring drawn for an earlier
    /// focus is repainted before the capture (#323). Call it immediately before `cacheDisplay`.
    static func prepareForCapture(_ window: NSWindow) {
        window.makeFirstResponder(nil)
        window.layoutIfNeeded()
        window.contentView?.layoutSubtreeIfNeeded()
        window.contentView?.displayIfNeeded()
        window.displayIfNeeded()
        window.makeFirstResponder(nil)
    }
}
