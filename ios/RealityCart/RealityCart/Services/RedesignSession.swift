import Foundation
import SwiftUI
import UIKit

@MainActor
final class RedesignSession: ObservableObject {
    @Published var capturedImage: UIImage?
    @Published var roomPlanJSON: Data?
    @Published var deskWidthMeters: Double = 1.2
    @Published var deskDepthMeters: Double = 0.6
    @Published var intentText: String =
        "Make this a minimalist developer setup for $600. Keep my existing monitor, add a lamp and storage, and make everything black."
    @Published var budgetUSD: Double = 600
    @Published var apiBaseURL: String = "http://127.0.0.1:8000"

    @Published var isLoading = false
    @Published var statusMessage: String = "Capture a desk or room to begin."
    @Published var result: RedesignResponseDTO?
    @Published var errorMessage: String?
    @Published var health: HealthResponse?

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
        isLoading = true
        statusMessage = "Hydrogen scene → Carbon plan → Shopify → Decisions…"
        defer { isLoading = false }

        let calibration = SurfaceCalibration(
            surface_id: "desk_surface",
            width_m: deskWidthMeters,
            depth_m: deskDepthMeters,
            height_m: 0.74
        )

        do {
            let response = try await client.redesign(
                image: image,
                intentText: intentText,
                budgetUSD: budgetUSD,
                roomPlanJSON: roomPlanJSON,
                calibration: calibration
            )
            result = response
            statusMessage = "Done — $\(String(format: "%.0f", response.total_usd)) of $\(String(format: "%.0f", response.plan.budget_usd))"
        } catch {
            errorMessage = error.localizedDescription
            statusMessage = "Redesign failed"
        }
    }
}
