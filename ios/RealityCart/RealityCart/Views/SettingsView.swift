import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var session: RedesignSession
    @FocusState private var urlFocused: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section("Backend") {
                    TextField("API base URL", text: $session.apiBaseURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        .textContentType(.URL)
                        .focused($urlFocused)
                        .submitLabel(.done)
                        .onSubmit { urlFocused = false }

                    Button("Check health") {
                        urlFocused = false
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
                        "Say: “Make this living room feel minimalist for $600. Keep my sofa, add a floor lamp and storage, mostly black.”"
                    )
                    .font(.footnote)
                }
            }
            .dismissKeyboardToolbar()
            .navigationTitle("Settings")
            .task {
                await session.checkHealth()
            }
        }
    }
}
