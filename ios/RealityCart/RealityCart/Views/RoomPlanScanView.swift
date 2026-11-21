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

struct RoomPlanScanResult {
    var image: UIImage?
    var json: Data
    var usdzURL: URL?
}

struct RoomPlanScanView: View {
    var onComplete: (RoomPlanScanResult) -> Void
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
    var onComplete: (RoomPlanScanResult) -> Void
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
    var onComplete: ((RoomPlanScanResult) -> Void)?
    var onCancel: (() -> Void)?

    private var roomCaptureView: RoomCaptureView!
    private var captureSessionConfig = RoomCaptureSession.Configuration()
    private var finalRoom: CapturedRoom?
    private var isScanning = false
    private var reviewContainer: UIView?

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
        controls.tag = 100

        let cancel = UIButton(type: .system)
        cancel.setTitle("Cancel", for: .normal)
        cancel.tintColor = .white
        cancel.addTarget(self, action: #selector(cancelTapped), for: .touchUpInside)

        let done = UIButton(type: .system)
        done.setTitle("Finish Scan", for: .normal)
        done.tintColor = .white
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
        if reviewContainer == nil {
            startSession()
        }
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
        presentReview(for: processedResult)
    }

    private func presentReview(for room: CapturedRoom) {
        view.viewWithTag(100)?.isHidden = true

        let exported = exportRoom(room)
        let container = UIView(frame: view.bounds)
        container.backgroundColor = UIColor.systemBackground
        container.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        reviewContainer = container
        view.addSubview(container)

        let title = UILabel()
        title.text = "RoomPlan scan ready"
        title.font = .boldSystemFont(ofSize: 22)
        title.translatesAutoresizingMaskIntoConstraints = false

        let subtitle = UILabel()
        subtitle.text = "Inspect the scanned room, then use it for redesign."
        subtitle.font = .systemFont(ofSize: 14)
        subtitle.textColor = .secondaryLabel
        subtitle.numberOfLines = 0
        subtitle.translatesAutoresizingMaskIntoConstraints = false

        let host = UIHostingController(
            rootView: RoomPlanViewerView(
                usdzURL: exported.usdzURL,
                roomPlanJSON: exported.json,
                onDone: nil,
                showsChrome: false
            )
        )
        addChild(host)
        host.view.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(host.view)
        host.didMove(toParent: self)

        let useButton = UIButton(type: .system)
        useButton.setTitle("Use this scan", for: .normal)
        useButton.titleLabel?.font = .boldSystemFont(ofSize: 17)
        useButton.backgroundColor = UIColor(red: 0.12, green: 0.45, blue: 0.42, alpha: 1)
        useButton.setTitleColor(.white, for: .normal)
        useButton.layer.cornerRadius = 12
        useButton.translatesAutoresizingMaskIntoConstraints = false
        useButton.addAction(UIAction { [weak self] _ in
            self?.onComplete?(exported)
        }, for: .touchUpInside)

        let rescanButton = UIButton(type: .system)
        rescanButton.setTitle("Rescan", for: .normal)
        rescanButton.translatesAutoresizingMaskIntoConstraints = false
        rescanButton.addAction(UIAction { [weak self] _ in
            self?.reviewContainer?.removeFromSuperview()
            self?.reviewContainer = nil
            self?.view.viewWithTag(100)?.isHidden = false
            host.willMove(toParent: nil)
            host.view.removeFromSuperview()
            host.removeFromParent()
            self?.startSession()
        }, for: .touchUpInside)

        container.addSubview(title)
        container.addSubview(subtitle)
        container.addSubview(useButton)
        container.addSubview(rescanButton)

        NSLayoutConstraint.activate([
            title.topAnchor.constraint(equalTo: container.safeAreaLayoutGuide.topAnchor, constant: 12),
            title.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 16),
            title.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -16),

            subtitle.topAnchor.constraint(equalTo: title.bottomAnchor, constant: 4),
            subtitle.leadingAnchor.constraint(equalTo: title.leadingAnchor),
            subtitle.trailingAnchor.constraint(equalTo: title.trailingAnchor),

            host.view.topAnchor.constraint(equalTo: subtitle.bottomAnchor, constant: 12),
            host.view.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            host.view.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            host.view.bottomAnchor.constraint(equalTo: useButton.topAnchor, constant: -12),

            useButton.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 16),
            useButton.trailingAnchor.constraint(equalTo: container.trailingAnchor, constant: -16),
            useButton.heightAnchor.constraint(equalToConstant: 50),
            useButton.bottomAnchor.constraint(equalTo: rescanButton.topAnchor, constant: -8),

            rescanButton.centerXAnchor.constraint(equalTo: container.centerXAnchor),
            rescanButton.bottomAnchor.constraint(equalTo: container.safeAreaLayoutGuide.bottomAnchor, constant: -8),
        ])
    }

    private func exportRoom(_ room: CapturedRoom) -> RoomPlanScanResult {
        var objects: [[String: Any]] = []
        for obj in room.objects {
            let dims = obj.dimensions
            let t = obj.transform
            objects.append([
                "id": obj.identifier.uuidString,
                "category": String(describing: obj.category),
                "dimensions": [
                    "width": Double(dims.x),
                    "height": Double(dims.y),
                    "depth": Double(dims.z),
                ],
                "transform": [
                    "tx": Double(t.columns.3.x),
                    "ty": Double(t.columns.3.y),
                    "tz": Double(t.columns.3.z),
                ],
                "confidence": String(describing: obj.confidence),
            ])
        }

        var walls: [[String: Any]] = []
        for surface in room.walls {
            let dims = surface.dimensions
            let t = surface.transform
            walls.append([
                "id": surface.identifier.uuidString,
                "category": "wall",
                "dimensions": [
                    "width": Double(dims.x),
                    "height": Double(dims.y),
                    "depth": Double(dims.z),
                ],
                "transform": [
                    "tx": Double(t.columns.3.x),
                    "ty": Double(t.columns.3.y),
                    "tz": Double(t.columns.3.z),
                ],
            ])
        }

        let payload: [String: Any] = [
            "objects": objects,
            "walls": walls,
            "floors": [],
        ]

        let json = (try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted]))
            ?? Data("{}".utf8)

        var usdzURL: URL?
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("RealityCart-\(UUID().uuidString).usdz")
        do {
            try room.export(to: url)
            usdzURL = url
        } catch {
            print("RoomPlan USDZ export failed: \(error)")
        }

        let renderer = UIGraphicsImageRenderer(bounds: roomCaptureView.bounds)
        let image = renderer.image { ctx in
            roomCaptureView.layer.render(in: ctx.cgContext)
        }

        return RoomPlanScanResult(image: image, json: json, usdzURL: usdzURL)
    }
}

@available(iOS 16.0, *)
extension RoomPlanViewController: RoomCaptureSessionDelegate {
    func captureSession(_ session: RoomCaptureSession, didUpdate room: CapturedRoom) {}
    func captureSession(_ session: RoomCaptureSession, didProvide instruction: RoomCaptureSession.Instruction) {}
    func captureSession(_ session: RoomCaptureSession, didStartWith configuration: RoomCaptureSession.Configuration) {}
    func captureSession(_ session: RoomCaptureSession, didEndWith data: CapturedRoomData, error: Error?) {}
}
