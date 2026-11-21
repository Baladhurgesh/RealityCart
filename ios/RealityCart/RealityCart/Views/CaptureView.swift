import SwiftUI
import UIKit

struct CaptureView: View {
    @EnvironmentObject private var session: RedesignSession
    var onContinue: () -> Void
    var onOpenSaved: () -> Void = {}

    @State private var showRoomPlan = false
    @State private var showScanViewer = false
    @State private var showHistory = false

    private var canContinue: Bool {
        session.capturedImage != nil && session.roomPlanJSON != nil
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("Scan your room with LiDAR, then set how you want it to feel.")
                        .foregroundStyle(.secondary)

                    ZStack(alignment: .bottomLeading) {
                        if let image = session.capturedImage {
                            Image(uiImage: image)
                                .resizable()
                                .scaledToFill()
                                .frame(maxWidth: .infinity)
                                .frame(height: 260)
                                .clipped()
                        } else {
                            Color(.secondarySystemBackground)
                                .frame(height: 260)
                                .overlay {
                                    VStack(spacing: 8) {
                                        Image(systemName: "camera.metering.matrix")
                                            .font(.largeTitle)
                                        Text("No scan yet")
                                            .font(.subheadline)
                                    }
                                    .foregroundStyle(.secondary)
                                }
                        }
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 16))

                    if canContinue {
                        Label("Scan ready", systemImage: "checkmark.circle.fill")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(.green)
                    }

                    Button {
                        showRoomPlan = true
                    } label: {
                        Label(
                            session.roomPlanJSON == nil ? "Scan room" : "Rescan",
                            systemImage: "camera.metering.matrix"
                        )
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(!RoomPlanCoordinator.isSupported)

                    if !RoomPlanCoordinator.isSupported {
                        Text("RoomPlan needs a LiDAR iPhone or iPad Pro.")
                            .font(.footnote)
                            .foregroundStyle(.orange)
                    }

                    if canContinue {
                        Button {
                            showScanViewer = true
                        } label: {
                            Label("Review 3D scan", systemImage: "cube.transparent")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.large)

                        Button {
                            onContinue()
                        } label: {
                            Text("Continue")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                    }
                }
                .padding()
            }
            .navigationTitle("Capture")
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Image("Logo")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 28, height: 28)
                        .clipShape(RoundedRectangle(cornerRadius: 6))
                        .accessibilityLabel("RealityCart")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showHistory = true
                    } label: {
                        Image(systemName: "clock")
                    }
                    .accessibilityLabel("Past scans")
                }
            }
            .sheet(isPresented: $showHistory) {
                DesignHistorySheet { onOpenSaved() }
            }
            .onAppear {
                session.reloadHistory()
            }
            .fullScreenCover(isPresented: $showRoomPlan) {
                RoomPlanScanView { result in
                    if let image = result.image {
                        session.capturedImage = image
                    }
                    session.roomPlanJSON = result.json
                    session.roomPlanUSDZURL = result.usdzURL
                    showRoomPlan = false
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
