import Foundation
import Testing
@testable import MijiaCore

private final class PTZStubProtocol: URLProtocol {
    struct Stub: Sendable {
        var status: Int
        var body: String
    }

    nonisolated(unsafe) static var stub = Stub(status: 200, body: #"{"ok":true}"#)
    nonisolated(unsafe) static var requests: [URLRequest] = []
    private static let lock = NSLock()

    static func reset(_ new: Stub) {
        lock.lock()
        stub = new
        requests = []
        lock.unlock()
    }

    static func recorded() -> [URLRequest] {
        lock.lock()
        defer { lock.unlock() }
        return requests
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let current: Stub
        Self.lock.lock()
        Self.requests.append(request)
        current = Self.stub
        Self.lock.unlock()

        let response = HTTPURLResponse(
            url: request.url ?? URL(string: "http://127.0.0.1")!,
            statusCode: current.status,
            httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"]
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(current.body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

@Suite("Camera PTZ bridge client (#97)", .serialized)
struct CameraPTZClientTests {
    private func makeClient() -> CameraPTZClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [PTZStubProtocol.self]
        return CameraPTZClient(
            baseURL: URL(string: "http://127.0.0.1:9000")!,
            streamName: "cam_raw_test",
            username: "user",
            password: "secret",
            session: URLSession(configuration: configuration)
        )
    }

    private static func bodyData(of request: URLRequest) -> Data? {
        if let data = request.httpBody { return data }
        guard let stream = request.httpBodyStream else { return nil }
        stream.open()
        defer { stream.close() }
        var data = Data()
        var buffer = [UInt8](repeating: 0, count: 1024)
        while stream.hasBytesAvailable {
            let count = stream.read(&buffer, maxLength: buffer.count)
            if count <= 0 { break }
            data.append(buffer, count: count)
        }
        return data.isEmpty ? nil : data
    }

    @Test("Begin allocates a bridge session token")
    func begin() async throws {
        PTZStubProtocol.reset(.init(
            status: 200,
            body: #"{"ok":true,"epoch":"epoch-1","session":7,"mode":"unknown"}"#
        ))
        let reply = try await makeClient().begin()
        #expect(reply.token == CameraPTZSessionToken(epoch: "epoch-1", session: 7))
        #expect(reply.mode == .unknown)
    }

    @Test("A move carries the stream, token, sequence and direction")
    func moveRequestShape() async throws {
        PTZStubProtocol.reset(.init(status: 200, body: #"{"ok":true,"mode":"step"}"#))
        let token = CameraPTZSessionToken(epoch: "epoch-1", session: 7)
        try await makeClient().move(direction: .left, token: token, seq: 3)

        let request = try #require(PTZStubProtocol.recorded().last)
        #expect(request.httpMethod == "POST")
        #expect(request.url?.path == "/api/xiaomi/ptz")
        #expect(request.value(forHTTPHeaderField: "Authorization")?.hasPrefix("Basic ") == true)
        let body = try #require(Self.bodyData(of: request))
        let json = try #require(JSONSerialization.jsonObject(with: body) as? [String: Any])
        #expect(json["src"] as? String == "cam_raw_test")
        #expect(json["action"] as? String == "move")
        #expect(json["epoch"] as? String == "epoch-1")
        #expect(json["session"] as? Int == 7)
        #expect(json["seq"] as? Int == 3)
        #expect(json["direction"] as? String == "left")
    }

    @Test("A stale token is reported as a stale session, not a generic failure")
    func staleSession() async {
        PTZStubProtocol.reset(.init(status: 409, body: "move already ended"))
        let token = CameraPTZSessionToken(epoch: "epoch-1", session: 1)
        await #expect(throws: CameraPTZClientError.staleSession) {
            try await makeClient().heartbeat(token: token, seq: 2)
        }
    }

    @Test("An epoch mismatch is reported as a bridge restart")
    func bridgeChanged() async {
        PTZStubProtocol.reset(.init(status: 409, body: "bridge epoch changed"))
        let token = CameraPTZSessionToken(epoch: "old", session: 1)
        await #expect(throws: CameraPTZClientError.bridgeChanged) {
            try await makeClient().stop(token: token)
        }
    }

    @Test("A stop write failure is visible, not reported as sent")
    func stopWriteFailure() async throws {
        PTZStubProtocol.reset(.init(
            status: 200,
            body: #"{"ok":true,"mode":"continuous","stopSent":false,"stopConfirmed":false,"stopError":"write_failed"}"#
        ))
        let result = try await makeClient().stop(
            token: CameraPTZSessionToken(epoch: "epoch-1", session: 7)
        )
        #expect(result.sent == false)
        #expect(result.errorReason == "write_failed")
    }

    @Test("Stop never claims a physical confirmation")
    func stopIsUnconfirmed() async throws {
        PTZStubProtocol.reset(.init(
            status: 200,
            body: #"{"ok":true,"mode":"continuous","stopSent":true,"stopConfirmed":false}"#
        ))
        let result = try await makeClient().stop(
            token: CameraPTZSessionToken(epoch: "epoch-1", session: 7)
        )
        #expect(result.sent)
        #expect(result.confirmed == false)
        #expect(result.mode == .continuous)
    }
}
