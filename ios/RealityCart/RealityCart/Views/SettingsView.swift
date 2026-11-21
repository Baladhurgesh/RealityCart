import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var session: RedesignSession

    var body: some View {
        NavigationStack {
            Form {
                Section("Backend") {
                    TextField("API base URL", text: $session.apiBaseURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button("Check health") {
                        Task { await session.checkHealth() }
                    }
                    if let health = session.health {
                        LabeledContent("Status", value: health.status)
                        LabeledContent(
                            "Gemini",
                            value: health.gemini_configured ? "configured" : "missing key"
                        )
                        ForEach(health.models.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                            LabeledContent(key, value: value)
                        }
                    }
                    Text(session.statusMessage)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }

                Section("Model jobs") {
                    Text("Hydrogen — scene parse from photo + RoomPlan")
                    Text("Carbon — shopping plan + re-plan after failures")
                    Text("Decision 1 — fit / style / budget gates")
                    Text("Shopify Catalog MCP — real product discovery")
                }

                Section("Demo tip") {
                    Text(
                        "Say: “Make this a minimalist developer setup for $600. Keep my monitor, add a lamp and storage, all black.”"
                    )
                    .font(.footnote)
                }
            }
            .navigationTitle("Settings")
            .task {
                await session.checkHealth()
            }
        }
    }
}
