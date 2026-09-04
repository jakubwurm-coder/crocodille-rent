import Foundation

struct VehicleEnvelope: Decodable {
    let vehicles: [Vehicle]

    enum CodingKeys: String, CodingKey { case vehicles }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        vehicles = try container.decodeIfPresent([Vehicle].self, forKey: .vehicles) ?? []
    }
}

struct AlertsEnvelope: Decodable {
    let days: Int
    let count: Int
    let alerts: [FleetAlert]

    enum CodingKeys: String, CodingKey { case days, count, alerts }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        days = container.flexibleInt(forKey: .days)
        alerts = try container.decodeIfPresent([FleetAlert].self, forKey: .alerts) ?? []
        count = container.flexibleInt(forKey: .count, default: alerts.count)
    }
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
        liabilityUntil = c.flexibleString(forKey: .liabilityUntil)
        cascoUntil = c.flexibleString(forKey: .cascoUntil)
        assistanceUntil = c.flexibleString(forKey: .assistanceUntil)
        nextServiceDate = c.flexibleString(forKey: .nextServiceDate)
        nextServiceKm = c.flexibleString(forKey: .nextServiceKm)
        fuel = c.flexibleString(forKey: .fuel)
        engineType = c.flexibleString(forKey: .engineType)
        engineCapacity = c.flexibleString(forKey: .engineCapacity)
        powerKw = c.flexibleString(forKey: .powerKw)
        emission = c.flexibleString(forKey: .emission)
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
        if let value = try? decode(String.self, forKey: key) {
            return value
        }
        if let value = try? decode(Int.self, forKey: key) {
            return String(value)
        }
        if let value = try? decode(Double.self, forKey: key) {
            return value.rounded() == value ? String(Int(value)) : String(value)
        }
        if let value = try? decode(Bool.self, forKey: key) {
            return value ? "true" : "false"
        }
        return defaultValue
    }

    func flexibleInt(forKey key: Key, default defaultValue: Int = 0) -> Int {
        if let value = try? decode(Int.self, forKey: key) {
            return value
        }
        if let value = try? decode(Double.self, forKey: key) {
            return Int(value)
        }
        if let value = try? decode(String.self, forKey: key),
           let intValue = Int(value) {
            return intValue
        }
        return defaultValue
    }
}
