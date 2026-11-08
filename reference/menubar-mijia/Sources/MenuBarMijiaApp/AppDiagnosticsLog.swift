import Foundation
import MijiaCore
import Observation

/// App-local ring buffer of *when* errors happened, used by "复制诊断信息" (#45).
///
/// Only timestamps are stored: error strings can embed a host or device payload, so the raw text is
/// never persisted. The diagnostics report therefore shows a count and the latest time, nothing else.
@Observable
@MainActor
final class AppDiagnosticsLog {
    static let shared = AppDiagnosticsLog()

    private static let storageKey = "diagnostics_error_timestamps"
    private static let capacity = 20

    private(set) var errorTimestamps: [Date]
    @ObservationIgnored private var lastRecordedToastId: UUID?

    private init() {
        let stored = UserDefaults.standard.array(forKey: Self.storageKey) as? [Double] ?? []
        errorTimestamps = stored.map(Date.init(timeIntervalSince1970:))
    }

    func record(_ notice: ToastNotice?) {
        guard let notice, notice.isError else { return }
        guard notice.id != lastRecordedToastId else { return }
        lastRecordedToastId = notice.id
        errorTimestamps.append(Date())
        if errorTimestamps.count > Self.capacity {
            errorTimestamps.removeFirst(errorTimestamps.count - Self.capacity)
        }
        UserDefaults.standard.set(errorTimestamps.map(\.timeIntervalSince1970), forKey: Self.storageKey)
    }

    // MARK: - Local crash files (#17)

    /// The system folder macOS writes crash reports to.
    nonisolated static func diagnosticReportsDirectory() -> URL? {
        FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask).first?
            .appendingPathComponent("Logs", isDirectory: true)
            .appendingPathComponent("DiagnosticReports", isDirectory: true)
    }

    /// Names and modification times of the entries in a crash report folder.
    ///
    /// The scan never opens a crash file: its contents can carry a user name, a device payload or a
    /// stack trace, and none of that belongs in a report the user pastes into an issue (#17). Only
    /// regular files are returned, so subfolders such as `Retired` stay out.
    nonisolated static func crashReportStamps(
        in directory: URL? = AppDiagnosticsLog.diagnosticReportsDirectory()
    ) -> [DiagnosticsReportBuilder.CrashReportStamp] {
        guard let directory else { return [] }
        let keys: [URLResourceKey] = [.contentModificationDateKey, .isRegularFileKey]
        guard let entries = try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: keys,
            options: [.skipsHiddenFiles]
        ) else { return [] }
        return entries.compactMap { url in
            guard let values = try? url.resourceValues(forKeys: Set(keys)),
                  values.isRegularFile == true,
                  let modifiedAt = values.contentModificationDate else { return nil }
            return DiagnosticsReportBuilder.CrashReportStamp(
                fileName: url.lastPathComponent,
                modifiedAt: modifiedAt
            )
        }
    }
}
