#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include "execution.h"
#include "shaders.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <mutex>

namespace kai {
bool metal_available() {
    @autoreleasepool {
        id<MTLDevice> device = MTLCreateSystemDefaultDevice();
        return device != nil && device.hasUnifiedMemory;
    }
}
namespace {

struct GemmParams { uint32_t M, N, K; float alpha; uint32_t epilogue; };
struct RowParams { uint32_t rows, width; float eps, scale; };
struct RopeNormParams { uint32_t rows, heads, width; float eps; };
struct AttentionParams { uint32_t rows, heads, kv_heads, head_dim, window, global; };
static_assert(sizeof(GemmParams) == 20 && sizeof(RowParams) == 16 && sizeof(AttentionParams) == 24);

struct LayerBuffers {
    id<MTLBuffer> in_norm, post_attn, pre_ffw, post_ffw, q_norm, k_norm, ple_norm;
};

class MetalOptimized : public Engine {
  public:
    explicit MetalOptimized(const std::string& dir) : weights_(dir) {
        @autoreleasepool {
            const Config& c = weights_.config;
            if (c.hidden != 512 || (c.head_dim != 256 && c.head_dim != 512) ||
                (c.global_head_dim != 256 && c.global_head_dim != 512))
                throw std::runtime_error("metal-opt supports the EmbeddingGemma-2 widths");
            device_ = MTLCreateSystemDefaultDevice();
            if (!device_ || !device_.hasUnifiedMemory) throw std::runtime_error("metal-opt requires unified-memory Metal");
            queue_ = [device_ newCommandQueue];
            if (!queue_) throw std::runtime_error("cannot create Metal queue");
            library_ = compile(optimized_shader_source, false);
            const SafeTensors& file = weights_.file;
            weight_buffer_ = [device_ newBufferWithBytesNoCopy:const_cast<uint8_t*>(file.mapping_data())
                                                   length:file.mapping_length() options:MTLResourceStorageModeShared deallocator:nil];
            if (!weight_buffer_) throw std::runtime_error("cannot wrap the weight mapping");
            weight_buffer_.label = @"mapped weights";
            ple_norm_ = upload(weights_.ple_norm, "PLE norm");
            final_norm_ = upload(weights_.final_norm, "final norm");
            ones_ = upload(std::vector<float>(std::max({c.hidden, c.head_dim, c.global_head_dim, c.ple}), 1), "unit norm");
            for (const auto& w : weights_.layers) {
                LayerBuffers b;
                b.in_norm = upload(w.in_norm, "input norm"); b.post_attn = upload(w.post_attn, "post-attention norm");
                b.pre_ffw = upload(w.pre_ffw, "FFN norm"); b.post_ffw = upload(w.post_ffw, "post-FFN norm");
                b.q_norm = upload(w.q_norm, "Q norm"); b.k_norm = upload(w.k_norm, "K norm"); b.ple_norm = upload(w.ple_norm, "PLE output norm");
                layers_.push_back(b);
            }
            std::string slice = std::getenv("KAI_OPT_ATTN_SLICE") ? std::getenv("KAI_OPT_ATTN_SLICE") : "auto";
            if (slice != "auto" && slice != "full" && slice != "128" && slice != "256")
                throw std::runtime_error("invalid KAI_OPT_ATTN_SLICE");
            attention_slice_ = slice == "auto" ? 0 : slice == "full" ? 512 : std::stoi(slice);
            configure_tensor();
            validate_fragments();
        }
    }

    std::string name() const override { return "metal-opt"; }

    Response run(const Request& r, bool dump_states) override {
        auto batch = run_requests({&r}, dump_states);
        return std::move(batch.responses[0]);
    }

    BatchResponse run_batch(const std::vector<Request>& requests, bool dump_states) override {
        std::vector<const Request*> refs;
        for (const auto& request : requests) refs.push_back(&request);
        return run_requests(refs, dump_states);
    }

  private:
    BatchResponse run_requests(const std::vector<const Request*>& requests, bool dump_states) {
        std::lock_guard<std::mutex> lock(mutex_);
        const auto start = std::chrono::steady_clock::now();
        @autoreleasepool {
            const Config& c = weights_.config;
            BatchLayout layout(requests, c);
            const uint32_t n = uint32_t(layout.ids.size()), d = c.hidden, p = c.ple;
            reserve(n);
            const uint32_t queries = tensor_attention_ || n < 64 ? 16 : 32;
            auto local_work = layout.work(queries, false), global_work = layout.work(queries, true);
            write(ids_, layout.ids.data(), n * sizeof(int32_t));
            write(pos_, layout.pos.data(), n * sizeof(int32_t));
            write(local_work_, local_work.data(), local_work.size() * sizeof(AttentionWork));
            write(global_work_, global_work.data(), global_work.size() * sizeof(AttentionWork));
            const int positions = *std::max_element(layout.pos.begin(), layout.pos.end()) + 1;
            local_table_ = rope(local_rope_, local_table_, positions, c.head_dim, c.theta_sliding);
            global_table_ = rope(global_rope_, global_table_, positions, c.global_head_dim, c.theta_global);

            id<MTLCommandBuffer> cb = [queue_ commandBuffer];
            id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
            if (!cb || !enc) throw std::runtime_error("cannot encode Metal forward");
            cb.label = @"Kai optimized forward";
            [enc useResource:weight_buffer_ usage:MTLResourceUsageRead];
            bind(enc, "embed_opt", {ids_, weight_buffer_, initial_, h_});
            [enc setBuffer:weight_buffer_ offset:offset(weights_.embed) atIndex:1];
            const uint32_t embed_shape[] = {n, d};
            [enc setBytes:embed_shape length:sizeof embed_shape atIndex:4];
            threads(enc, size_t(n) * d);
            norm(enc, h_, x_, layers_[0].in_norm, n, d, c.eps);
            for (int li = 0; li < c.layers; ++li) {
                const LayerWeights& w = weights_.layers[li];
                const LayerBuffers& b = layers_[li];
                gemm(enc, x_, w.q, q_, n, c.heads * w.hd);
                gemm(enc, x_, w.k, k_, n, w.kvh * w.hd);
                gemm(enc, x_, w.v, v_, n, w.kvh * w.hd);
                gemm(enc, initial_, weights_.ple_proj, ple_, n, p, li * p, 1 / std::sqrt(float(d)));
                id<MTLBuffer> table = w.global ? global_table_ : local_table_;
                norm_rope(enc, q_, b.q_norm, table, n, c.heads, w.hd, c.eps);
                norm_rope(enc, k_, b.k_norm, table, n, w.kvh, w.hd, c.eps);
                norm(enc, v_, v_, ones_, n * w.kvh, w.hd, c.eps);
                norm(enc, ple_, ple_, ple_norm_, n, p, c.eps);
                attention(enc, w, n, queries, w.global ? global_work.size() : local_work.size());
                gemm(enc, att_, w.o, tmp_, n, d);
                residual(enc, b.post_attn, b.pre_ffw, n, d, c.eps);
                gemm(enc, x_, w.gate, gate_, n, c.intermediate);
                gemm(enc, x_, w.up, up_, n, c.intermediate, 0, 1, 1, gate_);
                gemm(enc, up_, w.down, tmp_, n, d);
                residual(enc, b.post_ffw, nil, n, d, c.eps);
                gemm(enc, h_, w.ple_gate, gate_, n, p, 0, 1, 2, ple_);
                gemm(enc, gate_, w.ple_proj, tmp_, n, d);
                id<MTLBuffer> next = li + 1 < c.layers ? layers_[li + 1].in_norm : final_norm_;
                residual(enc, b.ple_norm, next, n, d, c.eps, w.scalar);
            }
            id<MTLBuffer> states = x_;
            if (c.read == "projected") { gemm(enc, x_, weights_.output, states_, n, c.embedding_dim); states = states_; }
            [enc endEncoding];
            const auto encoded = std::chrono::steady_clock::now();
            [cb commit];
            [cb waitUntilCompleted];
            completed(cb);
            const auto heads_begin = std::chrono::steady_clock::now();
            BatchResponse result;
            const float* state_data = static_cast<const float*>(states.contents);
            for (size_t i = 0; i < requests.size(); ++i) {
                Response out;
                const float* row = state_data + size_t(layout.offsets[i]) * c.d_read();
                weights_.heads.apply(*requests[i], row, out, true);
                if (dump_states) out.states.assign(row, row + requests[i]->ids.size() * c.d_read());
                result.responses.push_back(std::move(out));
            }
            result.seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
            for (auto& out : result.responses) out.seconds = result.seconds;
            if (std::getenv("KAI_PROFILE"))
                fprintf(stderr, "profile encode %.3f ms, gpu %.3f ms, heads %.3f ms, wall %.3f ms\n",
                        std::chrono::duration<double>(encoded - start).count() * 1000,
                        (cb.GPUEndTime - cb.GPUStartTime) * 1000,
                        std::chrono::duration<double>(std::chrono::steady_clock::now() - heads_begin).count() * 1000,
                        result.seconds * 1000);
            return result;
        }
    }

    Weights weights_;
    std::mutex mutex_;
    id<MTLDevice> device_;
    id<MTLCommandQueue> queue_;
    id<MTLLibrary> library_, tensor_library_;
    std::map<std::string, id<MTLComputePipelineState>> pipelines_;
    std::vector<LayerBuffers> layers_;
    id<MTLBuffer> weight_buffer_, ple_norm_, final_norm_, ones_;
    id<MTLBuffer> ids_, pos_, initial_, h_, x_, tmp_, ple_, q_, k_, v_, att_, gate_, up_, states_, cast_;
    id<MTLBuffer> local_work_, global_work_, local_table_, global_table_;
    RopeTable local_rope_, global_rope_;
    uint32_t capacity_ = 0;
    uint32_t attention_slice_ = 0;
    bool tensor_bf16_ = false, tensor_matmul_ = false, tensor_attention_ = false, tensor_attention_bf16_ = false;

    id<MTLLibrary> compile(const char* source, bool tensor) {
        MTLCompileOptions* opts = [MTLCompileOptions new];
        if (@available(macOS 15.0, *)) opts.mathMode = MTLMathModeSafe;
        opts.languageVersion = MTLLanguageVersion3_1;
        if (tensor) {
            if (@available(macOS 26.0, *)) opts.languageVersion = MTLLanguageVersion4_0;
        }
        NSError* error = nil;
        id<MTLLibrary> lib = [device_ newLibraryWithSource:[NSString stringWithUTF8String:source] options:opts error:&error];
        if (!lib) throw std::runtime_error(std::string("Metal shader compilation: ") + error.localizedDescription.UTF8String);
        return lib;
    }

    void configure_tensor() {
        std::string mode = std::getenv("KAI_OPT_MATMUL") ? std::getenv("KAI_OPT_MATMUL") : "matrix";
        if (mode != "matrix" && mode != "tensor" && mode != "tensor-bf16" && mode != "auto")
            throw std::runtime_error("invalid KAI_OPT_MATMUL");
        std::string attn = std::getenv("KAI_OPT_ATTENTION") ? std::getenv("KAI_OPT_ATTENTION") : "matrix";
        if (attn != "matrix" && attn != "tensor" && attn != "tensor-bf16" && attn != "auto")
            throw std::runtime_error("invalid KAI_OPT_ATTENTION");
        if (mode == "matrix" && attn == "matrix") return;
        const bool required = (mode != "matrix" && mode != "auto") || (attn != "matrix" && attn != "auto");
        bool capable = false;
        if (@available(macOS 26.3, *)) capable = [device_ supportsFamily:MTLGPUFamilyApple10];
        if (!capable) {
            if (required) throw std::runtime_error("TensorOps requires Apple10 and macOS 26.3 or later");
            return;
        }
        tensor_bf16_ = mode == "tensor-bf16";
        tensor_matmul_ = mode != "matrix";
        tensor_attention_ = attn != "matrix";
        tensor_attention_bf16_ = attn == "tensor-bf16";
        try {
            tensor_library_ = compile(tensor_shader_source, true);
            if (tensor_matmul_) (void)pipeline(tensor_name());
            if (tensor_attention_) { (void)pipeline(tensor_attention_name(256)); (void)pipeline(tensor_attention_name(512)); }
        } catch (const std::exception&) {
            if (required) throw;
            tensor_library_ = nil; tensor_matmul_ = false; tensor_attention_ = false;
        }
    }

    id<MTLComputePipelineState> pipeline(const std::string& name) {
        auto found = pipelines_.find(name);
        if (found != pipelines_.end()) return found->second;
        bool tensor = name.rfind("gemm_tensor_", 0) == 0 || name.rfind("attention_tensor_", 0) == 0 || name == "tensor_fragment_layout";
        id<MTLLibrary> lib = tensor ? tensor_library_ : library_;
        id<MTLFunction> fn = [lib newFunctionWithName:[NSString stringWithUTF8String:name.c_str()]];
        if (!fn) throw std::runtime_error("missing Metal kernel " + name);
        NSError* error = nil;
        id<MTLComputePipelineState> p = [device_ newComputePipelineStateWithFunction:fn error:&error];
        if (!p) throw std::runtime_error(std::string("Metal pipeline ") + name + ": " + error.localizedDescription.UTF8String);
        pipelines_[name] = p;
        return p;
    }

    id<MTLComputePipelineState> bind(id<MTLComputeCommandEncoder> enc, const std::string& name,
                                    std::initializer_list<id<MTLBuffer>> buffers) {
        id<MTLComputePipelineState> p = pipeline(name);
        [enc setComputePipelineState:p];
        uint32_t i = 0;
        for (id<MTLBuffer> b : buffers) [enc setBuffer:b offset:0 atIndex:i++];
        return p;
    }

    void groups(id<MTLComputeCommandEncoder> enc, MTLSize grid, uint32_t count, const std::string& name) {
        if (pipeline(name).maxTotalThreadsPerThreadgroup < count) throw std::runtime_error("Metal threadgroup limit for " + name);
        [enc dispatchThreadgroups:grid threadsPerThreadgroup:MTLSizeMake(count, 1, 1)];
    }

    void threads(id<MTLComputeCommandEncoder> enc, size_t count) {
        [enc dispatchThreads:MTLSizeMake(count, 1, 1) threadsPerThreadgroup:MTLSizeMake(128, 1, 1)];
    }

    id<MTLBuffer> buffer(size_t bytes, const char* label) {
        id<MTLBuffer> b = [device_ newBufferWithLength:std::max<size_t>(bytes, 16) options:MTLResourceStorageModeShared];
        if (!b) throw std::runtime_error(std::string("cannot allocate ") + label);
        b.label = [NSString stringWithUTF8String:label];
        std::memset(b.contents, 0, b.length);
        return b;
    }

    static void write(id<MTLBuffer> b, const void* data, size_t bytes) {
        if (bytes > b.length) throw std::runtime_error("workspace buffer capacity exceeded");
        if (bytes) std::memcpy(b.contents, data, bytes);
    }

    id<MTLBuffer> upload(const std::vector<float>& v, const char* label) {
        id<MTLBuffer> b = buffer(v.size() * sizeof(float), label);
        write(b, v.data(), v.size() * sizeof(float));
        return b;
    }

    NSUInteger offset(const Tensor* t, size_t row = 0) const {
        return NSUInteger(t->data - weights_.file.mapping_data()) + row * size_t(t->shape[1]) * 2;
    }

    static void completed(id<MTLCommandBuffer> cb) {
        if (cb.error) throw std::runtime_error(std::string("Metal execution: ") + cb.error.localizedDescription.UTF8String);
        if (cb.status != MTLCommandBufferStatusCompleted) throw std::runtime_error("Metal command did not complete");
    }

    void validate_fragments() {
        validate_fragment("fragment_layout", 64);
        if (tensor_attention_) validate_fragment("tensor_fragment_layout", 512);
    }

    void validate_fragment(const std::string& name, int count) {
        id<MTLComputePipelineState> p = pipeline(name);
        if (p.threadExecutionWidth != 32) throw std::runtime_error("unsupported Metal SIMD width");
        id<MTLBuffer> output = buffer(count * sizeof(float), "fragment layout");
        id<MTLCommandBuffer> cb = [queue_ commandBuffer];
        id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
        bind(enc, name, {output});
        groups(enc, MTLSizeMake(1, 1, 1), 32, name);
        [enc endEncoding]; [cb commit]; [cb waitUntilCompleted]; completed(cb);
        const float* values = static_cast<const float*>(output.contents);
        for (int i = 0; i < count; ++i) if (values[i] != float(i)) throw std::runtime_error("unsupported matrix fragment ownership: " + name);
    }

    void reserve(uint32_t rows) {
        if (rows <= capacity_) return;
        capacity_ = (rows + 63) / 64 * 64;
        const Config& c = weights_.config;
        const size_t count = size_t(capacity_) + 32;
        size_t qdim = 0, kvdim = 0;
        for (const auto& w : weights_.layers) {
            qdim = std::max(qdim, size_t(c.heads) * w.hd); kvdim = std::max(kvdim, size_t(w.kvh) * w.hd);
        }
        auto floats = [&](size_t width, const char* name) { return buffer(count * width * 4, name); };
        initial_ = floats(c.hidden, "initial"); h_ = floats(c.hidden, "residual"); x_ = floats(c.hidden, "normalized");
        tmp_ = floats(c.hidden, "block output"); ple_ = floats(c.ple, "PLE");
        q_ = floats(qdim, "Q"); k_ = floats(kvdim, "K"); v_ = floats(kvdim, "V"); att_ = floats(qdim, "attention");
        gate_ = floats(std::max(c.intermediate, c.ple), "gate"); up_ = floats(c.intermediate, "up");
        states_ = floats(c.d_read(), "states");
        ids_ = buffer(count * 4, "tokens"); pos_ = buffer(count * 4, "positions");
        local_work_ = buffer(count * sizeof(AttentionWork), "local attention work");
        global_work_ = buffer(count * sizeof(AttentionWork), "global attention work");
        if (tensor_matmul_ && tensor_bf16_)
            cast_ = buffer(count * std::max({size_t(c.hidden), size_t(c.intermediate), size_t(c.ple), qdim}) * 2, "bf16 operands");
    }

    id<MTLBuffer> rope(RopeTable& table, id<MTLBuffer> gpu, int positions, int width, float theta) {
        if (table.positions >= positions) return gpu;
        table.reserve((positions + 63) / 64 * 64, width, theta);
        return upload(table.values, "RoPE");
    }

    void norm(id<MTLComputeCommandEncoder> enc, id<MTLBuffer> x, id<MTLBuffer> out, id<MTLBuffer> weight,
              uint32_t rows, uint32_t width, float eps) {
        bind(enc, "norm_opt", {x, out, weight});
        RowParams p{rows, width, eps, 1}; [enc setBytes:&p length:sizeof p atIndex:3];
        groups(enc, MTLSizeMake(rows, 1, 1), 32, "norm_opt");
    }

    void norm_rope(id<MTLComputeCommandEncoder> enc, id<MTLBuffer> x, id<MTLBuffer> weight, id<MTLBuffer> table,
                   uint32_t rows, uint32_t heads, uint32_t width, float eps) {
        bind(enc, "norm_rope_opt", {x, weight, pos_, table});
        RopeNormParams p{rows, heads, width, eps}; [enc setBytes:&p length:sizeof p atIndex:4];
        groups(enc, MTLSizeMake(rows * heads, 1, 1), 32, "norm_rope_opt");
    }

    void residual(id<MTLComputeCommandEncoder> enc, id<MTLBuffer> weight, id<MTLBuffer> next,
                  uint32_t rows, uint32_t width, float eps, float scale = 1) {
        bind(enc, "norm_residual_opt", {h_, tmp_, weight, x_, next ? next : ones_});
        RowParams p{rows, width, eps, scale}; [enc setBytes:&p length:sizeof p atIndex:5];
        uint32_t normalize_next = next != nil; [enc setBytes:&normalize_next length:sizeof normalize_next atIndex:6];
        groups(enc, MTLSizeMake(rows, 1, 1), 32, "norm_residual_opt");
    }

    void gemm(id<MTLComputeCommandEncoder> enc, id<MTLBuffer> x, const Tensor* weight, id<MTLBuffer> out,
              uint32_t rows, uint32_t outputs, uint32_t first = 0, float alpha = 1,
              uint32_t epilogue = 0, id<MTLBuffer> aux = nil) {
        const uint32_t inputs = uint32_t(weight->shape[1]);
        GemmParams p{rows, outputs, inputs, alpha, epilogue};
        if (tensor_matmul_ && outputs % 64 == 0 && rows >= 64) {
            if (tensor_bf16_) {
                uint32_t count[] = {rows * inputs, rows * inputs};
                bind(enc, "cast_bf16", {x, cast_}); [enc setBytes:count length:sizeof count atIndex:2]; threads(enc, count[1]);
            }
            const std::string name = tensor_name();
            bind(enc, name, {tensor_bf16_ ? cast_ : x, weight_buffer_, out});
            [enc setBuffer:weight_buffer_ offset:offset(weight, first) atIndex:1];
            [enc setBytes:&p length:sizeof p atIndex:3];
            groups(enc, MTLSizeMake(outputs / 64, (rows + 63) / 64, 1), 128, name);
            if (epilogue) {
                bind(enc, "gemm_epilogue", {out, aux}); [enc setBytes:&p length:sizeof p atIndex:2]; threads(enc, size_t(rows) * outputs);
            }
            return;
        }
        uint32_t bm = rows <= 16 ? 8 : rows <= 128 ? 32 : 64;
        uint32_t bn = bm == 8 ? 32 : 64, sg = bm == 8 ? 2 : 4;
        const std::string name = "gemm_" + std::to_string(bm) + "x" + std::to_string(bn);
        bind(enc, name, {x, weight_buffer_, out});
        [enc setBuffer:weight_buffer_ offset:offset(weight, first) atIndex:1];
        [enc setBuffer:(aux ? aux : out) offset:0 atIndex:4];
        [enc setBytes:&p length:sizeof p atIndex:3];
        groups(enc, MTLSizeMake((outputs + bn - 1) / bn, (rows + bm - 1) / bm, 1), sg * 32, name);
    }

    std::string tensor_name() const { return tensor_bf16_ ? "gemm_tensor_bf16" : "gemm_tensor_fp32"; }
    std::string tensor_attention_name(int hd) const {
        return std::string(tensor_attention_bf16_ ? "attention_tensor_bf16_" : "attention_tensor_") + std::to_string(hd);
    }

    void attention(id<MTLComputeCommandEncoder> enc, const LayerWeights& w, uint32_t rows, uint32_t queries, size_t work_count) {
        const Config& c = weights_.config;
        AttentionParams p{rows, uint32_t(c.heads), uint32_t(w.kvh), uint32_t(w.hd), uint32_t(c.window), uint32_t(w.global)};
        if (tensor_attention_) {
            const std::string name = tensor_attention_name(w.hd);
            bind(enc, name, {q_, k_, v_, att_, w.global ? global_work_ : local_work_, pos_});
            [enc setBytes:&p length:sizeof p atIndex:6];
            groups(enc, MTLSizeMake(work_count, c.heads, 1), w.hd / 128 * 32, name);
            return;
        }
        uint32_t slice = w.hd;
        if (attention_slice_) slice = std::min(slice, attention_slice_);
        else while (slice > 128 && work_count * c.heads * (w.hd / slice) < 128) slice /= 2;
        std::string name = "attention_" + std::to_string(slice) + (rows >= 512 ? "_register" : "");
        bind(enc, name, {q_, k_, v_, att_, w.global ? global_work_ : local_work_, pos_});
        [enc setBytes:&p length:sizeof p atIndex:6]; [enc setBytes:&queries length:sizeof queries atIndex:7];
        groups(enc, MTLSizeMake(work_count, c.heads, w.hd / slice), queries / 8 * 32, name);
    }
};

}  // namespace

std::unique_ptr<Engine> make_metal_optimized_engine(const std::string& dir) { return std::make_unique<MetalOptimized>(dir); }

}  // namespace kai
