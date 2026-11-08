import Foundation
import Testing
@testable import MijiaCore

@Suite("Live camera playback clock (#97)")
struct CameraLivePlaybackClockTests {
    private let frame = 1.0 / 30.0

    @Test("Four minutes of live frames reuse one timebase and stay due, not minutes away from the host clock")
    func fourMinutesStaysOnOneTimebase() {
        var clock = CameraLivePlaybackClock()
        var host = 50_000.0
        var starts = 0
        var reanchors = 0
        var maxHeld = 0
        var held: [Double] = []

        for n in 0..<(30 * 240) {
            let media = Double(n) * frame
            switch clock.step(mediaSeconds: media, hostNowSeconds: host) {
            case .start:
                starts += 1
            case .reanchor:
                reanchors += 1
            case .follow:
                break
            }
            let timebase = clock.timebaseSeconds(hostNowSeconds: host) ?? host
            #expect(abs(media - timebase) < frame + 0.000_001)
            held.append(media)
            held.removeAll { timebase - $0 >= frame - 0.001 }
            maxHeld = max(maxHeld, held.count)
            host += frame
        }

        #expect(starts == 1)
        #expect(reanchors == 0)
        #expect(maxHeld <= 2)
    }

    @Test("A media-time jump moves the timebase once instead of queuing the gap")
    func mediaJumpReanchorsOnce() {
        var clock = CameraLivePlaybackClock()
        var host = 1_000.0
        var media = 0.0
        _ = clock.step(mediaSeconds: media, hostNowSeconds: host)
        for _ in 0..<30 {
            media += frame
            host += frame
            #expect(clock.step(mediaSeconds: media, hostNowSeconds: host) == .follow)
        }

        media += 60
        host += frame
        #expect(clock.step(mediaSeconds: media, hostNowSeconds: host) == .reanchor(media))
        #expect(clock.timebaseSeconds(hostNowSeconds: host) == media)

        let after = clock.step(mediaSeconds: media + frame, hostNowSeconds: host + frame)
        #expect(after == .follow)
    }

    @Test("A new connection starts a new timebase instead of keeping the previous hour")
    func resetStartsAgain() {
        var clock = CameraLivePlaybackClock()
        _ = clock.step(mediaSeconds: 0, hostNowSeconds: 10_000)
        _ = clock.step(mediaSeconds: 240, hostNowSeconds: 10_240)
        clock.reset()
        #expect(clock.timebaseSeconds(hostNowSeconds: 10_240) == nil)
        #expect(clock.step(mediaSeconds: 0, hostNowSeconds: 10_241) == .start(0))
        #expect(clock.timebaseSeconds(hostNowSeconds: 10_241) == 0)
    }
}
