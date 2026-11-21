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
                        description: Text("Capture a room and run redesign from Intent.")
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

            if let urlString = placement.product.product_url, let url = URL(string: urlString) {
                Link("Buy on Shopify merchant", destination: url)
                    .font(.caption)
            }

            Text("3D proxy: \(placement.proxy_model)")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .padding(.vertical, 4)
    }
}
