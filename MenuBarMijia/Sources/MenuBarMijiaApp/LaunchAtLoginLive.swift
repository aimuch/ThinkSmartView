import ServiceManagement
import MijiaCore

/// Bridges `LaunchAtLoginCoordinator` to the real login-item registration (#281).
@MainActor
struct SystemLaunchAtLogin: LaunchAtLoginSystem {
    var status: LaunchAtLoginStatus {
        switch SMAppService.mainApp.status {
        case .enabled: return .enabled
        case .requiresApproval: return .requiresApproval
        case .notFound: return .notFound
        case .notRegistered: return .notRegistered
        @unknown default: return .notRegistered
        }
    }

    func register() throws {
        try SMAppService.mainApp.register()
    }

    func unregister() throws {
        try SMAppService.mainApp.unregister()
    }

    func openSystemSettings() {
        SMAppService.openSystemSettingsLoginItems()
    }
}

extension LaunchAtLoginCoordinator {
    static func live(store: HomeStore) -> LaunchAtLoginCoordinator {
        LaunchAtLoginCoordinator(
            system: store.launchAtLoginSystem ?? SystemLaunchAtLogin(),
            store: store
        )
    }
}
