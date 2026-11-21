import SwiftUI
import UIKit

extension View {
    /// Dismiss keyboard via toolbar Done + interactive scroll.
    func dismissKeyboardToolbar() -> some View {
        toolbar {
            ToolbarItemGroup(placement: .keyboard) {
                Spacer()
                Button("Done") {
                    UIApplication.shared.sendAction(
                        #selector(UIResponder.resignFirstResponder),
                        to: nil,
                        from: nil,
                        for: nil
                    )
                }
            }
        }
        .scrollDismissesKeyboard(.interactively)
    }
}
