import Foundation
import UserNotifications

enum NotificationManagerError: LocalizedError {
    case permissionDenied

    var errorDescription: String? {
        switch self {
        case .permissionDenied:
            return "Notifikace nejsou povolené. Povol je v Nastavení → Oznámení → Vans Renting."
        }
    }
}

actor NotificationManager {
    static let shared = NotificationManager()

    @discardableResult
    func requestPermission() async -> Bool {
        do {
            return try await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .badge, .sound])
        } catch {
            return false
        }
    }

    func schedule(alerts: [FleetAlert]) async {
        let center = UNUserNotificationCenter.current()
        let pending = await center.pendingNotificationRequests()
        let fleetIdentifiers = pending
            .map(\.identifier)
            .filter { $0.hasPrefix("fleet-") }

        center.removePendingNotificationRequests(withIdentifiers: fleetIdentifiers)

        for alert in alerts where alert.days >= 0 {
            let content = UNMutableNotificationContent()
            content.title = "\(alert.spz) · \(alert.title)"
            content.body = alert.message
            content.sound = .default

            let delay: TimeInterval
            if alert.days == 0 {
                delay = 5
            } else {
                delay = max(10, TimeInterval((alert.days - 1) * 24 * 60 * 60))
            }

            let trigger = UNTimeIntervalNotificationTrigger(timeInterval: delay, repeats: false)
            let request = UNNotificationRequest(
                identifier: "fleet-\(alert.id)",
                content: content,
                trigger: trigger
            )
            try? await center.add(request)
        }
    }

    func scheduleTest(after seconds: TimeInterval = 10) async throws {
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()

        if settings.authorizationStatus == .notDetermined {
            let granted = try await center.requestAuthorization(options: [.alert, .badge, .sound])
            guard granted else {
                throw NotificationManagerError.permissionDenied
            }
        } else if settings.authorizationStatus != .authorized,
                  settings.authorizationStatus != .provisional,
                  settings.authorizationStatus != .ephemeral {
            throw NotificationManagerError.permissionDenied
        }

        let identifier = "vans-renting-notification-test"
        center.removePendingNotificationRequests(withIdentifiers: [identifier])

        let content = UNMutableNotificationContent()
        content.title = "Vans Renting · test"
        content.body = "Lokální notifikace fungují správně."
        content.sound = .default

        let trigger = UNTimeIntervalNotificationTrigger(
            timeInterval: max(1, seconds),
            repeats: false
        )
        let request = UNNotificationRequest(
            identifier: identifier,
            content: content,
            trigger: trigger
        )

        try await center.add(request)
    }
}
