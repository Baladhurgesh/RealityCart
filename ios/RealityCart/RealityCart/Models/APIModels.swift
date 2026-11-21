import Foundation

struct HealthResponse: Codable {
    let status: String
    let gemini_configured: Bool
    let shopify_profile: String
    let models: [String: String]
}

struct SurfaceCalibration: Codable {
    var surface_id: String = "primary_surface"
    var width_m: Double?
    var depth_m: Double?
    var height_m: Double?
    var corner_pixels: [[Double]]?
}

struct RoomPlanPayload: Codable {
    var raw: [String: AnyCodable]?
    var walls: [[String: AnyCodable]] = []
    var objects: [[String: AnyCodable]] = []
    var floors: [[String: AnyCodable]] = []
}

struct SurfaceDTO: Codable, Identifiable {
    let id: String
    let type: String?
    let label: String?
    let width_m: Double?
    let depth_m: Double?
    let height_m: Double?
    let dimensions_source: String?
    let confidence: Double?
}

struct SceneObjectDTO: Codable, Identifiable {
    let id: String
    let type: String
    let label: String?
    let location: String?
    let surface_id: String?
    let keep: Bool?
    let notes: String?
}

struct AvailablePositionDTO: Codable, Identifiable {
    let id: String
    let surface_id: String
    let region: String
    let description: String?
}

struct SceneAnalysisDTO: Codable {
    let scene_type: String
    let style_cues: [String]?
    let surfaces: [SurfaceDTO]?
    let objects: [SceneObjectDTO]?
    let available_positions: [AvailablePositionDTO]?
    let clutter_level: String?
    let lighting: String?
    let notes: String?
    let model: String?
}

struct PlacementSlotDTO: Codable, Identifiable {
    let id: String
    let category: String
    let surface_id: String
    let region: String
    let search_query: String?
    let max_price_usd: Double?
    let preferred_color: String?
    let reason: String?
    let proxy_model: String?
}

struct DesignPlanDTO: Codable {
    let summary: String
    let style: String
    let budget_usd: Double
    let keep_object_ids: [String]?
    let remove_object_ids: [String]?
    let slots: [PlacementSlotDTO]?
    let model: String?
}

struct ProductOfferDTO: Codable, Identifiable {
    let id: String
    let title: String
    let description: String?
    let price_usd: Double?
    let currency: String?
    let image_url: String?
    let product_url: String?
    let vendor: String?
    let has_3d: Bool?
    // Added with the Shop MCP integration (all optional → backward compatible).
    let price_max_usd: Double?
    let image_urls: [String]?
    let seller_domain: String?
    let rating: Double?
    let review_count: Int?
    let available: Bool?
    let dimensions_hint: String?
}

struct DecisionResultDTO: Codable {
    let fits: Bool
    let style_match: Bool
    let within_budget: Bool
    let fits_score: Double
    let style_score: Double
    let budget_score: Double
    let confidence: Double
    let accepted: Bool
    let reason: String
}

struct PositionDTO: Codable {
    let x: Double
    let y: Double
    let z: Double
}

struct PlacedProductDTO: Codable, Identifiable {
    var id: String { slot_id }
    let slot_id: String
    let category: String
    let product: ProductOfferDTO
    let decision: DecisionResultDTO
    let proxy_model: String
    let position: PositionDTO?
    let scale: Double?
}

struct PipelineStepDTO: Codable, Identifiable {
    var id: String { name }
    let name: String
    let status: String
    let detail: String?
    let model: String?
}

struct RedesignResponseDTO: Codable {
    let scene: SceneAnalysisDTO
    let plan: DesignPlanDTO
    let placements: [PlacedProductDTO]
    let total_usd: Double
    let remaining_budget_usd: Double
    let steps: [PipelineStepDTO]?
    let warnings: [String]?
}

/// Lightweight AnyCodable for RoomPlan JSON bags.
struct AnyCodable: Codable {
    let value: Any

    init(_ value: Any) { self.value = value }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let v = try? container.decode(Bool.self) { value = v; return }
        if let v = try? container.decode(Int.self) { value = v; return }
        if let v = try? container.decode(Double.self) { value = v; return }
        if let v = try? container.decode(String.self) { value = v; return }
        if let v = try? container.decode([AnyCodable].self) { value = v.map(\.value); return }
        if let v = try? container.decode([String: AnyCodable].self) {
            value = v.mapValues(\.value)
            return
        }
        value = NSNull()
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch value {
        case let v as Bool: try container.encode(v)
        case let v as Int: try container.encode(v)
        case let v as Double: try container.encode(v)
        case let v as String: try container.encode(v)
        case let v as [Any]:
            try container.encode(v.map { AnyCodable($0) })
        case let v as [String: Any]:
            try container.encode(v.mapValues { AnyCodable($0) })
        default:
            try container.encodeNil()
        }
    }
}
