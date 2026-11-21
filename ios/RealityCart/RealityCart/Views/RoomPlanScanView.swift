import RoomPlan
import SwiftUI
import UIKit

enum RoomPlanCoordinator {
    static var isSupported: Bool {
        if #available(iOS 16.0, *) {
            return RoomCaptureSession.isSupported
        }
        return false
    }
}

struct RoomPlanScanView: View {
    var onComplete: (UIImage?, Data) -> Void
    var onCancel: () -> Void

    var body: some View {
        if #available(iOS 16.0, *), RoomCaptureSession.isSupported {
            RoomPlanRepresentable(onComplete: onComplete, onCancel: onCancel)
                .ignoresSafeArea()
        } else {
            VStack(spacing: 16) {
                Text("RoomPlan requires a LiDAR device on iOS 16+.")
                Button("Close", action: onCancel)
            }
            .padding()
        }
    }
}

@available(iOS 16.0, *)
struct RoomPlanRepresentable: UIViewControllerRepresentable {
    var onComplete: (UIImage?, Data) -> Void
    var onCancel: () -> Void

    func makeUIViewController(context: Context) -> RoomPlanViewController {
        let vc = RoomPlanViewController()
        vc.onComplete = onComplete
        vc.onCancel = onCancel
        return vc
    }

    func updateUIViewController(_ uiViewController: RoomPlanViewController, context: Context) {}
}

@available(iOS 16.0, *)
final class RoomPlanViewController: UIViewController, RoomCaptureViewDelegate {
    var onComplete: ((UIImage?, Data) -> Void)?
    var onCancel: (() -> Void)?

    private var roomCaptureView: RoomCaptureView!
    private var captureSessionConfig = RoomCaptureSession.Configuration()
    private var finalRoom: CapturedRoom?
    private var isScanning = false

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black

        roomCaptureView = RoomCaptureView(frame: view.bounds)
        roomCaptureView.captureSession.delegate = self
        roomCaptureView.delegate = self
        roomCaptureView.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(roomCaptureView)

        let controls = UIStackView()
        controls.axis = .horizontal
        controls.spacing = 12
        controls.translatesAutoresizingMaskIntoConstraints = false

        let cancel = UIButton(type: .system)
        cancel.setTitle("Cancel", for: .normal)
        cancel.addTarget(self, action: #selector(cancelTapped), for: .touchUpInside)

        let done = UIButton(type: .system)
        done.setTitle("Finish Scan", for: .normal)
        done.addTarget(self, action: #selector(doneTapped), for: .touchUpInside)

        controls.addArrangedSubview(cancel)
        controls.addArrangedSubview(done)
        view.addSubview(controls)

        NSLayoutConstraint.activate([
            controls.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 16),
            controls.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -16),
            controls.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor, constant: -12),
        ])
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        startSession()
    }

    private func startSession() {
        isScanning = true
        roomCaptureView?.captureSession.run(configuration: captureSessionConfig)
    }

    @objc private func cancelTapped() {
        roomCaptureView?.captureSession.stop()
        onCancel?()
    }

    @objc private func doneTapped() {
        isScanning = false
        roomCaptureView?.captureSession.stop()
    }

    func captureView(shouldPresent roomDataForProcessing: CapturedRoomData, error: Error?) -> Bool {
        true
    }

    func captureView(didPresent processedResult: CapturedRoom, error: Error?) {
        finalRoom = processedResult
        exportAndFinish(processedResult)
    }

    private func exportAndFinish(_ room: CapturedRoom) {
        var objects: [[String: Any]] = []
        for obj in room.objects {
            let dims = obj.dimensions
            objects.append([
                "id": obj.identifier.uuidString,
                "category": String(describing: obj.category),
                "dimensions": [
                    "width": Double(dims.x),
                    "height": Double(dims.y),
                    "depth": Double(dims.z),
                ],
                "confidence": String(describing: obj.confidence),
            ])
        }

        var walls: [[String: Any]] = []
        for surface in room.walls {
            let dims = surface.dimensions
            walls.append([
                "id": surface.identifier.uuidString,
                "category": "wall",
                "dimensions": [
                    "width": Double(dims.x),
                    "height": Double(dims.y),
                    "depth": Double(dims.z),
                ],
            ])
        }

        let payload: [String: Any] = [
            "objects": objects,
            "walls": walls,
            "floors": [],
        ]

        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted]) else {
            onCancel?()
            return
        }

        // Snapshot from the capture view hierarchy as a stand-in keyframe.
        let renderer = UIGraphicsImageRenderer(bounds: roomCaptureView.bounds)
        let image = renderer.image { ctx in
            roomCaptureView.layer.render(in: ctx.cgContext)
        }
        onComplete?(image, data)
    }
}

@available(iOS 16.0, *)
extension RoomPlanViewController: RoomCaptureSessionDelegate {
    func captureSession(_ session: RoomCaptureSession, didUpdate room: CapturedRoom) {}
    func captureSession(_ session: RoomCaptureSession, didProvide instruction: RoomCaptureSession.Instruction) {}
    func captureSession(_ session: RoomCaptureSession, didStartWith configuration: RoomCaptureSession.Configuration) {}
    func captureSession(_ session: RoomCaptureSession, didEndWith data: CapturedRoomData, error: Error?) {}
}
