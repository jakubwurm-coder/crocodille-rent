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
    let vignetteStatus: String
    let vignetteFutureFrom: String
    let vignetteFutureUntil: String
    let insuranceUntil: String
    let assistanceUntil: String
    let nextServiceKm: String
    let fuel: String
    let engineType: String
    let engineCapacity: String
    let powerKw: String
    let emission: String
    let firstRegistration: String
    let photoUrl: String
    let alerts: [FleetAlert]

    enum CodingKeys: String, CodingKey {
        case id, spz, vin, brand, model, year, status, km, fuel, alerts, emission
        case vehicleNumber = "vehicle_number"
        case stkUntil = "stk_until"
        case vignetteUntil = "vignette_until"
        case vignetteStatus = "vignette_status"
        case vignetteFutureFrom = "vignette_future_from"
        case vignetteFutureUntil = "vignette_future_until"
        case insuranceUntil = "insurance_until"
        case assistanceUntil = "assistance_until"
        case nextServiceKm = "next_service_km"
        case engineType = "engine_type"
        case engineCapacity = "engine_capacity"
        case powerKw = "power_kw"
        case firstRegistration = "first_registration"
        case photoUrl = "photo_url"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.flexibleString(forKey: .id)
        spz = c.flexibleString(forKey: .spz)
        vehicleNumber = c.flexibleString(forKey: .vehicleNumber)
        vin = c.flexibleString(forKey: .vin)
        brand = c.flexibleString(forKey: .brand)
        model = c.flexibleString(forKey: .model)
        year = c.flexibleString(forKey: .year)
        status = c.flexibleString(forKey: .status)
        km = c.flexibleString(forKey: .km)
        stkUntil = c.flexibleString(forKey: .stkUntil)
        vignetteUntil = c.flexibleString(forKey: .vignetteUntil)
        vignetteStatus = c.flexibleString(forKey: .vignetteStatus)
        vignetteFutureFrom = c.flexibleString(forKey: .vignetteFutureFrom)
        vignetteFutureUntil = c.flexibleString(forKey: .vignetteFutureUntil)
        insuranceUntil = c.flexibleString(forKey: .insuranceUntil)
        assistanceUntil = c.flexibleString(forKey: .assistanceUntil)
        nextServiceKm = c.flexibleString(forKey: .nextServiceKm)
        fuel = c.flexibleString(forKey: .fuel)
        engineType = c.flexibleString(forKey: .engineType)
        engineCapacity = c.flexibleString(forKey: .engineCapacity)
        powerKw = c.flexibleString(forKey: .powerKw)
        emission = c.flexibleString(forKey: .emission)
        firstRegistration = c.flexibleString(forKey: .firstRegistration)
        photoUrl = c.flexibleString(forKey: .photoUrl)
        alerts = (try? c.decodeIfPresent([FleetAlert].self, forKey: .alerts)) ?? []
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

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.flexibleString(forKey: .id)
        vehicleId = c.flexibleString(forKey: .vehicleId)
        spz = c.flexibleString(forKey: .spz)
        kind = c.flexibleString(forKey: .kind)
        title = c.flexibleString(forKey: .title)
        message = c.flexibleString(forKey: .message)
        date = c.flexibleString(forKey: .date)
        days = c.flexibleInt(forKey: .days)
        severity = c.flexibleString(forKey: .severity)
    }
}

private extension KeyedDecodingContainer {
    func flexibleString(forKey key: Key, default defaultValue: String = "") -> String {
        if let value = try? decode(String.self, forKey: key) { return value }
        if let value = try? decode(Int.self, forKey: key) { return String(value) }
        if let value = try? decode(Double.self, forKey: key) { return value.rounded() == value ? String(Int(value)) : String(value) }
        if let value = try? decode(Bool.self, forKey: key) { return value ? "true" : "false" }
        return defaultValue
    }

    func flexibleInt(forKey key: Key, default defaultValue: Int = 0) -> Int {
        if let value = try? decode(Int.self, forKey: key) { return value }
        if let value = try? decode(Double.self, forKey: key) { return Int(value) }
        if let value = try? decode(String.self, forKey: key), let parsed = Int(value) { return parsed }
        return defaultValue
    }
}
