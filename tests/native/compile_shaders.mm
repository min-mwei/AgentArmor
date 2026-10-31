#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <cstdio>
#include "shaders.h"

// Compile and create pipelines without loading weights or submitting GPU work.
int main() {
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        if (!device) return 77;
        for (int variant = 0; variant < 3; ++variant) {
            if (variant == 2) {
                if (@available(macOS 26.3, *)) { if (![device supportsFamily:MTLGPUFamilyApple10]) continue; }
                else continue;
            }
            const char* source = variant == 0 ? kai::legacy_shader_source :
                                 variant == 1 ? kai::optimized_shader_source : kai::tensor_shader_source;
            MTLCompileOptions* options = [MTLCompileOptions new];
            if (@available(macOS 15.0, *)) options.mathMode = MTLMathModeSafe;
            options.languageVersion = MTLLanguageVersion3_1;
            if (variant == 2) {
                if (@available(macOS 26.0, *)) options.languageVersion = MTLLanguageVersion4_0;
            }
            NSError* error = nil;
            id<MTLLibrary> library = [device newLibraryWithSource:[NSString stringWithUTF8String:source]
                                                        options:options error:&error];
            if (!library) { fprintf(stderr, "%s\n", error.localizedDescription.UTF8String); return 1; }
            for (NSString* name in library.functionNames) {
                id<MTLFunction> function = [library newFunctionWithName:name];
                if (![device newComputePipelineStateWithFunction:function error:&error]) {
                    fprintf(stderr, "%s: %s\n", name.UTF8String, error.localizedDescription.UTF8String); return 1;
                }
            }
            printf("shader library %d: %lu pipelines compiled, no GPU dispatch\n", variant, (unsigned long)library.functionNames.count);
        }
    }
}
