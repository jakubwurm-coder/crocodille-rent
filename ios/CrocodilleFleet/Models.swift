import Foundation

struct VehicleEnvelope: Decodable {
    let vehicles: [Vehicle]
}

struct AlertsEnvelope: Decodable {
    let days: Int
    let count: Int
    let alerts: [FleetAlert]
}

struct Vehicle: Decodable, Identifiable, Hashable {
    let id: String
    let spz: String
    let vehicleNumber: String
    let vin: String
    let brand: String
    let model: String
    let year: String
    let status: String
    let km: String
    let stkUntil: String
    let vignetteUntil: String
    let liabilityUntil: String
    let cascoUntil: String
    let assistanceUntil: String
    let nextServiceDate: String
    let nextServiceKm: String
    let fuel: String
    let engineType: String
    let engineCapacity: String
    let powerKw: String
    let emission: String
    let photoUrl: String
    let alerts: [FleetAlert]

    enum CodingKeys: String, CodingKey {
        case id, spz, vin, brand, model, year, status, km, fuel, alerts, emission
        case vehicleNumber = "vehicle_number"
        case stkUntil = "stk_until"
        case vignetteUntil = "vignette_until"
        case liabilityUntil = "liability_until"
        case cascoUntil = "casco_until"
        case assistanceUntil = "assistance_until"
        case nextServiceDate = "next_service_date"
        case nextServiceKm = "next_service_km"
        case engineType = "engine_type"
        case engineCapacity = "engine_capacity"
        case powerKw = "power_kw"
        case photoUrl = "photo_url"
    }
}

struct FleetAlert: Decodable, Identifiable, Hashable {
    let id: String
    let vehicleId: String
    let spz: String
    let kind: String
    let title: String
    let message: String
    let date: String
    let days: Int
    let severity: String

    enum CodingKeys: String, CodingKey {
        case id, spz, kind, title, message, date, days, severity
        case vehicleId = "vehicle_id"
    }
}
