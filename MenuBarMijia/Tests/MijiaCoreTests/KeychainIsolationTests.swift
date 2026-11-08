#if os(macOS)
import Testing
import KeychainTestSupport
@testable import MijiaCore

struct KeychainIsolationTests {
    @Test("An uninjected product credential read exits before reaching Security")
    func uninjectedProductReadFailsImmediately() async {
        await #expect(processExitsWith: .exitCode(86)) {
            _ = KeychainHelper.loadSecret(account: "hermetic-guard-probe")
        }
    }

    @Test("An uninjected product credential write exits before reaching Security")
    func uninjectedProductWriteFailsImmediately() async {
        await #expect(processExitsWith: .exitCode(86)) {
            _ = CredentialVault.Storage.keychainItem(account: "hermetic-guard-probe").write(.init())
        }
    }
}
#endif
