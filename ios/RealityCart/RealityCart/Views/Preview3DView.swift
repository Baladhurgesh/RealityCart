import RealityKit
import SwiftUI
import UIKit

struct Preview3DView: View {
    @EnvironmentObject private var session: RedesignSession

    var body: some View {
        NavigationStack {
            ZStack {
                if let image = session.capturedImage {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFill()
                        .opacity(0.45)
                        .ignoresSafeArea()
                } else {
                    Color(.systemBackground).ignoresSafeArea()
                }

                if let result = session.result {
                    PlacementRealityView(
                        placements: result.placements,
                        surfaceHeight: result.scene.surfaces?.first?.height_m ?? 0.74,
                        surfaceWidth: result.scene.surfaces?.first?.width_m
                            ?? session.deskWidthMeters
                    )
                    .id(result.placements.map(\.slot_id).joined())
                } else {
                    ContentUnavailableView(
                        "3D preview",
                        systemImage: "cube",
                        description: Text("Run a redesign to place category proxies on the calibrated plane.")
                    )
                }
            }
            .navigationTitle("3D Preview")
            .toolbar {
                ToolbarItem(placement: .bottomBar) {
                    Text("Approximate proxies — real Shopify prices shown in Plan")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}

struct PlacementRealityView: UIViewRepresentable {
    let placements: [PlacedProductDTO]
    let surfaceHeight: Double
    let surfaceWidth: Double

    func makeUIView(context: Context) -> ARView {
        let view = ARView(frame: .zero)
        view.cameraMode = .nonAR
        view.environment.background = .color(.clear)
        context.coordinator.rebuild(on: view, placements: placements, surfaceHeight: surfaceHeight, surfaceWidth: surfaceWidth)
        return view
    }

    func updateUIView(_ uiView: ARView, context: Context) {
        context.coordinator.rebuild(
            on: uiView,
            placements: placements,
            surfaceHeight: surfaceHeight,
            surfaceWidth: surfaceWidth
        )
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator {
        private var contentAnchor: AnchorEntity?
        private var cameraAnchor: AnchorEntity?

        func rebuild(
            on view: ARView,
            placements: [PlacedProductDTO],
            surfaceHeight: Double,
            surfaceWidth: Double
        ) {
            if let contentAnchor {
                view.scene.removeAnchor(contentAnchor)
            }
            if let cameraAnchor {
                view.scene.removeAnchor(cameraAnchor)
            }

            let anchor = AnchorEntity(world: .zero)
            view.scene.addAnchor(anchor)
            contentAnchor = anchor

            let planeWidth = Float(max(surfaceWidth, 0.5))
            let planeDepth: Float = 0.7
            let planeMaterial = SimpleMaterial(
                color: UIColor(white: 1, alpha: 0.18),
                isMetallic: false
            )
            let plane = ModelEntity(
                mesh: .generatePlane(width: planeWidth, depth: planeDepth),
                materials: [planeMaterial]
            )
            plane.position = SIMD3(0, Float(surfaceHeight), 0)
            anchor.addChild(plane)

            for placement in placements {
                let entity = ProxyFactory.make(proxy: placement.proxy_model)
                let pos = placement.position
                entity.position = SIMD3(
                    Float(pos?.x ?? 0),
                    Float(pos?.y ?? surfaceHeight) + 0.05,
                    Float(pos?.z ?? 0)
                )
                let scale = Float(placement.scale ?? 1)
                entity.scale = SIMD3(repeating: scale)
                anchor.addChild(entity)
            }

            let camera = PerspectiveCamera()
            camera.camera.fieldOfViewInDegrees = 55
            let camAnchor = AnchorEntity(world: .zero)
            camAnchor.addChild(camera)
            camera.look(
                at: SIMD3(0, Float(surfaceHeight), 0),
                from: SIMD3(0.0, 1.4, 1.8),
                relativeTo: nil
            )
            view.scene.addAnchor(camAnchor)
            cameraAnchor = camAnchor
        }
    }
}

enum ProxyFactory {
    /// Box/sphere primitives only — available on iOS 17+.
    static func make(proxy: String) -> ModelEntity {
        switch proxy.lowercased() {
        case "lamp", "desk_lamp":
            let base = ModelEntity(
                mesh: .generateBox(size: [0.12, 0.02, 0.12]),
                materials: [SimpleMaterial(color: UIColor.darkGray, isMetallic: true)]
            )
            let stem = ModelEntity(
                mesh: .generateBox(size: [0.02, 0.35, 0.02]),
                materials: [SimpleMaterial(color: UIColor.darkGray, isMetallic: true)]
            )
            stem.position.y = 0.18
            let shade = ModelEntity(
                mesh: .generateSphere(radius: 0.07),
                materials: [SimpleMaterial(color: UIColor.gray, isMetallic: false)]
            )
            shade.position.y = 0.38
            let root = ModelEntity()
            root.addChild(base)
            root.addChild(stem)
            root.addChild(shade)
            return root

        case "plant":
            let pot = ModelEntity(
                mesh: .generateBox(size: [0.1, 0.08, 0.1]),
                materials: [SimpleMaterial(color: UIColor.brown, isMetallic: false)]
            )
            let foliage = ModelEntity(
                mesh: .generateSphere(radius: 0.09),
                materials: [SimpleMaterial(color: UIColor.systemGreen, isMetallic: false)]
            )
            foliage.position.y = 0.12
            let root = ModelEntity()
            root.addChild(pot)
            root.addChild(foliage)
            return root

        case "organizer", "storage":
            return ModelEntity(
                mesh: .generateBox(size: [0.22, 0.12, 0.14]),
                materials: [SimpleMaterial(color: UIColor.black, isMetallic: false)]
            )

        case "shelf", "wall_shelf":
            return ModelEntity(
                mesh: .generateBox(size: [0.4, 0.03, 0.16]),
                materials: [SimpleMaterial(color: UIColor.darkGray, isMetallic: false)]
            )

        case "monitor_stand", "monitor_arm":
            return ModelEntity(
                mesh: .generateBox(size: [0.25, 0.08, 0.2]),
                materials: [SimpleMaterial(color: UIColor.darkGray, isMetallic: true)]
            )

        case "cable_tray", "cable_management":
            return ModelEntity(
                mesh: .generateBox(size: [0.35, 0.04, 0.08]),
                materials: [SimpleMaterial(color: UIColor.black, isMetallic: false)]
            )

        default:
            return ModelEntity(
                mesh: .generateBox(size: [0.12, 0.12, 0.12]),
                materials: [SimpleMaterial(color: UIColor.systemTeal, isMetallic: false)]
            )
        }
    }
}
