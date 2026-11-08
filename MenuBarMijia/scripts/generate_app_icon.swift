#!/usr/bin/env swift
import AppKit
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

let rootURL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
let iconDirURL = rootURL.appendingPathComponent("Resources/AppIcon", isDirectory: true)
let darkSVGURL = iconDirURL.appendingPathComponent("AppIcon-Dark.svg")
let lightSVGURL = iconDirURL.appendingPathComponent("AppIcon-Light.svg")

let workDirURL = FileManager.default.temporaryDirectory
    .appendingPathComponent("menubarmijia-icon-gen-\(ProcessInfo.processInfo.processIdentifier)", isDirectory: true)
try FileManager.default.createDirectory(at: workDirURL, withIntermediateDirectories: true)
defer { try? FileManager.default.removeItem(at: workDirURL) }

func run(_ executable: String, _ arguments: [String]) throws {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    let pipe = Pipe()
    process.standardOutput = pipe
    process.standardError = pipe
    try process.run()
    process.waitUntilExit()
    if process.terminationStatus != 0 {
        let output = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        throw NSError(
            domain: "IconGen",
            code: Int(process.terminationStatus),
            userInfo: [NSLocalizedDescriptionKey: "\(executable) \(arguments.joined(separator: " ")) failed:\n\(output)"]
        )
    }
}

func parseSquircleCGPath(from svgURL: URL) throws -> CGPath {
    let svgText = try String(contentsOf: svgURL, encoding: .utf8)
    let pattern = #"(<path id="sq" d="([^"]+)"|<clipPath id="c"><path d="([^"]+)")"#
    guard let clipRange = svgText.range(of: pattern, options: .regularExpression) else {
        throw NSError(domain: "IconGen", code: 1, userInfo: [NSLocalizedDescriptionKey: "Missing squircle path in \(svgURL.path)"])
    }
    let match = String(svgText[clipRange])
    guard let dStart = match.range(of: #"d=""#)?.upperBound,
          let dEnd = match.lastIndex(of: "\"") else {
        throw NSError(domain: "IconGen", code: 2, userInfo: [NSLocalizedDescriptionKey: "Malformed path data in \(svgURL.path)"])
    }
    let pathData = String(match[dStart..<dEnd])
    let path = CGMutablePath()
    let tokens = pathData.split(whereSeparator: { $0 == " " || $0 == "\n" || $0 == "\t" })
    var isFirst = true
    for token in tokens {
        if token == "Z" || token == "z" {
            path.closeSubpath()
            continue
        }
        let clean = token.drop(while: { $0 == "M" || $0 == "L" })
        let parts = clean.split(separator: ",")
        guard parts.count == 2,
              let x = Double(parts[0]),
              let ySvg = Double(parts[1]) else {
            continue
        }
        let point = CGPoint(x: x, y: 1024.0 - ySvg)
        if isFirst {
            path.move(to: point)
            isFirst = false
        } else {
            path.addLine(to: point)
        }
    }
    path.closeSubpath()
    return path
}

// CoreSVG skips <filter> elements, whereas qlmanage renders WebKit SVG filters accurately
// onto an opaque white backdrop. Clipping the qlmanage bitmap to the SVG superellipse path
// inside a transparent CGContext preserves inner filter shadows and transparent outer corners.
func renderMasterIcon(svgURL: URL, squirclePath: CGPath, isLight: Bool) throws -> CGImage {
    try run("/usr/bin/qlmanage", ["-t", "-s", "1024", "-o", workDirURL.path, svgURL.path])
    let qlPNGURL = workDirURL.appendingPathComponent(svgURL.lastPathComponent + ".png")
    guard let source = CGImageSourceCreateWithURL(qlPNGURL as CFURL, nil),
          let rawImage = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        throw NSError(domain: "IconGen", code: 3, userInfo: [NSLocalizedDescriptionKey: "Failed to load qlmanage output for \(svgURL.lastPathComponent)"])
    }

    let size = 1024
    let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
    guard let ctx = CGContext(
        data: nil,
        width: size,
        height: size,
        bitsPerComponent: 8,
        bytesPerRow: size * 4,
        space: colorSpace,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else {
        throw NSError(domain: "IconGen", code: 4, userInfo: [NSLocalizedDescriptionKey: "Failed to allocate 1024x1024 CGContext"])
    }

    ctx.clear(CGRect(x: 0, y: 0, width: size, height: size))

    ctx.saveGState()
    let shadowAlpha: CGFloat = isLight ? 0.22 : 0.30
    let shadowColor = CGColor(colorSpace: colorSpace, components: [0, 0, 0, shadowAlpha])!
    ctx.setShadow(offset: CGSize(width: 0, height: -10), blur: 14, color: shadowColor)
    ctx.addPath(squirclePath)
    ctx.setFillColor(CGColor(colorSpace: colorSpace, components: [0, 0, 0, 1])!)
    ctx.fillPath()
    ctx.restoreGState()

    ctx.saveGState()
    ctx.addPath(squirclePath)
    ctx.clip()
    ctx.draw(rawImage, in: CGRect(x: 0, y: 0, width: size, height: size))
    ctx.restoreGState()

    guard let outImage = ctx.makeImage(),
          let dataProvider = outImage.dataProvider,
          let pixelData = dataProvider.data,
          let ptr = CFDataGetBytePtr(pixelData) else {
        throw NSError(domain: "IconGen", code: 5, userInfo: [NSLocalizedDescriptionKey: "Failed to inspect output image pixels"])
    }
    let cornerAlpha = ptr[3]
    let centerOffset = (512 * size + 512) * 4
    let centerAlpha = ptr[centerOffset + 3]
    assert(cornerAlpha == 0, "Expected transparent corner (0,0), got alpha=\(cornerAlpha)")
    assert(centerAlpha == 255, "Expected opaque center (512,512), got alpha=\(centerAlpha)")

    return outImage
}

func resizeImage(_ image: CGImage, pixelSize: Int) throws -> Data {
    let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
    guard let ctx = CGContext(
        data: nil,
        width: pixelSize,
        height: pixelSize,
        bitsPerComponent: 8,
        bytesPerRow: pixelSize * 4,
        space: colorSpace,
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else {
        throw NSError(domain: "IconGen", code: 6, userInfo: [NSLocalizedDescriptionKey: "Failed to allocate \(pixelSize)x\(pixelSize) CGContext"])
    }
    ctx.interpolationQuality = .high
    ctx.draw(image, in: CGRect(x: 0, y: 0, width: pixelSize, height: pixelSize))
    guard let scaled = ctx.makeImage() else {
        throw NSError(domain: "IconGen", code: 7, userInfo: [NSLocalizedDescriptionKey: "Failed to create \(pixelSize)x\(pixelSize) CGImage"])
    }
    let mutableData = NSMutableData()
    guard let dest = CGImageDestinationCreateWithData(mutableData, UTType.png.identifier as CFString, 1, nil) else {
        throw NSError(domain: "IconGen", code: 8, userInfo: [NSLocalizedDescriptionKey: "Failed to create PNG destination"])
    }
    CGImageDestinationAddImage(dest, scaled, nil)
    guard CGImageDestinationFinalize(dest) else {
        throw NSError(domain: "IconGen", code: 9, userInfo: [NSLocalizedDescriptionKey: "Failed to encode PNG data"])
    }
    return mutableData as Data
}

struct IconSlot {
    let pointSize: Int
    let scale: Int
    var pixelSize: Int { pointSize * scale }
    var sizeString: String { "\(pointSize)x\(pointSize)" }
    var scaleString: String { "\(scale)x" }
    var iconsetFilename: String {
        scale == 1
            ? "icon_\(pointSize)x\(pointSize).png"
            : "icon_\(pointSize)x\(pointSize)@2x.png"
    }
}

let slots: [IconSlot] = [
    IconSlot(pointSize: 16, scale: 1),
    IconSlot(pointSize: 16, scale: 2),
    IconSlot(pointSize: 32, scale: 1),
    IconSlot(pointSize: 32, scale: 2),
    IconSlot(pointSize: 128, scale: 1),
    IconSlot(pointSize: 128, scale: 2),
    IconSlot(pointSize: 256, scale: 1),
    IconSlot(pointSize: 256, scale: 2),
    IconSlot(pointSize: 512, scale: 1),
    IconSlot(pointSize: 512, scale: 2),
]

func writeIconset(master: CGImage, name: String, outputICNS: URL) throws -> [Int: Data] {
    let iconsetURL = workDirURL.appendingPathComponent("\(name).iconset", isDirectory: true)
    try FileManager.default.createDirectory(at: iconsetURL, withIntermediateDirectories: true)
    var cache: [Int: Data] = [:]
    for slot in slots {
        let pngData: Data
        if let existing = cache[slot.pixelSize] {
            pngData = existing
        } else {
            let rendered = try resizeImage(master, pixelSize: slot.pixelSize)
            cache[slot.pixelSize] = rendered
            pngData = rendered
        }
        try pngData.write(to: iconsetURL.appendingPathComponent(slot.iconsetFilename))
    }
    try run("/usr/bin/iconutil", ["-c", "icns", iconsetURL.path, "-o", outputICNS.path])
    return cache
}

func buildAssetsCar(lightCache: [Int: Data], darkCache: [Int: Data], outputCarURL: URL) throws {
    let xcassetsURL = workDirURL.appendingPathComponent("Assets.xcassets", isDirectory: true)
    let appiconsetURL = xcassetsURL.appendingPathComponent("AppIcon.appiconset", isDirectory: true)
    let compiledURL = workDirURL.appendingPathComponent("CompiledAssets", isDirectory: true)
    try FileManager.default.createDirectory(at: appiconsetURL, withIntermediateDirectories: true)
    try FileManager.default.createDirectory(at: compiledURL, withIntermediateDirectories: true)

    var imagesJSON: [[String: Any]] = []
    for slot in slots {
        let lightName = "light_\(slot.pointSize)x\(slot.pointSize)@\(slot.scale)x.png"
        let darkName = "dark_\(slot.pointSize)x\(slot.pointSize)@\(slot.scale)x.png"
        try lightCache[slot.pixelSize]!.write(to: appiconsetURL.appendingPathComponent(lightName))
        try darkCache[slot.pixelSize]!.write(to: appiconsetURL.appendingPathComponent(darkName))

        imagesJSON.append([
            "filename": lightName,
            "idiom": "mac",
            "scale": slot.scaleString,
            "size": slot.sizeString,
        ])
        imagesJSON.append([
            "appearances": [
                ["appearance": "luminosity", "value": "dark"]
            ],
            "filename": darkName,
            "idiom": "mac",
            "scale": slot.scaleString,
            "size": slot.sizeString,
        ])
    }

    let contents: [String: Any] = [
        "images": imagesJSON,
        "info": ["author": "xcode", "version": 1],
    ]
    let jsonData = try JSONSerialization.data(withJSONObject: contents, options: [.prettyPrinted, .sortedKeys])
    try jsonData.write(to: appiconsetURL.appendingPathComponent("Contents.json"))

    let partialPlistURL = compiledURL.appendingPathComponent("assetcatalog_generated_info.plist")
    try run("/usr/bin/xcrun", [
        "actool",
        "--compile", compiledURL.path,
        "--platform", "macosx",
        "--minimum-deployment-target", "14.0",
        "--app-icon", "AppIcon",
        "--output-partial-info-plist", partialPlistURL.path,
        xcassetsURL.path,
    ])

    let compiledCarURL = compiledURL.appendingPathComponent("Assets.car")
    if FileManager.default.fileExists(atPath: outputCarURL.path) {
        try FileManager.default.removeItem(at: outputCarURL)
    }
    try FileManager.default.copyItem(at: compiledCarURL, to: outputCarURL)
}

func writeWindowsIcons(master: CGImage, cache: [Int: Data]) throws {
    let winIconsDir = rootURL.appendingPathComponent("windows/src-tauri/icons", isDirectory: true)
    guard FileManager.default.fileExists(atPath: winIconsDir.path) else { return }

    let png512 = cache[512]!
    try png512.write(to: winIconsDir.appendingPathComponent("icon.png"))
    try cache[32]!.write(to: winIconsDir.appendingPathComponent("32x32.png"))
    try cache[128]!.write(to: winIconsDir.appendingPathComponent("128x128.png"))
    try cache[256]!.write(to: winIconsDir.appendingPathComponent("128x128@2x.png"))

    let icoSizes = [16, 32, 48, 64, 128, 256]
    var pngPayloads: [(size: Int, data: Data)] = []
    for sz in icoSizes {
        let data = try cache[sz] ?? resizeImage(master, pixelSize: sz)
        pngPayloads.append((sz, data))
    }

    var ico = Data()
    func appendUInt16LE(_ v: UInt16) {
        var le = v.littleEndian
        ico.append(Data(bytes: &le, count: 2))
    }
    func appendUInt32LE(_ v: UInt32) {
        var le = v.littleEndian
        ico.append(Data(bytes: &le, count: 4))
    }

    appendUInt16LE(0)
    appendUInt16LE(1)
    appendUInt16LE(UInt16(pngPayloads.count))

    var offset = UInt32(6 + pngPayloads.count * 16)
    for entry in pngPayloads {
        let dimByte: UInt8 = entry.size >= 256 ? 0 : UInt8(entry.size)
        ico.append(dimByte)
        ico.append(dimByte)
        ico.append(0)
        ico.append(0)
        appendUInt16LE(1)
        appendUInt16LE(32)
        appendUInt32LE(UInt32(entry.data.count))
        appendUInt32LE(offset)
        offset += UInt32(entry.data.count)
    }
    for entry in pngPayloads {
        ico.append(entry.data)
    }
    try ico.write(to: winIconsDir.appendingPathComponent("icon.ico"))
}

/// Raycast extensions need a 512×512 PNG; it renders from the same master as the Windows icon so
/// the store listing and the app can never drift apart.
func writeRaycastIcon(cache: [Int: Data]) throws {
    let assetsDir = rootURL.appendingPathComponent("raycast/assets", isDirectory: true)
    guard FileManager.default.fileExists(atPath: rootURL.appendingPathComponent("raycast").path) else { return }
    try FileManager.default.createDirectory(at: assetsDir, withIntermediateDirectories: true)
    try cache[512]!.write(to: assetsDir.appendingPathComponent("icon.png"))
}

let squirclePath = try parseSquircleCGPath(from: darkSVGURL)
let darkMaster = try renderMasterIcon(svgURL: darkSVGURL, squirclePath: squirclePath, isLight: false)
let lightMaster = try renderMasterIcon(svgURL: lightSVGURL, squirclePath: squirclePath, isLight: true)

let darkICNSURL = iconDirURL.appendingPathComponent("AppIcon-Dark.icns")
let lightICNSURL = iconDirURL.appendingPathComponent("AppIcon-Light.icns")
let defaultICNSURL = iconDirURL.appendingPathComponent("AppIcon.icns")
let assetsCarURL = iconDirURL.appendingPathComponent("Assets.car")

let darkCache = try writeIconset(master: darkMaster, name: "AppIcon-Dark", outputICNS: darkICNSURL)
let lightCache = try writeIconset(master: lightMaster, name: "AppIcon-Light", outputICNS: lightICNSURL)

if FileManager.default.fileExists(atPath: defaultICNSURL.path) {
    try FileManager.default.removeItem(at: defaultICNSURL)
}
try FileManager.default.copyItem(at: darkICNSURL, to: defaultICNSURL)

try buildAssetsCar(lightCache: lightCache, darkCache: darkCache, outputCarURL: assetsCarURL)
try writeWindowsIcons(master: darkMaster, cache: darkCache)
try writeRaycastIcon(cache: darkCache)

let docsAssetsDir = rootURL.appendingPathComponent("docs/assets", isDirectory: true)
let logoDarkURL = docsAssetsDir.appendingPathComponent("logo-dark.svg")
let logoLightURL = docsAssetsDir.appendingPathComponent("logo-light.svg")
let logoAdaptiveURL = docsAssetsDir.appendingPathComponent("logo.svg")
let appIconAdaptiveURL = iconDirURL.appendingPathComponent("AppIcon.svg")

for (src, dst) in [(darkSVGURL, logoDarkURL), (lightSVGURL, logoLightURL), (logoAdaptiveURL, appIconAdaptiveURL)] {
    if FileManager.default.fileExists(atPath: dst.path) {
        try FileManager.default.removeItem(at: dst)
    }
    try FileManager.default.copyItem(at: src, to: dst)
}

for svg in [darkSVGURL, lightSVGURL, appIconAdaptiveURL, logoDarkURL, logoLightURL, logoAdaptiveURL] {
    try run("/usr/bin/xmllint", ["--noout", svg.path])
}

print("Generated AppIcon.icns, AppIcon-Dark.icns, AppIcon-Light.icns, Assets.car, synced SVG logos, Windows icons, and the Raycast icon.")
