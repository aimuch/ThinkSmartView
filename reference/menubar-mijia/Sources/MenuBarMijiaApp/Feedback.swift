import AppKit
import Foundation

/// Optional success/failure sounds (#54). Off by default: a menu-bar utility should stay quiet
/// unless the user explicitly asks for audio feedback in Settings.
enum SoundFeedback {
    static let storageKey = "sound_feedback_enabled"

    static var isEnabled: Bool {
        UserDefaults.standard.bool(forKey: storageKey)
    }

    @MainActor
    static func playSuccess() {
        play(named: "Tink")
    }

    @MainActor
    static func playFailure() {
        play(named: "Submarine")
    }

    @MainActor
    private static func play(named name: String) {
        guard isEnabled else { return }
        NSSound(named: name)?.play()
    }
}
