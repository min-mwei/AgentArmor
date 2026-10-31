// The reference Metal backend: embedded shaders compiled at startup, weights uploaded once as
// bf16 buffers, one command buffer per forward (every layer's kernels encoded back to back), the heads on the CPU.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cmath>
#include <map>

#include "model.h"
#include "shaders.h"

namespace kai {
namespace {

struct EmbedParams { uint32_t L, d; float scale; };
struct MatmulParams { uint32_t M, N, K; float alpha; };
struct NormParams { uint32_t M, d; float eps; uint32_t has_weight; };
struct RopeParams { uint32_t L, H, D; float theta; };
struct AttnParams { uint32_t L, H, KVH, hd, window, is_global, full; };
struct CastParams { uint32_t n; };
struct GeluParams { uint32_t n; };
struct PleParams { uint32_t L, P, layers, layer; };
struct AddParams { uint32_t n; float scale; };

struct LayerB {
    id<MTLBuffer> q, k, v, o, gate, up, down, ple_gate, ple_proj;
    id<MTLBuffer> in_norm, post_attn, pre_ffw, post_ffw, q_norm, k_norm, ple_norm;
    float scalar; bool global; int hd, kvh; float theta;
};

class MetalEngine : public Engine {
  public:
    explicit MetalEngine(const std::string& dir) : st_(dir + "/model.safetensors"), cfg_(load_config(dir + "/config.json")) {
        dev_ = MTLCreateSystemDefaultDevice();
        if (!dev_) throw std::runtime_error("no Metal device");
        queue_ = [dev_ newCommandQueue];
        NSError* err = nil;
        MTLCompileOptions* opts = [MTLCompileOptions new];
        opts.mathMode = MTLMathModeSafe;   // exact float semantics, as the CPU path and MLX
        id<MTLLibrary> lib = [dev_ newLibraryWithSource:[NSString stringWithUTF8String:legacy_shader_source] options:opts error:&err];
        if (!lib) throw std::runtime_error(std::string("metal compile: ") + err.localizedDescription.UTF8String);
        for (const char* name : {"embed", "matmul_bf16", "matmul_sgm", "matmul_sgm_bf16", "attention_mma", "to_bf16", "rmsnorm", "rope", "attention", "gelu_mul", "gelu_mul_ple", "add_scale"}) {
            id<MTLFunction> fn = [lib newFunctionWithName:[NSString stringWithUTF8String:name]];
            if (!fn) throw std::runtime_error(std::string("missing kernel ") + name);
            id<MTLComputePipelineState> ps = [dev_ newComputePipelineStateWithFunction:fn error:&err];
            if (!ps) throw std::runtime_error(std::string("pipeline ") + name + ": " + err.localizedDescription.UTF8String);
            pipes_[name] = ps;
        }
        embed_ = upload(st_.get("text.embed_tokens.weight"));
        ple_proj_ = upload(st_.get("text.ple.per_layer_model_projection.weight"));
        ple_norm_ = upload_f32(st_.floats("text.ple.per_layer_projection_norm.weight"));
        final_norm_ = upload_f32(st_.floats("text.norm.weight"));
        out_proj_ = upload(st_.get("text.embedding_projection.weight"));
        for (int i = 0; i < cfg_.layers; ++i) {
            std::string p = "text.layers." + std::to_string(i) + ".";
            LayerB w;
            w.global = cfg_.is_global[i]; w.hd = w.global ? cfg_.global_head_dim : cfg_.head_dim;
            w.kvh = w.global ? cfg_.global_kv_heads : cfg_.kv_heads; w.theta = w.global ? cfg_.theta_global : cfg_.theta_sliding;
            w.q = upload(st_.get(p + "self_attn.q_proj.weight")); w.k = upload(st_.get(p + "self_attn.k_proj.weight"));
            w.v = upload(st_.get(p + "self_attn.v_proj.weight")); w.o = upload(st_.get(p + "self_attn.o_proj.weight"));
            w.gate = upload(st_.get(p + "mlp.gate_proj.weight")); w.up = upload(st_.get(p + "mlp.up_proj.weight")); w.down = upload(st_.get(p + "mlp.down_proj.weight"));
            w.ple_gate = upload(st_.get(p + "ple_block.per_layer_input_gate.weight")); w.ple_proj = upload(st_.get(p + "ple_block.per_layer_projection.weight"));
            w.in_norm = upload_f32(st_.floats(p + "input_layernorm.weight")); w.post_attn = upload_f32(st_.floats(p + "post_attention_layernorm.weight"));
            w.pre_ffw = upload_f32(st_.floats(p + "pre_feedforward_layernorm.weight")); w.post_ffw = upload_f32(st_.floats(p + "post_feedforward_layernorm.weight"));
            w.q_norm = upload_f32(st_.floats(p + "self_attn.q_norm.weight")); w.k_norm = upload_f32(st_.floats(p + "self_attn.k_norm.weight"));
            w.ple_norm = upload_f32(st_.floats(p + "ple_block.post_per_layer_input_norm.weight"));
            w.scalar = st_.floats(p + "layer_scalar")[0];
            layers_.push_back(w);
        }
        heads_.load(st_, cfg_);
    }
    std::string name() const override { return "metal"; }

    Response run(const Request& r, bool dump_states) override {
        auto t0 = std::chrono::steady_clock::now();
        @autoreleasepool {
            const uint32_t L = r.ids.size(), d = cfg_.hidden, I = cfg_.intermediate, P = cfg_.ple, H = cfg_.heads, NL = cfg_.layers;
            const int maxhd = cfg_.global_head_dim;
            id<MTLBuffer> ids = upload_i32(r.ids), seg = upload_i32(r.seg), pos = upload_i32(r.pos);
            id<MTLBuffer> h = act(L * d), x = act(L * d), ple = act((size_t)L * NL * P), q = act((size_t)L * H * maxhd);
            id<MTLBuffer> k = act((size_t)L * cfg_.kv_heads * maxhd), v = act((size_t)L * cfg_.kv_heads * maxhd), att = act((size_t)L * H * maxhd);
            id<MTLBuffer> g = act((size_t)L * I), u = act((size_t)L * I), pg = act((size_t)L * P);
            id<MTLBuffer> states = act((size_t)L * cfg_.d_read());
            // KAI_ATTN=mma: bf16 simdgroup-matrix attention (rows padded to a multiple of 8; padded rows zero)
            static const bool mma = std::getenv("KAI_ATTN") && std::string(std::getenv("KAI_ATTN")) == "mma";
            const uint32_t Lp = (L + 7) / 8 * 8;
            id<MTLBuffer> qb = nil, kb = nil, vb = nil, attp = nil;
            if (mma) {
                qb = [dev_ newBufferWithLength:(size_t)Lp * H * maxhd * 2 options:MTLResourceStorageModeShared];
                kb = [dev_ newBufferWithLength:(size_t)Lp * cfg_.kv_heads * maxhd * 2 options:MTLResourceStorageModeShared];
                vb = [dev_ newBufferWithLength:(size_t)Lp * cfg_.kv_heads * maxhd * 2 options:MTLResourceStorageModeShared];
                attp = act((size_t)Lp * H * maxhd);
                memset(qb.contents, 0, qb.length); memset(kb.contents, 0, kb.length); memset(vb.contents, 0, vb.length);
            }

            id<MTLCommandBuffer> cb = [queue_ commandBuffer];
            id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
            // KAI_PROFILE=2: one command buffer per stage, GPU time accumulated by stage label (sync overhead included)
            const bool staged = std::getenv("KAI_PROFILE") && std::string(std::getenv("KAI_PROFILE")) == "2";
            std::map<std::string, double> prof;
            auto sync = [&](const char* label) {
                if (!staged) return;
                [enc endEncoding]; [cb commit]; [cb waitUntilCompleted];
                prof[label] += (cb.GPUEndTime - cb.GPUStartTime) * 1000;
                cb = [queue_ commandBuffer]; enc = [cb computeCommandEncoder];
            };
            EmbedParams ep{L, d, std::sqrt((float)d)};
            dispatch1(enc, "embed", {ids, embed_, h}, &ep, sizeof ep, (size_t)L * d);
            matmul(enc, h, ple_proj_, ple, L, NL * P, d, 1.0f / std::sqrt((float)d));
            norm(enc, ple, ple, ple_norm_, L * NL, P);
            sync("embed+ple");
            for (uint32_t li = 0; li < NL; ++li) {
                const LayerB& w = layers_[li];
                const uint32_t hd = w.hd, kvh = w.kvh;
                norm(enc, h, x, w.in_norm, L, d);
                matmul(enc, x, w.q, q, L, H * hd, d); matmul(enc, x, w.k, k, L, kvh * hd, d); matmul(enc, x, w.v, v, L, kvh * hd, d);
                norm(enc, q, q, w.q_norm, L * H, hd); norm(enc, k, k, w.k_norm, L * kvh, hd); norm(enc, v, v, nil, L * kvh, hd);
                RopeParams rq{L, H, hd, w.theta}, rk{L, kvh, hd, w.theta};
                dispatch1(enc, "rope", {q, pos}, &rq, sizeof rq, (size_t)L * H * hd / 2);
                dispatch1(enc, "rope", {k, pos}, &rk, sizeof rk, (size_t)L * kvh * hd / 2);
                sync("qkv");
                AttnParams ap{L, H, kvh, hd, (uint32_t)cfg_.window, w.global ? 1u : 0u, cfg_.full_mask ? 1u : 0u};
                if (mma) {
                    CastParams cq{L * H * hd}, ck{L * kvh * hd};
                    dispatch1(enc, "to_bf16", {q, qb}, &cq, sizeof cq, cq.n);
                    dispatch1(enc, "to_bf16", {k, kb}, &ck, sizeof ck, ck.n);
                    dispatch1(enc, "to_bf16", {v, vb}, &ck, sizeof ck, ck.n);
                    [enc setComputePipelineState:pipes_["attention_mma"]];
                    bind(enc, {qb, kb, vb, attp, seg, pos}); [enc setBytes:&ap length:sizeof ap atIndex:6];
                    [enc dispatchThreadgroups:MTLSizeMake((Lp + 31) / 32, H, 1) threadsPerThreadgroup:MTLSizeMake(128, 1, 1)];
                } else {
                    [enc setComputePipelineState:pipes_["attention"]];
                    bind(enc, {q, k, v, att, seg, pos}); [enc setBytes:&ap length:sizeof ap atIndex:6];
                    [enc dispatchThreadgroups:MTLSizeMake((L + 7) / 8, H, 1) threadsPerThreadgroup:MTLSizeMake(256, 1, 1)];
                }
                sync("attention");
                matmul(enc, mma ? attp : att, w.o, x, L, d, H * hd);
                sync("o_proj");
                norm(enc, x, x, w.post_attn, L, d);
                add(enc, h, x, L * d, 1.0f);
                norm(enc, h, x, w.pre_ffw, L, d);
                sync("norms+adds");
                matmul(enc, x, w.gate, g, L, I, d); matmul(enc, x, w.up, u, L, I, d);
                GeluParams gp{L * I};
                dispatch1(enc, "gelu_mul", {g, u}, &gp, sizeof gp, (size_t)L * I);
                matmul(enc, g, w.down, x, L, d, I);
                sync("mlp");
                norm(enc, x, x, w.post_ffw, L, d);
                add(enc, h, x, L * d, 1.0f);
                sync("norms+adds");
                matmul(enc, h, w.ple_gate, pg, L, P, d);
                PleParams pp{L, P, NL, li};
                dispatch1(enc, "gelu_mul_ple", {pg, ple}, &pp, sizeof pp, (size_t)L * P);
                matmul(enc, pg, w.ple_proj, x, L, d, P);
                norm(enc, x, x, w.ple_norm, L, d);
                add(enc, h, x, L * d, w.scalar);
                sync("ple_block");
            }
            norm(enc, h, x, final_norm_, L, d);
            if (cfg_.read == "projected") matmul(enc, x, out_proj_, states, L, cfg_.embedding_dim, d);
            else states = x;
            [enc endEncoding];
            [cb commit];
            [cb waitUntilCompleted];
            if (cb.error) throw std::runtime_error(std::string("metal: ") + cb.error.localizedDescription.UTF8String);
            if (std::getenv("KAI_PROFILE")) fprintf(stderr, "profile gpu %.1f ms, wall to here %.1f ms\n", (cb.GPUEndTime - cb.GPUStartTime) * 1000,
                                                      std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count() * 1000);
            for (auto& kv : prof) fprintf(stderr, "profile %-14s %7.1f ms\n", kv.first.c_str(), kv.second);
            Response out;
            const float* sp = static_cast<const float*>(states.contents);
            heads_.apply(r, sp, out);
            if (dump_states) out.states.assign(sp, sp + (size_t)L * cfg_.d_read());
            out.seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
            return out;
        }
    }

  private:
    SafeTensors st_;
    Config cfg_;
    id<MTLDevice> dev_;
    id<MTLCommandQueue> queue_;
    std::map<std::string, id<MTLComputePipelineState>> pipes_;
    id<MTLBuffer> embed_, ple_proj_, ple_norm_, final_norm_, out_proj_;
    std::vector<LayerB> layers_;
    Heads heads_;

    id<MTLBuffer> upload(const Tensor& t) {
        if (t.dtype != DType::BF16) throw std::runtime_error("expected bf16 weight");
        return [dev_ newBufferWithBytes:t.data length:t.bytes options:MTLResourceStorageModeShared];
    }
    id<MTLBuffer> upload_f32(const std::vector<float>& v) { return [dev_ newBufferWithBytes:v.data() length:v.size() * 4 options:MTLResourceStorageModeShared]; }
    id<MTLBuffer> upload_i32(const std::vector<int32_t>& v) { return [dev_ newBufferWithBytes:v.data() length:v.size() * 4 options:MTLResourceStorageModeShared]; }
    id<MTLBuffer> act(size_t n) { return [dev_ newBufferWithLength:n * 4 options:MTLResourceStorageModeShared]; }

    void bind(id<MTLComputeCommandEncoder> enc, std::initializer_list<id<MTLBuffer>> bufs) {
        int i = 0; for (id<MTLBuffer> b : bufs) [enc setBuffer:b offset:0 atIndex:i++];
    }
    void dispatch1(id<MTLComputeCommandEncoder> enc, const char* name, std::initializer_list<id<MTLBuffer>> bufs, const void* params, size_t psize, size_t n) {
        id<MTLComputePipelineState> ps = pipes_[name];
        [enc setComputePipelineState:ps];
        bind(enc, bufs);
        [enc setBytes:params length:psize atIndex:(int)bufs.size()];
        NSUInteger w = std::min<NSUInteger>(ps.maxTotalThreadsPerThreadgroup, 256);
        [enc dispatchThreadgroups:MTLSizeMake((n + w - 1) / w, 1, 1) threadsPerThreadgroup:MTLSizeMake(w, 1, 1)];
    }
    void matmul(id<MTLComputeCommandEncoder> enc, id<MTLBuffer> x, id<MTLBuffer> W, id<MTLBuffer> y, uint32_t M, uint32_t N, uint32_t K, float alpha = 1.0f) {
        MatmulParams p{M, N, K, alpha};
        // KAI_MATMUL: "tiled" (default; fp32 scalar FMAs), "sgm" (fp32 simdgroup matrices), "bf16" (bfloat simdgroup matrices)
        static const std::string variant = getenv("KAI_MATMUL") ? getenv("KAI_MATMUL") : "tiled";
        const bool tiled = variant == "tiled";
        [enc setComputePipelineState:pipes_[tiled ? "matmul_bf16" : (variant == "bf16" ? "matmul_sgm_bf16" : "matmul_sgm")]];
        bind(enc, {x, W, y}); [enc setBytes:&p length:sizeof p atIndex:3];
        if (tiled) [enc dispatchThreadgroups:MTLSizeMake((N + 63) / 64, (M + 63) / 64, 1) threadsPerThreadgroup:MTLSizeMake(16, 16, 1)];
        else [enc dispatchThreadgroups:MTLSizeMake((N + 63) / 64, (M + 63) / 64, 1) threadsPerThreadgroup:MTLSizeMake(128, 1, 1)];
    }
    void norm(id<MTLComputeCommandEncoder> enc, id<MTLBuffer> x, id<MTLBuffer> y, id<MTLBuffer> w, uint32_t M, uint32_t d) {
        NormParams p{M, d, cfg_.eps, w ? 1u : 0u};
        [enc setComputePipelineState:pipes_["rmsnorm"]];
        bind(enc, {x, y, w ? w : x}); [enc setBytes:&p length:sizeof p atIndex:3];
        [enc dispatchThreadgroups:MTLSizeMake(M, 1, 1) threadsPerThreadgroup:MTLSizeMake(256, 1, 1)];
    }
    void add(id<MTLComputeCommandEncoder> enc, id<MTLBuffer> h, id<MTLBuffer> x, uint32_t n, float scale) {
        AddParams p{n, scale};
        dispatch1(enc, "add_scale", {h, x}, &p, sizeof p, n);
    }
};

}  // namespace

std::unique_ptr<Engine> make_metal_engine(const std::string& dir) { return std::make_unique<MetalEngine>(dir); }

}  // namespace kai
