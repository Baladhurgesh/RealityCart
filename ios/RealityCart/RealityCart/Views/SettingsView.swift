import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var session: RedesignSession
    @FocusState private var urlFocused: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("API base URL", text: $session.apiBaseURL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                        .textContentType(.URL)
                        .focused($urlFocused)
                        .submitLabel(.done)
                        .onSubmit { urlFocused = false }

                    Button("Check connection") {
                        urlFocused = false
                        Task { await session.checkHealth() }
                    }

                    if let health = session.health {
                        LabeledContent("API", value: health.status)
                        LabeledContent(
                            "Gemini",
                            value: health.gemini_configured ? "Ready" : "Missing key"
                        )
                    } else {
                        Text(session.statusMessage)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                } header: {
                    Text("Backend")
                } footer: {
                    Text("Use your Mac’s LAN IP when testing on a phone.")
                }

                Section("Tips") {
                    Text("Scan → Intent → Plan → 3D. Add products to the cart, then pay from the cart sheet.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
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
