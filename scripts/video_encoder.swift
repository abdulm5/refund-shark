// Native macOS H.264 encoder for the deterministic Pillow motion-design render.
// Build: swiftc -module-cache-path .cache/swift-modules scripts/video_encoder.swift -o .cache/video-encoder
import Foundation
import AVFoundation
import AppKit
import CoreVideo

func fail(_ text: String) -> Never {
    FileHandle.standardError.write(Data((text + "\n").utf8)); exit(1)
}
let args = CommandLine.arguments
guard args.count >= 3 else { fail("Usage: video-encoder encode OUTPUT WIDTH HEIGHT FPS | inspect INPUT THUMBNAIL_DIR") }
if args[1] == "inspect" {
    let asset = AVURLAsset(url: URL(fileURLWithPath: args[2]))
    let duration = CMTimeGetSeconds(asset.duration)
    guard let track = asset.tracks(withMediaType: .video).first else { fail("No video track") }
    print("duration=\(duration) size=\(track.naturalSize) fps=\(track.nominalFrameRate) audio_tracks=\(asset.tracks(withMediaType: .audio).count)")
    if args.count > 3 {
        try FileManager.default.createDirectory(atPath: args[3], withIntermediateDirectories: true)
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter = .zero
        let times = args.count > 4 ? args[4].split(separator: ",").compactMap { Double($0) } : [1.0, 5, 12, 20, 28, 38, 49, 60, 72, 84, 96, 107, 119, 130, 140, 148, max(0, duration-1)]
        for seconds in times where seconds < duration {
            let cg = try generator.copyCGImage(at: CMTime(seconds: seconds, preferredTimescale: 600), actualTime: nil)
            let bitmap = NSBitmapImageRep(cgImage: cg)
            guard let data = bitmap.representation(using: .png, properties: [:]) else { fail("Thumbnail failed") }
            try data.write(to: URL(fileURLWithPath: "\(args[3])/frame-\(Int(seconds)).png"))
        }
    }
    exit(0)
}
guard args[1] == "encode", args.count == 6, let width = Int(args[3]), let height = Int(args[4]), let fps = Int32(args[5]) else { fail("Invalid encode arguments") }
let output = URL(fileURLWithPath: args[2])
if FileManager.default.fileExists(atPath: output.path) { try FileManager.default.removeItem(at: output) }
let writer = try AVAssetWriter(outputURL: output, fileType: .mp4)
writer.shouldOptimizeForNetworkUse = true
let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
    AVVideoCodecKey: AVVideoCodecType.h264,
    AVVideoWidthKey: width, AVVideoHeightKey: height,
    AVVideoCompressionPropertiesKey: [
        AVVideoAverageBitRateKey: Int(ProcessInfo.processInfo.environment["SHARK_VIDEO_BITRATE"] ?? "5000000") ?? 5_000_000,
        AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
        AVVideoMaxKeyFrameIntervalKey: Int(fps) * 2,
        AVVideoExpectedSourceFrameRateKey: fps,
        AVVideoAllowFrameReorderingKey: false
    ]
])
input.expectsMediaDataInRealTime = false
let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
    kCVPixelBufferWidthKey as String: width, kCVPixelBufferHeightKey as String: height,
    kCVPixelBufferIOSurfacePropertiesKey as String: [:]
])
writer.add(input)
guard writer.startWriting() else { fail("Writer failed: \(String(describing: writer.error))") }
writer.startSession(atSourceTime: .zero)
let frameBytes = width * height * 4
var index: Int64 = 0
while true {
    var data = Data(capacity: frameBytes)
    while data.count < frameBytes {
        guard let chunk = try FileHandle.standardInput.read(upToCount: frameBytes - data.count), !chunk.isEmpty else { break }
        data.append(chunk)
    }
    if data.isEmpty { break }
    guard data.count == frameBytes else { fail("Truncated frame") }
    while !input.isReadyForMoreMediaData {
        if writer.status == .failed { fail("Encoder failed: \(String(describing: writer.error))") }
        Thread.sleep(forTimeInterval: 0.001)
    }
    autoreleasepool {
        var optional: CVPixelBuffer?
        let status = CVPixelBufferPoolCreatePixelBuffer(nil, adaptor.pixelBufferPool!, &optional)
        guard status == kCVReturnSuccess, let buffer = optional else { fail("Pixel-buffer allocation failed") }
        CVPixelBufferLockBaseAddress(buffer, [])
        let target = CVPixelBufferGetBaseAddress(buffer)!
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        data.withUnsafeBytes { bytes in
            for row in 0..<height { memcpy(target.advanced(by: row * stride), bytes.baseAddress!.advanced(by: row * width * 4), width * 4) }
        }
        CVPixelBufferUnlockBaseAddress(buffer, [])
        guard adaptor.append(buffer, withPresentationTime: CMTime(value: index, timescale: fps)) else { fail("Append failed: \(String(describing: writer.error))") }
    }
    index += 1
}
writer.endSession(atSourceTime: CMTime(value: index, timescale: fps))
input.markAsFinished()
let done = DispatchSemaphore(value: 0)
writer.finishWriting { done.signal() }
done.wait()
guard writer.status == .completed else { fail("Finalization failed: \(String(describing: writer.error))") }
print("Encoded \(index) frames / \(Double(index)/Double(fps)) seconds to \(output.path)")
