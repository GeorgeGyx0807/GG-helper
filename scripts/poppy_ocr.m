#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#import <Vision/Vision.h>

static NSString *RecognizeImage(NSString *path, NSError **error) {
    NSImage *image = [[NSImage alloc] initWithContentsOfFile:path];
    if (image == nil) {
        *error = [NSError errorWithDomain:@"PoppyOCR" code:1 userInfo:@{
            NSLocalizedDescriptionKey: [NSString stringWithFormat:@"无法打开图像：%@", path]
        }];
        return nil;
    }
    NSRect rect = NSMakeRect(0, 0, image.size.width, image.size.height);
    CGImageRef cgImage = [image CGImageForProposedRect:&rect context:nil hints:nil];
    if (cgImage == nil) {
        *error = [NSError errorWithDomain:@"PoppyOCR" code:2 userInfo:@{
            NSLocalizedDescriptionKey: [NSString stringWithFormat:@"无法转换图像：%@", path]
        }];
        return nil;
    }

    VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
    request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
    request.usesLanguageCorrection = YES;
    request.recognitionLanguages = @[@"zh-Hans", @"en-US"];
    VNImageRequestHandler *handler = [[VNImageRequestHandler alloc] initWithCGImage:cgImage options:@{}];
    if (![handler performRequests:@[request] error:error]) {
        return nil;
    }

    NSMutableArray<NSString *> *lines = [NSMutableArray array];
    for (VNRecognizedTextObservation *observation in request.results) {
        VNRecognizedText *candidate = [[observation topCandidates:1] firstObject];
        if (candidate.string.length > 0) {
            [lines addObject:candidate.string];
        }
    }
    return [lines componentsJoinedByString:@"\n"];
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSMutableArray<NSString *> *texts = [NSMutableArray array];
        for (int index = 1; index < argc; index++) {
            NSError *error = nil;
            NSString *text = RecognizeImage([NSString stringWithUTF8String:argv[index]], &error);
            if (text == nil) {
                fprintf(stderr, "%s", error.localizedDescription.UTF8String);
                return 1;
            }
            [texts addObject:text];
        }
        NSError *error = nil;
        NSData *data = [NSJSONSerialization dataWithJSONObject:texts options:0 error:&error];
        if (data == nil) {
            fprintf(stderr, "%s", error.localizedDescription.UTF8String);
            return 1;
        }
        fwrite(data.bytes, 1, data.length, stdout);
    }
    return 0;
}
