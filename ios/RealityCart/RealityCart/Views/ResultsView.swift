import SafariServices
import SwiftUI

struct ResultsView: View {
    @EnvironmentObject private var session: RedesignSession

    var body: some View {
        NavigationStack {
            Group {
                if session.isLoading {
                    VStack(spacing: 16) {
                        ProgressView()
                        Text(session.statusMessage)
                            .multilineTextAlignment(.center)
                            .padding()
                    }
                } else if let result = session.result {
                    List {
                        Section("Shopping plan") {
                            Text(result.plan.summary)
                                .font(.headline)
                            Text(result.plan.style)
                                .foregroundStyle(.secondary)
                            BudgetMeter(
                                spent: result.total_usd,
                                budget: result.plan.budget_usd
                            )
                        }

                        if let steps = result.steps, !steps.isEmpty {
                            Section("Pipeline") {
                                ForEach(steps) { step in
                                    VStack(alignment: .leading, spacing: 4) {
                                        HStack {
                                            Text(step.name)
                                            Spacer()
                                            Text(step.status.uppercased())
                                                .font(.caption.bold())
                                                .foregroundStyle(step.status == "ok" ? .green : .orange)
                                        }
                                        if let detail = step.detail {
                                            Text(detail).font(.caption).foregroundStyle(.secondary)
                                        }
                                        if let model = step.model {
                                            Text(model).font(.caption2).foregroundStyle(.tertiary)
                                        }
                                    }
                                }
                            }
                        }

                        Section("Scene (\(result.scene.model ?? "gemini"))") {
                            Text("Type: \(result.scene.scene_type)")
                            if let cues = result.scene.style_cues, !cues.isEmpty {
                                Text("Style cues: \(cues.joined(separator: ", "))")
                            }
                            ForEach(result.scene.objects ?? []) { obj in
                                HStack {
                                    Text(obj.label ?? obj.type)
                                    Spacer()
                                    if obj.keep == true {
                                        Text("KEEP")
                                            .font(.caption.bold())
                                            .foregroundStyle(.green)
                                    }
                                }
                            }
                        }

                        Section("Products") {
                            ForEach(result.placements) { placement in
                                ProductRow(placement: placement)
                            }
                        }

                        if let warnings = result.warnings, !warnings.isEmpty {
                            Section("Warnings") {
                                ForEach(warnings, id: \.self) { warning in
                                    Text(warning).foregroundStyle(.orange)
                                }
                            }
                        }
                    }
                } else {
                    ContentUnavailableView(
                        "No plan yet",
                        systemImage: "cart",
                        description: Text("Complete a RoomPlan LiDAR scan, then run redesign from Intent.")
                    )
                }
            }
            .navigationTitle("Plan")
            .toolbar {
                if session.result != nil {
                    Button("Re-run") {
                        Task { await session.runRedesign() }
                    }
                }
            }
        }
    }
}

struct BudgetMeter: View {
    let spent: Double
    let budget: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("$\(Int(spent)) spent")
                Spacer()
                Text("$\(Int(max(budget - spent, 0))) left")
            }
            .font(.subheadline)
            ProgressView(value: min(spent / max(budget, 1), 1.0))
                .tint(spent > budget ? .red : Color(red: 0.12, green: 0.45, blue: 0.42))
        }
    }
}

struct ProductRow: View {
    @EnvironmentObject private var session: RedesignSession
    let placement: PlacedProductDTO
    @State private var paying = false
    @State private var payError: String?
    @State private var checkout: CheckoutSessionDTO?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top) {
                AsyncImage(url: URL(string: placement.product.image_url ?? "")) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFill()
                    default:
                        Color(.tertiarySystemFill)
                    }
                }
                .frame(width: 64, height: 64)
                .clipShape(RoundedRectangle(cornerRadius: 8))

                VStack(alignment: .leading, spacing: 4) {
                    Text(placement.product.title)
                        .font(.subheadline.bold())
                    Text(placement.category)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if let price = placement.product.price_usd {
                        Text(String(format: "$%.2f", price))
                    }
                    Text("Decision: \(placement.decision.reason)")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }

            if placement.product.variant_id != nil, placement.product.seller_domain != nil {
                Button {
                    Task { await startCheckout() }
                } label: {
                    if paying {
                        ProgressView()
                    } else {
                        Label("Pay with Shop", systemImage: "creditcard")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(paying)
            }

            if let payError {
                Text(payError)
                    .font(.caption2)
                    .foregroundStyle(.red)
            }

            if let urlString = placement.product.product_url, let url = URL(string: urlString) {
                Link("View on merchant site", destination: url)
                    .font(.caption)
            }

            Text(overlayCaption)
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(.vertical, 4)
        .sheet(item: $checkout) { session in
            CheckoutPaySheet(checkout: session)
        }
    }

    private func startCheckout() async {
        guard let shop = placement.product.seller_domain,
              let variantID = placement.product.variant_id else { return }
        paying = true
        payError = nil
        defer { paying = false }
        do {
            checkout = try await session.startCheckout(shop: shop, variantID: variantID)
        } catch {
            payError = error.localizedDescription
        }
    }

    private var overlayCaption: String {
        if placement.product.usdzURL != nil {
            return "3D: merchant model"
        }
        if session.generatedModels[placement.product.id] != nil {
            return "3D: model generated from the product photo"
        }
        if session.modelStatus[placement.product.id] == "generating" {
            return "3D: generating a model from the product photo…"
        }
        if let box = placement.product.boxMeters {
            let hint = placement.product.dimensions_hint.map { " (\($0))" } ?? ""
            return String(
                format: "3D: %.0f × %.0f × %.0f cm box%@",
                box.width * 100,
                box.depth * 100,
                box.height * 100,
                hint
            )
        }
        return "3D: category stand-in (\(placement.proxy_model))"
    }
}

struct CheckoutPaySheet: View {
    let checkout: CheckoutSessionDTO
    @Environment(\.dismiss) private var dismiss
    @State private var payURL: URL?

    var body: some View {
        NavigationStack {
            List {
                Section("Amount") {
                    ForEach(checkout.totals) { line in
                        HStack {
                            Text(line.label)
                            Spacer()
                            Text(line.amount_display).fontWeight(.semibold)
                        }
                    }
                }
                if !checkout.messages.isEmpty {
                    Section("Before you pay") {
                        ForEach(Array(checkout.messages.enumerated()), id: \.offset) { _, message in
                            Text(message).font(.caption)
                        }
                    }
                }
                if !checkout.links.isEmpty {
                    Section("Merchant policies") {
                        ForEach(checkout.links) { link in
                            if let url = URL(string: link.url) {
                                Link(link.type, destination: url)
                            }
                        }
                    }
                }
                Section {
                    Button("Continue to pay") {
                        if let raw = checkout.continue_url, let url = URL(string: raw) {
                            payURL = url
                        }
                    }
                    .disabled(checkout.continue_url == nil)
                } footer: {
                    Text("This opens the merchant’s Shop checkout. The order is placed only after you pay there.")
                }
            }
            .navigationTitle("Pay with Shop")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
            .sheet(isPresented: Binding(
                get: { payURL != nil },
                set: { if !$0 { payURL = nil } }
            )) {
                if let payURL {
                    SafariView(url: payURL)
                        .ignoresSafeArea()
                }
            }
        }
    }
}

struct SafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url)
    }

    func updateUIViewController(_ uiViewController: SFSafariViewController, context: Context) {}
}
