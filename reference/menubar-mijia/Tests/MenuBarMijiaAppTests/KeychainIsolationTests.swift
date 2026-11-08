import Testing
import KeychainTestSupport
@testable import MijiaCore

struct AppKeychainIsolationTests {
    @Test("The app test bundle rejects real credential reads before Security")
    func uninjectedReadFailsImmediately() async {
        await #expect(processExitsWith: .exitCode(86)) {
            _ = KeychainHelper.loadSecret(account: "hermetic-app-guard-probe")
        }
    }
}
