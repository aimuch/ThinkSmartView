import Foundation

/// Why a control write failed, in the buckets a user can act on (#283). Anything that does not
/// fit stays `nil`, so the caller keeps the generic copy it always showed.
enum ControlFailure: Sendable {
    case deviceOffline
    case rateLimited
    /// The Xiaomi Home session lapsed; scanning the QR code again fixes it.
    case sessionExpired
    /// No Xiaomi Home account is signed in at all.
    case signedOut
    /// Home Assistant refused the long-lived access token; it has to be replaced in Settings.
    case tokenRejected
    case deviceRejected(code: Int)
    case network

    /// MIoT per-item code for "device offline". Other negative per-item codes are device/gateway
    /// errors, which are rejections rather than a lost connection.
    static let deviceOfflineMiotCode = -704042011

    /// The toast copy for a failed write. An account-wide verdict (session, token, rate limit) is direct
    /// evidence and wins; otherwise the device's last known online state wins over a device or
    /// transport error, because a write cannot land while the device is offline.
    static func userMessage(for error: Error, deviceName: String, isDeviceOnline: Bool, adjustment: Bool) -> String {
        guard let failure = classify(error, isDeviceOnline: isDeviceOnline) else {
            return verb(deviceName: deviceName, adjustment: adjustment)
        }
        return failure.message(deviceName: deviceName, adjustment: adjustment)
    }

    /// A write superseded by a newer one (a slider still moving) was cancelled, not failed.
    static func isCancellation(_ error: Error) -> Bool {
        error is CancellationError || (error as? URLError)?.code == .cancelled
    }

    private static func classify(_ error: Error, isDeviceOnline: Bool) -> ControlFailure? {
        let failure = classify(error)
        switch failure {
        case .some(.sessionExpired), .some(.signedOut), .some(.tokenRejected), .some(.rateLimited):
            return failure
        default:
            // Errors raised before anything reached the device say nothing about whether it is online.
            if isDeviceOnline || isCancellation(error) || neverLeftThisMac(error) { return failure }
            return .deviceOffline
        }
    }

    /// The command was refused locally: no backend for it, a malformed address, or an action the spec lacks.
    private static func neverLeftThisMac(_ error: Error) -> Bool {
        if error is MiotCommandError { return true }
        switch error as? HomeProviderError {
        case .invalidURL?, .backendNotConfigured?: return true
        default: return false
        }
    }

    private static func classify(_ error: Error) -> ControlFailure? {
        if isCancellation(error) { return nil }
        if let cloud = error as? MiCloudError {
            switch cloud {
            case .notLoggedIn:
                return .signedOut
            case .sessionExpired:
                return .sessionExpired
            case .http(let status):
                return failure(forHTTPStatus: status, authFailure: .sessionExpired)
            case .api:
                // A cloud-envelope code is not a device verdict.
                return nil
            case .deviceError(let code, _):
                if code == deviceOfflineMiotCode { return .deviceOffline }
                return .deviceRejected(code: code)
            case .badResponse:
                return nil
            }
        }
        if let provider = error as? HomeProviderError {
            switch provider {
            case .unauthorized:
                return .tokenRejected
            case .networkError:
                return .network
            case .serviceCallFailed(let status, _):
                return failure(forHTTPStatus: status, authFailure: .tokenRejected)
            case .invalidURL, .backendNotConfigured:
                return nil
            }
        }
        if error is URLError {
            return .network
        }
        return nil
    }

    /// Only statuses that mean "no answer from the service itself" are network errors. A plain 500 is the
    /// service (or a Home Assistant integration) reporting its own failure, so it keeps the generic copy.
    private static func failure(forHTTPStatus status: Int, authFailure: ControlFailure) -> ControlFailure? {
        switch status {
        case 401, 403: return authFailure
        case 429: return .rateLimited
        case 408, 502, 503, 504: return .network
        default: return nil
        }
    }

    private func message(deviceName: String, adjustment: Bool) -> String {
        let reason: String
        switch self {
        case .deviceOffline:
            reason = L10n.tr("设备离线，等它重新上线后再试", "device offline, try again when it reconnects")
        case .rateLimited:
            reason = L10n.tr("请求太频繁，稍后再试", "too many requests, try again shortly")
        case .sessionExpired:
            reason = L10n.tr("登录已过期，请在设置里重新登录", "sign-in expired, sign in again in Settings")
        case .signedOut:
            reason = L10n.tr("未登录米家，请在设置里登录", "not signed in to Xiaomi Home, sign in from Settings")
        case .tokenRejected:
            reason = L10n.tr("访问令牌无效，请在设置里更新", "access token rejected, update it in Settings")
        case .deviceRejected(let code):
            reason = L10n.tr(
                "设备拒绝了这次操作（米家错误码 \(code)）",
                "the device rejected this command (Mijia error code \(code))"
            )
        case .network:
            reason = L10n.tr("网络异常，检查网络后重试", "network error, check the connection and try again")
        }
        return L10n.format(
            "%@：%@",
            "%@: %@",
            Self.verb(deviceName: deviceName, adjustment: adjustment),
            reason
        )
    }

    private static func verb(deviceName: String, adjustment: Bool) -> String {
        if adjustment {
            return L10n.tr("\(deviceName) 调节未生效", "\(deviceName) adjustment failed")
        }
        return L10n.tr("\(deviceName) 操作未生效", "\(deviceName) action failed")
    }
}
