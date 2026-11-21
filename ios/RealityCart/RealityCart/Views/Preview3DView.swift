import ARKit
import RealityKit
import SceneKit
import SwiftUI
import UIKit

enum PreviewMode: String, CaseIterable, Identifiable {
    case photoOverlay = "Photo overlay"
    case liveAR = "Live camera AR"

    var id: String { rawValue }
}

struct Preview3DView: View {
    @EnvironmentObject private var session: RedesignSession
    @State private var mode: PreviewMode = .photoOverlay

    var body: some View {
        NavigationStack {
            Group {
                if let result = session.result {
                    ZStack {
                        if mode == .photoOverlay {
                            if let image = session.capturedImage {
                                Image(uiImage: image)
                                    .resizable()
                                    .scaledToFill()
                                    .ignoresSafeArea()
                                    .allowsHitTesting(false) // don't steal orbit gestures
                            } else {
                                Color.black.ignoresSafeArea().allowsHitTesting(false)
                            }

                            SceneKitOverlayView(
                                placements: result.placements,
                                surfaceHeight: result.scene.surfaces?.first?.height_m ?? 0,
                                surfaceWidth: result.scene.surfaces?.first?.width_m ?? 2,
                                surfaceDepth: result.scene.surfaces?.first?.depth_m ?? 2
                            )
                            .id(result.placements.map(\.slot_id).joined(separator: "-"))
                        } else {
                            LiveARPlacementView(
                                placements: result.placements
                            )
                            .id("ar-" + result.placements.map(\.slot_id).joined(separator: "-"))
                            .ignoresSafeArea(edges: .bottom)
                        }
                    }
                    .safeAreaInset(edge: .top) {
                        VStack(spacing: 8) {
                            Picker("Mode", selection: $mode) {
                                ForEach(PreviewMode.allCases) { item in
                                    Text(item.rawValue).tag(item)
                                }
                            }
                            .pickerStyle(.segmented)
                            .padding(.horizontal)

                            Text(hintText)
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.white)
                                .multilineTextAlignment(.center)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 8)
                                .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 10))
                                .padding(.horizontal)
                        }
                        .padding(.top, 6)
                    }
                } else {
                    ContentUnavailableView(
                        "3D / AR",
                        systemImage: "cube",
                        description: Text("Run a redesign first, then come back to move products on your room photo or live camera.")
                    )
                }
            }
            .navigationTitle("3D / AR")
            .navigationBarTitleDisplayMode(.inline)
        }
    }

    private var hintText: String {
        switch mode {
        case .photoOverlay:
            return "Drag with one finger to orbit · Pinch to zoom · Use two fingers to pan"
        case .liveAR:
            return "Live camera: aim at the floor, then drag teal markers to move products"
        }
    }
}

// MARK: - Photo overlay (SceneKit — reliable camera controls)

struct SceneKitOverlayView: UIViewRepresentable {
    let placements: [PlacedProductDTO]
    let surfaceHeight: Double
    let surfaceWidth: Double
    let surfaceDepth: Double

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> SCNView {
        let view = SCNView(frame: .zero)
        view.backgroundColor = .clear
        view.isOpaque = false
        view.autoenablesDefaultLighting = true
        view.allowsCameraControl = true
        view.isUserInteractionEnabled = true
        view.antialiasingMode = .multisampling4X
        view.defaultCameraController.interactionMode = .orbitTurntable
        view.defaultCameraController.inertiaEnabled = true
        view.defaultCameraController.minimumVerticalAngle = -10
        view.defaultCameraController.maximumVerticalAngle = 85

        context.coordinator.configure(
            view: view,
            placements: placements,
            surfaceHeight: surfaceHeight,
            surfaceWidth: surfaceWidth,
            surfaceDepth: surfaceDepth
        )
        return view
    }

    func updateUIView(_ uiView: SCNView, context: Context) {
        // Only rebuild when placement set changes — rebuilding every SwiftUI
        // pass was resetting the camera and made orbit feel broken.
        context.coordinator.configure(
            view: uiView,
            placements: placements,
            surfaceHeight: surfaceHeight,
            surfaceWidth: surfaceWidth,
            surfaceDepth: surfaceDepth
        )
    }

    final class Coordinator {
        private var signature = ""

        func configure(
            view: SCNView,
            placements: [PlacedProductDTO],
            surfaceHeight: Double,
            surfaceWidth: Double,
            surfaceDepth: Double
        ) {
            let next = placements.map { "\($0.slot_id):\($0.proxy_model)" }.joined(separator: "|")
            guard next != signature else {
                view.allowsCameraControl = true
                view.isUserInteractionEnabled = true
                return
            }
            signature = next

            let scene = Self.buildScene(
                placements: placements,
                surfaceHeight: surfaceHeight,
                surfaceWidth: surfaceWidth,
                surfaceDepth: surfaceDepth
            )
            view.scene = scene
            view.pointOfView = scene.rootNode.childNode(withName: "camera", recursively: false)
            view.allowsCameraControl = true
            view.isUserInteractionEnabled = true
        }

        private static func buildScene(
            placements: [PlacedProductDTO],
            surfaceHeight: Double,
            surfaceWidth: Double,
            surfaceDepth: Double
        ) -> SCNScene {
            let scene = SCNScene()
            scene.background.contents = UIColor.clear

            let height = Float(max(surfaceHeight, 0))
            let width = Float(max(surfaceWidth, 1))
            let depth = Float(max(surfaceDepth, 1))

            let floor = SCNNode(
                geometry: SCNPlane(width: CGFloat(min(width, 4)), height: CGFloat(min(depth, 4)))
            )
            floor.geometry?.firstMaterial?.diffuse.contents = UIColor(white: 1, alpha: 0.25)
            floor.geometry?.firstMaterial?.isDoubleSided = true
            floor.eulerAngles.x = -.pi / 2
            floor.position = SCNVector3(0, height, 0)
            scene.rootNode.addChildNode(floor)

            for (index, placement) in placements.enumerated() {
                let node = ProxyNodes.make(proxy: placement.proxy_model)
                let x: Float
                if let px = placement.position?.x {
                    x = Float(px)
                } else {
                    x = Float(index) * 0.4 - 0.4
                }
                let z = Float(placement.position?.z ?? 0)
                node.position = SCNVector3(x, height + 0.05, z)
                let scale = Float(placement.scale ?? 1)
                node.scale = SCNVector3(scale, scale, scale)
                scene.rootNode.addChildNode(node)
            }

            let camera = SCNNode()
            camera.name = "camera"
            camera.camera = SCNCamera()
            camera.camera?.fieldOfView = 55
            camera.camera?.zNear = 0.01
            camera.camera?.zFar = 100
            camera.position = SCNVector3(0, height + 1.4, max(width, depth) * 0.75 + 1.4)
            camera.look(at: SCNVector3(0, height + 0.2, 0))
            scene.rootNode.addChildNode(camera)

            let light = SCNNode()
            light.light = SCNLight()
            light.light?.type = .omni
            light.position = SCNVector3(1, height + 2.5, 2)
            scene.rootNode.addChildNode(light)

            let ambient = SCNNode()
            ambient.light = SCNLight()
            ambient.light?.type = .ambient
            ambient.light?.intensity = 600
            scene.rootNode.addChildNode(ambient)

            return scene
        }
    }
}

enum ProxyNodes {
    static func make(proxy: String) -> SCNNode {
        let root = SCNNode()
        switch proxy.lowercased() {
        case "lamp", "desk_lamp", "floor_lamp", "table_lamp":
            let base = SCNNode(geometry: SCNBox(width: 0.14, height: 0.03, length: 0.14, chamferRadius: 0.01))
            base.geometry?.firstMaterial?.diffuse.contents = UIColor.darkGray
            let stem = SCNNode(geometry: SCNBox(width: 0.025, height: 0.55, length: 0.025, chamferRadius: 0))
            stem.geometry?.firstMaterial?.diffuse.contents = UIColor.darkGray
            stem.position.y = 0.28
            let shade = SCNNode(geometry: SCNSphere(radius: 0.09))
            shade.geometry?.firstMaterial?.diffuse.contents = UIColor.gray
            shade.position.y = 0.58
            root.addChildNode(base)
            root.addChildNode(stem)
            root.addChildNode(shade)

        case "plant":
            let pot = SCNNode(geometry: SCNBox(width: 0.12, height: 0.1, length: 0.12, chamferRadius: 0.01))
            pot.geometry?.firstMaterial?.diffuse.contents = UIColor.brown
            let foliage = SCNNode(geometry: SCNSphere(radius: 0.12))
            foliage.geometry?.firstMaterial?.diffuse.contents = UIColor.systemGreen
            foliage.position.y = 0.16
            root.addChildNode(pot)
            root.addChildNode(foliage)

        case "organizer", "storage":
            let box = SCNNode(geometry: SCNBox(width: 0.45, height: 0.55, length: 0.3, chamferRadius: 0.01))
            box.geometry?.firstMaterial?.diffuse.contents = UIColor.black
            box.position.y = 0.27
            root.addChildNode(box)

        case "shelf", "wall_shelf", "bookshelf", "accent_shelf":
            let shelf = SCNNode(geometry: SCNBox(width: 0.7, height: 0.08, length: 0.25, chamferRadius: 0.01))
            shelf.geometry?.firstMaterial?.diffuse.contents = UIColor.darkGray
            shelf.position.y = 0.04
            root.addChildNode(shelf)

        default:
            let box = SCNNode(geometry: SCNBox(width: 0.28, height: 0.28, length: 0.28, chamferRadius: 0.02))
            box.geometry?.firstMaterial?.diffuse.contents = UIColor.systemTeal
            box.position.y = 0.14
            root.addChildNode(box)
        }

        let marker = SCNNode(geometry: SCNSphere(radius: 0.03))
        marker.geometry?.firstMaterial?.diffuse.contents = UIColor.systemTeal
        marker.position.y = 0.7
        root.addChildNode(marker)
        return root
    }
}

// MARK: - Live camera AR

struct LiveARPlacementView: UIViewRepresentable {
    let placements: [PlacedProductDTO]

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero)
        view.cameraMode = .ar
        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal]
        config.environmentTexturing = .automatic
        view.session.run(config, options: [.resetTracking, .removeExistingAnchors])
        context.coordinator.build(on: view, placements: placements)
        return view
    }

    func updateUIView(_ uiView: ARView, context: Context) {
        let key = placements.map(\.slot_id).joined()
        guard key != context.coordinator.signature else { return }
        context.coordinator.build(on: uiView, placements: placements)
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator {
        var signature = ""
        private var anchor: AnchorEntity?

        func build(on view: ARView, placements: [PlacedProductDTO]) {
            signature = placements.map(\.slot_id).joined()
            if let anchor {
                view.scene.removeAnchor(anchor)
            }

            let root = AnchorEntity(
                .plane(.horizontal, classification: .any, minimumBounds: [0.35, 0.35])
            )
            view.scene.addAnchor(root)
            anchor = root

            let guide = ModelEntity(
                mesh: .generatePlane(width: 1.2, depth: 1.2),
                materials: [SimpleMaterial(color: UIColor(white: 1, alpha: 0.15), isMetallic: false)]
            )
            root.addChild(guide)

            for (index, placement) in placements.enumerated() {
                let entity = RealityProxyFactory.make(proxy: placement.proxy_model)
                let x = Float(placement.position?.x ?? Double(index) * 0.35 - 0.35)
                let z = Float(placement.position?.z ?? 0)
                entity.position = SIMD3<Float>(x, 0.02, z)
                entity.generateCollisionShapes(recursive: true)
                _ = view.installGestures([.translation, .rotation], for: entity)
                root.addChild(entity)
            }
        }
    }
}

enum RealityProxyFactory {
    static func make(proxy: String) -> ModelEntity {
        switch proxy.lowercased() {
        case "lamp", "desk_lamp", "floor_lamp", "table_lamp":
            let root = ModelEntity()
            let base = ModelEntity(
                mesh: .generateBox(size: [0.14, 0.03, 0.14]),
                materials: [SimpleMaterial(color: UIColor.darkGray, isMetallic: true)]
            )
            let stem = ModelEntity(
                mesh: .generateBox(size: [0.025, 0.55, 0.025]),
                materials: [SimpleMaterial(color: UIColor.darkGray, isMetallic: true)]
            )
            stem.position.y = 0.28
            let shade = ModelEntity(
                mesh: .generateSphere(radius: 0.09),
                materials: [SimpleMaterial(color: UIColor.gray, isMetallic: false)]
            )
            shade.position.y = 0.58
            root.addChild(base)
            root.addChild(stem)
            root.addChild(shade)
            return root
        case "plant":
            let root = ModelEntity()
            let pot = ModelEntity(
                mesh: .generateBox(size: [0.12, 0.1, 0.12]),
                materials: [SimpleMaterial(color: UIColor.brown, isMetallic: false)]
            )
            let foliage = ModelEntity(
                mesh: .generateSphere(radius: 0.12),
                materials: [SimpleMaterial(color: UIColor.systemGreen, isMetallic: false)]
            )
            foliage.position.y = 0.16
            root.addChild(pot)
            root.addChild(foliage)
            return root
        case "organizer", "storage":
            return ModelEntity(
                mesh: .generateBox(size: [0.45, 0.55, 0.3]),
                materials: [SimpleMaterial(color: UIColor.black, isMetallic: false)]
            )
        case "shelf", "wall_shelf", "bookshelf", "accent_shelf":
            return ModelEntity(
                mesh: .generateBox(size: [0.7, 0.08, 0.25]),
                materials: [SimpleMaterial(color: UIColor.darkGray, isMetallic: false)]
            )
        default:
            return ModelEntity(
                mesh: .generateBox(size: [0.28, 0.28, 0.28]),
                materials: [SimpleMaterial(color: UIColor.systemTeal, isMetallic: false)]
            )
        }
    }
}
