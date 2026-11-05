#include "kernels.h"
#include "shaders.h"
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>
#include <cmath>
#include <stdexcept>

namespace armor {
struct MetalKernels::Impl {
    id<MTLDevice> device;
    id<MTLCommandQueue> queue;
    id<MTLComputePipelineState> norm, pointer, delta;
    Impl() {
        device=MTLCreateSystemDefaultDevice();
        if (!device) throw std::runtime_error("Metal device unavailable");
        queue=[device newCommandQueue];
        MTLCompileOptions* options=[MTLCompileOptions new];
        options.mathMode=MTLMathModeSafe;
        NSError* error=nil;
        id<MTLLibrary> library=[device newLibraryWithSource:[NSString stringWithUTF8String:armor_metal_source]
            options:options error:&error];
        if (!library || !queue) throw std::runtime_error("Metal initialization failed");
        auto pipeline=[&](NSString* name) {
            id<MTLComputePipelineState> p=[device newComputePipelineStateWithFunction:[library newFunctionWithName:name] error:&error];
            if (!p || p.threadExecutionWidth!=32 || p.maxTotalThreadsPerThreadgroup<256)
                throw std::runtime_error("unsupported Metal execution geometry");
            return p;
        };
        norm=pipeline(@"armor_rms"); pointer=pipeline(@"armor_pointer"); delta=pipeline(@"armor_delta");
    }
    id<MTLBuffer> buffer(const Floats& v) {
        id<MTLBuffer> b=[device newBufferWithBytes:v.data() length:v.size()*sizeof(float) options:MTLResourceStorageModeShared];
        if (!b) throw std::runtime_error("Metal allocation failed");
        return b;
    }
    id<MTLBuffer> output(size_t n) {
        id<MTLBuffer> b=[device newBufferWithLength:n*sizeof(float) options:MTLResourceStorageModeShared];
        if (!b) throw std::runtime_error("Metal allocation failed");
        return b;
    }
    Floats read(id<MTLBuffer> b) {
        const auto* p=static_cast<const float*>(b.contents);
        Floats out(p,p+b.length/sizeof(float));
        for (float f:out) if (!std::isfinite(f)) throw std::runtime_error("nonfinite Metal output");
        return out;
    }
    void finish(id<MTLCommandBuffer> cmd, id<MTLComputeCommandEncoder> e, size_t groups, size_t threads) {
        [e dispatchThreadgroups:MTLSizeMake(groups,1,1) threadsPerThreadgroup:MTLSizeMake(threads,1,1)];
        [e endEncoding]; [cmd commit]; [cmd waitUntilCompleted];
        if (cmd.status!=MTLCommandBufferStatusCompleted) throw std::runtime_error("Metal execution failed");
    }
};
MetalKernels::MetalKernels() { @autoreleasepool { impl_=std::make_unique<Impl>(); } }
MetalKernels::~MetalKernels()=default;
Floats MetalKernels::rms_norm(const Floats& x, const Floats& w, size_t width, float eps, float offset) {
    validate_norm(x,w,width,eps,offset);
    @autoreleasepool {
        auto& p=*impl_; auto bx=p.buffer(x), bw=p.buffer(w), by=p.output(x.size());
        id<MTLCommandBuffer> cmd=[p.queue commandBuffer];
        id<MTLComputeCommandEncoder> e=[cmd computeCommandEncoder];
        if (!cmd || !e) throw std::runtime_error("Metal command allocation failed");
        [e setComputePipelineState:p.norm]; [e setBuffer:bx offset:0 atIndex:0];
        [e setBuffer:bw offset:0 atIndex:1]; [e setBuffer:by offset:0 atIndex:2];
        uint32_t d=uint32_t(width);
        [e setBytes:&d length:sizeof(d) atIndex:3]; [e setBytes:&eps length:sizeof(eps) atIndex:4];
        [e setBytes:&offset length:sizeof(offset) atIndex:5]; p.finish(cmd,e,x.size()/width,256);
        return p.read(by);
    }
}
Floats MetalKernels::pointer_logits(const Floats& q, const Floats& k) {
    validate_pointer(q,k);
    @autoreleasepool {
        auto& p=*impl_; auto bq=p.buffer(q), bk=p.buffer(k), by=p.output(k.size()/q.size());
        id<MTLCommandBuffer> cmd=[p.queue commandBuffer]; id<MTLComputeCommandEncoder> e=[cmd computeCommandEncoder];
        if (!cmd || !e) throw std::runtime_error("Metal command allocation failed");
        [e setComputePipelineState:p.pointer]; [e setBuffer:bq offset:0 atIndex:0];
        [e setBuffer:bk offset:0 atIndex:1]; [e setBuffer:by offset:0 atIndex:2];
        uint32_t d=uint32_t(q.size()); [e setBytes:&d length:sizeof(d) atIndex:3];
        p.finish(cmd,e,k.size()/q.size(),32); return p.read(by);
    }
}
DeltaResult MetalKernels::delta_scan(const DeltaInput& in) {
    validate_delta(in);
    if (in.dim!=128) throw std::invalid_argument("Metal DeltaNet requires head dimension 128");
    @autoreleasepool {
        auto& p=*impl_;
        NSArray<id<MTLBuffer>>* buffers=@[p.buffer(in.q),p.buffer(in.k),p.buffer(in.v),
            p.buffer(in.decay),p.buffer(in.beta),p.buffer(in.state),p.output(in.state.size()),p.output(in.v.size())];
        id<MTLCommandBuffer> cmd=[p.queue commandBuffer]; id<MTLComputeCommandEncoder> e=[cmd computeCommandEncoder];
        if (!cmd || !e) throw std::runtime_error("Metal command allocation failed");
        [e setComputePipelineState:p.delta];
        for (size_t i=0;i<8;++i) [e setBuffer:buffers[i] offset:0 atIndex:i];
        uint32_t shape[]={uint32_t(in.tokens),uint32_t(in.key_heads),uint32_t(in.value_heads)};
        [e setBytes:shape length:sizeof(shape) atIndex:8];
        p.finish(cmd,e,in.value_heads*8,128); return {p.read(buffers[7]),p.read(buffers[6])};
    }
}
} // namespace armor
