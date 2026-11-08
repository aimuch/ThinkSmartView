import KeychainTestGuard
@testable import MijiaCore

@_cdecl("install_test_keychain_guard")
public func installTestKeychainGuard() {
    SystemKeychainAccess.installGuard { keychain_guard_fail() }
}
