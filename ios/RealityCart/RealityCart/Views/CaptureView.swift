import SwiftUI
import UIKit

struct CaptureView: View {
    @EnvironmentObject private var session: RedesignSession
    var onContinue: () -> Void

    @State private var showRoomPlan = false
    @State private var showScanViewer = false

    private var canContinue: Bool {
        session.capturedImage != nil && session.roomPlanJSON != nil
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    HStack(spacing: 12) {
                        Image("Logo")
                            .resizable()
                            .scaledToFit()
                            .frame(width: 56, height: 56)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                        Text("RealityCart")
                            .font(.largeTitle.bold())
                    }
                    Text("Scan any room or indoor space with RoomPlan LiDAR. Review the 3D scan, then continue.")
                        .foregroundStyle(.secondary)

                    if let image = session.capturedImage {
                        Image(uiImage: image)
                            .resizable()
                            .scaledToFit()
                            .frame(maxHeight: 240)
                            .clipShape(RoundedRectangle(cornerRadius: 12))
                    } else {
                        RoundedRectangle(cornerRadius: 12)
                            .fill(Color(.secondarySystemBackground))
                            .frame(height: 180)
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

                        Button {
                            showScanViewer = true
                        } label: {
                            Label("View RoomPlan 3D scan", systemImage: "cube.transparent")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
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
                RoomPlanScanView { result in
                    if let image = result.image {
                        session.capturedImage = image
                    }
                    session.roomPlanJSON = result.json
                    session.roomPlanUSDZURL = result.usdzURL
                    showRoomPlan = false
                    // Open viewer right after accepting the scan.
                    showScanViewer = true
                } onCancel: {
                    showRoomPlan = false
                }
            }
            .sheet(isPresented: $showScanViewer) {
                RoomPlanViewerView(
                    usdzURL: session.roomPlanUSDZURL,
                    roomPlanJSON: session.roomPlanJSON
                ) {
                    showScanViewer = false
                }
                .presentationDetents([.large])
            }
        }
    }
}
