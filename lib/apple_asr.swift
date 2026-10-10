// Apple SpeechTranscriber (macOS 26+, on-device speech recognition).
// usage: apple_asr --locales                      → supported / installed locales
//        apple_asr <lang> <audio> <out.json>       → exit 3 if the language is not supported
import AVFoundation
import Foundation
import Speech

func fail(_ msg: String, _ code: Int32) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(code)
}

let args = CommandLine.arguments
guard #available(macOS 26.0, *) else { fail("SpeechTranscriber needs macOS 26", 3) }

if args.count == 2 && args[1] == "--locales" {
    let s = await SpeechTranscriber.supportedLocales.map { $0.identifier(.bcp47) }.sorted()
    let i = await SpeechTranscriber.installedLocales.map { $0.identifier(.bcp47) }.sorted()
    print("supported:", s.joined(separator: " "))
    print("installed:", i.joined(separator: " "))
    exit(0)
}
guard args.count == 4 else { fail("usage: apple_asr <lang> <audio> <out.json> | --locales", 2) }

guard let locale = await SpeechTranscriber.supportedLocale(equivalentTo: Locale(identifier: args[1])) else {
    fail("SpeechTranscriber: '\(args[1])' not supported", 3)
}
let transcriber = SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [],
                                    attributeOptions: [.audioTimeRange])
do {
    if let req = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
        FileHandle.standardError.write("  downloading Apple speech model for \(locale.identifier)\n".data(using: .utf8)!)
        try await req.downloadAndInstall()
    }
    let analyzer = SpeechAnalyzer(modules: [transcriber])
    let file = try AVAudioFile(forReading: URL(fileURLWithPath: args[2]))
    let collector = Task {
        var segs: [[String: Any]] = []
        for try await r in transcriber.results {
            let text = String(r.text.characters).trimmingCharacters(in: .whitespacesAndNewlines)
            if text.isEmpty { continue }
            segs.append(["start": r.range.start.seconds, "end": r.range.end.seconds, "text": text])
        }
        return segs
    }
    if let last = try await analyzer.analyzeSequence(from: file) {
        try await analyzer.finalizeAndFinish(through: last)
    } else {
        await analyzer.cancelAndFinishNow()
    }
    let segs = try await collector.value
    let data = try JSONSerialization.data(withJSONObject: ["engine": "apple", "segments": segs], options: [.prettyPrinted])
    try data.write(to: URL(fileURLWithPath: args[3]))
} catch {
    fail("SpeechTranscriber: \(error)", 1)
}
