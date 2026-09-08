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
        return store.vehicles.filter {
            [$0.spz, $0.vin, $0.vehicleNumber, $0.brand, $0.model]
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
                        Text(vehicle.spz).font(.headline)
                        Text("\(vehicle.brand) \(vehicle.model)").font(.subheadline).lineLimit(1)
                        Text(vehicle.vin).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                    Spacer()
                    if !vehicle.alerts.isEmpty {
                        Text("\(vehicle.alerts.count)").font(.caption.bold()).padding(7).background(.orange.opacity(0.18), in: Circle())
                    }
                }
            }
        }
        .navigationTitle("Vans Renting")
        .searchable(text: $search, prompt: "SPZ nebo VIN")
        .navigationDestination(for: Vehicle.self) { VehicleDetailView(vehicle: $0) }
        .refreshable { await store.refresh() }
        .overlay {
            if store.isLoading && store.vehicles.isEmpty { ProgressView("Načítám vozidla…") }
            else if let error = store.errorMessage, store.vehicles.isEmpty {
                ContentUnavailableView("Vozidla se nepodařilo načíst", systemImage: "wifi.exclamationmark", description: Text(error))
            }
        }
    }
}

struct AlertsView: View {
    @EnvironmentObject var store: FleetStore
    @Environment(\.openURL) private var openURL
    @State private var testMessage: String?

    var body: some View {
        List {
            Section("Serverové notifikace") {
                HStack { Text("Oprávnění"); Spacer(); Text(store.notificationAuthorization).bold() }
                HStack { Text("APNs registrace"); Spacer(); Text(store.pushRegistration).bold() }
                Text("Termínová upozornění posílá server 30, 14 a 7 dní před expirací, potom každý den až do dne expirace.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Otevřít nastavení iPhonu") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
                }
            }

            Section("Test zařízení") {
                Button("Test notifikace za 10 sekund") {
                    Task {
                        do {
                            try await NotificationManager.shared.scheduleTest(after: 10)
                            testMessage = "Test naplánován."
                        } catch { testMessage = error.localizedDescription }
                    }
                }
                if let testMessage { Text(testMessage).font(.caption).foregroundStyle(.secondary) }
            }

            Section("Aktuální upozornění") {
                if store.alerts.isEmpty {
                    Text("Žádná upozornění v následujících 30 dnech.").foregroundStyle(.secondary)
                } else {
                    ForEach(store.alerts) { alert in
                        VStack(alignment: .leading, spacing: 5) {
                            HStack { Text(alert.spz).font(.headline); Spacer(); Text(alert.days < 0 ? "PO TERMÍNU" : alert.days == 0 ? "DNES" : "\(alert.days) dní").font(.caption.bold()) }
                            Text(alert.title).font(.subheadline.bold())
                            Text(alert.message).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .navigationTitle("Upozornění")
        .refreshable { await store.refresh() }
        .task { await store.refreshNotificationStatus() }
    }
}

struct VehicleDetailView: View {
    let vehicle: Vehicle

    var body: some View {
        List {
            Section {
                HStack { Spacer(); VehicleThumbnail(vehicle: vehicle, width: 280, height: 170); Spacer() }
                Text(vehicle.spz).font(.largeTitle.bold())
                Text("\(vehicle.brand) \(vehicle.model)")
                Text("VIN: \(vehicle.vin)").font(.caption).foregroundStyle(.secondary)
            }

            Section("Termíny") {
                dateRow("STK", vehicle.stkUntil)
                if vehicle.vignetteStatus == "exempt" { valueRow("Dálniční známka", "Osvobozeno") }
                else if vehicle.vignetteStatus == "missing" { valueRow("Dálniční známka", "Nenalezena") }
                else {
                    dateRow("Dálniční známka", vehicle.vignetteUntil)
                    if vehicle.vignetteStatus == "future" { dateRow("Známka začíná", vehicle.vignetteFutureFrom) }
                }
                dateRow("Pojištění", vehicle.insuranceUntil)
                dateRow("Asistence", vehicle.assistanceUntil)
            }

            Section("Servis") {
                valueRow("Příští výměna oleje", vehicle.nextServiceKm.isEmpty ? "nezadáno" : "\(vehicle.nextServiceKm) km")
            }

            Section("Technické údaje") {
                valueRow("Rok", vehicle.year)
                dateRow("První registrace", vehicle.firstRegistration)
                valueRow("Palivo", vehicle.fuel)
                valueRow("Typ motoru", vehicle.engineType)
                valueRow("Objem", vehicle.engineCapacity.isEmpty ? "" : "\(vehicle.engineCapacity) cm³")
                valueRow("Výkon", vehicle.powerKw.isEmpty ? "" : "\(vehicle.powerKw) kW")
                valueRow("Emise", vehicle.emission)
            }
        }
        .navigationTitle(vehicle.spz)
        .navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder private func valueRow(_ title: String, _ value: String) -> some View {
        if !value.isEmpty { HStack { Text(title); Spacer(); Text(value).foregroundStyle(.secondary) } }
    }

    @ViewBuilder private func dateRow(_ title: String, _ value: String) -> some View {
        if !value.isEmpty { valueRow(title, formatDate(value)) }
    }

    private func formatDate(_ raw: String) -> String {
        guard raw.count >= 10 else { return raw }
        let input = DateFormatter(); input.dateFormat = "yyyy-MM-dd"
        let output = DateFormatter(); output.dateFormat = "d.M.yyyy"
        return input.date(from: String(raw.prefix(10))).map(output.string) ?? raw
    }
}

private struct VehicleThumbnail: View {
    let vehicle: Vehicle
    let width: CGFloat
    let height: CGFloat

    var body: some View {
        AsyncImage(url: imageURL) { phase in
            switch phase {
            case .success(let image): image.resizable().scaledToFit().padding(4)
            case .empty: ProgressView()
            default: Image(systemName: "truck.box.fill").font(.system(size: 30)).foregroundStyle(.secondary)
            }
        }
        .frame(width: width, height: height)
        .background(.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private var imageURL: URL? {
        let raw = vehicle.photoUrl.trimmingCharacters(in: .whitespacesAndNewlines)
        if !raw.isEmpty { return URL(string: raw, relativeTo: productionBaseURL)?.absoluteURL }
        let label = "\(vehicle.brand) \(vehicle.model)".uppercased()
        if label.contains("IVECO") || label.contains("DAILY") { return productionBaseURL.appending(path: "static/images/iveco_daily.png") }
        if label.contains("RENAULT") || label.contains("MASTER") { return productionBaseURL.appending(path: "static/images/renault_master.png") }
        return nil
    }
}
