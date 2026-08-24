import SwiftUI

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
                    AsyncImage(url: URL(string: vehicle.photoUrl)) { image in
                        image.resizable().scaledToFit()
                    } placeholder: {
                        Image(systemName: "truck.box.fill").font(.title2)
                    }
                    .frame(width: 64, height: 48)

                    VStack(alignment: .leading, spacing: 3) {
                        Text(vehicle.spz).font(.headline)
                        Text("\(vehicle.brand) \(vehicle.model)").font(.subheadline)
                        Text(vehicle.status).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if !vehicle.alerts.isEmpty {
                        Text("\(vehicle.alerts.count)")
                            .font(.caption.bold())
                            .padding(7)
                            .background(.orange.opacity(0.18), in: Circle())
                    }
                }
            }
        }
        .navigationTitle("Crocodille Fleet")
        .searchable(text: $search, prompt: "SPZ, VIN, ID")
        .navigationDestination(for: Vehicle.self) { VehicleDetailView(vehicle: $0) }
        .refreshable { await store.refresh() }
        .overlay {
            if store.isLoading && store.vehicles.isEmpty { ProgressView("Načítám vozidla…") }
        }
    }
}

struct AlertsView: View {
    @EnvironmentObject var store: FleetStore

    var body: some View {
        List(store.alerts) { alert in
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
        .navigationTitle("Upozornění")
        .refreshable { await store.refresh() }
    }
}

struct VehicleDetailView: View {
    let vehicle: Vehicle

    var body: some View {
        List {
            Section {
                HStack {
                    Spacer()
                    AsyncImage(url: URL(string: vehicle.photoUrl)) { image in
                        image.resizable().scaledToFit()
                    } placeholder: {
                        Image(systemName: "truck.box.fill").font(.system(size: 55))
                    }
                    .frame(maxWidth: 220, maxHeight: 130)
                    Spacer()
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(vehicle.spz).font(.largeTitle.bold())
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
