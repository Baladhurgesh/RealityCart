import Foundation
import UIKit

enum APIError: LocalizedError {
    case invalidURL
    case badStatus(Int, String)
    case decoding(Error)
    case emptyImage
    case missingRoomPlan

    var errorDescription: String? {
        switch self {
        case .invalidURL: return "Invalid API URL"
        case .badStatus(let code, let body): return "HTTP \(code): \(body)"
        case .decoding(let err): return "Decode error: \(err.localizedDescription)"
        case .emptyImage: return "Complete a RoomPlan LiDAR scan first — RealityCart is media-central."
        case .missingRoomPlan: return "RoomPlan LiDAR data is required. Scan your space on a Pro device."
        }
    }
}

final class APIClient {
    /// Simulator → host machine. Physical device: set your Mac LAN IP.
    var baseURL: URL

    init(baseURL: URL? = nil) {
        if let baseURL {
            self.baseURL = baseURL
        } else if let env = ProcessInfo.processInfo.environment["REALITYCART_API"] ,
                  let url = URL(string: env) {
            self.baseURL = url
        } else {
            #if targetEnvironment(simulator)
            self.baseURL = URL(string: "http://127.0.0.1:8000")!
            #else
            self.baseURL = URL(string: "http://127.0.0.1:8000")!
            #endif
        }
    }

    func health() async throws -> HealthResponse {
        let url = baseURL.appendingPathComponent("health")
        let (data, response) = try await URLSession.shared.data(from: url)
        try Self.throwIfNeeded(response, data: data)
        return try JSONDecoder().decode(HealthResponse.self, from: data)
    }

    func redesign(
        image: UIImage,
        intentText: String,
        budgetUSD: Double,
        roomPlanJSON: Data?,
        calibration: SurfaceCalibration?
    ) async throws -> RedesignResponseDTO {
        guard let jpeg = image.jpegData(compressionQuality: 0.85) else {
            throw APIError.emptyImage
        }

        let url = baseURL.appendingPathComponent("v1/redesign")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"

        let boundary = "Boundary-\(UUID().uuidString)"
        request.setValue(
            "multipart/form-data; boundary=\(boundary)",
            forHTTPHeaderField: "Content-Type"
        )

        var body = Data()
        func appendField(_ name: String, _ value: String) {
            body.append("--\(boundary)\r\n".data(using: .utf8)!)
            body.append(
                "Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n".data(using: .utf8)!
            )
            body.append("\(value)\r\n".data(using: .utf8)!)
        }

        appendField("intent_text", intentText)
        appendField("budget_usd", String(budgetUSD))

        if let roomPlanJSON, let str = String(data: roomPlanJSON, encoding: .utf8) {
            appendField("room_plan_json", str)
        }
        if let calibration {
            let data = try JSONEncoder().encode(calibration)
            if let str = String(data: data, encoding: .utf8) {
                appendField("calibration_json", str)
            }
        }

        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append(
            "Content-Disposition: form-data; name=\"image\"; filename=\"capture.jpg\"\r\n"
                .data(using: .utf8)!
        )
        body.append("Content-Type: image/jpeg\r\n\r\n".data(using: .utf8)!)
        body.append(jpeg)
        body.append("\r\n".data(using: .utf8)!)
        body.append("--\(boundary)--\r\n".data(using: .utf8)!)

        request.httpBody = body
        request.timeoutInterval = 180

        let (data, response) = try await URLSession.shared.data(for: request)
        try Self.throwIfNeeded(response, data: data)
        do {
            return try JSONDecoder().decode(RedesignResponseDTO.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }

    private static func throwIfNeeded(_ response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { return }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(data: data, encoding: .utf8) ?? ""
            throw APIError.badStatus(http.statusCode, body)
        }
    }
}
