import Foundation
import Testing
@testable import MijiaCore

@Suite("Camera PTZ capability (#97)")
struct CameraPTZCapabilityTests {
    @Test("The 3 Pro PTZ model resolves to the pending adapter, never to a verified motor")
    func userModelIsPending() throws {
        let capability = try #require(
            CameraPTZCapability.resolve(model: "chuangmi.camera.061a01", vendor: "cs2")
        )
        #expect(capability.mode == .unknown)
        #expect(capability.confidence == .pendingRealDevice)
        #expect(capability.supportsPan)
        #expect(capability.supportsTilt)
    }

    @Test("Verified models keep their measured motion mode")
    func verifiedModels() throws {
        let step = try #require(CameraPTZCapability.resolve(model: "chuangmi.camera.029a02", vendor: "cs2"))
        #expect(step.mode == .step)
        #expect(step.confidence == .verified)

        let continuous = try #require(CameraPTZCapability.resolve(model: "xiaomi.camera.c01a01", vendor: "cs2"))
        #expect(continuous.mode == .continuous)
    }

    @Test("A brand alone never grants a motor")
    func brandIsNotCapability() {
        #expect(CameraPTZCapability.resolve(model: "chuangmi.camera.ipc016", vendor: "cs2") == nil)
        #expect(CameraPTZCapability.resolve(model: "loock.cateye.v06", vendor: "cs2") == nil)
    }

    @Test("The Swift table matches the shared model fixture")
    func sharedFixture() throws {
        let fixtureURL = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .appendingPathComponent("../Fixtures/camera_ptz_models.json")
        let data = try Data(contentsOf: fixtureURL)
        let object = try #require(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let rows = try #require(object["models"] as? [[String: String]])
        for row in rows {
            let model = try #require(row["model"])
            let vendor = try #require(row["vendor"])
            let mode = try #require(row["mode"])
            let confidence = try #require(row["confidence"])
            let capability = try #require(
                CameraPTZCapability.resolve(model: model, vendor: vendor),
                "fixture model \(model)"
            )
            #expect(capability.mode.rawValue == mode, "\(model)")
            #expect(capability.confidence.rawValue == confidence, "\(model)")
        }
    }

    @Test("Only the source-verified Dafang models get the TUTK path")
    func tutkDafang() throws {
        let dafang = try #require(CameraPTZCapability.resolve(model: "isa.camera.df3", vendor: "tutk"))
        #expect(dafang.mode == .continuous)
        #expect(dafang.confidence == .verified)
        #expect(CameraPTZCapability.resolve(model: "isa.camera.isc5", vendor: "tutk") == nil)
        #expect(CameraPTZCapability.resolve(model: "chuangmi.camera.061a01", vendor: "tutk") == nil)
    }
}

@Suite("Camera PTZ drag planner (#97)")
struct CameraPTZDragPlannerTests {
    private let capability = CameraPTZCapability(
        model: "chuangmi.camera.061a01",
        vendor: "cs2",
        mode: .unknown,
        confidence: .pendingRealDevice
    )

    private func planner() -> CameraPTZDragPlanner {
        CameraPTZDragPlanner(timing: .init(
            activationDistance: 4,
            axisHysteresis: 0.25,
            heartbeatInterval: 0.25,
            maxHold: 8
        ))
    }

    @Test("A tiny jitter below the threshold sends nothing")
    func threshold() {
        var planner = planner()
        planner.began(at: .init(x: 100, y: 100), time: 0)
        #expect(planner.moved(to: .init(x: 102, y: 101), time: 0.05, capability: capability).isEmpty)
        #expect(planner.moved(to: .init(x: 110, y: 100), time: 0.1, capability: capability) == [.move(.right)])
    }

    @Test("Dragging right, left, up and down maps to the camera moving the same way")
    func directions() {
        var planner = planner()
        planner.began(at: .init(x: 0, y: 0), time: 0)
        #expect(planner.moved(to: .init(x: -20, y: 0), time: 0.1, capability: capability) == [.move(.left)])
        #expect(planner.moved(to: .init(x: 20, y: 0), time: 0.2, capability: capability) == [.move(.right)])
        #expect(planner.moved(to: .init(x: 0, y: -20), time: 0.3, capability: capability) == [.move(.up)])
        #expect(planner.moved(to: .init(x: 0, y: 20), time: 0.4, capability: capability) == [.move(.down)])
    }

    @Test("A diagonal jitter cannot flip the axis until it dominates by the hysteresis")
    func axisLock() {
        var planner = planner()
        planner.began(at: .init(x: 0, y: 0), time: 0)
        #expect(planner.moved(to: .init(x: 30, y: 4), time: 0.1, capability: capability) == [.move(.right)])
        #expect(planner.moved(to: .init(x: 30, y: 8), time: 0.2, capability: capability).isEmpty)
        #expect(planner.moved(to: .init(x: 20, y: 60), time: 0.4, capability: capability) == [.move(.down)])
    }

    @Test("Holding still renews at the heartbeat cadence, not on every move event")
    func heartbeatCadence() {
        var planner = planner()
        planner.began(at: .init(x: 0, y: 0), time: 0)
        #expect(planner.moved(to: .init(x: 20, y: 0), time: 0.1, capability: capability) == [.move(.right)])
        #expect(planner.moved(to: .init(x: 22, y: 0), time: 0.2, capability: capability).isEmpty)
        #expect(planner.moved(to: .init(x: 22, y: 0), time: 0.4, capability: capability) == [.heartbeat])
        #expect(planner.moved(to: .init(x: 22, y: 0), time: 0.5, capability: capability).isEmpty)
    }

    @Test("Release and cancel stop an active move; an idle press sends nothing")
    func releaseAndCancel() {
        var idle = planner()
        idle.began(at: .init(x: 0, y: 0), time: 0)
        #expect(idle.ended().isEmpty)

        var active = planner()
        active.began(at: .init(x: 0, y: 0), time: 0)
        _ = active.moved(to: .init(x: 20, y: 0), time: 0.1, capability: capability)
        #expect(active.ended() == [.stop])
        #expect(active.isActive == false)
    }

    @Test("The hold cap stops the motor even when the user never releases")
    func holdCap() {
        var planner = planner()
        planner.began(at: .init(x: 0, y: 0), time: 0)
        _ = planner.moved(to: .init(x: 20, y: 0), time: 0.1, capability: capability)
        #expect(planner.heartbeat(time: 8.1) == [.stop])
        #expect(planner.isActive == false)
    }

    @Test("A pan-only or tilt-only camera ignores the unsupported axis")
    func axisSupport() {
        let horizontal = CameraPTZCapability(
            model: "test", vendor: "cs2", mode: .step, confidence: .verified,
            supportsPan: true, supportsTilt: false
        )
        var planner = planner()
        planner.began(at: .init(x: 0, y: 0), time: 0)
        #expect(planner.moved(to: .init(x: 0, y: 30), time: 0.1, capability: horizontal).isEmpty)
        #expect(planner.moved(to: .init(x: 30, y: 0), time: 0.2, capability: horizontal) == [.move(.right)])
    }
}

@Suite("Camera PTZ command queue (#97)")
struct CameraPTZCommandQueueTests {
    @Test("A pending direction survives a heartbeat and still reaches the wire")
    func heartbeatDoesNotReplacePendingMove() {
        var queue = CameraPTZCommandQueue()
        #expect(queue.submit(.move(.left, 1)) == .move(.left, 1))
        #expect(queue.submit(.move(.right, 2)) == nil)
        #expect(queue.submit(.heartbeat(3)) == nil)
        #expect(queue.pending == .move(.right, 2))
        #expect(queue.complete() == .move(.right, 2))
        #expect(queue.complete() == nil)
    }

    @Test("A newer move replaces an older pending move and a pending heartbeat")
    func newestDirectionWins() {
        var queue = CameraPTZCommandQueue()
        #expect(queue.submit(.move(.left, 1)) == .move(.left, 1))
        #expect(queue.submit(.move(.right, 2)) == nil)
        #expect(queue.submit(.move(.up, 3)) == nil)
        #expect(queue.pending == .move(.up, 3))
        #expect(queue.complete() == .move(.up, 3))
        #expect(queue.submit(.heartbeat(4)) == nil)
        #expect(queue.pending == .heartbeat(4))
        #expect(queue.complete() == .heartbeat(4))
    }

    @Test("Planner direction change survives a heartbeat while the previous move is in flight")
    func plannerAndQueueKeepLatestDirection() {
        let capability = CameraPTZCapability(
            model: "chuangmi.camera.061a01",
            vendor: "cs2",
            mode: .unknown,
            confidence: .pendingRealDevice
        )
        var planner = CameraPTZDragPlanner()
        var queue = CameraPTZCommandQueue()
        planner.began(at: .init(x: 0, y: 0), time: 0)
        #expect(queue.submit(.move(.left, 1)) == .move(.left, 1))
        #expect(planner.moved(to: .init(x: 30, y: 0), time: 0.05, capability: capability) == [.move(.right)])
        #expect(queue.submit(.move(.right, 2)) == nil)
        _ = planner.heartbeat(time: 0.5)
        #expect(queue.submit(.heartbeat(3)) == nil)
        #expect(queue.pending == .move(.right, 2))
        #expect(queue.complete() == .move(.right, 2))
    }

    @Test("Reset on release drops the pending intent")
    func reset() {
        var queue = CameraPTZCommandQueue()
        #expect(queue.submit(.move(.left, 1)) == .move(.left, 1))
        #expect(queue.submit(.heartbeat(2)) == nil)
        queue.reset()
        #expect(queue.inFlight == false)
        #expect(queue.pending == nil)
        #expect(queue.complete() == nil)
    }
}

@Suite("CS2 command channel drain mode (#97)")
struct CS2CommandChannelDrainTests {
    private func packet(seq: UInt16) -> [UInt8] {
        [0xF1, 0xD0, 0x00, 0x10, 0xD1, 0, UInt8(seq >> 8), UInt8(seq & 0xFF), 0, 0, 0, 4, 1, 2, 3, 4]
    }

    @Test("A patched bridge that drains channel 0 receives every command message")
    func drainingBridgeKeepsEveryMessage() {
        var gate = CS2CommandChannelGate(isDraining: true)
        for seq in UInt16(0)..<UInt16(20) {
            let dropped = gate.shouldDrop(packet(seq: seq), count: 16)
            #expect(!dropped)
        }
    }

    @Test("An official bridge still drops the overflow to protect the video session")
    func stockBridgeStillGates() {
        var gate = CS2CommandChannelGate(isDraining: false)
        for seq in UInt16(0)..<UInt16(CS2CommandChannelGate.passthroughLimit) {
            let dropped = gate.shouldDrop(packet(seq: seq), count: 16)
            #expect(!dropped)
        }
        let overflow = gate.shouldDrop(packet(seq: 100), count: 16)
        #expect(overflow)
    }
}
