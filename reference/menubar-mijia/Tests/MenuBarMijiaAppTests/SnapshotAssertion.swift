import AppKit
import Foundation

enum SnapshotAssertion {
    static func sanitizePathComponent(_ raw: String) -> String {
        raw.replacingOccurrences(of: "\\W+", with: "-", options: .regularExpression)
            .replacingOccurrences(of: "^-|-$", with: "", options: .regularExpression)
    }

    static func pngData(for image: NSImage) -> Data? {
        guard let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
            return nil
        }
        let rep = NSBitmapImageRep(cgImage: cgImage)
        rep.size = image.size
        return rep.representation(using: .png, properties: [:])
    }

    private static func rasterContext(for cgImage: CGImage) -> CGContext? {
        let space = cgImage.colorSpace.flatMap { $0.model == .rgb ? $0 : nil }
            ?? CGColorSpaceCreateDeviceRGB()
        guard
            let context = CGContext(
                data: nil,
                width: cgImage.width,
                height: cgImage.height,
                bitsPerComponent: 8,
                bytesPerRow: cgImage.width * 4,
                space: space,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            )
        else { return nil }
        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: cgImage.width, height: cgImage.height))
        return context
    }

    static func compare(_ old: NSImage, _ new: NSImage, newPNG: Data) -> String? {
        guard let oldCG = old.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
            return "Reference image could not be loaded."
        }
        guard let newCG = new.cgImage(forProposedRect: nil, context: nil, hints: nil),
              newCG.width != 0, newCG.height != 0
        else {
            return "Newly-taken snapshot is empty."
        }
        guard new.size == old.size else {
            return "Newly-taken snapshot@\(new.size) does not match reference@\(old.size)."
        }
        guard oldCG.width == newCG.width, oldCG.height == newCG.height else {
            return "Newly-taken snapshot@(\(newCG.width)x\(newCG.height)px) does not match reference@(\(oldCG.width)x\(oldCG.height)px)."
        }
        guard let oldCtx = rasterContext(for: oldCG), let oldBytes = oldCtx.data,
              let newCtx = rasterContext(for: newCG), let newBytes = newCtx.data
        else {
            return "Snapshot pixel data could not be loaded."
        }
        let byteCount = oldCtx.height * oldCtx.bytesPerRow
        if memcmp(oldBytes, newBytes, byteCount) == 0 { return nil }
        if let roundTripped = NSImage(data: newPNG)?.cgImage(forProposedRect: nil, context: nil, hints: nil),
           let rtCtx = rasterContext(for: roundTripped), let rtBytes = rtCtx.data,
           memcmp(oldBytes, rtBytes, byteCount) == 0 {
            return nil
        }
        return "Newly-taken snapshot does not match reference."
    }

    static func verifyImage(
        _ image: NSImage,
        named name: String,
        recordAll: Bool,
        snapshotDirectory: URL? = nil,
        artifactsDirectory: String? = ProcessInfo.processInfo.environment["SNAPSHOT_ARTIFACTS"],
        file: StaticString = #filePath,
        testName: String = #function
    ) -> String? {
        let fileURL = URL(fileURLWithPath: "\(file)", isDirectory: false)
        let suiteName = fileURL.deletingPathExtension().lastPathComponent
        let dirURL = snapshotDirectory ?? fileURL.deletingLastPathComponent()
            .appendingPathComponent("__Snapshots__", isDirectory: true)
            .appendingPathComponent(suiteName, isDirectory: true)
        let filename = "\(sanitizePathComponent(testName)).\(sanitizePathComponent(name)).png"
        let snapshotURL = dirURL.appendingPathComponent(filename, isDirectory: false)
        guard let newPNG = pngData(for: image) else { return "Could not encode PNG snapshot." }
        do {
            try FileManager.default.createDirectory(at: dirURL, withIntermediateDirectories: true)
            if recordAll || !FileManager.default.fileExists(atPath: snapshotURL.path) {
                try newPNG.write(to: snapshotURL)
                return recordAll
                    ? "Record mode is on. Automatically recorded snapshot: \(snapshotURL.path)"
                    : "No reference was found on disk. Automatically recorded snapshot: \(snapshotURL.path)"
            }
            let referenceData = try Data(contentsOf: snapshotURL)
            guard let referenceImage = NSImage(data: referenceData) else {
                return "Reference image could not be loaded."
            }
            guard let reason = compare(referenceImage, image, newPNG: newPNG) else { return nil }
            let artifactsRoot = URL(fileURLWithPath: artifactsDirectory ?? NSTemporaryDirectory(), isDirectory: true)
                .appendingPathComponent(suiteName, isDirectory: true)
            try FileManager.default.createDirectory(at: artifactsRoot, withIntermediateDirectories: true)
            let failedURL = artifactsRoot.appendingPathComponent(filename, isDirectory: false)
            try newPNG.write(to: failedURL)
            return "Snapshot \"\(name)\" does not match reference.\n\nksdiff \"\(snapshotURL.path)\" \"\(failedURL.path)\"\n\n\(reason)"
        } catch {
            return error.localizedDescription
        }
    }
}
