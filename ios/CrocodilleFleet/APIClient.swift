import Foundation

@MainActor
final class FleetStore: ObservableObject {
    @Published var vehicles: [Vehicle] = []
    @Published var alerts: [FleetAlert] = []
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let baseURL = URL(string: "https://vansrenting-crocodille.onrender.com")!

    func refresh() async {
        isLoading = true
        defer { isLoading = false }
        do {
            async let vehiclesResponse: VehicleEnvelope = fetch("/api/v1/vehicles")
            async let alertsResponse: AlertsEnvelope = fetch("/api/v1/alerts?days=30")
            let (v, a) = try await (vehiclesResponse, alertsResponse)
            vehicles = v.vehicles
            alerts = a.alerts
            errorMessage = nil
            await NotificationManager.shared.schedule(alerts: a.alerts)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func fetch<T: Decodable>(_ path: String) async throws -> T {
        let url = URL(string: path, relativeTo: baseURL)!
        let (data, response) = try await URLSession.shared.data(from: url)
        guard let http = response as? HTTPURLResponse, 200..<300 ~= http.statusCode else {
            throw URLError(.badServerResponse)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }
}
