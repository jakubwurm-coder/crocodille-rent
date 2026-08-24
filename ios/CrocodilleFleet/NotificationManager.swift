import Foundation
import UserNotifications

actor NotificationManager {
    static let shared = NotificationManager()

    func requestPermission() async {
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound])
    }

    func schedule(alerts: [FleetAlert]) async {
        let center = UNUserNotificationCenter.current()
        center.removeAllPendingNotificationRequests()

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
            let request = UNNotificationRequest(identifier: alert.id, content: content, trigger: trigger)
            try? await center.add(request)
        }
    }
}
