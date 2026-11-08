import Foundation

/// A parsed AI endpoint address, used to decide which provider a URL belongs to and whether an API key may be sent to it.
///
/// Provider detection only trusts exact hosts or subdomains of a provider-owned domain. Substring checks
/// (`host.contains("openrouter.ai")`) would also match `openrouter.ai.evil.com` and hand the saved key to it (#19).
public struct AssistantEndpoint: Equatable, Sendable {
    public let scheme: String
    /// Lowercased host without IPv6 brackets or a trailing dot.
    public let host: String
    public let port: Int?

    /// Parses a user-typed endpoint. A missing scheme means https (`api.deepseek.com/v1`).
    public init?(_ raw: String) {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return nil }
        if !text.contains("://") {
            text = "https://" + text
        }
        guard let components = URLComponents(string: text),
              let scheme = components.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              var host = components.host?.lowercased() else {
            return nil
        }
        if host.hasPrefix("["), host.hasSuffix("]") {
            host = String(host.dropFirst().dropLast())
        }
        while host.hasSuffix(".") {
            host.removeLast()
        }
        guard !host.isEmpty else { return nil }
        if let ipv4 = Self.canonicalIPv4Host(host) {
            host = ipv4
        } else if Self.endsInNumber(host) {
            // WHATWG rejects a host whose last label is numeric but is not a valid IPv4 address
            // (`4294967296`, `0x100000000`); refusing it here keeps both platforms failing closed.
            return nil
        }
        self.scheme = scheme
        self.host = host
        self.port = components.port
    }

    /// Host plus explicit port. A custom endpoint's key is bound to this value.
    public var identity: String {
        guard let port else { return host }
        return host.contains(":") ? "[\(host)]:\(port)" : "\(host):\(port)"
    }

    /// `domain` itself or one of its subdomains: `api.openrouter.ai` matches `openrouter.ai`, `openrouter.ai.evil.com` does not.
    public func hostMatches(domain: String) -> Bool {
        host == domain || host.hasSuffix("." + domain)
    }

    /// Traffic never leaves this computer.
    public var isLoopback: Bool {
        if host == "localhost" || host == "::1" || host == "0:0:0:0:0:0:0:1" {
            return true
        }
        return Self.ipv4Octets(host)?.first == 127
    }

    /// Loopback, private LAN (RFC 1918), link-local, CGNAT/Tailscale (100.64.0.0/10), IPv6 ULA/link-local,
    /// `.local`/`.lan`/`.home.arpa`/`.internal` names, or a dotless single-label host such as `homeassistant`
    /// (not a two-letter or IDN country-code label, which can resolve publicly).
    public var isLocalNetwork: Bool {
        if isLoopback || Self.localHostSuffixes.contains(where: { host.hasSuffix($0) }) {
            return true
        }
        if let octets = Self.ipv4Octets(host) {
            switch (octets[0], octets[1]) {
            case (10, _), (192, 168), (169, 254): return true
            case (172, 16...31): return true
            case (100, 64...127): return true
            default: return false
            }
        }
        if host.contains(":") {
            // fc00::/7 unique-local and fe80::/10 link-local.
            return host.hasPrefix("fc") || host.hasPrefix("fd")
                || host.hasPrefix("fe8") || host.hasPrefix("fe9") || host.hasPrefix("fea") || host.hasPrefix("feb")
        }
        return Self.isPrivateSingleLabel(host)
    }

    /// A dotless name such as `homeassistant` resolves through the router's search domain.
    /// ICANN forbids dotless records only for generic TLDs; country-code TLDs (`ai`, `xn--p1ai`) may resolve
    /// on their own to a public server, so those labels never count as local.
    private static func isPrivateSingleLabel(_ host: String) -> Bool {
        // Foundation may hand back an IDN label decoded (`рф`), so non-ASCII counts as IDN too.
        guard !host.contains("."), !host.hasPrefix("xn--"), host.allSatisfy(\.isASCII) else { return false }
        return !(host.count == 2 && host.allSatisfy { ("a"..."z").contains($0) })
    }

    private static let localHostSuffixes = [".local", ".lan", ".home.arpa", ".internal"]

    /// API keys only travel over https, or over plain http that stays on the local network.
    public var allowsSendingSecrets: Bool {
        scheme == "https" || isLocalNetwork
    }

    /// Four dotted decimal octets, or nil when `host` is not an IPv4 literal (so `10.evil.com` is not a LAN address).
    static func ipv4Octets(_ host: String) -> [Int]? {
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return nil }
        var octets: [Int] = []
        for part in parts {
            guard !part.isEmpty, part.count <= 3,
                  part.allSatisfy({ ("0"..."9").contains($0) }),
                  let value = Int(part), value <= 255 else {
                return nil
            }
            octets.append(value)
        }
        return octets
    }

    /// Rewrites WHATWG/inet_aton IPv4 spellings to dotted decimal so `134744072`, `0x7f000001`,
    /// `0177.0.0.1`, and `127.1` classify like the addresses they actually resolve to.
    private static func canonicalIPv4Host(_ host: String) -> String? {
        var parts = host.split(separator: ".", omittingEmptySubsequences: false).map(String.init)
        if parts.last == "" {
            guard parts.count > 1 else { return nil }
            parts.removeLast()
        }
        guard !parts.isEmpty, parts.count <= 4 else { return nil }
        var numbers: [UInt64] = []
        for part in parts {
            guard let number = ipv4Number(part) else { return nil }
            numbers.append(UInt64(number))
        }
        guard let last = numbers.last else { return nil }
        let lastLimit: UInt64
        switch numbers.count {
        case 1: lastLimit = 1 << 32
        case 2: lastLimit = 1 << 24
        case 3: lastLimit = 1 << 16
        default: lastLimit = 1 << 8
        }
        guard last < lastLimit else { return nil }
        var value = last
        var shift = 24
        for number in numbers.dropLast() {
            guard number <= 255 else { return nil }
            value += number << UInt64(shift)
            shift -= 8
        }
        return (0...3).map { String((value >> UInt64(24 - $0 * 8)) & 255) }.joined(separator: ".")
    }

    /// WHATWG's "ends in a number": the last non-empty label is decimal digits or a `0x` hex literal.
    private static func endsInNumber(_ host: String) -> Bool {
        var labels = host.split(separator: ".", omittingEmptySubsequences: false)
        if labels.last?.isEmpty == true, labels.count > 1 {
            labels.removeLast()
        }
        guard let last = labels.last?.lowercased(), !last.isEmpty else { return false }
        if last.allSatisfy({ ("0"..."9").contains($0) }) { return true }
        guard last.hasPrefix("0x") else { return false }
        return last.dropFirst(2).allSatisfy { ("0"..."9").contains($0) || ("a"..."f").contains($0) }
    }

    private static func ipv4Number(_ part: String) -> UInt32? {
        guard !part.isEmpty else { return nil }
        var text = part.lowercased()
        var radix = 10
        if text.count >= 2, text.hasPrefix("0x") {
            text.removeFirst(2)
            radix = 16
        } else if text.count >= 2, text.hasPrefix("0") {
            text.removeFirst()
            radix = 8
        }
        if text.isEmpty { return 0 }
        let isDigit: (Character) -> Bool
        switch radix {
        case 8: isDigit = { ("0"..."7").contains($0) }
        case 16: isDigit = { ("0"..."9").contains($0) || ("a"..."f").contains($0) }
        default: isDigit = { ("0"..."9").contains($0) }
        }
        guard text.allSatisfy(isDigit) else { return nil }
        return UInt32(text, radix: radix)
    }
}
