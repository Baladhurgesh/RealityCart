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

    /// Floating tab bars overlay content and do not grow the bottom safe area.
    /// Lift controls by this amount so they sit just above the tab pill.
    private let tabBarClearance: CGFloat = 64

    var body: some View {
        Group {
            if let result = session.result {
                ZStack(alignment: .bottom) {
                    Group {
                        if mode == .photoOverlay {
                            ZStack {
                                if let image = session.capturedImage {
                                    Image(uiImage: image)
                                        .resizable()
                                        .scaledToFill()
                                        .allowsHitTesting(false)
                                } else {
                                    Color.black.allowsHitTesting(false)
                                }

                                SceneKitOverlayView(
                                    placements: result.placements,
                                    surfaceHeight: result.scene.surfaces?.first?.height_m ?? 0,
                                    surfaceWidth: result.scene.surfaces?.first?.width_m ?? 2,
                                    surfaceDepth: result.scene.surfaces?.first?.depth_m ?? 2,
                                    extraModelURLs: session.generatedModels
                                )
                                .id(result.placements.map(\.slot_id).joined(separator: "-")
                                    + session.generatedModels.values.sorted().joined())
                            }
                        } else {
                            LiveARPlacementView(
                                placements: result.placements,
                                extraModelURLs: session.generatedModels
                            )
                            .id("ar-" + result.placements.map(\.slot_id).joined(separator: "-")
                                + session.generatedModels.values.sorted().joined())
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .clipped()

                    VStack(spacing: 6) {
                        Picker("Mode", selection: $mode) {
                            ForEach(PreviewMode.allCases) { item in
                                Text(item.rawValue).tag(item)
                            }
                        }
                        .pickerStyle(.segmented)

                        Text(hintText)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 8)
                    .padding(.bottom, 8)
                    .frame(maxWidth: .infinity)
                    .background(.ultraThinMaterial)
                    .padding(.bottom, tabBarClearance)
                }
            } else {
                ContentUnavailableView(
                    "3D / AR",
                    systemImage: "cube",
                    description: Text("Run a redesign first, then come back to move products on your room photo or live camera.")
                )
            }
        }
    }

    private var hintText: String {
        if session.modelStatus.values.contains("generating") {
            return "Building product 3D models…"
        }
        switch mode {
        case .photoOverlay:
            return "Drag to orbit · Pinch to zoom"
        case .liveAR:
            return "Point at the floor · Drag to move · Twist with two fingers to rotate"
        }
    }
}

// MARK: - Photo overlay (SceneKit — reliable camera controls)

func overlayModelURL(for placement: PlacedProductDTO, extras: [String: String]) -> URL? {
    if let merchant = placement.product.usdzURL { return merchant }
    if let raw = extras[placement.product.id] { return URL(string: raw) }
    return nil
}

struct SceneKitOverlayView: UIViewRepresentable {
    let placements: [PlacedProductDTO]
    let surfaceHeight: Double
    let surfaceWidth: Double
    let surfaceDepth: Double
    var extraModelURLs: [String: String] = [:]

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
            surfaceDepth: surfaceDepth,
            extraModelURLs: extraModelURLs
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
            surfaceDepth: surfaceDepth,
            extraModelURLs: extraModelURLs
        )
    }

    final class Coordinator {
        private var signature = ""

        func configure(
            view: SCNView,
            placements: [PlacedProductDTO],
            surfaceHeight: Double,
            surfaceWidth: Double,
            surfaceDepth: Double,
            extraModelURLs: [String: String]
        ) {
            let next = placements.map {
                let extra = extraModelURLs[$0.product.id] ?? ""
                return "\($0.slot_id):\($0.proxy_model):\($0.product.model_url ?? ""):\(extra)"
            }.joined(separator: "|")
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
                surfaceDepth: surfaceDepth,
                extraModelURLs: extraModelURLs
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
            surfaceDepth: Double,
            extraModelURLs: [String: String]
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
                let node: SCNNode
                if let box = placement.product.boxMeters {
                    node = DimensionBox.sceneNode(width: box.width, height: box.height, depth: box.depth)
                } else {
                    node = ProxyNodes.make(proxy: placement.proxy_model)
                }
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
                if let remote = overlayModelURL(for: placement, extras: extraModelURLs) {
                    ProductModelInstaller.swapSceneKit(
                        node: node,
                        remote: remote,
                        fit: placement.product.boxMeters.map {
                            FitBox(width: $0.width, height: $0.height, depth: $0.depth)
                        }
                    )
                }
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

enum DimensionBox {
    /// Translucent box at the product's real size (meters). Sits on the floor.
    static func sceneNode(width: Float, height: Float, depth: Float) -> SCNNode {
        let root = SCNNode()
        let solid = SCNBox(
            width: CGFloat(width),
            height: CGFloat(height),
            length: CGFloat(depth),
            chamferRadius: 0.005
        )
        let fill = SCNMaterial()
        fill.diffuse.contents = UIColor(white: 0.08, alpha: 1)
        fill.transparency = 0.45
        fill.isDoubleSided = true
        solid.materials = [fill]
        let body = SCNNode(geometry: solid)
        body.position.y = height / 2

        let outline = SCNBox(
            width: CGFloat(width),
            height: CGFloat(height),
            length: CGFloat(depth),
            chamferRadius: 0
        )
        let lines = SCNMaterial()
        lines.fillMode = .lines
        lines.diffuse.contents = UIColor.systemTeal
        outline.materials = [lines]
        let frame = SCNNode(geometry: outline)
        frame.position.y = height / 2

        let marker = SCNNode(geometry: SCNSphere(radius: 0.03))
        marker.geometry?.firstMaterial?.diffuse.contents = UIColor.systemTeal
        marker.position.y = height + 0.06

        root.addChildNode(body)
        root.addChildNode(frame)
        root.addChildNode(marker)
        return root
    }

    static func reality(width: Float, height: Float, depth: Float) -> ModelEntity {
        let root = ModelEntity()
        var material = SimpleMaterial()
        material.color = .init(tint: UIColor(white: 0.08, alpha: 0.45))
        material.metallic = 0
        material.roughness = 0.9
        let body = ModelEntity(
            mesh: .generateBox(size: [width, height, depth]),
            materials: [material]
        )
        body.position.y = height / 2
        let marker = ModelEntity(
            mesh: .generateSphere(radius: 0.035),
            materials: [SimpleMaterial(color: UIColor.systemTeal, isMetallic: false)]
        )
        marker.position.y = height + 0.06
        root.addChild(body)
        root.addChild(marker)
        return root
    }
}

enum ProductModelCache {
    static func localFile(for remote: URL) async throws -> URL {
        let dir = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("ProductModels", isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let key = remote.pathComponents.dropLast().last ?? remote.lastPathComponent
        let name = (key.isEmpty || key == "file" ? "model" : key) + ".usdz"
        let dest = dir.appendingPathComponent(name)
        if FileManager.default.fileExists(atPath: dest.path) {
            return dest
        }
        let (tmp, response) = try await URLSession.shared.download(from: remote)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw URLError(.badServerResponse)
        }
        if FileManager.default.fileExists(atPath: dest.path) {
            try? FileManager.default.removeItem(at: dest)
        }
        try FileManager.default.moveItem(at: tmp, to: dest)
        return dest
    }
}

struct FitBox {
    var width: Float
    var height: Float
    var depth: Float
}

enum ProductModelInstaller {
    /// Replace the size box with a USDZ. When `fit` is set, scale to those meters.
    static func swapSceneKit(node: SCNNode, remote: URL, fit: FitBox? = nil) {
        Task {
            do {
                let local = try await ProductModelCache.localFile(for: remote)
                let loaded = try SCNScene(url: local, options: [.checkConsistency: false])
                let wrapper = SCNNode()
                for child in loaded.rootNode.childNodes {
                    wrapper.addChildNode(child.clone())
                }
                Self.alignUp(wrapper)
                let (minB, maxB) = wrapper.boundingBox
                let dx = max(maxB.x - minB.x, 0.001)
                let dy = max(maxB.y - minB.y, 0.001)
                let dz = max(maxB.z - minB.z, 0.001)
                let sx: Float
                let sy: Float
                let sz: Float
                if let fit {
                    sx = fit.width / dx
                    sy = fit.height / dy
                    sz = fit.depth / dz
                } else {
                    let extent = max(dx, max(dy, dz))
                    let uniform: Float = (extent > 1.4 || extent < 0.05) ? (0.9 / extent) : 1
                    sx = uniform
                    sy = uniform
                    sz = uniform
                }
                wrapper.scale = SCNVector3(sx, sy, sz)
                wrapper.position.y = -minB.y * sy
                let markerY = (fit?.height ?? (maxB.y * sy)) + 0.06
                await MainActor.run {
                    node.childNodes.forEach { $0.removeFromParentNode() }
                    node.addChildNode(wrapper)
                    let marker = SCNNode(geometry: SCNSphere(radius: 0.03))
                    marker.geometry?.firstMaterial?.diffuse.contents = UIColor.systemTeal
                    marker.position.y = markerY
                    node.addChildNode(marker)
                }
            } catch {
                // Size box remains visible.
            }
        }
    }

    static func swapReality(entity: ModelEntity, remote: URL, fit: FitBox? = nil) {
        Task {
            do {
                let local = try await ProductModelCache.localFile(for: remote)
                let model = try await Entity.load(contentsOf: local)
                alignUp(model)
                var bounds = model.visualBounds(relativeTo: model)
                if let fit {
                    model.scale = SIMD3<Float>(
                        fit.width / max(bounds.extents.x, 0.001),
                        fit.height / max(bounds.extents.y, 0.001),
                        fit.depth / max(bounds.extents.z, 0.001)
                    )
                } else {
                    let extent = max(bounds.extents.x, max(bounds.extents.y, bounds.extents.z))
                    if extent > 1.4 || (extent > 0 && extent < 0.05) {
                        model.scale *= SIMD3<Float>(repeating: 0.9 / extent)
                    }
                }
                bounds = model.visualBounds(relativeTo: model)
                model.position.y -= bounds.min.y
                let top = fit?.height ?? bounds.extents.y
                await MainActor.run {
                    entity.children.removeAll()
                    stripCollisions(model)
                    entity.addChild(model)
                    let marker = ModelEntity(
                        mesh: .generateSphere(radius: 0.035),
                        materials: [SimpleMaterial(color: UIColor.systemTeal, isMetallic: false)]
                    )
                    marker.position.y = max(top, 0.2) + 0.06
                    entity.addChild(marker)
                    let box = model.visualBounds(relativeTo: entity)
                    let shape = ShapeResource
                        .generateBox(size: SIMD3(max(box.extents.x, 0.12), max(box.extents.y, 0.12), max(box.extents.z, 0.12)))
                        .offsetBy(translation: box.center)
                    entity.components.set(CollisionComponent(shapes: [shape]))
                }
            } catch {
                // Size box remains visible.
            }
        }
    }

    /// Generated meshes sometimes arrive pitched or upside down. Stand them on +Y, then seat the base at y = 0.
    private static func alignUp(_ entity: Entity) {
        let probe = entity.children.first ?? entity
        let basis = probe === entity ? matrix_identity_float4x4 : probe.transform.matrix
        let yAxis = basis.columns.1
        let zAxis = basis.columns.2
        if yAxis.y < -0.5 {
            entity.orientation = simd_quatf(angle: .pi, axis: [1, 0, 0]) * entity.orientation
        } else if abs(zAxis.y) > abs(yAxis.y) + 0.25 {
            let angle: Float = zAxis.y > 0 ? -.pi / 2 : .pi / 2
            entity.orientation = simd_quatf(angle: angle, axis: [1, 0, 0]) * entity.orientation
        }
    }

    private static func alignUp(_ node: SCNNode) {
        let probe = node.childNodes.first ?? node
        let up = probe.simdConvertVector(simd_float3(0, 1, 0), to: node.parent)
        let forward = probe.simdConvertVector(simd_float3(0, 0, 1), to: node.parent)
        if up.y < -0.5 {
            node.simdOrientation = simd_quatf(angle: .pi, axis: [1, 0, 0]) * node.simdOrientation
        } else if abs(forward.y) > abs(up.y) + 0.25 {
            let angle: Float = forward.y > 0 ? -.pi / 2 : .pi / 2
            node.simdOrientation = simd_quatf(angle: angle, axis: [1, 0, 0]) * node.simdOrientation
        }
    }

    static func stripCollisions(_ entity: Entity) {
        entity.components.remove(CollisionComponent.self)
        for child in entity.children {
            stripCollisions(child)
        }
    }
}

enum ProxyNodes {
    static func make(proxy: String) -> SCNNode {
        let root = SCNNode()
        switch proxy.lowercased() {
        case "lamp", "desk_lamp", "floor_lamp", "table_lamp", "lighting":
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

        // Wide low cabinet — sideboard / storage furniture (not a tall cube).
        case "sideboard", "cabinet", "credenza", "console", "storage":
            let body = SCNNode(geometry: SCNBox(width: 1.0, height: 0.55, length: 0.38, chamferRadius: 0.01))
            body.geometry?.firstMaterial?.diffuse.contents = UIColor.black
            body.position.y = 0.275
            let top = SCNNode(geometry: SCNBox(width: 1.04, height: 0.03, length: 0.4, chamferRadius: 0.005))
            top.geometry?.firstMaterial?.diffuse.contents = UIColor.darkGray
            top.position.y = 0.565
            root.addChildNode(body)
            root.addChildNode(top)

        // Shallow catchall / ceramic tray.
        case "tray", "catchall", "dish", "bowl", "cable_tray":
            let dish = SCNNode(geometry: SCNCylinder(radius: 0.12, height: 0.025))
            dish.geometry?.firstMaterial?.diffuse.contents = UIColor(white: 0.92, alpha: 1)
            dish.position.y = 0.015
            let rim = SCNNode(geometry: SCNTube(innerRadius: 0.1, outerRadius: 0.12, height: 0.03))
            rim.geometry?.firstMaterial?.diffuse.contents = UIColor(white: 0.85, alpha: 1)
            rim.position.y = 0.02
            root.addChildNode(dish)
            root.addChildNode(rim)

        // Small desk organizer cubby (distinct from sideboard).
        case "organizer":
            let box = SCNNode(geometry: SCNBox(width: 0.22, height: 0.12, length: 0.16, chamferRadius: 0.01))
            box.geometry?.firstMaterial?.diffuse.contents = UIColor.darkGray
            box.position.y = 0.06
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
        let topY = root.boundingBox.max.y.isFinite ? root.boundingBox.max.y : 0.3
        marker.position.y = max(topY + 0.06, 0.2)
        root.addChildNode(marker)
        return root
    }
}

// MARK: - Live camera AR

struct LiveARPlacementView: UIViewRepresentable {
    let placements: [PlacedProductDTO]
    var extraModelURLs: [String: String] = [:]

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero)
        view.cameraMode = .ar
        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal]
        config.environmentTexturing = .automatic
        view.session.run(config, options: [.resetTracking, .removeExistingAnchors])
        let rotation = UIRotationGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.rotate(_:)))
        rotation.delegate = context.coordinator
        view.addGestureRecognizer(rotation)
        context.coordinator.view = view
        context.coordinator.build(on: view, placements: placements, extraModelURLs: extraModelURLs)
        return view
    }

    func updateUIView(_ uiView: ARView, context: Context) {
        let key = placements.map {
            "\($0.slot_id):\($0.product.model_url ?? ""):\(extraModelURLs[$0.product.id] ?? "")"
        }.joined()
        guard key != context.coordinator.signature else { return }
        context.coordinator.build(on: uiView, placements: placements, extraModelURLs: extraModelURLs)
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        var signature = ""
        weak var view: ARView?
        private var anchor: AnchorEntity?
        private var rotating: Entity?
        private var baseOrientation = simd_quatf()

        func gestureRecognizer(
            _ gestureRecognizer: UIGestureRecognizer,
            shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer
        ) -> Bool {
            true
        }

        /// Yaw only, around the floor normal, so a twist cannot flip a product over.
        @objc func rotate(_ gesture: UIRotationGestureRecognizer) {
            guard let view else { return }
            switch gesture.state {
            case .began:
                guard let hit = view.entity(at: gesture.location(in: view)),
                      let root = placementRoot(from: hit) else { return }
                rotating = root
                baseOrientation = root.orientation
            case .changed:
                guard let rotating else { return }
                let yaw = simd_quatf(angle: -Float(gesture.rotation), axis: [0, 1, 0])
                rotating.orientation = yaw * baseOrientation
            default:
                rotating = nil
            }
        }

        private func placementRoot(from entity: Entity) -> Entity? {
            var current: Entity? = entity
            while let node = current {
                if node.name == "placement" { return node }
                current = node.parent
            }
            return nil
        }

        func build(on view: ARView, placements: [PlacedProductDTO], extraModelURLs: [String: String]) {
            signature = placements.map {
                "\($0.slot_id):\($0.product.model_url ?? ""):\(extraModelURLs[$0.product.id] ?? "")"
            }.joined()
            if let anchor {
                view.scene.removeAnchor(anchor)
            }

            let root = AnchorEntity(
                .plane(.horizontal, classification: .floor, minimumBounds: [0.2, 0.2])
            )
            view.scene.addAnchor(root)
            anchor = root

            // Visual guide only — no collision, so it won't steal gesture hits.
            let guide = ModelEntity(
                mesh: .generatePlane(width: 1.2, depth: 1.2),
                materials: [SimpleMaterial(color: UIColor(white: 1, alpha: 0.15), isMetallic: false)]
            )
            root.addChild(guide)

            for (index, placement) in placements.enumerated() {
                let entity: ModelEntity
                if let box = placement.product.boxMeters {
                    entity = DimensionBox.reality(width: box.width, height: box.height, depth: box.depth)
                } else {
                    entity = RealityProxyFactory.make(proxy: placement.proxy_model)
                }
                let x = Float(placement.position?.x ?? Double(index) * 0.35 - 0.35)
                let z = Float(placement.position?.z ?? 0)
                entity.name = "placement"
                entity.position = SIMD3<Float>(x, 0, z)

                // Gestures attach to this entity; collision must live on it too.
                // Child meshes (and a loaded USDZ) steal the hit if they keep their own shapes.
                ProductModelInstaller.stripCollisions(entity)
                Self.installRootCollision(on: entity)

                root.addChild(entity)
                if let remote = overlayModelURL(for: placement, extras: extraModelURLs) {
                    ProductModelInstaller.swapReality(
                        entity: entity,
                        remote: remote,
                        fit: placement.product.boxMeters.map {
                            FitBox(width: $0.width, height: $0.height, depth: $0.depth)
                        }
                    )
                }
                // One finger slides on the floor. Twist is a separate yaw-only recognizer.
                _ = view.installGestures([.translation], for: entity)
            }
        }

        /// Single hittable box on the gestured root, covering all child meshes.
        private static func installRootCollision(on entity: ModelEntity) {
            let bounds = entity.visualBounds(relativeTo: entity)
            let extents = SIMD3<Float>(
                max(bounds.extents.x, 0.12),
                max(bounds.extents.y, 0.12),
                max(bounds.extents.z, 0.12)
            )
            let shape = ShapeResource
                .generateBox(size: extents)
                .offsetBy(translation: bounds.center)
            entity.components.set(CollisionComponent(shapes: [shape]))
        }
    }
}

enum RealityProxyFactory {
    static func make(proxy: String) -> ModelEntity {
        let root: ModelEntity
        switch proxy.lowercased() {
        case "lamp", "desk_lamp", "floor_lamp", "table_lamp", "lighting":
            root = ModelEntity()
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
        case "plant":
            root = ModelEntity()
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
        case "sideboard", "cabinet", "credenza", "console", "storage":
            root = ModelEntity()
            let body = ModelEntity(
                mesh: .generateBox(size: [1.0, 0.55, 0.38]),
                materials: [SimpleMaterial(color: UIColor.black, isMetallic: false)]
            )
            body.position.y = 0.275
            let top = ModelEntity(
                mesh: .generateBox(size: [1.04, 0.03, 0.4]),
                materials: [SimpleMaterial(color: UIColor.darkGray, isMetallic: false)]
            )
            top.position.y = 0.565
            root.addChild(body)
            root.addChild(top)
        case "tray", "catchall", "dish", "bowl", "cable_tray":
            root = ModelEntity(
                mesh: .generateBox(size: [0.24, 0.02, 0.16]),
                materials: [SimpleMaterial(color: UIColor(white: 0.92, alpha: 1), isMetallic: false)]
            )
        case "organizer":
            root = ModelEntity(
                mesh: .generateBox(size: [0.22, 0.12, 0.16]),
                materials: [SimpleMaterial(color: UIColor.darkGray, isMetallic: false)]
            )
        case "shelf", "wall_shelf", "bookshelf", "accent_shelf":
            root = ModelEntity(
                mesh: .generateBox(size: [0.7, 0.08, 0.25]),
                materials: [SimpleMaterial(color: UIColor.darkGray, isMetallic: false)]
            )
        default:
            root = ModelEntity(
                mesh: .generateBox(size: [0.28, 0.28, 0.28]),
                materials: [SimpleMaterial(color: UIColor.systemTeal, isMetallic: false)]
            )
        }

        // Visible grab target (matches photo-overlay markers).
        let marker = ModelEntity(
            mesh: .generateSphere(radius: 0.035),
            materials: [SimpleMaterial(color: UIColor.systemTeal, isMetallic: false)]
        )
        let top = root.visualBounds(relativeTo: root).max.y
        marker.position.y = max(top + 0.06, 0.2)
        root.addChild(marker)
        return root
    }
}
