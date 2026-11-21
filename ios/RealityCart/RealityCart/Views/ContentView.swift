import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var session: RedesignSession
    @State private var tab = 0

    var body: some View {
        TabView(selection: $tab) {
            CaptureView(onContinue: {
                Keyboard.dismiss()
                tab = 1
            })
            .tabItem { Label("Capture", systemImage: "camera.viewfinder") }
            .tag(0)

            IntentView(onRun: {
                Keyboard.dismiss()
                tab = 2
                Task { await session.runRedesign() }
            })
            .tabItem { Label("Intent", systemImage: "text.bubble") }
            .tag(1)

            ResultsView()
                .tabItem { Label("Plan", systemImage: "cart") }
                .tag(2)

            Preview3DView()
                .tabItem { Label("3D", systemImage: "cube.transparent") }
                .tag(3)

            SettingsView()
                .tabItem { Label("Settings", systemImage: "gear") }
                .tag(4)
        }
        .tint(Color(red: 0.12, green: 0.45, blue: 0.42))
        .dismissKeyboardOnAllTabs()
        .onChange(of: tab) { _, _ in
            Keyboard.dismiss()
        }
    }
}
