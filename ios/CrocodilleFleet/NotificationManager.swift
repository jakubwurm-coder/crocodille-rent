import Foundation
import UIKit
import UserNotifications

enum NotificationAuthorizationState {
    case allowed
    case denied
    case notDetermined

    var title: String {
        switch self {
        case .allowed: return "Povolena"
        case .denied: return "Zakázána"
        case .notDetermined: return "Čekají na povolení"
        }
    }
}

enum NotificationManagerError: LocalizedError {
    case permissionDenied

    var errorDescription: String? {
        "Notifikace nejsou povolené. Povol je v Nastavení → Oznámení → Vans Renting."
    }
}

actor NotificationManager {
    static let shared = NotificationManager()

    func authorizationState() async -> NotificationAuthorizationState {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral: return .allowed
        case .denied: return .denied
        case .notDetermined: return .notDetermined
        @unknown default: return .denied
        }
    }

    @discardableResult
    func requestPermission() async -> Bool {
        let center = UNUserNotificationCenter.current()
        let state = await authorizationState()
        let granted: Bool
        if state == .allowed {
            granted = true
        } else {
            granted = (try? await center.requestAuthorization(options: [.alert, .badge, .sound])) ?? false
        }
        if granted {
            await MainActor.run { UIApplication.shared.registerForRemoteNotifications() }
        }
        return granted
    }

    // Pouze diagnostický lokální test. Provozní termínové notifikace posílá server přes APNs.
    func scheduleTest(after seconds: TimeInterval = 10) async throws {
        guard await authorizationState() == .allowed else { throw NotificationManagerError.permissionDenied }
        let content = UNMutableNotificationContent()
        content.title = "Vans Renting · test"
        content.body = "Notifikace na tomto iPhonu fungují. Provozní upozornění chodí ze serveru."
        content.sound = .default
        let request = UNNotificationRequest(
            identifier: "vans-renting-notification-test",
            content: content,
            trigger: UNTimeIntervalNotificationTrigger(timeInterval: max(1, seconds), repeats: false)
        )
        try await UNUserNotificationCenter.current().add(request)
    }
}
