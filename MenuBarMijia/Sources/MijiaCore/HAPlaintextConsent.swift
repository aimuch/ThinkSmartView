import Foundation

/// Per-address consent to send the Home Assistant token over plain http to a non-local host (#276).
///
/// https addresses and `LocalNetworkEndpoint`s never ask. Every other address needs the user to agree
/// once; the agreement is keyed by origin, so a changed address asks again.
public enum HAPlaintextConsent {
    static let defaultsKey = "ha_plaintext_consent_v1"

    /// True when sending the token to `baseURL` would cross the internet unencrypted. An address that
    /// does not parse counts as needing consent, so an odd URL fails closed.
    public static func needsConsent(_ baseURL: String) -> Bool {
        !(AssistantEndpoint(HAWebSocketProvider.normalizeBaseURL(baseURL))?.allowsSendingSecrets ?? false)
    }

    public static func isApproved(_ baseURL: String, defaults: UserDefaults) -> Bool {
        approved(defaults).contains(key(baseURL))
    }

    /// Whether the token may go to `baseURL` right now.
    public static func allowsToken(to baseURL: String, defaults: UserDefaults) -> Bool {
        !needsConsent(baseURL) || isApproved(baseURL, defaults: defaults)
    }

    public static func approve(_ baseURL: String, defaults: UserDefaults) {
        var urls = approved(defaults)
        urls.insert(key(baseURL))
        defaults.set(urls.sorted(), forKey: defaultsKey)
    }

    public static func revokeAll(defaults: UserDefaults) {
        defaults.removeObject(forKey: defaultsKey)
    }

    /// The token goes to a host, so consent covers the origin (scheme, host, port), not a path.
    private static func key(_ baseURL: String) -> String {
        let normalized = HAWebSocketProvider.normalizeBaseURL(baseURL)
        guard let endpoint = AssistantEndpoint(normalized) else { return normalized }
        return "\(endpoint.scheme)://\(endpoint.identity)"
    }

    private static func approved(_ defaults: UserDefaults) -> Set<String> {
        Set(defaults.stringArray(forKey: defaultsKey) ?? [])
    }

    public static func host(_ baseURL: String) -> String {
        URL(string: HAWebSocketProvider.normalizeBaseURL(baseURL))?.host ?? baseURL
    }

    static func waitingMessage(_ baseURL: String) -> String {
        let host = host(baseURL)
        return L10n.tr(
            "\(host) 是公网明文地址，令牌会以明文传输。同意前不会连接。",
            "\(host) is a public plain-http address; your token would travel unencrypted. Not connecting until you agree."
        )
    }

    public static func declinedMessage(_ baseURL: String) -> String {
        let host = host(baseURL)
        return L10n.tr(
            "未连接 \(host)：你拒绝了以明文发送令牌。改用 https 或局域网地址后再连接。",
            "Not connected to \(host): you declined sending the token unencrypted. Use an https or local-network address."
        )
    }
}

/// What the panel shows about a public plain-http Home Assistant address.
public enum HAPlaintextPrompt: Equatable, Sendable {
    case asking(String)
    case declined(String)

    public var address: String {
        switch self {
        case .asking(let url), .declined(let url): url
        }
    }
}
