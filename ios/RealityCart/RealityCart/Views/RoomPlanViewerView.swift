import QuickLook
import SceneKit
import SwiftUI
import UIKit

/// Interactive viewer for a completed RoomPlan scan (USDZ when available, JSON fallback).
struct RoomPlanViewerView: View {
    let usdzURL: URL?
    let roomPlanJSON: Data?
    var onDone: (() -> Void)?
    var showsChrome: Bool = true

    var body: some View {
        Group {
            if showsChrome {
                NavigationStack { viewerContent }
            } else {
                viewerContent
            }
        }
    }

    @ViewBuilder
    private var viewerContent: some View {
        Group {
            if let usdzURL, FileManager.default.fileExists(atPath: usdzURL.path) {
                USDZQuickLookView(url: usdzURL)
            } else if let roomPlanJSON {
                RoomPlanJSONSceneView(json: roomPlanJSON)
            } else {
                ContentUnavailableView(
                    "No scan to show",
                    systemImage: "cube.transparent",
                    description: Text("Complete a RoomPlan scan first.")
                )
            }
        }
        .navigationTitle("RoomPlan Scan")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if let onDone {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done", action: onDone)
                }
            }
        }
        .safeAreaInset(edge: .bottom) {
            Text("Drag to orbit · Pinch to zoom · Two-finger pan")
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(.ultraThinMaterial)
        }
    }
}

struct USDZQuickLookView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> QLPreviewController {
        let controller = QLPreviewController()
        controller.dataSource = context.coordinator
        context.coordinator.url = url
        return controller
    }

    func updateUIViewController(_ uiViewController: QLPreviewController, context: Context) {
        context.coordinator.url = url
        uiViewController.reloadData()
    }

    func makeCoordinator() -> Coordinator { Coordinator(url: url) }

    final class Coordinator: NSObject, QLPreviewControllerDataSource {
        var url: URL
        init(url: URL) { self.url = url }

        func numberOfPreviewItems(in controller: QLPreviewController) -> Int { 1 }

        func previewController(
            _ controller: QLPreviewController,
            previewItemAt index: Int
        ) -> QLPreviewItem {
            url as QLPreviewItem
        }
    }
}

/// Fallback 3D dollhouse from RoomPlan JSON walls/objects.
struct RoomPlanJSONSceneView: UIViewRepresentable {
    let json: Data

    func makeUIView(context: Context) -> SCNView {
        let view = SCNView(frame: .zero)
        view.backgroundColor = UIColor.systemBackground
        view.allowsCameraControl = true
        view.autoenablesDefaultLighting = true
        view.antialiasingMode = .multisampling4X
        view.scene = Self.buildScene(from: json)
        return view
    }

    func updateUIView(_ uiView: SCNView, context: Context) {
        uiView.scene = Self.buildScene(from: json)
    }

    private static func buildScene(from data: Data) -> SCNScene {
        let scene = SCNScene()
        guard
            let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return scene }

        let objects = root["objects"] as? [[String: Any]] ?? []
        let walls = root["walls"] as? [[String: Any]] ?? []

        var index = 0
        for wall in walls {
            let dims = wall["dimensions"] as? [String: Any] ?? [:]
            let transform = wall["transform"] as? [String: Any]
            let w = CGFloat(dims["width"] as? Double ?? 1)
            let h = CGFloat(dims["height"] as? Double ?? 2.4)
            let node = SCNNode(geometry: SCNBox(width: w, height: h, length: 0.08, chamferRadius: 0))
            node.geometry?.firstMaterial?.diffuse.contents = UIColor.systemGray3
            if let transform {
                let tx = Float(transform["tx"] as? Double ?? 0)
                let ty = Float(transform["ty"] as? Double ?? Double(h / 2))
                let tz = Float(transform["tz"] as? Double ?? 0)
                node.position = SCNVector3(tx, ty, tz)
            } else {
                node.position = SCNVector3(Float(index) * 0.2 - 1.0, Float(h / 2), -Float(index) * 0.15)
            }
            scene.rootNode.addChildNode(node)
            index += 1
        }

        for (i, obj) in objects.enumerated() {
            let dims = obj["dimensions"] as? [String: Any] ?? [:]
            let transform = obj["transform"] as? [String: Any]
            let w = CGFloat(max(dims["width"] as? Double ?? 0.4, 0.15))
            let h = CGFloat(max(dims["height"] as? Double ?? 0.4, 0.15))
            let d = CGFloat(max(dims["depth"] as? Double ?? 0.4, 0.15))
            let category = (obj["category"] as? String ?? "object").lowercased()

            let color: UIColor
            if category.contains("sofa") || category.contains("bed") {
                color = .systemIndigo
            } else if category.contains("table") || category.contains("desk") {
                color = .systemBrown
            } else if category.contains("storage") || category.contains("refrigerator") {
                color = .darkGray
            } else {
                color = .systemTeal
            }

            let node = SCNNode(geometry: SCNBox(width: w, height: h, length: d, chamferRadius: 0.02))
            node.geometry?.firstMaterial?.diffuse.contents = color
            if let transform {
                let tx = Float(transform["tx"] as? Double ?? 0)
                let ty = Float(transform["ty"] as? Double ?? Double(h / 2))
                let tz = Float(transform["tz"] as? Double ?? 0)
                node.position = SCNVector3(tx, ty, tz)
            } else {
                let x = Float(i % 3) * 0.9 - 0.9
                let z = Float(i / 3) * 0.9
                node.position = SCNVector3(x, Float(h / 2), z)
            }
            scene.rootNode.addChildNode(node)
        }

        let floor = SCNNode(geometry: SCNPlane(width: 6, height: 6))
        floor.geometry?.firstMaterial?.diffuse.contents = UIColor.secondarySystemFill
        floor.geometry?.firstMaterial?.isDoubleSided = true
        floor.eulerAngles.x = -.pi / 2
        scene.rootNode.addChildNode(floor)

        let camera = SCNNode()
        camera.camera = SCNCamera()
        camera.position = SCNVector3(0, 2.2, 4.5)
        camera.look(at: SCNVector3(0, 0.5, 0))
        scene.rootNode.addChildNode(camera)

        let light = SCNNode()
        light.light = SCNLight()
        light.light?.type = .omni
        light.position = SCNVector3(2, 4, 3)
        scene.rootNode.addChildNode(light)

        return scene
    }
}
