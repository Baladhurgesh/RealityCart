import AVFoundation
import Speech
import SwiftUI

struct IntentView: View {
    @EnvironmentObject private var session: RedesignSession
    var onRun: () -> Void

    @StateObject private var speech = SpeechRecognizer()
    @FocusState private var focused: Bool

    var body: some View {
        NavigationStack {
            Form {
                Section("What should we build?") {
                    TextEditor(text: $session.intentText)
                        .frame(minHeight: 120)
                        .focused($focused)

                    HStack {
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
                                speech.isRecording ? "Stop voice" : "Voice intent",
                                systemImage: speech.isRecording ? "stop.circle.fill" : "mic.circle.fill"
                            )
                        }

                        if speech.isRecording {
                            Text("Listening…")
                                .foregroundStyle(.red)
                        }
                    }

                    if !speech.transcript.isEmpty && speech.isRecording {
                        Text(speech.transcript)
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                }

                Section("Budget") {
                    HStack {
                        Text("$\(Int(session.budgetUSD))")
                        Slider(value: $session.budgetUSD, in: 50...2000, step: 25)
                    }
                }

                Section {
                    Button {
                        onRun()
                    } label: {
                        if session.isLoading {
                            ProgressView()
                        } else {
                            Label("Run redesign", systemImage: "sparkles")
                        }
                    }
                    .disabled(session.capturedImage == nil || session.isLoading)
                }

                Section("Status") {
                    Text(session.statusMessage)
                    if let error = session.errorMessage {
                        Text(error).foregroundStyle(.red)
                    }
                }
            }
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
        request.shouldReportPartialResults = true

        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.record, mode: .measurement, options: .duckOthers)
        try? session.setActive(true, options: .notifyOthersOnDeactivation)

        let input = audioEngine.inputNode
        let format = input.outputFormat(forBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            request.append(buffer)
        }
        audioEngine.prepare()
        try? audioEngine.start()
        isRecording = true

        task = recognizer?.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            if let result {
                self.transcript = result.bestTranscription.formattedString
            }
            if error != nil || (result?.isFinal ?? false) {
                self.stop()
            }
        }
    }

    func stop() {
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        request?.endAudio()
        task?.cancel()
        task = nil
        request = nil
        isRecording = false
    }
}
