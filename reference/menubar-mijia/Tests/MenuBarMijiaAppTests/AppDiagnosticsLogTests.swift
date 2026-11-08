import Foundation
import Testing
import MijiaCore
@testable import MenuBarMijiaApp

/// #17: the scan behind "复制诊断信息" reads crash file names and dates, never file contents.
struct AppDiagnosticsLogTests {
    @Test("The crash scan returns regular files with names and modification dates only")
    func crashScanReadsNamesAndDates() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("crash-scan-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let modified = Date(timeIntervalSince1970: 1_800_000_000)
        for name in ["MenuBarMijia-2026-11-05-101010.ips", "OtherApp-2026-11-05-101010.ips"] {
            let url = directory.appendingPathComponent(name)
            try Data().write(to: url)
            try FileManager.default.setAttributes([.modificationDate: modified], ofItemAtPath: url.path)
        }
        try FileManager.default.createDirectory(
            at: directory.appendingPathComponent("Retired"),
            withIntermediateDirectories: true
        )

        let stamps = AppDiagnosticsLog.crashReportStamps(in: directory)

        #expect(stamps.map(\.fileName).sorted()
            == ["MenuBarMijia-2026-11-05-101010.ips", "OtherApp-2026-11-05-101010.ips"])
        #expect(stamps.allSatisfy { abs($0.modifiedAt.timeIntervalSince(modified)) < 1 })
        let recent = DiagnosticsReportBuilder.crashTimestamps(from: stamps, now: modified.addingTimeInterval(60))
        #expect(recent == [modified])
    }

    @Test("The default scan directory is the system crash report folder")
    func defaultCrashDirectory() throws {
        let directory = try #require(AppDiagnosticsLog.diagnosticReportsDirectory())

        #expect(directory.path.hasSuffix("Library/Logs/DiagnosticReports"))
    }

    @Test("An absent folder yields no crashes instead of failing the report")
    func missingCrashDirectory() {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("absent-\(UUID().uuidString)", isDirectory: true)

        #expect(AppDiagnosticsLog.crashReportStamps(in: directory).isEmpty)
    }
}
