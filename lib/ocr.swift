// OCR via Apple Vision (built into macOS, no model download).
// Usage: ocr <lang1,lang2,...> <image>...   → one JSON line per image: {"file":..., "text":...}
import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write("usage: ocr <langs> <image>...\n".data(using: .utf8)!)
    exit(2)
}
let langs = args[1].split(separator: ",").map(String.init)

for path in args.dropFirst(2) {
    var text = ""
    if let img = NSImage(contentsOfFile: path),
       let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) {
        let req = VNRecognizeTextRequest()
        req.recognitionLevel = .accurate
        req.usesLanguageCorrection = true
        req.recognitionLanguages = langs
        try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
        // Vision returns observations roughly top-to-bottom; keep that order
        let lines = (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
        text = lines.joined(separator: "\n")
    }
    let obj: [String: String] = ["file": (path as NSString).lastPathComponent, "text": text]
    let data = try! JSONSerialization.data(withJSONObject: obj, options: [])
    print(String(data: data, encoding: .utf8)!)
    fflush(stdout)
}
