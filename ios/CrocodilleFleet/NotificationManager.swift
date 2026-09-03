import Foundation
import UserNotifications

enum NotificationAuthorizationState {
    case allowed
    case denied
    case notDetermined

    var title: String {
        switch self {
        case .allowed:
            return "Povolena"
        case .denied:
            return "Zakázána"
        case .notDetermined:
            return "Čekají na povolení"
        }
    }
}

enum NotificationManagerError: LocalizedError {
    case permissionDenied

    var errorDescription: String? {
        switch self {
        case .permissionDenied:
            return "Notifikace nejsou povolené. Povol je v Nastavení → Oznámení → Vans Renting."
        }
    }
}

private struct NotificationCandidate {
    let identifier: String
    let title: String
    let body: String
    let date: Date
    let threadIdentifier: String
}

actor NotificationManager {
    static let shared = NotificationManager()

    private let firstReminderDays = [30, 14]
    private let dailyReminderDays = Array(stride(from: 13, through: 0, by: -1))
    private let dailyReminderHours = [9, 16]
    private let maximumFleetNotifications = 60

    func authorizationState() async -> NotificationAuthorizationState {
        let settings = await UNUserNotificationCenter.current().notificationSettings()

        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral:
            return .allowed
        case .denied:
            return .denied
        case .notDetermined:
            return .notDetermined
        @unknown default:
            return .denied
        }
    }

    @discardableResult
    func requestPermission() async -> Bool {
        if await authorizationState() == .allowed {
            return true
        }

        do {
            return try await UNUserNotificationCenter.current()
                .requestAuthorization(options: [.alert, .badge, .sound])
        } catch {
            return false
        }
    }

    @discardableResult
    func schedule(alerts: [FleetAlert]) async -> Int {
        let center = UNUserNotificationCenter.current()
        let pending = await center.pendingNotificationRequests()
        let fleetIdentifiers = pending
            .map(\.identifier)
            .filter { $0.hasPrefix("fleet-") }

        center.removePendingNotificationRequests(withIdentifiers: fleetIdentifiers)

        guard await authorizationState() == .allowed else {
            return 0
        }

        let candidates = buildCandidates(from: alerts)
            .sorted { $0.date < $1.date }
            .prefix(maximumFleetNotifications)

        var scheduledCount = 0

        for candidate in candidates {
            let content = UNMutableNotificationContent()
            content.title = candidate.title
            content.body = candidate.body
            content.sound = .default
            content.threadIdentifier = candidate.threadIdentifier

            var calendar = Calendar.autoupdatingCurrent
            calendar.timeZone = .autoupdatingCurrent
            let dateComponents = calendar.dateComponents(
                [.year, .month, .day, .hour, .minute],
                from: candidate.date
            )
            let trigger = UNCalendarNotificationTrigger(
                dateMatching: dateComponents,
                repeats: false
            )
            let request = UNNotificationRequest(
                identifier: candidate.identifier,
                content: content,
                trigger: trigger
            )

            do {
                try await center.add(request)
                scheduledCount += 1
            } catch {
                continue
            }
        }

        return scheduledCount
    }

    func scheduleTest(after seconds: TimeInterval = 10) async throws {
        let center = UNUserNotificationCenter.current()

        if await authorizationState() == .notDetermined {
            let granted = try await center.requestAuthorization(options: [.alert, .badge, .sound])
            guard granted else {
                throw NotificationManagerError.permissionDenied
            }
        } else if await authorizationState() != .allowed {
            throw NotificationManagerError.permissionDenied
        }

        let identifier = "vans-renting-notification-test"
        center.removePendingNotificationRequests(withIdentifiers: [identifier])

        let content = UNMutableNotificationContent()
        content.title = "Vans Renting · test"
        content.body = "Lokální notifikace na tomto iPhonu fungují správně."
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

    private func buildCandidates(from alerts: [FleetAlert]) -> [NotificationCandidate] {
        let now = Date()
        var candidates: [NotificationCandidate] = []

        for alert in alerts where alert.days >= 0 {
            guard let dueDate = parseAPIDate(alert.date) else {
                if let fallback = fallbackCandidate(for: alert, now: now) {
                    candidates.append(fallback)
                }
                continue
            }

            for daysBefore in firstReminderDays {
                if let candidate = candidate(
                    for: alert,
                    dueDate: dueDate,
                    daysBefore: daysBefore,
                    hour: 9,
                    now: now
                ) {
                    candidates.append(candidate)
                }
            }

            for daysBefore in dailyReminderDays {
                for hour in dailyReminderHours {
                    if let candidate = candidate(
                        for: alert,
                        dueDate: dueDate,
                        daysBefore: daysBefore,
                        hour: hour,
                        now: now
                    ) {
                        candidates.append(candidate)
                    }
                }
            }
        }

        return candidates
    }

    private func candidate(
        for alert: FleetAlert,
        dueDate: Date,
        daysBefore: Int,
        hour: Int,
        now: Date
    ) -> NotificationCandidate? {
        guard let scheduledDate = notificationDate(
            dueDate: dueDate,
            daysBefore: daysBefore,
            hour: hour,
            now: now
        ) else {
            return nil
        }

        return NotificationCandidate(
            identifier: "fleet-\(daysBefore)-\(hour)-\(alert.id)",
            title: "\(alert.spz) · \(alert.title)",
            body: notificationBody(
                title: alert.title,
                dueDate: dueDate,
                daysBefore: daysBefore
            ),
            date: scheduledDate,
            threadIdentifier: alert.spz
        )
    }

    private func notificationDate(
        dueDate: Date,
        daysBefore: Int,
        hour: Int,
        now: Date
    ) -> Date? {
        var calendar = Calendar.autoupdatingCurrent
        calendar.timeZone = .autoupdatingCurrent

        guard let day = calendar.date(byAdding: .day, value: -daysBefore, to: dueDate) else {
            return nil
        }

        var components = calendar.dateComponents([.year, .month, .day], from: day)
        components.hour = hour
        components.minute = 0

        guard let scheduledDate = calendar.date(from: components) else {
            return nil
        }

        if daysBefore == 0,
           hour == dailyReminderHours.last,
           calendar.isDate(dueDate, inSameDayAs: now),
           scheduledDate <= now {
            return now.addingTimeInterval(5)
        }

        return scheduledDate > now ? scheduledDate : nil
    }

    private func fallbackCandidate(
        for alert: FleetAlert,
        now: Date
    ) -> NotificationCandidate? {
        let delay: TimeInterval

        if alert.days == 0 {
            delay = 5
        } else {
            delay = max(10, TimeInterval((alert.days - 1) * 24 * 60 * 60))
        }

        return NotificationCandidate(
            identifier: "fleet-fallback-\(alert.id)",
            title: "\(alert.spz) · \(alert.title)",
            body: alert.message,
            date: now.addingTimeInterval(delay),
            threadIdentifier: alert.spz
        )
    }

    private func parseAPIDate(_ value: String) -> Date? {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = .autoupdatingCurrent
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.date(from: String(value.prefix(10)))
    }

    private func notificationBody(
        title: String,
        dueDate: Date,
        daysBefore: Int
    ) -> String {
        let timing: String

        switch daysBefore {
        case 0:
            timing = "končí dnes"
        case 1:
            timing = "končí zítra"
        default:
            timing = "končí za \(daysBefore) dní"
        }

        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "cs_CZ")
        formatter.dateFormat = "d. M. yyyy"

        return "\(title) \(timing). Termín: \(formatter.string(from: dueDate))."
    }
}
