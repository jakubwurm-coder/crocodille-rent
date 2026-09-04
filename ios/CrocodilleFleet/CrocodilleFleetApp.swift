import BackgroundTasks
import SwiftUI
import UIKit
import UserNotifications

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    private static let backgroundRefreshIdentifier = "cz.jakubwurm.vansrenting.crocodille.refresh"

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        registerBackgroundRefresh()
        return true
    }

    func applicationDidEnterBackground(_ application: UIApplication) {
        scheduleBackgroundRefresh()
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .list, .sound, .badge])
    }

    private func registerBackgroundRefresh() {
        BGTaskScheduler.shared.register(
            forTaskWithIdentifier: Self.backgroundRefreshIdentifier,
            using: nil
        ) { [weak self] task in
            guard let self,
                  let refreshTask = task as? BGAppRefreshTask else {
                task.setTaskCompleted(success: false)
                return
            }

            self.handleBackgroundRefresh(refreshTask)
        }
    }

    private func scheduleBackgroundRefresh() {
        BGTaskScheduler.shared.cancel(
            taskRequestWithIdentifier: Self.backgroundRefreshIdentifier
        )

        let request = BGAppRefreshTaskRequest(
            identifier: Self.backgroundRefreshIdentifier
        )
        request.earliestBeginDate = Date(timeIntervalSinceNow: 6 * 60 * 60)

        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            print("Background refresh scheduling failed: \(error.localizedDescription)")
        }
    }

    private func handleBackgroundRefresh(_ task: BGAppRefreshTask) {
        scheduleBackgroundRefresh()

        let operation = Task { @MainActor in
            let store = FleetStore()
            await store.refresh()
            task.setTaskCompleted(
                success: !Task.isCancelled && store.errorMessage == nil
            )
        }

        task.expirationHandler = {
            operation.cancel()
        }
    }
}

@main
struct CrocodilleFleetApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var store = FleetStore()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(store)
                .task {
                    await NotificationManager.shared.requestPermission()
                    await store.refresh()
                }
        }
    }
}
