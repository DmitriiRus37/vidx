// Apple SpeechTranscriber (macOS 26+, on-device speech recognition).
// usage: apple_asr --locales                                   → supported / installed locales
//        apple_asr [--timeout SEC] <locale> <audio> <out.json>
// <locale> is language-REGION (de-DE): matched exactly against the supported locales, because a bare
// "de" resolves to whatever the system prefers (de-AT). A missing speech model is downloaded first;
// that download gets SEC seconds (default 300) and has been seen to hang.
// exit: 3 locale not supported, 4 model download timed out, 1 other errors
import AVFoundation
import Foundation
import Speech

func fail(_ msg: String, _ code: Int32) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(code)
}

var args = Array(CommandLine.arguments.dropFirst())
guard #available(macOS 26.0, *) else { fail("SpeechTranscriber needs macOS 26", 3) }

if args == ["--locales"] {
    let s = await SpeechTranscriber.supportedLocales.map { $0.identifier(.bcp47) }.sorted()
    let i = await SpeechTranscriber.installedLocales.map { $0.identifier(.bcp47) }.sorted()
    print("supported:", s.joined(separator: " "))
    print("installed:", i.joined(separator: " "))
    exit(0)
}
var timeout = 300.0
if args.count >= 2 && args[0] == "--timeout" {
    guard let t = Double(args[1]), t > 0 else { fail("--timeout needs a positive number of seconds", 2) }
    timeout = t
    args.removeFirst(2)
}
guard args.count == 3 else { fail("usage: apple_asr [--timeout SEC] <locale> <audio> <out.json> | --locales", 2) }

let downloadTimeout = timeout
let supported = await SpeechTranscriber.supportedLocales
let wanted = args[0]
var match = supported.first { $0.identifier(.bcp47) == wanted }
if match == nil && !wanted.contains("-") {  // a bare language: let the system pick its region
    match = await SpeechTranscriber.supportedLocale(equivalentTo: Locale(identifier: wanted))
}
guard let locale = match else { fail("SpeechTranscriber: '\(wanted)' not supported", 3) }
let transcriber = SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [],
                                    attributeOptions: [.audioTimeRange])
do {
    if let req = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
        let id = locale.identifier(.bcp47)
        FileHandle.standardError.write("  downloading Apple speech model for \(id)\n".data(using: .utf8)!)
        let watchdog = Task {
            try await Task.sleep(nanoseconds: UInt64(downloadTimeout * 1_000_000_000))
            fail("SpeechTranscriber: model download for \(id) timed out after \(Int(downloadTimeout)) s", 4)
        }
        try await req.downloadAndInstall()
        watchdog.cancel()
    }
    let analyzer = SpeechAnalyzer(modules: [transcriber])
    let file = try AVAudioFile(forReading: URL(fileURLWithPath: args[1]))
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
    try data.write(to: URL(fileURLWithPath: args[2]))
} catch {
    fail("SpeechTranscriber: \(error)", 1)
}
