import SwiftUI
import UIKit

enum Keyboard {
    static func dismiss() {
        UIApplication.shared.sendAction(
            #selector(UIResponder.resignFirstResponder),
            to: nil,
            from: nil,
            for: nil
        )
    }
}

extension View {
    /// Done button above keyboard + swipe-to-dismiss on scroll views.
    func dismissKeyboardToolbar() -> some View {
        toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") { Keyboard.dismiss() }
            }
        }
        .scrollDismissesKeyboard(.interactively)
    }

    /// Apply on the root TabView so every tab gets Done + scroll-dismiss.
    func dismissKeyboardOnAllTabs() -> some View {
        dismissKeyboardToolbar()
    }
}
