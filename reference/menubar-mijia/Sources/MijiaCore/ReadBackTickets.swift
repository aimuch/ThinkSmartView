import Foundation

/// Who is waiting on a device's read-back after a primary toggle.
///
/// A ticket has three lives: it **owns** the tap (the card shows waiting and a second tap is
/// ignored), it stays **in flight** through the echo retry after the tap is released (its
/// snapshots still bypass an older optimistic lock), and it is the device's **latest** ticket until a
/// newer toggle starts, so an older retry can tell it was superseded (#201 review).
/// The waiting set the UI shows is exactly the owned devices.
struct ReadBackTickets: Equatable {
    private var owner: [String: UInt64] = [:]
    private var inFlight: [String: UInt64] = [:]
    private var latest: [String: UInt64] = [:]
    private var next: UInt64 = 0

    /// Devices whose toggle is waiting for the device to answer.
    var waitingDeviceIds: Set<String> { Set(owner.keys) }

    var isEmpty: Bool { owner.isEmpty && inFlight.isEmpty && latest.isEmpty }

    /// A new ticket for `deviceId`, or nil while an earlier toggle still owns the tap.
    mutating func claim(_ deviceId: String) -> UInt64? {
        guard owner[deviceId] == nil else { return nil }
        next &+= 1
        owner[deviceId] = next
        latest[deviceId] = next
        inFlight[deviceId] = next
        return next
    }

    /// Lets the tap go before the echo retry; the ticket stays in flight and latest.
    mutating func releaseTap(_ deviceId: String, ticket: UInt64) {
        if owner[deviceId] == ticket { owner[deviceId] = nil }
    }

    /// The task holding `ticket` is done. Only the latest task clears the lock exemption, so a
    /// newer toggle keeps it alive.
    mutating func finish(_ deviceId: String, ticket: UInt64) {
        if owner[deviceId] == ticket { owner[deviceId] = nil }
        if inFlight[deviceId] == ticket { inFlight[deviceId] = nil }
        if latest[deviceId] == ticket { latest[deviceId] = nil }
    }

    func isLatest(_ deviceId: String, ticket: UInt64) -> Bool { latest[deviceId] == ticket }

    /// A read-back is running for `deviceId`, including the echo retry.
    func isInFlight(_ deviceId: String) -> Bool { inFlight[deviceId] != nil }

    /// Drops every ticket when the control context changes. Ticket numbers keep counting so an
    /// old task can never match a new ticket.
    mutating func reset() {
        owner.removeAll()
        inFlight.removeAll()
        latest.removeAll()
    }
}
