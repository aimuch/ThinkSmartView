import Foundation

// MARK: - Control write
//
// Every path that changes a device row ahead of the device's own report goes through here:
// card controls, the assistant, undo, quick actions and Raycast links.
//
//     let write = beginOptimisticWrite(from: previous, to: updated, lock: .seconds(2.5))
//     try await send(command, for: write)   // a failure puts `previous` back
//
// The invariant this module owns: a failed send restores the pre-write row, drops the lock and
// the cover travel origin, but only while no newer write has taken the same device. Before it
// existed the rollback was copied into five places, and the Raycast copies had lost the
// newer-write check and the cover-origin cleanup.

/// One optimistic change to a device row, returned by `beginOptimisticWrite` and handed back
/// to `send` or `revert`.
struct OptimisticWrite: Sendable {
    let deviceId: String
    /// The row before the write; a failed send restores it.
    let previous: SmartDevice
    /// The lock revision this write owns. A newer write on the same device bumps it.
    let revision: UInt64?
    /// False when nothing was shown ahead of the device (a momentary button), so a failure has
    /// nothing to undo.
    let isShown: Bool
}

extension HomeStore {
    @discardableResult
    func lockOptimistic(_ deviceId: String, duration: Duration) -> UInt64 {
        let rev = (optimisticLockRevision[deviceId] ?? 0) &+ 1
        optimisticLockRevision[deviceId] = rev
        optimisticLockUntil[deviceId] = ContinuousClock.now.advanced(by: duration)
        return rev
    }

    func executeDeviceCommand(_ command: DeviceCommand) async throws {
        if LGWebOSProtocol.isLGEntity(command.targetEntityId), let lgClient = lgWebOSClient {
            try await lgClient.execute(command)
        } else {
            try await provider.execute(command)
        }
    }

    /// Shows `updated` now. With a `lock`, stale snapshots cannot overwrite it until the lock
    /// expires or the device reports the same state. Without one the row changes but an earlier
    /// lock (if any) stays in charge. Pass `updated: nil` to send without showing anything.
    func beginOptimisticWrite(from previous: SmartDevice, to updated: SmartDevice?, lock: Duration?) -> OptimisticWrite {
        guard let updated, let idx = devices.firstIndex(where: { $0.id == previous.id }) else {
            return OptimisticWrite(deviceId: previous.id, previous: previous, revision: nil, isShown: false)
        }
        devices[idx] = updated
        syncGenericPowerFacet(updated)
        let revision = lock.map { lockOptimistic(previous.id, duration: $0) } ?? optimisticLockRevision[previous.id]
        return OptimisticWrite(deviceId: previous.id, previous: previous, revision: revision, isShown: true)
    }

    /// True while no newer write has taken this write's device.
    func ownsRow(_ write: OptimisticWrite) -> Bool {
        optimisticLockRevision[write.deviceId] == write.revision
    }

    /// Sends `commands` in order for `write`. On failure the row is rolled back (see `revert`)
    /// unless `context` is given and the control context has moved on, then the error is rethrown.
    func send(_ commands: [DeviceCommand], for write: OptimisticWrite, context: UUID? = nil) async throws {
        do {
            for command in commands {
                try await executeDeviceCommand(command)
            }
        } catch {
            if context == nil || context == controlContextID {
                revert(write)
            }
            throw error
        }
    }

    func send(_ command: DeviceCommand, for write: OptimisticWrite, context: UUID? = nil) async throws {
        try await send([command], for: write, context: context)
    }

    /// Restores `restoring` (the write's previous row by default) when the write still owns the device.
    func revert(_ write: OptimisticWrite, restoring: SmartDevice? = nil) {
        guard write.isShown, ownsRow(write) else { return }
        if let idx = devices.firstIndex(where: { $0.id == write.deviceId }) {
            devices[idx] = restoring ?? write.previous
            syncGenericPowerFacet(devices[idx])
        }
        optimisticLockUntil.removeValue(forKey: write.deviceId)
        coverCommandOrigins.removeValue(forKey: write.deviceId)
    }

    /// Keeps a stored facet row on the same value as the generic tile's main switch. The switch is
    /// normally not in `deviceFacets`, so this is a no-op unless a test or an older snapshot row
    /// still carries it.
    func syncGenericPowerFacet(_ device: SmartDevice) {
        guard case .generic(let spec) = device.kind,
              let control = spec.powerControl,
              let index = deviceFacets[device.id]?.firstIndex(where: { $0.id == control.id }) else {
            return
        }
        deviceFacets[device.id]?[index].value = .bool(spec.isOn)
    }

    // MARK: Card controls

    /// The row a card control acts on, after the lock-screen check.
    func controllableDevice(_ deviceId: String) -> SmartDevice? {
        guard !rejectControlWhileLocked() else { return nil }
        return devices.first(where: { $0.id == deviceId })
    }

    /// A card control's write: show `updated`, send `command` (after `debounceKey`'s quiet period
    /// when given, so a slider sends only its last value), record `habitIntents` on success, and
    /// roll back with a toast on failure.
    func commitManualWrite(
        from previous: SmartDevice,
        to updated: SmartDevice?,
        command: DeviceCommand,
        lock: Duration?,
        debounceKey: String? = nil,
        habitIntents: [DeviceIntent] = []
    ) {
        let write = beginOptimisticWrite(from: previous, to: updated, lock: lock)
        guard let debounceKey else {
            Task {
                do {
                    try await self.send(command, for: write)
                    self.confirmManualControl(intents: habitIntents)
                } catch {
                    self.showControlFailure(error, device: previous, adjustment: false)
                }
            }
            return
        }
        debounceTasks[debounceKey]?.cancel()
        debounceTasks[debounceKey] = Task { [weak self] in
            try? await Task.sleep(for: self?.controlDebounceInterval ?? .milliseconds(280))
            guard !Task.isCancelled, let self, self.ownsRow(write) else { return }
            do {
                try await self.send(command, for: write)
                self.confirmManualControl(intents: habitIntents)
            } catch {
                self.showControlFailure(error, device: previous, adjustment: true)
            }
        }
    }

    func showControlFailure(_ error: Error, device: SmartDevice, adjustment: Bool) {
        guard !ControlFailure.isCancellation(error) else { return }
        showToast(
            ControlFailure.userMessage(
                for: error,
                deviceName: device.name,
                isDeviceOnline: device.isOnline,
                adjustment: adjustment
            ),
            isError: true
        )
    }
}
