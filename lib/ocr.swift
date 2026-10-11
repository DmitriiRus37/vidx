// OCR via Apple Vision (built into macOS, no model download).
// usage: ocr <groups> <image>...   → one JSON line per image: {"file":..., "text":...}
// <groups>: recognition-language lists separated by ";", e.g. "ru-RU,en-US;zh-Hans,en-US" (languages.py --ocr).
// Vision reads only the script of a request's first language (plus Latin), so each script group gets its own
// request. The first group's lines come first; a later group adds only lines that do not overlap one already
// read (the other models misread the first group's script, e.g. Cyrillic as Latin look-alikes). With several
// groups the lines are sorted top to bottom. A failed request is reported on stderr and the others still count.
import AppKit
import Foundation
import Vision

func warn(_ msg: String) { FileHandle.standardError.write((msg + "\n").data(using: .utf8)!) }

let args = CommandLine.arguments
guard args.count >= 3 else {
    warn("usage: ocr <lang,lang;lang,...> <image>...")
    exit(2)
}
let groups = args[1].split(separator: ";").map { $0.split(separator: ",").map(String.init) }.filter { !$0.isEmpty }

struct Line {
    let box: CGRect  // normalised, origin bottom-left
    let text: String
}

// share of the smaller box covered by the other: the same line read twice overlaps almost fully
func overlap(_ a: CGRect, _ b: CGRect) -> Double {
    let i = a.intersection(b)
    if i.isNull { return 0 }
    let small = min(a.width * a.height, b.width * b.height)
    return small > 0 ? Double(i.width * i.height / small) : 0
}

func read(_ cg: CGImage, _ langs: [String], _ path: String) -> [Line] {
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = true
    req.recognitionLanguages = langs
    do {
        try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    } catch {
        warn("ocr: \(path) [\(langs.joined(separator: ","))]: \(error)")
        return []
    }
    return (req.results ?? []).compactMap { obs in
        guard let s = obs.topCandidates(1).first?.string.trimmingCharacters(in: .whitespaces), !s.isEmpty
        else { return nil }
        return Line(box: obs.boundingBox, text: s)
    }
}

for path in args.dropFirst(2) {
    var lines: [Line] = []
    if let img = NSImage(contentsOfFile: path),
       let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) {
        for langs in groups {
            for l in read(cg, langs, path) where !lines.contains(where: { overlap($0.box, l.box) > 0.5 }) {
                lines.append(l)
            }
        }
        if groups.count > 1 {  // Vision returns each group's lines top to bottom; interleave the groups
            lines = lines.enumerated().sorted {
                abs($0.element.box.midY - $1.element.box.midY) > 0.01
                    ? $0.element.box.midY > $1.element.box.midY : $0.offset < $1.offset
            }.map { $0.element }
        }
    } else {
        warn("ocr: \(path): not an image")
    }
    let obj: [String: String] = ["file": (path as NSString).lastPathComponent,
                                 "text": lines.map { $0.text }.joined(separator: "\n")]
    let data = try! JSONSerialization.data(withJSONObject: obj, options: [])
    print(String(data: data, encoding: .utf8)!)
    fflush(stdout)
}
