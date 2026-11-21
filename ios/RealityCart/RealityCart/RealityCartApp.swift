import SwiftUI

@main
struct RealityCartApp: App {
    @StateObject private var session = RedesignSession()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(session)
        }
    }
}
