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
            let vehiclesResponse: VehicleEnvelope = try await fetch("/api/v1/vehicles")
            vehicles = vehiclesResponse.vehicles
            errorMessage = nil
        } catch {
            errorMessage = "Vozidla se nepodařilo načíst: \(readableMessage(for: error))"
            return
        }

        do {
            let alertsResponse: AlertsEnvelope = try await fetch("/api/v1/alerts?days=30")
            alerts = alertsResponse.alerts
            await NotificationManager.shared.schedule(alerts: alertsResponse.alerts)
        } catch {
            // Vozidla už jsou načtená, takže chyba upozornění nesmí shodit celý přehled.
            alerts = vehicles.flatMap(\.alerts)
            await NotificationManager.shared.schedule(alerts: alerts)
        }
    }

    private func fetch<T: Decodable>(_ path: String) async throws -> T {
        guard let url = URL(string: path, relativeTo: baseURL) else {
            throw APIError.invalidURL
        }

        var request = URLRequest(url: url)
        request.timeoutInterval = 30
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }

        guard 200..<300 ~= http.statusCode else {
            let preview = String(data: data.prefix(300), encoding: .utf8) ?? ""
            throw APIError.httpStatus(http.statusCode, preview)
        }

        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            let preview = String(data: data.prefix(300), encoding: .utf8) ?? ""
            throw APIError.decoding(error.localizedDescription, preview)
        }
    }

    private func readableMessage(for error: Error) -> String {
        if let apiError = error as? APIError {
            return apiError.localizedDescription
        }
        return error.localizedDescription
    }
}

private enum APIError: LocalizedError {
    case invalidURL
    case invalidResponse
    case httpStatus(Int, String)
    case decoding(String, String)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Neplatná adresa API."
        case .invalidResponse:
            return "Server vrátil neplatnou odpověď."
        case let .httpStatus(code, preview):
            return preview.isEmpty ? "Server vrátil HTTP \(code)." : "Server vrátil HTTP \(code): \(preview)"
        case let .decoding(message, preview):
            return preview.isEmpty ? "Chyba formátu dat: \(message)" : "Chyba formátu dat: \(message). Odpověď: \(preview)"
        }
    }
}
