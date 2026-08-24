import SwiftUI
import UserNotifications

@main
struct CrocodilleFleetApp: App {
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
