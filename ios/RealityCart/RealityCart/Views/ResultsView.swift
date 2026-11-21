import SafariServices
import SwiftUI

struct ResultsView: View {
    @EnvironmentObject private var session: RedesignSession
    @State private var showHistory = false
    @State private var showCart = false
    @State private var showDetails = false

    var body: some View {
        NavigationStack {
            Group {
                if session.isLoading {
                    VStack(spacing: 16) {
                        ProgressView()
                        Text(session.statusMessage)
                            .multilineTextAlignment(.center)
                            .foregroundStyle(.secondary)
                            .padding(.horizontal)
                    }
                } else if let result = session.result {
                    List {
                        Section {
                            Text(result.plan.summary)
                                .font(.headline)
                            Text(result.plan.style.capitalized)
                                .font(.subheadline)
                                .foregroundStyle(.secondary)
                            BudgetMeter(
                                spent: result.total_usd,
                                budget: result.plan.budget_usd
                            )
                        }

                        Section {
                            ForEach(result.placements) { placement in
                                ProductRow(placement: placement)
                            }
                        } header: {
                            Text("Suggested products")
                        } footer: {
                            Text("Add what you like, then open the cart to pay.")
                        }

                        if let warnings = result.warnings, !warnings.isEmpty {
                            Section("Notes") {
                                ForEach(warnings, id: \.self) { warning in
                                    Text(warning)
                                        .font(.footnote)
                                        .foregroundStyle(.orange)
                                }
                            }
                        }

                        Section {
                            DisclosureGroup("Room & pipeline details", isExpanded: $showDetails) {
                                VStack(alignment: .leading, spacing: 10) {
                                    Text(result.scene.scene_type.replacingOccurrences(of: "_", with: " ").capitalized)
                                        .font(.subheadline)
                                    if let cues = result.scene.style_cues, !cues.isEmpty {
                                        Text(cues.joined(separator: " · "))
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                    ForEach(result.scene.objects ?? []) { obj in
                                        HStack {
                                            Text(obj.label ?? obj.type)
                                            Spacer()
                                            if obj.keep == true {
                                                Text("Keep")
                                                    .font(.caption.bold())
                                                    .foregroundStyle(.green)
                                            }
                                        }
                                        .font(.caption)
                                    }
                                    if let steps = result.steps {
                                        Divider()
                                        ForEach(steps) { step in
                                            HStack {
                                                Text(step.name)
                                                Spacer()
                                                Text(step.status)
                                                    .foregroundStyle(step.status == "ok" ? .green : .orange)
                                            }
                                            .font(.caption2)
                                        }
                                    }
                                }
                                .padding(.top, 4)
                            }
                        }
                    }
                    .listStyle(.insetGrouped)
                } else {
                    ContentUnavailableView(
                        "No plan yet",
                        systemImage: "sparkles",
                        description: Text("Scan a room, set your intent, then run a redesign.")
                    )
                }
            }
            .navigationTitle("Plan")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        showHistory = true
                    } label: {
                        Image(systemName: "clock")
                    }
                    .accessibilityLabel("History")
                }
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button {
                        showCart = true
                    } label: {
                        CartIcon(count: session.cartItemCount)
                    }
                    .accessibilityLabel("Cart, \(session.cartItemCount) items")

                    if session.result != nil {
                        Button("Re-run") {
                            Task { await session.runRedesign() }
                        }
                    }
                }
            }
            .sheet(isPresented: $showHistory) {
                DesignHistorySheet()
            }
            .onAppear {
                session.reloadHistory()
            }
            .sheet(isPresented: $showCart) {
                CartSheet()
            }
        }
    }
}

struct CartSheet: View {
    @EnvironmentObject private var session: RedesignSession
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                if session.cart.isEmpty {
                    ContentUnavailableView(
                        "Cart is empty",
                        systemImage: "cart",
                        description: Text("Add products from your plan, then pay here.")
                    )
                } else {
                    List {
                        ForEach(session.cart) { line in
                            HStack(alignment: .top) {
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(line.title)
                                        .font(.subheadline.bold())
                                    if let price = line.priceUSD {
                                        Text(String(format: "$%.2f each", price))
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                    }
                                }
                                Spacer()
                                Stepper(
                                    value: Binding(
                                        get: { line.quantity },
                                        set: { newValue in
                                            let delta = newValue - line.quantity
                                            session.updateCartQuantity(
                                                variantID: line.variantID,
                                                shop: line.shop,
                                                delta: delta
                                            )
                                        }
                                    ),
                                    in: 0...20
                                ) {
                                    Text("\(line.quantity)")
                                        .monospacedDigit()
                                        .frame(minWidth: 20)
                                }
                            }
                        }
                        .onDelete { offsets in
                            for index in offsets {
                                let line = session.cart[index]
                                session.updateCartQuantity(
                                    variantID: line.variantID,
                                    shop: line.shop,
                                    delta: -line.quantity
                                )
                            }
                        }

                        Section {
                            HStack {
                                Text("Estimated total")
                                Spacer()
                                Text(String(format: "$%.2f", session.cartEstimateUSD))
                                    .fontWeight(.semibold)
                            }
                            CartPayButton()
                        } footer: {
                            Text("Items from different stores open separate Shop payments.")
                        }
                    }
                }
            }
            .navigationTitle("Cart")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}

struct DesignHistorySheet: View {
    @EnvironmentObject private var session: RedesignSession
    @Environment(\.dismiss) private var dismiss
    var onOpen: () -> Void = {}

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
                                onOpen()
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
                                            .multilineTextAlignment(.leading)
                                        Text(record.sceneType.replacingOccurrences(of: "_", with: " "))
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
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
            .onAppear {
                session.reloadHistory()
            }
        }
    }
}

struct CartIcon: View {
    let count: Int

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Image(systemName: count > 0 ? "cart.fill" : "cart")
                .font(.body)
                .frame(width: 22, height: 22)
                .padding(.top, 6)
                .padding(.trailing, 8)
            if count > 0 {
                Text("\(min(count, 99))")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 4)
                    .frame(minWidth: 16, minHeight: 16)
                    .background(Capsule().fill(Color.red))
            }
        }
        .frame(width: 36, height: 30)
    }
}

struct BudgetMeter: View {
    let spent: Double
    let budget: Double

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("$\(Int(spent)) selected")
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

    private var inCart: Bool {
        session.cart.contains {
            $0.variantID == placement.product.variant_id && $0.shop == placement.product.seller_domain
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 12) {
                AsyncImage(url: URL(string: placement.product.image_url ?? "")) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFill()
                    default:
                        Color(.tertiarySystemFill)
                    }
                }
                .frame(width: 72, height: 72)
                .clipShape(RoundedRectangle(cornerRadius: 10))

                VStack(alignment: .leading, spacing: 4) {
                    Text(placement.product.title)
                        .font(.subheadline.bold())
                        .lineLimit(2)
                    if let price = placement.product.price_usd {
                        Text(String(format: "$%.2f", price))
                            .font(.subheadline)
                    }
                    Text(placement.decision.reason)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }

            HStack(spacing: 12) {
                if let status = meshStatus {
                    Text(status.label)
                        .font(.caption2.bold())
                        .foregroundStyle(status.color)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Capsule().fill(status.color.opacity(0.15)))
                }

                Spacer(minLength: 8)

                if let urlString = placement.product.product_url, let url = URL(string: urlString) {
                    Link("Details", destination: url)
                        .font(.caption)
                }

                if placement.product.variant_id != nil, placement.product.seller_domain != nil {
                    Button {
                        session.addToCart(placement)
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: inCart ? "checkmark" : "cart.badge.plus")
                            Text(inCart ? "Added" : "Add to cart")
                        }
                        .frame(maxWidth: .infinity, alignment: .center)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .fixedSize(horizontal: true, vertical: false)
                }
            }
        }
        .padding(.vertical, 4)
    }

    private var meshStatus: (label: String, color: Color)? {
        let productID = placement.product.id
        if placement.product.usdzURL != nil || session.generatedModels[productID] != nil {
            return ("3D ready", .green)
        }
        if session.modelStatus[productID] == "generating" {
            return ("Building 3D", .secondary)
        }
        return nil
    }
}

struct CartPayButton: View {
    @EnvironmentObject private var session: RedesignSession
    @State private var paying = false
    @State private var errorMessage: String?
    @State private var payURL: URL?
    @State private var showSafari = false
    @State private var queuedURLs: [URL] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                Task { await pay() }
            } label: {
                HStack(spacing: 8) {
                    Spacer(minLength: 0)
                    if paying {
                        ProgressView()
                    } else {
                        Image(systemName: "creditcard")
                        Text("Pay cart")
                    }
                    Spacer(minLength: 0)
                }
            }
            .buttonStyle(.borderedProminent)
            .disabled(paying || session.cart.isEmpty || showSafari)

            if let errorMessage {
                Text(errorMessage)
                    .font(.caption2)
                    .foregroundStyle(.red)
            }
        }
        .sheet(isPresented: $showSafari, onDismiss: openNextPayment) {
            if let payURL {
                SafariView(url: payURL)
                    .ignoresSafeArea()
            }
        }
    }

    private func pay() async {
        paying = true
        errorMessage = nil
        defer { paying = false }
        do {
            let checkouts = try await session.startCartCheckout()
            let urls = checkouts.compactMap(\.continue_url).compactMap(URL.init(string:))
            guard let first = urls.first else {
                errorMessage = "Shop did not return a payment page."
                return
            }
            queuedURLs = Array(urls.dropFirst())
            payURL = first
            showSafari = true
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func openNextPayment() {
        guard !queuedURLs.isEmpty else {
            payURL = nil
            return
        }
        payURL = queuedURLs.removeFirst()
        showSafari = true
    }
}

struct SafariView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> SFSafariViewController {
        SFSafariViewController(url: url)
    }

    func updateUIViewController(_ uiViewController: SFSafariViewController, context: Context) {}
}
