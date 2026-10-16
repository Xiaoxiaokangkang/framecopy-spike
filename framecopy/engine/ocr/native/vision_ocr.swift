import CoreGraphics
import Foundation
import ImageIO
import Vision

struct LinePayload: Codable {
    let text: String
    let confidence: Float
    let bbox: [CGFloat]
}

struct OutputPayload: Codable {
    let provider: String
    let version: String
    let model: String
    let revision: Int
    let languages: [String]
    let lines: [LinePayload]
}

func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(code)
}

guard CommandLine.arguments.count >= 2 else {
    fail("usage: vision-ocr <image> [languages]", code: 2)
}

let imageURL = URL(fileURLWithPath: CommandLine.arguments[1]) as CFURL
guard let source = CGImageSourceCreateWithURL(imageURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    fail("无法读取图像")
}

let languages = CommandLine.arguments.count >= 3
    ? CommandLine.arguments[2].split(separator: ",").map(String.init).filter { !$0.isEmpty }
    : ["zh-Hans", "zh-Hant", "en-US"]
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = languages
request.minimumTextHeight = 0.008
// Use the OS default revision: the numerically newest advertised revision is not
// guaranteed to support every configured language on every macOS release.
let revision = VNRecognizeTextRequest.defaultRevision
request.revision = revision

let handler = VNImageRequestHandler(cgImage: image, options: [:])
do {
    try handler.perform([request])
} catch {
    fail("Vision 识别失败：\(error.localizedDescription)")
}

let observations = (request.results ?? []).sorted { left, right in
    let leftTop = 1.0 - left.boundingBox.maxY
    let rightTop = 1.0 - right.boundingBox.maxY
    if abs(leftTop - rightTop) > 0.025 {
        return leftTop < rightTop
    }
    return left.boundingBox.minX < right.boundingBox.minX
}

let lines = observations.compactMap { observation -> LinePayload? in
    guard let candidate = observation.topCandidates(1).first else { return nil }
    let box = observation.boundingBox
    return LinePayload(
        text: candidate.string,
        confidence: candidate.confidence,
        bbox: [box.minX, 1.0 - box.maxY, box.maxX, 1.0 - box.minY]
    )
}

let version = ProcessInfo.processInfo.operatingSystemVersionString
let output = OutputPayload(
    provider: "apple_vision",
    version: version,
    model: "VNRecognizeTextRequest.accurate",
    revision: revision,
    languages: languages,
    lines: lines
)

do {
    let data = try JSONEncoder().encode(output)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
} catch {
    fail("无法编码识别结果")
}
