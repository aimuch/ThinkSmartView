import CoreMedia
import Foundation
import Testing
@testable import MijiaCore
@testable import MenuBarMijiaApp

struct CameraLiveSampleBufferTests {
    @Test("Frames on the playback clock are not marked display-immediately")
    func playbackClockDoesNotDisplayImmediately() throws {
        var format: CMVideoFormatDescription?
        let status = CMVideoFormatDescriptionCreate(
            allocator: kCFAllocatorDefault,
            codecType: kCMVideoCodecType_422YpCbCr8,
            width: 16,
            height: 16,
            extensions: nil,
            formatDescriptionOut: &format
        )
        let description = try #require(status == noErr ? format : nil)
        let sample = FMP4Sample(
            trackID: 1,
            data: Data([0x01, 0x02, 0x03, 0x04]),
            decodeTime: 0,
            duration: 3_000,
            compositionOffset: 0,
            isKeyframe: false
        )
        let buffer = try #require(CameraLiveVideoPlayer.makeSampleBuffer(
            units: [Data([0x01, 0x02, 0x03, 0x04])],
            format: description,
            sample: sample,
            timescale: 90_000,
            isKeyframe: false,
            displayImmediately: false
        ))
        #expect(Self.attachesDisplayImmediately(buffer) == false)

        let immediate = try #require(CameraLiveVideoPlayer.makeSampleBuffer(
            units: [Data([0x01, 0x02, 0x03, 0x04])],
            format: description,
            sample: sample,
            timescale: 90_000,
            isKeyframe: true,
            displayImmediately: true
        ))
        #expect(Self.attachesDisplayImmediately(immediate))
    }

    private static func attachesDisplayImmediately(_ buffer: CMSampleBuffer) -> Bool {
        guard let attachments = CMSampleBufferGetSampleAttachmentsArray(buffer, createIfNecessary: false),
              CFArrayGetCount(attachments) > 0 else {
            return false
        }
        let dict = Unmanaged<CFDictionary>.fromOpaque(
            CFArrayGetValueAtIndex(attachments, 0)
        ).takeUnretainedValue()
        return CFDictionaryContainsKey(
            dict,
            Unmanaged.passUnretained(kCMSampleAttachmentKey_DisplayImmediately).toOpaque()
        )
    }
}
