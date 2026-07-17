#import <Foundation/Foundation.h>
#import <NaturalLanguage/NaturalLanguage.h>
#import <math.h>

static NSString *languageCode(NSString *text) {
    NLLanguageRecognizer *recognizer = [[NLLanguageRecognizer alloc] init];
    [recognizer processString:text];
    NLLanguage language = recognizer.dominantLanguage;
    if ([language isEqualToString:NLLanguageSimplifiedChinese] ||
        [language isEqualToString:NLLanguageTraditionalChinese]) {
        return @"zh";
    }
    return @"en";
}

static NLEmbedding *embeddingForCode(NSString *code) {
    NLLanguage language = [code isEqualToString:@"zh"]
        ? NLLanguageSimplifiedChinese
        : NLLanguageEnglish;
    NLEmbedding *embedding = [NLEmbedding sentenceEmbeddingForLanguage:language];
    return embedding ?: [NLEmbedding wordEmbeddingForLanguage:language];
}

static NSArray<NSNumber *> *vectorForText(NLEmbedding *embedding, NSString *text, NSString *code) {
    NSArray<NSNumber *> *direct = [embedding vectorForString:text];
    if (direct.count > 0) return direct;
    NSUInteger dimension = embedding.dimension;
    if (dimension == 0) return nil;
    NSMutableArray<NSNumber *> *sum = [NSMutableArray arrayWithCapacity:dimension];
    for (NSUInteger index = 0; index < dimension; index++) [sum addObject:@0.0];
    __block NSUInteger count = 0;
    NLTokenizer *tokenizer = [[NLTokenizer alloc] initWithUnit:NLTokenUnitWord];
    tokenizer.string = text;
    [tokenizer enumerateTokensInRange:NSMakeRange(0, text.length)
                           usingBlock:^(NSRange tokenRange, NLTokenizerAttributes flags, BOOL *stop) {
        if (count >= 256) {
            *stop = YES;
            return;
        }
        NSString *token = [text substringWithRange:tokenRange];
        NSArray<NSNumber *> *word = [embedding vectorForString:token];
        if (word.count != dimension) return;
        for (NSUInteger index = 0; index < dimension; index++) {
            sum[index] = @(sum[index].doubleValue + word[index].doubleValue);
        }
        count += 1;
    }];
    if (count == 0) return nil;
    for (NSUInteger index = 0; index < dimension; index++) {
        sum[index] = @(sum[index].doubleValue / (double)count);
    }
    return sum;
}

static NSDictionary *encodeText(NSString *text) {
    NSString *code = languageCode(text);
    NLEmbedding *embedding = embeddingForCode(code);
    NSArray<NSNumber *> *vector = embedding ? vectorForText(embedding, text, code) : nil;
    if (vector.count == 0) {
        return @{@"language": code, @"embedding": @""};
    }
    double norm = 0.0;
    for (NSNumber *value in vector) {
        double number = value.doubleValue;
        norm += number * number;
    }
    norm = sqrt(norm);
    if (norm <= 0.0) {
        return @{@"language": code, @"embedding": @""};
    }
    NSMutableData *data = [NSMutableData dataWithLength:vector.count];
    int8_t *bytes = data.mutableBytes;
    for (NSUInteger index = 0; index < vector.count; index++) {
        double normalized = vector[index].doubleValue / norm;
        long quantized = lround(fmax(-1.0, fmin(1.0, normalized)) * 127.0);
        bytes[index] = (int8_t)quantized;
    }
    return @{
        @"language": code,
        @"embedding": [data base64EncodedStringWithOptions:0],
    };
}

int main(void) {
    @autoreleasepool {
        NSData *input = [[NSFileHandle fileHandleWithStandardInput] readDataToEndOfFile];
        if (input.length == 0) return 0;
        NSError *error = nil;
        id payload = [NSJSONSerialization JSONObjectWithData:input options:0 error:&error];
        if (error || ![payload isKindOfClass:[NSArray class]]) return 2;
        NSMutableArray *results = [NSMutableArray array];
        for (id value in (NSArray *)payload) {
            NSString *text = [value isKindOfClass:[NSString class]] ? value : [value description];
            [results addObject:encodeText([text substringToIndex:MIN(text.length, 3000)])];
        }
        NSData *output = [NSJSONSerialization dataWithJSONObject:results options:0 error:&error];
        if (error || !output) return 3;
        [[NSFileHandle fileHandleWithStandardOutput] writeData:output];
    }
    return 0;
}
