import AppKit
import Foundation
import Testing

@MainActor
struct SnapshotAssertionTests {
    private func solidImage(color: NSColor, size: CGSize = CGSize(width: 8, height: 8)) -> NSImage {
        let rep = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: Int(size.width),
            pixelsHigh: Int(size.height),
            bitsPerSample: 8,
            samplesPerPixel: 4,
            hasAlpha: true,
            isPlanar: false,
            colorSpaceName: .deviceRGB,
            bytesPerRow: 0,
            bitsPerPixel: 0
        )!
        rep.size = size
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
        color.setFill()
        NSBezierPath(rect: NSRect(origin: .zero, size: size)).fill()
        NSGraphicsContext.restoreGraphicsState()
        let image = NSImage(size: size)
        image.addRepresentation(rep)
        return image
    }

    @Test("Sanitizes Swift function signatures and snapshot names into stable filenames")
    func sanitizesPathComponents() {
        #expect(SnapshotAssertion.sanitizePathComponent("panelSnapshot()") == "panelSnapshot")
        #expect(
            SnapshotAssertion.sanitizePathComponent("panelHAPlaintextSnapshot(prompt:name:)")
                == "panelHAPlaintextSnapshot-prompt-name"
        )
        #expect(SnapshotAssertion.sanitizePathComponent("panel-zh-light") == "panel-zh-light")
        #expect(SnapshotAssertion.sanitizePathComponent("  --hello world!-- ") == "hello-world")
    }

    @Test("Records missing baseline, passes on match, writes failure artifact on mismatch, and overwrites on recordAll")
    func lifecycleRecordCompareMismatchOverwrite() throws {
        let tempRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("SnapshotAssertionTests-\(UUID().uuidString)", isDirectory: true)
        let snapshotsDir = tempRoot.appendingPathComponent("baselines", isDirectory: true)
        let artifactsDir = tempRoot.appendingPathComponent("artifacts", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: tempRoot) }

        let blue = solidImage(color: .systemBlue)
        let red = solidImage(color: .systemRed)
        let wideBlue = solidImage(color: .systemBlue, size: CGSize(width: 12, height: 8))

        // 1. Missing baseline is recorded and reported as an issue.
        let missingMsg = SnapshotAssertion.verifyImage(
            blue,
            named: "tile-light",
            recordAll: false,
            snapshotDirectory: snapshotsDir,
            artifactsDirectory: artifactsDir.path,
            testName: "sampleTest()"
        )
        let recordedMsg = try #require(missingMsg)
        #expect(recordedMsg.contains("No reference was found on disk"))
        let baselineFile = snapshotsDir.appendingPathComponent("sampleTest.tile-light.png")
        #expect(FileManager.default.fileExists(atPath: baselineFile.path))

        // 2. Identical image passes with nil failure.
        let matchMsg = SnapshotAssertion.verifyImage(
            blue,
            named: "tile-light",
            recordAll: false,
            snapshotDirectory: snapshotsDir,
            artifactsDirectory: artifactsDir.path,
            testName: "sampleTest()"
        )
        #expect(matchMsg == nil)

        // 3. Different pixels fail and write the artifact PNG.
        let mismatchMsg = SnapshotAssertion.verifyImage(
            red,
            named: "tile-light",
            recordAll: false,
            snapshotDirectory: snapshotsDir,
            artifactsDirectory: artifactsDir.path,
            testName: "sampleTest()"
        )
        let diffText = try #require(mismatchMsg)
        #expect(diffText.contains("does not match reference"))
        let artifactFile = artifactsDir
            .appendingPathComponent("SnapshotAssertionTests", isDirectory: true)
            .appendingPathComponent("sampleTest.tile-light.png")
        #expect(FileManager.default.fileExists(atPath: artifactFile.path))

        // 4. Different dimensions fail with size mismatch reason.
        let sizeMsg = SnapshotAssertion.verifyImage(
            wideBlue,
            named: "tile-light",
            recordAll: false,
            snapshotDirectory: snapshotsDir,
            artifactsDirectory: artifactsDir.path,
            testName: "sampleTest()"
        )
        #expect(sizeMsg?.contains("does not match reference@") == true)

        // 5. recordAll overwrites baseline so subsequent check against `red` passes.
        let overwriteMsg = SnapshotAssertion.verifyImage(
            red,
            named: "tile-light",
            recordAll: true,
            snapshotDirectory: snapshotsDir,
            artifactsDirectory: artifactsDir.path,
            testName: "sampleTest()"
        )
        #expect(overwriteMsg?.contains("Record mode is on") == true)
        let matchAfterOverwrite = SnapshotAssertion.verifyImage(
            red,
            named: "tile-light",
            recordAll: false,
            snapshotDirectory: snapshotsDir,
            artifactsDirectory: artifactsDir.path,
            testName: "sampleTest()"
        )
        #expect(matchAfterOverwrite == nil)
    }

    @Test("Normalizes differing bytesPerRow strides, 24-bit RGB bitmaps, and @2x scale factors")
    func normalizesRowStridesColorChannelsAndScaleFactors() throws {
        func makeBitmapImage(
            pixelsWide: Int,
            pixelsHigh: Int,
            pointSize: CGSize,
            samplesPerPixel: Int = 4,
            hasAlpha: Bool = true,
            bytesPerRow: Int = 0
        ) -> NSImage {
            let rep = NSBitmapImageRep(
                bitmapDataPlanes: nil,
                pixelsWide: pixelsWide,
                pixelsHigh: pixelsHigh,
                bitsPerSample: 8,
                samplesPerPixel: samplesPerPixel,
                hasAlpha: hasAlpha,
                isPlanar: false,
                colorSpaceName: .deviceRGB,
                bytesPerRow: bytesPerRow,
                bitsPerPixel: 0
            )!
            rep.size = pointSize
            if let base = rep.bitmapData {
                for y in 0..<pixelsHigh {
                    let row = base.advanced(by: y * rep.bytesPerRow)
                    for x in 0..<pixelsWide {
                        let px = row.advanced(by: x * samplesPerPixel)
                        px[0] = 32
                        px[1] = 180
                        px[2] = 96
                        if samplesPerPixel == 4 {
                            px[3] = 255
                        }
                    }
                }
            }
            let image = NSImage(size: pointSize)
            image.addRepresentation(rep)
            return image
        }

        let tightRGBA = makeBitmapImage(
            pixelsWide: 8,
            pixelsHigh: 8,
            pointSize: CGSize(width: 8, height: 8),
            bytesPerRow: 32
        )
        let paddedRGBA = makeBitmapImage(
            pixelsWide: 8,
            pixelsHigh: 8,
            pointSize: CGSize(width: 8, height: 8),
            bytesPerRow: 64
        )
        let rgb24 = makeBitmapImage(
            pixelsWide: 8,
            pixelsHigh: 8,
            pointSize: CGSize(width: 8, height: 8),
            samplesPerPixel: 3,
            hasAlpha: false,
            bytesPerRow: 24
        )
        let tightPNG = try #require(SnapshotAssertion.pngData(for: tightRGBA))

        // Padded row stride (64 bytes/row) vs tight row stride (32 bytes/row) must match without over-reading.
        #expect(SnapshotAssertion.compare(paddedRGBA, tightRGBA, newPNG: tightPNG) == nil)
        #expect(SnapshotAssertion.compare(tightRGBA, paddedRGBA, newPNG: tightPNG) == nil)
        // 24-bit RGB without alpha channel (24 bytes/row) must normalize to 8-bit RGBA and match.
        #expect(SnapshotAssertion.compare(rgb24, tightRGBA, newPNG: tightPNG) == nil)

        // @2x (16x16px @ 8x8pt) vs @1x (16x16px @ 16x16pt): same pixels, different point size -> fail.
        let retina2x = makeBitmapImage(
            pixelsWide: 16,
            pixelsHigh: 16,
            pointSize: CGSize(width: 8, height: 8)
        )
        let standard16pt = makeBitmapImage(
            pixelsWide: 16,
            pixelsHigh: 16,
            pointSize: CGSize(width: 16, height: 16)
        )
        let standard16PNG = try #require(SnapshotAssertion.pngData(for: standard16pt))
        let pointMismatch = SnapshotAssertion.compare(retina2x, standard16pt, newPNG: standard16PNG)
        #expect(pointMismatch?.contains("does not match reference@") == true)

        // @2x (16x16px @ 8x8pt) vs @1x (8x8px @ 8x8pt): same point size, different pixel size -> fail with px dimensions.
        let pixelMismatch = SnapshotAssertion.compare(retina2x, tightRGBA, newPNG: tightPNG)
        #expect(pixelMismatch?.contains("(8x8px) does not match reference@(16x16px)") == true)
    }
}
