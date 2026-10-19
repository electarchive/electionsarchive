import Foundation
import Vision
import AppKit
for path in CommandLine.arguments.dropFirst() {
    guard let img = NSImage(contentsOfFile: path), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { continue }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = false
    req.recognitionLanguages = ["en-US"]
    if #available(macOS 13.0, *) { req.revision = VNRecognizeTextRequestRevision3 }
    req.minimumTextHeight = 0.005
    try? VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
    var out = ""
    for obs in req.results ?? [] {
        guard let cand = obs.topCandidates(1).first else { continue }
        let s = cand.string
        var idx = s.startIndex
        for tok in s.split(separator: " ", omittingEmptySubsequences: true) {
            guard let r = s.range(of: tok, range: idx..<s.endIndex) else { continue }
            idx = r.upperBound
            guard let b = try? cand.boundingBox(for: r)?.boundingBox else { continue }
            out += "\(tok)\t\(b.minX)\t\(1 - b.maxY)\t\(b.maxX)\t\(1 - b.minY)\n"
        }
    }
    try? out.write(toFile: path + ".vision.tsv", atomically: true, encoding: .utf8)
}
