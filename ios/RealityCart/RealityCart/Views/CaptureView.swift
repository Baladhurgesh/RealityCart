import SwiftUI
import UIKit

struct CaptureView: View {
    @EnvironmentObject private var session: RedesignSession
    var onContinue: () -> Void

    @State private var showRoomPlan = false

    private var canContinue: Bool {
        session.capturedImage != nil && session.roomPlanJSON != nil
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("RealityCart")
                        .font(.largeTitle.bold())
                    Text("Scan any room or indoor space with RoomPlan LiDAR. Dimensions come from the Pro scan.")
                        .foregroundStyle(.secondary)

                    if let image = session.capturedImage {
                        Image(uiImage: image)
                            .resizable()
                            .scaledToFit()
                            .frame(maxHeight: 280)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                    } else {
                        RoundedRectangle(cornerRadius: 12)
                            .fill(Color(.secondarySystemBackground))
                            .frame(height: 200)
                            .overlay {
                                Label("No RoomPlan scan yet", systemImage: "camera.metering.matrix")
                                    .foregroundStyle(.secondary)
                            }
                    }

                    Button {
                        showRoomPlan = true
                    } label: {
                        Label(
                            session.roomPlanJSON == nil ? "Start RoomPlan LiDAR Scan" : "Rescan with RoomPlan",
                            systemImage: "camera.metering.matrix"
                        )
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!RoomPlanCoordinator.isSupported)

                    if !RoomPlanCoordinator.isSupported {
                        Text("This device doesn’t support RoomPlan. RealityCart requires a LiDAR iPhone/iPad Pro.")
                            .font(.footnote)
                            .foregroundStyle(.orange)
                    }

                    if session.roomPlanJSON != nil {
                        Label("RoomPlan LiDAR data attached", systemImage: "checkmark.seal.fill")
                            .foregroundStyle(.green)
                    }

                    Button("Continue to intent") {
                        onContinue()
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!canContinue)
                }
                .padding()
            }
            .navigationTitle("Capture")
            .fullScreenCover(isPresented: $showRoomPlan) {
                RoomPlanScanView { image, json in
                    if let image { session.capturedImage = image }
                    session.roomPlanJSON = json
                    showRoomPlan = false
                } onCancel: {
                    showRoomPlan = false
                }
            }
        }
    }
}
