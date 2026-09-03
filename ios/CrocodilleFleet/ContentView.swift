import SwiftUI
import UIKit

private let productionBaseURL = URL(string: "https://vansrenting-crocodille.onrender.com")!

struct ContentView: View {
    @EnvironmentObject var store: FleetStore

    var body: some View {
        TabView {
            NavigationStack { VehiclesView() }
                .tabItem { Label("Vozidla", systemImage: "car.2.fill") }
            NavigationStack { AlertsView() }
                .tabItem { Label("Upozornění", systemImage: "bell.badge.fill") }
        }
        .tint(.orange)
    }
}

struct VehiclesView: View {
    @EnvironmentObject var store: FleetStore
    @State private var search = ""

    var filtered: [Vehicle] {
        guard !search.isEmpty else { return store.vehicles }
        return store.vehicles.filter { v in
            [v.spz, v.vin, v.vehicleNumber, v.brand, v.model]
                .joined(separator: " ")
                .localizedCaseInsensitiveContains(search)
        }
    }

    var body: some View {
        List(filtered) { vehicle in
            NavigationLink(value: vehicle) {
                HStack(spacing: 12) {
                    VehicleThumbnail(vehicle: vehicle, width: 82, height: 58)

                    VStack(alignment: .leading, spacing: 3) {
                        HStack(spacing: 8) {
                            Text(vehicle.spz).font(.headline)
                            if !vehicle.vehicleNumber.isEmpty {
                                Text("ID \(vehicle.vehicleNumber)")
                                    .font(.caption.bold())
                                    .foregroundStyle(.secondary)
                            }
                        }
                        Text("\(vehicle.brand) \(vehicle.model)")
                            .font(.subheadline)
                            .lineLimit(1)
                        Text(vehicle.status)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    if !vehicle.alerts.isEmpty {
                        Text("\(vehicle.alerts.count)")
                            .font(.caption.bold())
                            .padding(7)
                            .background(.orange.opacity(0.18), in: Circle())
                    }
                }
                .padding(.vertical, 3)
            }
        }
        .navigationTitle("Vans Renting")
        .searchable(text: $search, prompt: "SPZ, VIN, ID")
        .navigationDestination(for: Vehicle.self) { VehicleDetailView(vehicle: $0) }
        .refreshable { await store.refresh() }
        .overlay {
            if store.isLoading && store.vehicles.isEmpty {
                ProgressView("Načítám vozidla…")
            } else if let error = store.errorMessage, store.vehicles.isEmpty {
                ContentUnavailableView(
                    "Vozidla se nepodařilo načíst",
                    systemImage: "wifi.exclamationmark",
                    description: Text(error)
                )
            }
        }
    }
}

struct AlertsView: View {
    @EnvironmentObject var store: FleetStore
    @Environment(\.openURL) private var openURL
    @State private var isSchedulingTest = false
    @State private var notificationTestMessage: String?

    var body: some View {
        List {
            Section("Stav notifikací") {
                HStack {
                    Label("Oprávnění", systemImage: "bell.badge")
                    Spacer()
                    Text(store.notificationAuthorization)
                        .font(.subheadline.bold())
                        .foregroundStyle(notificationStatusColor)
                }

                HStack {
                    Label("Naplánovaná upozornění", systemImage: "calendar.badge.clock")
                    Spacer()
                    Text("\(store.scheduledNotificationCount)")
                        .font(.subheadline.bold())
                }

                Button {
                    guard let url = URL(string: UIApplication.openSettingsURLString) else {
                        return
                    }
                    openURL(url)
                } label: {
                    Label("Otevřít nastavení iPhonu", systemImage: "gear")
                }
            }

            Section("Kontrola funkčnosti") {
                Button {
                    scheduleNotificationTest()
                } label: {
                    Label(
                        isSchedulingTest ? "Plánuji test…" : "Test notifikace za 10 sekund",
                        systemImage: "bell.and.waves.left.and.right.fill"
                    )
                }
                .disabled(isSchedulingTest)

                if let notificationTestMessage {
                    Text(notificationTestMessage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } footer: {
                Text("Po spuštění testu může aplikace zůstat otevřená. Banner i zvuk se mají zobrazit za 10 sekund.")
            }

            Section("Aktuální upozornění") {
                if store.alerts.isEmpty {
                    Text("Žádná upozornění v následujících 30 dnech.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(store.alerts) { alert in
                        NavigationLink {
                            if let vehicle = store.vehicles.first(where: { $0.id == alert.vehicleId }) {
                                VehicleDetailView(vehicle: vehicle)
                            } else {
                                Text("Vozidlo nebylo nalezeno")
                            }
                        } label: {
                            VStack(alignment: .leading, spacing: 5) {
                                HStack {
                                    Text(alert.spz).font(.headline)
                                    Spacer()
                                    Text(alert.days < 0 ? "PO TERMÍNU" : alert.days == 0 ? "DNES" : "\(alert.days) dní")
                                        .font(.caption.bold())
                                        .foregroundStyle(alert.days <= 7 ? .red : .orange)
                                }
                                Text(alert.title).font(.subheadline.bold())
                                Text(alert.message).font(.caption).foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 4)
                        }
                    }
                }
            }
        }
        .navigationTitle("Upozornění")
        .refreshable { await store.refresh() }
        .task {
            await store.refreshNotificationStatus()
        }
    }

    private var notificationStatusColor: Color {
        switch store.notificationAuthorization {
        case "Povolena":
            return .green
        case "Zakázána":
            return .red
        default:
            return .orange
        }
    }

    private func scheduleNotificationTest() {
        isSchedulingTest = true
        notificationTestMessage = nil

        Task {
            do {
                try await NotificationManager.shared.scheduleTest(after: 10)
                notificationTestMessage = "Test je naplánovaný. Vyčkej 10 sekund."
                await store.refreshNotificationStatus()
            } catch {
                notificationTestMessage = error.localizedDescription
            }
            isSchedulingTest = false
        }
    }
}

struct VehicleDetailView: View {
    let vehicle: Vehicle

    var body: some View {
        List {
            Section {
                HStack {
                    Spacer()
                    VehicleThumbnail(vehicle: vehicle, width: 280, height: 170)
                    Spacer()
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(vehicle.spz).font(.largeTitle.bold())
                    if !vehicle.vehicleNumber.isEmpty {
                        Text("ID vozidla: \(vehicle.vehicleNumber)")
                            .font(.headline)
                    }
                    Text("\(vehicle.brand) \(vehicle.model)")
                    Text("VIN: \(vehicle.vin)").font(.caption).foregroundStyle(.secondary)
                }
            }

            Section("Termíny") {
                row("STK", vehicle.stkUntil)
                row("Dálniční známka", vehicle.vignetteUntil)
                row("Povinné ručení", vehicle.liabilityUntil)
                row("Havarijní pojištění", vehicle.cascoUntil)
                row("Asistence", vehicle.assistanceUntil)
                row("Servis", vehicle.nextServiceDate)
            }

            Section("Technické údaje") {
                row("Rok", vehicle.year)
                row("Palivo", vehicle.fuel)
                row("Typ motoru", vehicle.engineType)
                row("Objem", vehicle.engineCapacity.isEmpty ? "" : "\(vehicle.engineCapacity) cm³")
                row("Výkon", vehicle.powerKw.isEmpty ? "" : "\(vehicle.powerKw) kW")
                row("Emise", vehicle.emission)
            }

            if !vehicle.alerts.isEmpty {
                Section("Upozornění") {
                    ForEach(vehicle.alerts) { alert in
                        Label(alert.message, systemImage: alert.days <= 7 ? "exclamationmark.triangle.fill" : "clock.badge.exclamationmark")
                    }
                }
            }
        }
        .navigationTitle(vehicle.spz)
        .navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder private func row(_ title: String, _ value: String) -> some View {
        if !value.isEmpty {
            HStack {
                Text(title)
                Spacer()
                Text(formatDate(value)).foregroundStyle(.secondary)
            }
        }
    }

    private func formatDate(_ raw: String) -> String {
        guard raw.count >= 10 else { return raw }
        let input = DateFormatter(); input.dateFormat = "yyyy-MM-dd"
        let output = DateFormatter(); output.dateFormat = "d.M.yyyy"
        if let d = input.date(from: String(raw.prefix(10))) { return output.string(from: d) }
        return raw
    }
}

private struct VehicleThumbnail: View {
    let vehicle: Vehicle
    let width: CGFloat
    let height: CGFloat

    var body: some View {
        AsyncImage(url: imageURL) { phase in
            switch phase {
            case .success(let image):
                image
                    .resizable()
                    .scaledToFit()
                    .padding(4)
            case .empty:
                ProgressView()
            case .failure:
                fallbackIcon
            @unknown default:
                fallbackIcon
            }
        }
        .frame(width: width, height: height)
        .background(.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
    }

    private var imageURL: URL? {
        let raw = vehicle.photoUrl.trimmingCharacters(in: .whitespacesAndNewlines)
        if !raw.isEmpty {
            if let absolute = URL(string: raw), absolute.scheme != nil {
                return absolute
            }
            if let relative = URL(string: raw, relativeTo: productionBaseURL) {
                return relative.absoluteURL
            }
        }

        let label = "\(vehicle.brand) \(vehicle.model)".uppercased()
        if label.contains("IVECO") || label.contains("DAILY") {
            return productionBaseURL.appending(path: "static/images/iveco_daily.png")
        }
        if label.contains("RENAULT") || label.contains("MASTER") {
            return productionBaseURL.appending(path: "static/images/renault_master.png")
        }
        return nil
    }

    private var fallbackIcon: some View {
        Image(systemName: "truck.box.fill")
            .font(.system(size: max(24, min(width, height) * 0.42)))
            .foregroundStyle(.secondary)
    }
}
