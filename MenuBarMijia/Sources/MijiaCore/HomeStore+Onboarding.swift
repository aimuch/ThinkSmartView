import Foundation

extension HomeStore {
    static let onboardingMilestonesKey = "onboarding_milestones"
    static let askWhenUnsureKey = "assistant_ask_when_unsure"
    static let launchAtLoginOnboardingChoiceKey = "launch_at_login_onboarding_choice"
    static let launchAtLoginChoiceAppliedKey = "launch_at_login_onboarding_choice_applied"
    static let launchAtLoginSuggestionDismissedKey = "launch_at_login_suggestion_dismissed"
    static let launchAtLoginRegistrationFailedKey = "launch_at_login_registration_failed"

    /// Whether a real account or the offline demo is currently usable. The onboarding guide's first
    /// step and its demo escape hatch share this definition so they cannot disagree.
    public var hasConnectedHomeSource: Bool {
        if homeBackend == .demo { return true }
        if case .connected = connectionStatus { return true }
        return homeBackend == .xiaomiCloud && miSessionMaskedUserId != nil
    }

    /// Whether an account, token or the demo is configured, connected or not. Once this is true the
    /// full-screen connect form would be redundant, so onboarding moves to the dashboard checklist.
    public var hasConfiguredHomeSource: Bool {
        if homeBackend == .demo { return true }
        if hasHASource || miSessionMaskedUserId != nil || !miAccounts.isEmpty { return true }
        if case .connected = connectionStatus { return true }
        return false
    }

    /// The three-step checklist shown by the guide and the dashboard card (#180).
    public var onboardingChecklist: OnboardingChecklist {
        OnboardingChecklist(hasConfiguredSource: hasConfiguredHomeSource, milestones: onboardingMilestones)
    }

    /// Marks a step the user actually tried. Idempotent; the set is persisted for the next launch.
    public func recordOnboardingMilestone(_ milestone: OnboardingMilestone) {
        guard onboardingMilestones.insert(milestone).inserted else { return }
        userDefaults.set(
            onboardingMilestones.map(\.rawValue).sorted(),
            forKey: Self.onboardingMilestonesKey
        )
    }

    /// Launch-at-login state for the guide, the dashboard checklist and Settings (#281).
    public var launchAtLoginPolicy: LaunchAtLoginPolicy {
        LaunchAtLoginPolicy(
            status: launchAtLoginStatus,
            onboardingChoice: launchAtLoginOnboardingChoice,
            appliedOnboardingChoice: hasAppliedLaunchAtLoginOnboardingChoice,
            dismissedSuggestion: hasDismissedLaunchAtLoginSuggestion,
            registrationFailed: launchAtLoginRegistrationFailed
        )
    }

    /// Upgraded installs get one skippable row in the dashboard checklist, without opening the panel.
    public var shouldShowLaunchAtLoginSuggestion: Bool {
        launchAtLoginPolicy.showsChecklistSuggestion(
            hasCompletedOnboarding: hasCompletedOnboarding
        )
    }

    /// The panel draws the checklist card for the normal lessons or for that one launch-at-login row.
    public var showsOnboardingChecklistCard: Bool {
        showsOnboardingChecklist || shouldShowLaunchAtLoginSuggestion
    }

    public func markLaunchAtLoginOnboardingChoiceApplied() {
        hasAppliedLaunchAtLoginOnboardingChoice = true
    }

    public func dismissLaunchAtLoginSuggestion() {
        hasDismissedLaunchAtLoginSuggestion = true
    }

    /// The confirmation policy handed to `ModelActionPolicy`, so the advanced threshold and the
    /// plain-language switch stay one source of truth.
    public var aiConfirmationPolicy: ModelActionPolicy.ConfirmationPolicy {
        ModelActionPolicy.ConfirmationPolicy(
            askWhenUnsure: askWhenUnsureBeforeAIActions,
            threshold: ModelActionPolicy.autoRunThreshold
        )
    }
}
