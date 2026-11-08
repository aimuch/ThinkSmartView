@testable import MijiaCore

extension HomeStore {
    /// Waits for the first mock snapshot. Returns as soon as it lands; the deadline only matters on a
    /// loaded machine, where the 4 s production default and fixed poll counts flaked (#349).
    func waitForTestSnapshot() async throws {
        try await waitForInitialSnapshot(timeout: .seconds(30))
    }
}
