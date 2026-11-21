import AVFoundation
import Speech
import SwiftUI

struct IntentView: View {
    @EnvironmentObject private var session: RedesignSession
    var onRun: () -> Void

    @StateObject private var speech = SpeechRecognizer()
    @FocusState private var focused: Bool

    private let budgetPresets: [Double] = [300, 600, 1000]

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextEditor(text: $session.intentText)
                        .frame(minHeight: 110)
                        .focused($focused)

                    Button {
                        if speech.isRecording {
                            speech.stop()
                            if !speech.transcript.isEmpty {
                                session.intentText = speech.transcript
                            }
                        } else {
                            speech.start()
                        }
                    } label: {
                        Label(
                            speech.isRecording ? "Stop" : "Speak",
                            systemImage: speech.isRecording ? "stop.circle.fill" : "mic.circle.fill"
                        )
                    }

                    if speech.isRecording {
                        Text(speech.transcript.isEmpty ? "Listening…" : speech.transcript)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                } header: {
                    Text("Intent")
                } footer: {
                    Text("Describe the look, what to keep, and any must-haves.")
                }

                Section("Budget") {
                    Text("$\(Int(session.budgetUSD))")
                        .font(.title2.bold())
                        .monospacedDigit()
                    Slider(value: $session.budgetUSD, in: 50...2000, step: 25)
                    HStack {
                        ForEach(budgetPresets, id: \.self) { amount in
                            Button("$\(Int(amount))") {
                                session.budgetUSD = amount
                            }
                            .buttonStyle(.bordered)
                            .controlSize(.small)
                            .tint(session.budgetUSD == amount ? Color(red: 0.12, green: 0.45, blue: 0.42) : nil)
                        }
                    }
                }

                Section {
                    Button {
                        focused = false
                        onRun()
                    } label: {
                        if session.isLoading {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                        } else {
                            Label("Run redesign", systemImage: "sparkles")
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .disabled(session.capturedImage == nil || session.roomPlanJSON == nil || session.isLoading)
                } footer: {
                    if session.roomPlanJSON == nil {
                        Text("Scan a room on Capture first.")
                    } else if let error = session.errorMessage {
                        Text(error).foregroundStyle(.red)
                    } else if session.isLoading {
                        Text(session.statusMessage)
                    }
                }
            }
            .dismissKeyboardToolbar()
            .navigationTitle("Intent")
        }
    }
}

@MainActor
final class SpeechRecognizer: ObservableObject {
    @Published var transcript = ""
    @Published var isRecording = false

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let audioEngine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?

    func start() {
        SFSpeechRecognizer.requestAuthorization { status in
            DispatchQueue.main.async {
                guard status == .authorized else { return }
                self.begin()
            }
        }
    }

    private func begin() {
        stop()
        transcript = ""
        let request = SFSpeechAudioBufferRecognitionRequest()
        self.request = request
        let input = audioEngine.inputNode
        let format = input.outputFormat(forBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            request.append(buffer)
        }
        do {
            try AVAudioSession.sharedInstance().setCategory(.record, mode: .measurement, options: .duckOthers)
            try AVAudioSession.sharedInstance().setActive(true, options: .notifyOthersOnDeactivation)
            audioEngine.prepare()
            try audioEngine.start()
            isRecording = true
            task = recognizer?.recognitionTask(with: request) { result, error in
                if let result {
                    self.transcript = result.bestTranscription.formattedString
                }
                if error != nil || (result?.isFinal ?? false) {
                    self.stop()
                }
            }
        } catch {
            stop()
        }
    }

    func stop() {
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        request?.endAudio()
        task?.cancel()
        request = nil
        task = nil
        isRecording = false
    }
}
