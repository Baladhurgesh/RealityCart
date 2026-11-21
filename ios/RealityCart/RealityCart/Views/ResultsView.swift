import SafariServices
import SwiftUI

struct ResultsView: View {
    @EnvironmentObject private var session: RedesignSession
    @State private var showHistory = false

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

                        if !session.cart.isEmpty {
                            Section("Cart") {
                                ForEach(session.cart) { line in
                                    HStack {
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(line.title).font(.subheadline)
                                            Text(line.shop).font(.caption2).foregroundStyle(.secondary)
                                        }
                                        Spacer()
                                        Stepper(
                                            "\(line.quantity)",
                                            onIncrement: {
                                                session.updateCartQuantity(
                                                    variantID: line.variantID, shop: line.shop, delta: 1
                                                )
                                            },
                                            onDecrement: {
                                                session.updateCartQuantity(
                                                    variantID: line.variantID, shop: line.shop, delta: -1
                                                )
                                            }
                                        )
                                        .labelsHidden()
                                        Text("\(line.quantity)")
                                            .frame(width: 20)
                                    }
                                }
                                HStack {
                                    Text("Items")
                                    Spacer()
                                    Text("\(session.cartItemCount)")
                                }
                                HStack {
                                    Text("Estimated total")
                                    Spacer()
                                    Text(String(format: "$%.2f", session.cartEstimateUSD))
                                        .fontWeight(.semibold)
                                }
                                CartPayButton()
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
                Button("History") { showHistory = true }
                if session.result != nil {
                    Button("Re-run") {
                        Task { await session.runRedesign() }
                    }
                }
            }
            .sheet(isPresented: $showHistory) {
                DesignHistorySheet()
            }
        }
    }
}

struct DesignHistorySheet: View {
    @EnvironmentObject private var session: RedesignSession
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if session.designHistory.isEmpty {
                    ContentUnavailableView(
                        "No designs yet",
                        systemImage: "clock",
                        description: Text("Each scan and the products RealityCart suggests are saved here.")
                    )
                } else {
                    List {
                        ForEach(session.designHistory) { record in
                            Button {
                                session.showSavedDesign(record)
                                dismiss()
                            } label: {
                                HStack(alignment: .top, spacing: 12) {
                                    if let image = DesignHistory.loadImage(id: record.id) {
                                        Image(uiImage: image)
                                            .resizable()
                                            .scaledToFill()
                                            .frame(width: 64, height: 64)
                                            .clipShape(RoundedRectangle(cornerRadius: 8))
                                    } else {
                                        RoundedRectangle(cornerRadius: 8)
                                            .fill(Color(.tertiarySystemFill))
                                            .frame(width: 64, height: 64)
                                    }
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(record.createdAt.formatted(date: .abbreviated, time: .shortened))
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                        Text(record.summary)
                                            .font(.subheadline.bold())
                                            .foregroundStyle(.primary)
                                        Text(record.sceneType.replacingOccurrences(of: "_", with: " "))
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                        if !record.productTitles.isEmpty {
                                            Text(record.productTitles.joined(separator: " · "))
                                                .font(.caption2)
                                                .foregroundStyle(.secondary)
                                                .lineLimit(2)
                                        }
                                        Text(String(format: "$%.0f of $%.0f", record.totalUSD, record.budgetUSD))
                                            .font(.caption2)
                                            .foregroundStyle(.tertiary)
                                    }
                                }
                            }
                        }
                        .onDelete { offsets in
                            offsets.map { session.designHistory[$0] }.forEach(session.deleteSavedDesign)
                        }
                    }
                }
            }
            .navigationTitle("Scans & designs")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
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
                let inCart = session.cart.contains {
                    $0.variantID == placement.product.variant_id && $0.shop == placement.product.seller_domain
                }
                Button {
                    session.addToCart(placement)
                } label: {
                    Label(inCart ? "Add another" : "Add to cart", systemImage: "cart.badge.plus")
                }
                .buttonStyle(.bordered)
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

struct CartPayButton: View {
    @EnvironmentObject private var session: RedesignSession
    @State private var paying = false
    @State private var errorMessage: String?
    @State private var checkouts: [CheckoutSessionDTO] = []
    @State private var showPay = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                Task { await pay() }
            } label: {
                if paying {
                    ProgressView()
                } else {
                    Label("Pay cart", systemImage: "creditcard")
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(paying || session.cart.isEmpty)

            if let errorMessage {
                Text(errorMessage)
                    .font(.caption2)
                    .foregroundStyle(.red)
            }
        }
        .sheet(isPresented: $showPay) {
            CheckoutPaySheet(checkouts: checkouts)
        }
    }

    private func pay() async {
        paying = true
        errorMessage = nil
        defer { paying = false }
        do {
            checkouts = try await session.startCartCheckout()
            showPay = !checkouts.isEmpty
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

struct CheckoutPaySheet: View {
    let checkouts: [CheckoutSessionDTO]
    @Environment(\.dismiss) private var dismiss
    @State private var payURL: URL?

    var body: some View {
        NavigationStack {
            List {
                ForEach(checkouts) { checkout in
                    Section(checkout.shop) {
                        ForEach(checkout.totals) { line in
                            HStack {
                                Text(line.label)
                                Spacer()
                                Text(line.amount_display).fontWeight(.semibold)
                            }
                        }
                        ForEach(Array(checkout.messages.enumerated()), id: \.offset) { _, message in
                            Text(message).font(.caption).foregroundStyle(.secondary)
                        }
                        ForEach(checkout.links) { link in
                            if let url = URL(string: link.url) {
                                Link(link.type, destination: url)
                            }
                        }
                        if checkouts.count > 1 {
                            Button("Pay this store") {
                                if let raw = checkout.continue_url, let url = URL(string: raw) {
                                    payURL = url
                                }
                            }
                            .disabled(checkout.continue_url == nil)
                        }
                    }
                }
            }
            .navigationTitle(checkouts.count > 1 ? "Pay cart" : "Pay with Shop")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
            .safeAreaInset(edge: .bottom) {
                if checkouts.count == 1, let only = checkouts.first {
                    Button("Continue to pay") {
                        if let raw = only.continue_url, let url = URL(string: raw) {
                            payURL = url
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(only.continue_url == nil)
                    .padding()
                    .frame(maxWidth: .infinity)
                    .background(.bar)
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
