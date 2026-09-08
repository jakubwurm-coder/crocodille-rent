import Foundation

private let productionBaseURL = URL(string: "https://vansrenting-crocodille.onrender.com")!

@MainActor
final class FleetStore: ObservableObject {
    @Published var vehicles: [Vehicle] = []
    @Published var alerts: [FleetAlert] = []
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var notificationAuthorization = "Kontroluji…"
    @Published var pushRegistration = "Čekám na registraci…"

    func refresh() async {
        isLoading = true
        defer { isLoading = false }

        do {
            let response: VehicleEnvelope = try await fetch("/api/v1/vehicles")
            vehicles = response.vehicles
            errorMessage = nil
        } catch {
            errorMessage = "Vozidla se nepodařilo načíst: \(error.localizedDescription)"
            return
        }

        do {
            let response: AlertsEnvelope = try await fetch("/api/v1/alerts?days=30")
            alerts = response.alerts
        } catch {
            alerts = vehicles.flatMap(\.alerts)
        }
        await refreshNotificationStatus()
    }

    func refreshNotificationStatus() async {
        let state = await NotificationManager.shared.authorizationState()
        notificationAuthorization = state.title
        pushRegistration = UserDefaults.standard.string(forKey: "apnsDeviceToken")?.isEmpty == false
            ? "Registrováno na serveru"
            : "Čekám na APNs token"
    }

    private func fetch<T: Decodable>(_ path: String) async throws -> T {
        guard let url = URL(string: path, relativeTo: productionBaseURL) else { throw APIError.invalidURL }
        var request = URLRequest(url: url)
        request.timeoutInterval = 30
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard 200..<300 ~= http.statusCode else { throw APIError.httpStatus(http.statusCode) }
        return try JSONDecoder().decode(T.self, from: data)
    }
}

enum PushRegistrationService {
    static func register(deviceToken: String) async {
        let url = productionBaseURL.appending(path: "api/v1/devices/register")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 20
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
        request.httpBody = try? JSONSerialization.data(withJSONObject: [
            "token": deviceToken,
            "platform": "ios",
            "app_version": version
        ])
        do {
            let (_, response) = try await URLSession.shared.data(for: request)
            if let http = response as? HTTPURLResponse, 200..<300 ~= http.statusCode {
                UserDefaults.standard.set(deviceToken, forKey: "apnsDeviceToken")
            }
        } catch {
            print("Push token registration failed: \(error.localizedDescription)")
        }
    }
}

private enum APIError: LocalizedError {
    case invalidURL
    case invalidResponse
    case httpStatus(Int)

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "Neplatná adresa API."
        case .invalidResponse: return "Server vrátil neplatnou odpověď."
        case .httpStatus(let code): return "Server vrátil HTTP \(code)."
        }
    }
}
