#import <Foundation/Foundation.h>
#import <AVFoundation/AVFoundation.h>
#import <Vision/Vision.h>
#import <AppKit/AppKit.h>
#import <CoreImage/CoreImage.h>

static NSFileManager *fm;
static NSString *outputDir;

static void writeJSON(id object, NSString *name) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:object options:NSJSONWritingPrettyPrinted error:nil];
    [data writeToFile:[outputDir stringByAppendingPathComponent:name] atomically:YES];
}

static void progress(NSString *status, NSInteger amount, NSString *stage, NSString *detail) {
    writeJSON(@{@"status": status, @"progress": @(amount), @"stage": stage, @"detail": detail ?: @""}, @"progress.json");
}

static NSString *clockText(double seconds) {
    NSInteger value = MAX(0, (NSInteger)llround(seconds));
    return [NSString stringWithFormat:@"%02ld:%02ld", (long)(value / 60), (long)(value % 60)];
}

static NSString *normalized(NSString *text) {
    NSMutableString *result = [NSMutableString string];
    NSCharacterSet *ignored = [[NSCharacterSet alphanumericCharacterSet] invertedSet];
    NSString *folded = [[text precomposedStringWithCompatibilityMapping] lowercaseString];
    for (NSUInteger i = 0; i < folded.length; i++) {
        unichar c = [folded characterAtIndex:i];
        if (![ignored characterIsMember:c] || c > 127) [result appendFormat:@"%C", c];
    }
    return result;
}

static double similarity(NSString *left, NSString *right) {
    NSString *a = normalized(left), *b = normalized(right);
    if (!a.length || !b.length) return [a isEqualToString:b] ? 1.0 : 0.0;
    NSMutableSet *sa = [NSMutableSet set], *sb = [NSMutableSet set];
    for (NSUInteger i = 0; i < a.length; i++) [sa addObject:[a substringWithRange:NSMakeRange(i, 1)]];
    for (NSUInteger i = 0; i < b.length; i++) [sb addObject:[b substringWithRange:NSMakeRange(i, 1)]];
    NSMutableSet *intersection = [sa mutableCopy]; [intersection intersectSet:sb];
    return (2.0 * intersection.count) / (sa.count + sb.count);
}

static NSArray<NSNumber *> *signature(CVPixelBufferRef pixel) {
    CVPixelBufferLockBaseAddress(pixel, kCVPixelBufferLock_ReadOnly);
    uint8_t *base = CVPixelBufferGetBaseAddress(pixel);
    size_t width = CVPixelBufferGetWidth(pixel), height = CVPixelBufferGetHeight(pixel), stride = CVPixelBufferGetBytesPerRow(pixel);
    NSMutableArray *values = [NSMutableArray arrayWithCapacity:256];
    for (NSInteger y = 0; y < 16; y++) {
        size_t py = MIN(height - 1, (size_t)((y + .5) * height / 16.0));
        for (NSInteger x = 0; x < 16; x++) {
            size_t px = MIN(width - 1, (size_t)((x + .5) * width / 16.0));
            uint8_t *p = base + py * stride + px * 4;
            NSInteger gray = (NSInteger)(0.114 * p[0] + 0.587 * p[1] + 0.299 * p[2]);
            [values addObject:@(gray)];
        }
    }
    CVPixelBufferUnlockBaseAddress(pixel, kCVPixelBufferLock_ReadOnly);
    return values;
}

static double signatureDifference(NSArray<NSNumber *> *a, NSArray<NSNumber *> *b) {
    if (!a) return 1.0;
    NSInteger total = 0;
    for (NSInteger i = 0; i < 256; i++) total += labs(a[i].integerValue - b[i].integerValue);
    return total / (256.0 * 255.0);
}

static double focusScore(NSArray<NSNumber *> *values) {
    NSInteger total = 0;
    for (NSInteger y = 1; y < 15; y++) for (NSInteger x = 1; x < 15; x++) {
        NSInteger i = y * 16 + x;
        total += labs(values[i].integerValue * 4 - values[i-1].integerValue - values[i+1].integerValue - values[i-16].integerValue - values[i+16].integerValue);
    }
    return total / 196.0;
}

static BOOL saveJPEG(CVPixelBufferRef pixel, NSString *path) {
    CIImage *image = [CIImage imageWithCVPixelBuffer:pixel];
    CIContext *context = [CIContext contextWithOptions:nil];
    CGImageRef cg = [context createCGImage:image fromRect:image.extent];
    if (!cg) return NO;
    NSBitmapImageRep *rep = [[NSBitmapImageRep alloc] initWithCGImage:cg];
    CGImageRelease(cg);
    NSData *data = [rep representationUsingType:NSBitmapImageFileTypeJPEG properties:@{NSImageCompressionFactor: @0.84}];
    return [data writeToFile:path atomically:YES];
}

static NSArray *recognize(CVPixelBufferRef pixel, BOOL accurate, NSError **error) {
    VNRecognizeTextRequest *request = [VNRecognizeTextRequest new];
    request.recognitionLevel = accurate ? VNRequestTextRecognitionLevelAccurate : VNRequestTextRecognitionLevelFast;
    request.usesLanguageCorrection = YES;
    request.recognitionLanguages = @[@"zh-Hans", @"zh-Hant", @"en-US"];
    request.minimumTextHeight = accurate ? 0.008 : 0.012;
    VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCVPixelBuffer:pixel options:@{}];
    if (![handler performRequests:@[request] error:error]) return @[];
    NSArray *sorted = [request.results sortedArrayUsingComparator:^NSComparisonResult(VNRecognizedTextObservation *a, VNRecognizedTextObservation *b) {
        CGFloat delta = fabs(CGRectGetMidY(a.boundingBox) - CGRectGetMidY(b.boundingBox));
        if (delta > .035) return CGRectGetMidY(a.boundingBox) > CGRectGetMidY(b.boundingBox) ? NSOrderedAscending : NSOrderedDescending;
        return CGRectGetMinX(a.boundingBox) < CGRectGetMinX(b.boundingBox) ? NSOrderedAscending : NSOrderedDescending;
    }];
    NSMutableArray *lines = [NSMutableArray array];
    for (VNRecognizedTextObservation *observation in sorted) {
        VNRecognizedText *candidate = [observation topCandidates:1].firstObject;
        if (!candidate) continue;
        CGRect box = observation.boundingBox;
        [lines addObject:@{@"text": candidate.string, @"confidence": @(candidate.confidence), @"bbox": @[@(box.origin.x), @(1-CGRectGetMaxY(box)), @(box.size.width), @(box.size.height)]}];
    }
    return lines;
}

static NSArray *mergeFrames(NSArray *frames) {
    NSMutableArray *segments = [NSMutableArray array];
    for (NSDictionary *frame in frames) {
        NSArray *texts = [frame[@"lines"] valueForKey:@"text"];
        NSString *text = [texts componentsJoinedByString:@"\n"];
        if (!text.length) continue;
        double sum = 0; for (NSDictionary *line in frame[@"lines"]) sum += [line[@"confidence"] doubleValue];
        double confidence = sum / [frame[@"lines"] count];
        NSMutableDictionary *last = segments.lastObject;
        if (last && [frame[@"time"] doubleValue] - [last[@"end_time"] doubleValue] <= 3.25) {
            NSString *a = normalized(last[@"clean_text"]), *b = normalized(text);
            if ([a containsString:b] || [b containsString:a] || similarity(last[@"clean_text"], text) >= .72) {
                last[@"end_time"] = frame[@"time"];
                if (b.length > a.length || confidence > [last[@"confidence"] doubleValue] + .08) {
                    last[@"raw_text"] = text; last[@"consensus_text"] = text; last[@"clean_text"] = text;
                    last[@"confidence"] = @(confidence); last[@"evidence_frame"] = frame[@"image"];
                }
                last[@"needs_review"] = @([last[@"confidence"] doubleValue] < .72);
                continue;
            }
        }
        [segments addObject:[@{@"sequence": @(segments.count + 1), @"start_time": frame[@"time"], @"end_time": frame[@"time"], @"raw_text": text, @"consensus_text": text, @"clean_text": text, @"user_text": [NSNull null], @"confidence": @(confidence), @"evidence_frame": frame[@"image"], @"needs_review": @(confidence < .72)} mutableCopy]];
    }
    return segments;
}

static int fail(NSString *message) {
    progress(@"failed", 100, @"处理失败", message);
    fprintf(stderr, "%s\n", message.UTF8String);
    return 1;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 4) { fprintf(stderr, "usage: framecopy-extractor <video> <output> <mode>\n"); return 2; }
        fm = NSFileManager.defaultManager;
        NSString *input = @(argv[1]); outputDir = @(argv[2]); NSString *mode = @(argv[3]);
        NSString *evidenceDir = [outputDir stringByAppendingPathComponent:@"evidence"];
        [fm createDirectoryAtPath:evidenceDir withIntermediateDirectories:YES attributes:nil error:nil];
        progress(@"validating", 7, @"正在检查视频", @"读取媒体信息");

        AVURLAsset *asset = [AVURLAsset URLAssetWithURL:[NSURL fileURLWithPath:input] options:nil];
        double duration = CMTimeGetSeconds(asset.duration);
        NSArray<AVAssetTrack *> *tracks = [asset tracksWithMediaType:AVMediaTypeVideo];
        if (!tracks.count) return fail(@"UNSUPPORTED_MEDIA: 视频中没有可读取的视频轨道");
        AVAssetTrack *track = tracks.firstObject;
        CGSize transformed = CGSizeApplyAffineTransform(track.naturalSize, track.preferredTransform);
        NSInteger width = (NSInteger)fabs(transformed.width), height = (NSInteger)fabs(transformed.height);
        if (!isfinite(duration) || duration <= 0) return fail(@"MEDIA_CORRUPTED: 视频时长无效");
        if (duration > 600.1) return fail(@"DURATION_EXCEEDED: 视频超过10分钟");
        if (width > 4096 || height > 4096) return fail(@"UNSUPPORTED_MEDIA: 视频分辨率超过4K");

        NSError *error = nil;
        AVAssetReader *reader = [[AVAssetReader alloc] initWithAsset:asset error:&error];
        if (!reader) return fail([@"MEDIA_CORRUPTED: " stringByAppendingString:error.localizedDescription]);
        AVAssetReaderTrackOutput *videoOutput = [[AVAssetReaderTrackOutput alloc] initWithTrack:track outputSettings:@{(id)kCVPixelBufferPixelFormatTypeKey: @(kCVPixelFormatType_32BGRA)}];
        videoOutput.alwaysCopiesSampleData = NO;
        if (![reader canAddOutput:videoOutput]) return fail(@"MEDIA_CORRUPTED: 无法建立视频解码器");
        [reader addOutput:videoOutput];
        if (![reader startReading]) return fail([@"MEDIA_CORRUPTED: " stringByAppendingString:reader.error.localizedDescription ?: @"无法读取视频"]);

        BOOL accurate = [mode isEqualToString:@"accurate"];
        double interval = accurate ? .35 : .7, threshold = accurate ? .028 : .045, nextSample = 0;
        NSInteger sampled = 0, candidates = 0;
        NSArray *previous = nil;
        NSMutableArray *frames = [NSMutableArray array];
        progress(@"analyzing", 14, @"正在分析画面", @"筛选清晰且有变化的候选帧");
        CMSampleBufferRef sample;
        while ((sample = [videoOutput copyNextSampleBuffer])) {
            @autoreleasepool {
                double at = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample));
                if (at + .0001 < nextSample) { CFRelease(sample); continue; }
                nextSample = at + interval;
                CVPixelBufferRef pixel = CMSampleBufferGetImageBuffer(sample);
                sampled += 1;
                NSArray *sig = signature(pixel);
                double difference = signatureDifference(previous, sig), focus = focusScore(sig);
                BOOL inspect = !previous || difference >= threshold || sampled % (accurate ? 5 : 8) == 0;
                previous = sig;
                if (!inspect || focus < 5) { CFRelease(sample); continue; }
                candidates += 1;
                progress(@"recognizing", MIN(67, 18 + (NSInteger)((at / duration) * 49)), @"正在识别文字", [NSString stringWithFormat:@"候选画面 %ld · 视频位置 %@", (long)candidates, clockText(at)]);
                NSError *ocrError = nil;
                NSArray *lines = recognize(pixel, accurate, &ocrError);
                if (ocrError) { CFRelease(sample); return fail([@"OCR_INITIALIZATION_FAILED: " stringByAppendingString:ocrError.localizedDescription]); }
                if (lines.count) {
                    NSString *imageName = [NSString stringWithFormat:@"%09.3f.jpg", at];
                    saveJPEG(pixel, [evidenceDir stringByAppendingPathComponent:imageName]);
                    [frames addObject:@{@"time": @(at), @"image": imageName, @"sharpness": @(focus), @"lines": lines}];
                }
                CFRelease(sample);
            }
        }
        if (reader.status == AVAssetReaderStatusFailed) return fail([@"MEDIA_CORRUPTED: " stringByAppendingString:reader.error.localizedDescription]);
        if (!frames.count) return fail(@"NO_TEXT_DETECTED: 没有检测到有效画面文字");

        progress(@"merging", 78, @"正在合并重复内容", [NSString stringWithFormat:@"识别到 %ld 个含文字候选画面", (long)frames.count]);
        NSArray *segments = mergeFrames(frames);
        writeJSON(segments, @"segments.auto.json"); writeJSON(segments, @"segments.user.json");
        NSMutableString *jsonl = [NSMutableString string];
        for (id frame in frames) {
            NSData *data = [NSJSONSerialization dataWithJSONObject:frame options:0 error:nil];
            [jsonl appendFormat:@"%@\n", [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding]];
        }
        [jsonl writeToFile:[outputDir stringByAppendingPathComponent:@"raw-ocr.jsonl"] atomically:YES encoding:NSUTF8StringEncoding error:nil];

        progress(@"generating", 91, @"正在生成 Markdown", @"整理结果和核查资料");
        NSMutableArray *cleanParts = [NSMutableArray array], *timedParts = [NSMutableArray array];
        for (NSDictionary *segment in segments) {
            [cleanParts addObject:segment[@"clean_text"]];
            [timedParts addObject:[NSString stringWithFormat:@"## %@\n\n%@", clockText([segment[@"start_time"] doubleValue]), segment[@"clean_text"]]];
        }
        NSString *clean = [NSString stringWithFormat:@"# 视频画面文案（纯净版）\n\n%@\n", [cleanParts componentsJoinedByString:@"\n\n"]];
        NSString *timed = [NSString stringWithFormat:@"# 视频画面文案（带时间戳版）\n\n%@\n", [timedParts componentsJoinedByString:@"\n\n"]];
        [clean writeToFile:[outputDir stringByAppendingPathComponent:@"视频文案-纯净版.md"] atomically:YES encoding:NSUTF8StringEncoding error:nil];
        [timed writeToFile:[outputDir stringByAppendingPathComponent:@"视频文案-带时间戳.md"] atomically:YES encoding:NSUTF8StringEncoding error:nil];
        NSString *createdAt = [[NSISO8601DateFormatter new] stringFromDate:[NSDate date]];
        writeJSON(@{@"pipeline_version": @"spike-0.1.0", @"ocr_provider": @"Apple Vision", @"source_name": input.lastPathComponent, @"duration_seconds": @(duration), @"width": @(width), @"height": @(height), @"mode": mode, @"sampled_frames": @(sampled), @"candidate_frames": @(candidates), @"output_files": @[@"视频文案-纯净版.md", @"视频文案-带时间戳.md", @"raw-ocr.jsonl", @"segments.auto.json", @"segments.user.json"], @"created_at": createdAt ?: @""}, @"manifest.json");
        progress(@"ready", 100, @"处理完成", [NSString stringWithFormat:@"生成 %ld 个文案段落", (long)segments.count]);
        return 0;
    }
}
