// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MenuBarMijia",
    platforms: [
        .macOS(.v14)
    ],
    products: [
        .library(name: "MijiaCore", targets: ["MijiaCore"]),
        .executable(name: "MenuBarMijiaApp", targets: ["MenuBarMijiaApp"])
    ],
    targets: [
        .target(name: "KeychainTestGuard", path: "Tests/KeychainTestGuard"),
        .target(name: "KeychainTestSupport", dependencies: ["MijiaCore", "KeychainTestGuard"], path: "Tests/KeychainTestSupport"),
        .target(
            name: "MijiaCore",
            // Translation tables (#52): `Resources/Localizations/<language>.json`, copied into
            // `MenuBarMijia_MijiaCore.bundle/Contents/Resources/Localizations`. A new language is a
            // new file here — no Swift change, and `L10n.availableLanguages` picks it up.
            resources: [
                .copy("Resources/Localizations"),
                .copy("Resources/fallbackSpecs.json"),
                .copy("Resources/deviceProfiles.json"),
            ],
            swiftSettings: [
                .swiftLanguageMode(.v6)
            ]
        ),
        .executableTarget(
            name: "MenuBarMijiaApp",
            dependencies: ["MijiaCore"],
            swiftSettings: [
                .swiftLanguageMode(.v6)
            ]
        ),
        .testTarget(
            name: "MijiaCoreTests",
            dependencies: ["MijiaCore", "KeychainTestSupport"],
            swiftSettings: [
                .swiftLanguageMode(.v6)
            ]
        ),
        // SwiftPM 6 lets a test target import the executable target, so the app's views can be
        // rendered without moving 7,700 lines of SwiftUI into a library (#46).
        .testTarget(
            name: "MenuBarMijiaAppTests",
            dependencies: [
                "MenuBarMijiaApp",
                "MijiaCore",
                "KeychainTestSupport",
            ],
            swiftSettings: [
                .swiftLanguageMode(.v6)
            ]
        )
    ]
)
