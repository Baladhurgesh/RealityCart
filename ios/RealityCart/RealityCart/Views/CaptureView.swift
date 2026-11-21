import PhotosUI
import SwiftUI
import UIKit

struct CaptureView: View {
    @EnvironmentObject private var session: RedesignSession
    var onContinue: () -> Void

    @State private var pickerItem: PhotosPickerItem?
    @State private var showCamera = false
    @State private var showRoomPlan = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("RealityCart")
                        .font(.largeTitle.bold())
                    Text("Capture a desk or room. LiDAR RoomPlan preferred; photo + width works everywhere.")
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
                                Label("No capture yet", systemImage: "photo")
                                    .foregroundStyle(.secondary)
                            }
                    }

                    HStack(spacing: 12) {
                        Button {
                            showCamera = true
                        } label: {
                            Label("Camera", systemImage: "camera")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)

                        PhotosPicker(selection: $pickerItem, matching: .images) {
                            Label("Library", systemImage: "photo.on.rectangle")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                    }

                    Button {
                        showRoomPlan = true
                    } label: {
                        Label("RoomPlan LiDAR Scan", systemImage: "camera.metering.matrix")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .disabled(!RoomPlanCoordinator.isSupported)

                    if !RoomPlanCoordinator.isSupported {
                        Text("RoomPlan needs a LiDAR iPhone/iPad. Use camera + desk width instead.")
                            .font(.footnote)
                            .foregroundStyle(.orange)
                    }

                    if session.roomPlanJSON != nil {
                        Label("RoomPlan data attached", systemImage: "checkmark.seal.fill")
                            .foregroundStyle(.green)
                    }

                    VStack(alignment: .leading) {
                        Text("Desk width: \(String(format: "%.2f", session.deskWidthMeters)) m")
                        Slider(value: $session.deskWidthMeters, in: 0.6...2.4, step: 0.05)
                        Text("Desk depth: \(String(format: "%.2f", session.deskDepthMeters)) m")
                        Slider(value: $session.deskDepthMeters, in: 0.4...1.2, step: 0.05)
                    }

                    Button("Continue to intent") {
                        onContinue()
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(session.capturedImage == nil)
                }
                .padding()
            }
            .navigationTitle("Capture")
            .onChange(of: pickerItem) { _, item in
                Task {
                    guard let item,
                          let data = try? await item.loadTransferable(type: Data.self),
                          let image = UIImage(data: data)
                    else { return }
                    session.capturedImage = image
                }
            }
            .sheet(isPresented: $showCamera) {
                CameraPicker { image in
                    session.capturedImage = image
                }
            }
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

struct CameraPicker: UIViewControllerRepresentable {
    var onImage: (UIImage) -> Void
    @Environment(\.dismiss) private var dismiss

    func makeUIViewController(context: Context) -> UIImagePickerController {
        let picker = UIImagePickerController()
        picker.sourceType = .camera
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ uiViewController: UIImagePickerController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    final class Coordinator: NSObject, UINavigationControllerDelegate, UIImagePickerControllerDelegate {
        let parent: CameraPicker
        init(_ parent: CameraPicker) { self.parent = parent }

        func imagePickerController(
            _ picker: UIImagePickerController,
            didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]
        ) {
            if let image = info[.originalImage] as? UIImage {
                parent.onImage(image)
            }
            parent.dismiss()
        }

        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) {
            parent.dismiss()
        }
    }
}
