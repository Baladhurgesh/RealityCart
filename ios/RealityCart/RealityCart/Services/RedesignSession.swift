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

    init() {
        let defaults = UserDefaults.standard
        #if targetEnvironment(simulator)
        let fallbackURL = "http://127.0.0.1:8000"
        #else
        let fallbackURL = "http://10.0.210.115:8000"
        #endif
        self.apiBaseURL = defaults.string(forKey: Keys.apiBaseURL) ?? fallbackURL
        self.budgetUSD = defaults.object(forKey: Keys.budget) as? Double ?? 600
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
        } catch {
            errorMessage = error.localizedDescription
            statusMessage = "Redesign failed"
        }
    }
}
