import Foundation
import SwiftUI
import UIKit

@MainActor
final class RedesignSession: ObservableObject {
    private enum Keys {
        static let apiBaseURL = "realitycart.apiBaseURL"
        static let budget = "realitycart.budgetUSD"
    }

    @Published var capturedImage: UIImage?
    @Published var roomPlanJSON: Data?
    @Published var roomPlanUSDZURL: URL?
    @Published var intentText: String =
        "Make this space feel minimalist for $600. Keep the main furniture I already have, add lighting and storage, and keep the palette mostly black."
    @Published var budgetUSD: Double {
        didSet { UserDefaults.standard.set(budgetUSD, forKey: Keys.budget) }
    }
    @Published var apiBaseURL: String {
        didSet { UserDefaults.standard.set(apiBaseURL, forKey: Keys.apiBaseURL) }
    }

    @Published var isLoading = false
    @Published var statusMessage: String = "Scan any space with RoomPlan LiDAR to begin."
    @Published var result: RedesignResponseDTO?
    @Published var errorMessage: String?
    @Published var health: HealthResponse?
    /// Product id → local-server USDZ URL, filled when Meshy finishes.
    @Published var cart: [CartLine] = []
    @Published var designHistory: [DesignRecord] = []
    @Published var generatedModels: [String: String] = [:]
    /// Product id → "generating" | "ready" | "failed"
    @Published var modelStatus: [String: String] = [:]
    private var generationTask: Task<Void, Never>?

    init() {
        let defaults = UserDefaults.standard
        #if targetEnvironment(simulator)
        let fallbackURL = "http://127.0.0.1:8000"
        #else
        let fallbackURL = "http://10.0.210.115:8000"
        #endif
        self.apiBaseURL = defaults.string(forKey: Keys.apiBaseURL) ?? fallbackURL
        self.budgetUSD = defaults.object(forKey: Keys.budget) as? Double ?? 600
        self.designHistory = DesignHistory.loadIndex()
    }

    private var client: APIClient { APIClient(baseURL: URL(string: apiBaseURL)) }

    func checkHealth() async {
        do {
            health = try await client.health()
            statusMessage = "API ok — models \(health?.models.values.joined(separator: ", ") ?? "")"
        } catch {
            health = nil
            statusMessage = "API unreachable at \(apiBaseURL)"
        }
    }

    func runRedesign() async {
        errorMessage = nil
        guard let image = capturedImage else {
            errorMessage = APIError.emptyImage.localizedDescription
            return
        }
        guard let roomPlanJSON else {
            errorMessage = APIError.missingRoomPlan.localizedDescription
            return
        }

        isLoading = true
        statusMessage = "Hydrogen scene → Carbon plan → Shopify → Decisions…"
        generatedModels = [:]
        modelStatus = [:]
        cart = []
        generationTask?.cancel()
        defer { isLoading = false }

        do {
            let response = try await client.redesign(
                image: image,
                intentText: intentText,
                budgetUSD: budgetUSD,
                roomPlanJSON: roomPlanJSON,
                calibration: nil
            )
            result = response
            statusMessage = "Done — $\(String(format: "%.0f", response.total_usd)) of $\(String(format: "%.0f", response.plan.budget_usd))"
            if let saved = DesignHistory.save(
                image: image,
                intent: intentText,
                budgetUSD: budgetUSD,
                result: response
            ) {
                designHistory.insert(saved, at: 0)
            }
            generateMissingModels()
        } catch {
            errorMessage = error.localizedDescription
            statusMessage = "Redesign failed"
        }
    }

    func showSavedDesign(_ record: DesignRecord) {
        guard let saved = DesignHistory.loadResult(id: record.id) else { return }
        generationTask?.cancel()
        result = saved
        capturedImage = DesignHistory.loadImage(id: record.id)
        cart = []
        generatedModels = [:]
        modelStatus = [:]
        statusMessage = record.summary
        generateMissingModels()
    }

    func deleteSavedDesign(_ record: DesignRecord) {
        DesignHistory.delete(id: record.id)
        designHistory.removeAll { $0.id == record.id }
    }

    func addToCart(_ placement: PlacedProductDTO) {
        guard let variantID = placement.product.variant_id,
              let shop = placement.product.seller_domain else { return }
        if let index = cart.firstIndex(where: { $0.variantID == variantID && $0.shop == shop }) {
            cart[index].quantity += 1
        } else {
            cart.append(
                CartLine(
                    variantID: variantID,
                    shop: shop,
                    title: placement.product.title,
                    priceUSD: placement.product.price_usd,
                    quantity: 1
                )
            )
        }
    }

    func updateCartQuantity(variantID: String, shop: String, delta: Int) {
        guard let index = cart.firstIndex(where: { $0.variantID == variantID && $0.shop == shop }) else { return }
        cart[index].quantity += delta
        if cart[index].quantity <= 0 {
            cart.remove(at: index)
        }
    }

    var cartItemCount: Int { cart.reduce(0) { $0 + $1.quantity } }

    var cartEstimateUSD: Double {
        cart.reduce(0) { $0 + ($1.priceUSD ?? 0) * Double($1.quantity) }
    }

    func startCartCheckout() async throws -> [CheckoutSessionDTO] {
        try await client.startCartCheckout(items: cart)
    }

    func startCheckout(shop: String, variantID: String) async throws -> CheckoutSessionDTO {
        try await client.startCheckout(shop: shop, variantID: variantID)
    }

    /// Ask the backend for a Meshy USDZ for every placement that has photos but no merchant model.
    func generateMissingModels() {
        guard let placements = result?.placements else { return }
        generationTask?.cancel()
        let snapshots = placements.map { placement in
            (
                placement.product.id,
                placement.product.usdzURL != nil,
                (placement.product.image_urls ?? []) + (placement.product.image_url.map { [$0] } ?? [])
            )
        }
        generationTask = Task {
            await withTaskGroup(of: Void.self) { group in
                for (productID, hasMerchantModel, images) in snapshots {
                    if hasMerchantModel || images.isEmpty { continue }
                    if generatedModels[productID] != nil { continue }
                    group.addTask { await self.generateOne(productID: productID, imageURLs: images) }
                }
            }
        }
    }

    private func generateOne(productID: String, imageURLs: [String]) async {
        let key = ModelKey.make(productID)
        modelStatus[productID] = "generating"
        do {
            _ = try await client.startGeneratedModel(productID: productID, imageURLs: imageURLs)
            while !Task.isCancelled {
                let job = try await client.generatedModelStatus(key: key)
                if job.status == "ready" {
                    generatedModels[productID] = client.generatedModelFileURL(key: key).absoluteString
                    modelStatus[productID] = "ready"
                    return
                }
                if job.status == "failed" {
                    modelStatus[productID] = "failed"
                    return
                }
                try await Task.sleep(nanoseconds: 4_000_000_000)
            }
        } catch {
            if !Task.isCancelled {
                modelStatus[productID] = "failed"
            }
        }
    }
}

struct DesignRecord: Codable, Identifiable, Equatable {
    let id: String
    let createdAt: Date
    let intent: String
    let budgetUSD: Double
    let summary: String
    let sceneType: String
    let productTitles: [String]
    let totalUSD: Double
}

enum DesignHistory {
    private static var root: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("DesignHistory", isDirectory: true)
    }

    private static var indexURL: URL { root.appendingPathComponent("index.json") }

    static func loadIndex() -> [DesignRecord] {
        guard let data = try? Data(contentsOf: indexURL),
              let records = try? JSONDecoder().decode([DesignRecord].self, from: data) else {
            return []
        }
        return records.sorted { $0.createdAt > $1.createdAt }
    }

    static func save(
        image: UIImage,
        intent: String,
        budgetUSD: Double,
        result: RedesignResponseDTO
    ) -> DesignRecord? {
        let id = UUID().uuidString
        let folder = root.appendingPathComponent(id, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            if let jpeg = image.jpegData(compressionQuality: 0.7) {
                try jpeg.write(to: folder.appendingPathComponent("capture.jpg"))
            }
            let encoded = try JSONEncoder().encode(result)
            try encoded.write(to: folder.appendingPathComponent("result.json"))
            let record = DesignRecord(
                id: id,
                createdAt: Date(),
                intent: intent,
                budgetUSD: budgetUSD,
                summary: result.plan.summary,
                sceneType: result.scene.scene_type,
                productTitles: result.placements.map(\.product.title),
                totalUSD: result.total_usd
            )
            var records = loadIndex()
            records.insert(record, at: 0)
            if records.count > 40 {
                records.suffix(from: 40).forEach { delete(id: $0.id) }
                records = Array(records.prefix(40))
            }
            try JSONEncoder().encode(records).write(to: indexURL)
            return record
        } catch {
            return nil
        }
    }

    static func loadResult(id: String) -> RedesignResponseDTO? {
        let url = root.appendingPathComponent(id).appendingPathComponent("result.json")
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(RedesignResponseDTO.self, from: data)
    }

    static func loadImage(id: String) -> UIImage? {
        let url = root.appendingPathComponent(id).appendingPathComponent("capture.jpg")
        guard let data = try? Data(contentsOf: url) else { return nil }
        return UIImage(data: data)
    }

    static func delete(id: String) {
        let folder = root.appendingPathComponent(id, isDirectory: true)
        try? FileManager.default.removeItem(at: folder)
        var records = loadIndex().filter { $0.id != id }
        try? JSONEncoder().encode(records).write(to: indexURL)
    }
}
