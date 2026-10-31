// The CPU backend: hand-written kernels (NEON where it pays: the bf16-weight matmul and the attention dot products),
// float32 activations, bf16 weights read straight from the memory-mapped checkpoint. Parallel over rows / heads with
// libdispatch on Apple platforms, serial elsewhere. Every operation mirrors train/kai/model.py line by line.
#include <chrono>
#include <cstdlib>
#include <map>
#include <cstdio>
#include <cmath>
#include <cstring>
#include <memory>
#include <vector>

#include "model.h"

#if defined(__ARM_NEON)
#include <arm_neon.h>
#endif
#if defined(__APPLE__)
#include <dispatch/dispatch.h>
#endif

namespace kai {
namespace {

template <class F>
void parallel_for(int n, F f) {
#if defined(__APPLE__)
    dispatch_apply(n, dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^(size_t i) { f((int)i); });
#else
    for (int i = 0; i < n; ++i) f(i);
#endif
}

// y[m, n] = sum_k x[m, k] * W[n, k], W bf16 [N, K] row-major (torch / MLX Linear layout), x f32 [M, K].
// A task owns 16 output columns (16 weight rows, 16-64 KB: L1-resident); it walks the activation rows four at a time, so
// each x block is read once per task and each W row is widened (a 16-bit shift) once per x block, in NEON FMA registers.
void matmul_bf16(const float* x, int M, int K, const uint16_t* W, int N, float* y) {
    const int NB = 16;
    parallel_for((N + NB - 1) / NB, [&](int task) {
        int n0 = task * NB, n1 = std::min(N, n0 + NB), nb = n1 - n0;
        std::vector<float> wf((size_t)nb * K);                   // the task's weight rows widened once
        for (int b = 0; b < nb; ++b) {
            const uint16_t* w = W + (size_t)(n0 + b) * K; float* o = wf.data() + (size_t)b * K;
            int k = 0;
#if defined(__ARM_NEON)
            for (; k + 8 <= K; k += 8) {
                uint16x8_t h = vld1q_u16(w + k);
                vst1q_f32(o + k, vreinterpretq_f32_u32(vshll_n_u16(vget_low_u16(h), 16)));
                vst1q_f32(o + k + 4, vreinterpretq_f32_u32(vshll_high_n_u16(h, 16)));
            }
#endif
            for (; k < K; ++k) o[k] = bf16_to_f32(w[k]);
        }
        int m = 0;
        for (; m + 4 <= M; m += 4) {
            const float* xr[4] = {x + (size_t)m * K, x + (size_t)(m + 1) * K, x + (size_t)(m + 2) * K, x + (size_t)(m + 3) * K};
            int n = 0;
#if defined(__ARM_NEON)
            for (; n + 4 <= nb; n += 4) {   // 4 x 4 micro-kernel: 8 loads per 16 FMAs, 16 independent accumulators
                const float* wr[4] = {wf.data() + (size_t)n * K, wf.data() + (size_t)(n + 1) * K, wf.data() + (size_t)(n + 2) * K, wf.data() + (size_t)(n + 3) * K};
                float32x4_t acc[4][4];
                for (int a = 0; a < 4; ++a) for (int b = 0; b < 4; ++b) acc[a][b] = vdupq_n_f32(0);
                int k = 0;
                for (; k + 4 <= K; k += 4) {
                    float32x4_t xv[4], wv[4];
                    for (int a = 0; a < 4; ++a) xv[a] = vld1q_f32(xr[a] + k);
                    for (int b = 0; b < 4; ++b) wv[b] = vld1q_f32(wr[b] + k);
                    for (int a = 0; a < 4; ++a) for (int b = 0; b < 4; ++b) acc[a][b] = vfmaq_f32(acc[a][b], xv[a], wv[b]);
                }
                for (int a = 0; a < 4; ++a) for (int b = 0; b < 4; ++b) {
                    float s = vaddvq_f32(acc[a][b]);
                    for (int kk = k; kk < K; ++kk) s += wr[b][kk] * xr[a][kk];
                    y[(size_t)(m + a) * N + n0 + n + b] = s;
                }
            }
#endif
            for (; n < nb; ++n) {
                const float* w = wf.data() + (size_t)n * K;
                for (int a = 0; a < 4; ++a) {
                    float s = 0;
                    for (int k = 0; k < K; ++k) s += w[k] * xr[a][k];
                    y[(size_t)(m + a) * N + n0 + n] = s;
                }
            }
        }
        for (; m < M; ++m) {
            const float* xr = x + (size_t)m * K;
            for (int n = 0; n < nb; ++n) {
                const float* w = wf.data() + (size_t)n * K;
                float s = 0;
#if defined(__ARM_NEON)
                float32x4_t a0 = vdupq_n_f32(0), a1 = a0;
                int k = 0;
                for (; k + 8 <= K; k += 8) { a0 = vfmaq_f32(a0, vld1q_f32(w + k), vld1q_f32(xr + k)); a1 = vfmaq_f32(a1, vld1q_f32(w + k + 4), vld1q_f32(xr + k + 4)); }
                s = vaddvq_f32(vaddq_f32(a0, a1));
                for (; k < K; ++k) s += w[k] * xr[k];
#else
                for (int k = 0; k < K; ++k) s += w[k] * xr[k];
#endif
                y[(size_t)m * N + n0 + n] = s;
            }
        }
    });
}

// RMSNorm over the last dimension of x [M, d] with an optional scale w [d] (f32), in place or into y.
void rmsnorm(const float* x, float* y, int M, int d, const float* w, float eps) {
    parallel_for(M, [&](int m) {
        const float* r = x + (size_t)m * d; float* o = y + (size_t)m * d;
        float ss = 0; for (int i = 0; i < d; ++i) ss += r[i] * r[i];
        float inv = 1.0f / std::sqrt(ss / d + eps);
        if (w) for (int i = 0; i < d; ++i) o[i] = r[i] * inv * w[i];
        else for (int i = 0; i < d; ++i) o[i] = r[i] * inv;
    });
}

inline float gelu_tanh(float x) { return 0.5f * x * (1.0f + std::tanh(0.7978845608028654f * (x + 0.044715f * x * x * x))); }

// Rotate-half RoPE in place on x [L, H, D] for positions pos [L], per head.
void rope(float* x, int L, int H, int D, const int32_t* pos, float theta) {
    std::vector<float> inv(D / 2);
    for (int i = 0; i < D / 2; ++i) inv[i] = 1.0f / std::pow(theta, (2.0f * i) / D);
    parallel_for(L, [&](int l) {
        for (int h = 0; h < H; ++h) {
            float* v = x + ((size_t)l * H + h) * D;
            for (int i = 0; i < D / 2; ++i) {
                float a = (float)pos[l] * inv[i], c = std::cos(a), s = std::sin(a);
                float x1 = v[i], x2 = v[i + D / 2];
                v[i] = x1 * c - x2 * s; v[i + D / 2] = x2 * c + x1 * s;
            }
        }
    });
}

struct LayerW {
    const uint16_t *in_norm, *post_attn, *pre_ffw, *post_ffw, *q, *k, *v, *o, *gate, *up, *down, *ple_gate, *ple_proj, *ple_norm;
    float scalar;
    std::vector<float> in_norm_f, post_attn_f, pre_ffw_f, post_ffw_f, q_norm_f, k_norm_f, ple_norm_f;
    bool global; int hd, kvh; float theta;
};

class CpuEngine : public Engine {
  public:
    explicit CpuEngine(const std::string& dir) : st_(dir + "/model.safetensors"), cfg_(load_config(dir + "/config.json")) {
        embed_ = st_.get("text.embed_tokens.weight").bf16();
        ple_proj_ = st_.get("text.ple.per_layer_model_projection.weight").bf16();
        ple_norm_ = st_.floats("text.ple.per_layer_projection_norm.weight");
        final_norm_ = st_.floats("text.norm.weight");
        out_proj_ = st_.get("text.embedding_projection.weight").bf16();
        for (int i = 0; i < cfg_.layers; ++i) {
            std::string p = "text.layers." + std::to_string(i) + ".";
            LayerW w;
            w.global = cfg_.is_global[i]; w.hd = w.global ? cfg_.global_head_dim : cfg_.head_dim;
            w.kvh = w.global ? cfg_.global_kv_heads : cfg_.kv_heads; w.theta = w.global ? cfg_.theta_global : cfg_.theta_sliding;
            w.q = st_.get(p + "self_attn.q_proj.weight").bf16(); w.k = st_.get(p + "self_attn.k_proj.weight").bf16();
            w.v = st_.get(p + "self_attn.v_proj.weight").bf16(); w.o = st_.get(p + "self_attn.o_proj.weight").bf16();
            w.gate = st_.get(p + "mlp.gate_proj.weight").bf16(); w.up = st_.get(p + "mlp.up_proj.weight").bf16(); w.down = st_.get(p + "mlp.down_proj.weight").bf16();
            w.ple_gate = st_.get(p + "ple_block.per_layer_input_gate.weight").bf16(); w.ple_proj = st_.get(p + "ple_block.per_layer_projection.weight").bf16();
            w.in_norm_f = st_.floats(p + "input_layernorm.weight"); w.post_attn_f = st_.floats(p + "post_attention_layernorm.weight");
            w.pre_ffw_f = st_.floats(p + "pre_feedforward_layernorm.weight"); w.post_ffw_f = st_.floats(p + "post_feedforward_layernorm.weight");
            w.q_norm_f = st_.floats(p + "self_attn.q_norm.weight"); w.k_norm_f = st_.floats(p + "self_attn.k_norm.weight");
            w.ple_norm_f = st_.floats(p + "ple_block.post_per_layer_input_norm.weight");
            w.scalar = st_.floats(p + "layer_scalar")[0];
            layers_.push_back(std::move(w));
        }
        heads_.load(st_, cfg_);
    }
    std::string name() const override { return "cpu"; }

    Response run(const Request& r, bool dump_states) override {
        auto t0 = std::chrono::steady_clock::now();
        const bool profile = std::getenv("KAI_PROFILE") != nullptr;
        std::map<std::string, double> prof; auto tp = t0;
        auto tick = [&](const char* what) { if (!profile) return; auto now = std::chrono::steady_clock::now(); prof[what] += std::chrono::duration<double>(now - tp).count(); tp = now; };
        const int L = r.ids.size(), d = cfg_.hidden, I = cfg_.intermediate, P = cfg_.ple, H = cfg_.heads;
        const float eps = cfg_.eps;
        std::vector<float> h((size_t)L * d), x((size_t)L * d), tmp((size_t)L * d), ple((size_t)L * cfg_.layers * P);
        // embeddings and per-layer inputs
        const float scale = std::sqrt((float)d);
        for (int l = 0; l < L; ++l) {
            const uint16_t* row = embed_ + (size_t)r.ids[l] * d;
            for (int i = 0; i < d; ++i) h[(size_t)l * d + i] = bf16_to_f32(row[i]) * scale;
        }
        matmul_bf16(h.data(), L, d, ple_proj_, cfg_.layers * P, ple.data());
        const float ple_scale = 1.0f / std::sqrt((float)d);
        for (auto& v : ple) v *= ple_scale;
        rmsnorm(ple.data(), ple.data(), L * cfg_.layers, P, ple_norm_.data(), eps);
        tick("embed+ple");

        std::vector<float> q, k, v, att((size_t)L * H * cfg_.global_head_dim), g((size_t)L * I), u((size_t)L * I), pg((size_t)L * P);
        for (int li = 0; li < cfg_.layers; ++li) {
            const LayerW& w = layers_[li];
            const int hd = w.hd, kvh = w.kvh, groups = H / kvh;
            // attention block
            rmsnorm(h.data(), x.data(), L, d, w.in_norm_f.data(), eps);
            q.assign((size_t)L * H * hd, 0); k.assign((size_t)L * kvh * hd, 0); v.assign((size_t)L * kvh * hd, 0);
            matmul_bf16(x.data(), L, d, w.q, H * hd, q.data());
            matmul_bf16(x.data(), L, d, w.k, kvh * hd, k.data());
            matmul_bf16(x.data(), L, d, w.v, kvh * hd, v.data());
            rmsnorm(q.data(), q.data(), L * H, hd, w.q_norm_f.data(), eps);
            rmsnorm(k.data(), k.data(), L * kvh, hd, w.k_norm_f.data(), eps);
            rmsnorm(v.data(), v.data(), L * kvh, hd, nullptr, eps);
            rope(q.data(), L, H, hd, r.pos.data(), w.theta);
            rope(k.data(), L, kvh, hd, r.pos.data(), w.theta);
            tick("qkv");
            // scores over the allowed keys only (block mask from seg; |pos_i - pos_j| <= window on sliding layers), scale 1
            parallel_for(L, [&](int i) {
                // one task per query token: its H heads share the mask and (grouped) the key and value rows
                std::vector<float> s((size_t)H * L);
                float mxs[8]; for (int hh = 0; hh < H; ++hh) mxs[hh] = -INFINITY;
                for (int j = 0; j < L; ++j) {
                    bool ok = allowed(r, i, j, cfg_.full_mask) && (w.global || std::abs(r.pos[i] - r.pos[j]) <= cfg_.window);
                    if (!ok) { for (int hh = 0; hh < H; ++hh) s[(size_t)hh * L + j] = -INFINITY; continue; }
                    for (int hh = 0; hh < H; ++hh) {
                        const float* qi = q.data() + ((size_t)i * H + hh) * hd;
                        const float* kj = k.data() + ((size_t)j * kvh + hh / groups) * hd;
                        float sc;
#if defined(__ARM_NEON)
                        float32x4_t a0 = vdupq_n_f32(0), a1 = a0, a2 = a0, a3 = a0;   // four chains: hd is a multiple of 16
                        for (int t = 0; t < hd; t += 16) {
                            a0 = vfmaq_f32(a0, vld1q_f32(qi + t), vld1q_f32(kj + t));
                            a1 = vfmaq_f32(a1, vld1q_f32(qi + t + 4), vld1q_f32(kj + t + 4));
                            a2 = vfmaq_f32(a2, vld1q_f32(qi + t + 8), vld1q_f32(kj + t + 8));
                            a3 = vfmaq_f32(a3, vld1q_f32(qi + t + 12), vld1q_f32(kj + t + 12));
                        }
                        sc = vaddvq_f32(vaddq_f32(vaddq_f32(a0, a1), vaddq_f32(a2, a3)));
#else
                        float acc = 0; for (int t = 0; t < hd; ++t) acc += qi[t] * kj[t]; sc = acc;
#endif
                        s[(size_t)hh * L + j] = sc;
                        if (sc > mxs[hh]) mxs[hh] = sc;
                    }
                }
                for (int hh = 0; hh < H; ++hh) {
                    float* sh = s.data() + (size_t)hh * L;
                    double z = 0;
                    for (int j = 0; j < L; ++j) { if (sh[j] == -INFINITY) continue; sh[j] = std::exp(sh[j] - mxs[hh]); z += sh[j]; }
                    float inv = (float)(1.0 / z);
                    float* o = att.data() + ((size_t)i * H + hh) * hd;
#if defined(__ARM_NEON)
                    float32x4_t acc[32];                                  // 32 vectors = 128 lanes per block of the head dim
                    const int nv = hd / 4;                                // 64 (sliding) or 128 (global) vectors: in blocks of 32
                    for (int t0 = 0; t0 < nv; t0 += 32) {
                        int nb = std::min(32, nv - t0);
                        for (int b = 0; b < nb; ++b) acc[b] = vdupq_n_f32(0);
                        for (int j = 0; j < L; ++j) {
                            if (sh[j] == -INFINITY || sh[j] == 0.f) continue;
                            float32x4_t p = vdupq_n_f32(sh[j] * inv);
                            const float* vj = v.data() + ((size_t)j * kvh + hh / groups) * hd + t0 * 4;
                            for (int b = 0; b < nb; ++b) acc[b] = vfmaq_f32(acc[b], p, vld1q_f32(vj + b * 4));
                        }
                        for (int b = 0; b < nb; ++b) vst1q_f32(o + (t0 + b) * 4, acc[b]);
                    }
#else
                    std::fill(o, o + hd, 0.f);
                    for (int j = 0; j < L; ++j) {
                        if (sh[j] == -INFINITY || sh[j] == 0.f) continue;
                        float p = sh[j] * inv; const float* vj = v.data() + ((size_t)j * kvh + hh / groups) * hd;
                        for (int t = 0; t < hd; ++t) o[t] += p * vj[t];
                    }
#endif
                }
            });
            tick("attention");
            matmul_bf16(att.data(), L, H * hd, w.o, d, x.data());
            rmsnorm(x.data(), x.data(), L, d, w.post_attn_f.data(), eps);
            for (size_t i = 0; i < h.size(); ++i) h[i] += x[i];
            tick("o_proj");
            // MLP block
            rmsnorm(h.data(), x.data(), L, d, w.pre_ffw_f.data(), eps);
            matmul_bf16(x.data(), L, d, w.gate, I, g.data());
            matmul_bf16(x.data(), L, d, w.up, I, u.data());
            for (size_t i = 0; i < g.size(); ++i) g[i] = gelu_tanh(g[i]) * u[i];
            matmul_bf16(g.data(), L, I, w.down, d, x.data());
            rmsnorm(x.data(), x.data(), L, d, w.post_ffw_f.data(), eps);
            for (size_t i = 0; i < h.size(); ++i) h[i] += x[i];
            tick("mlp");
            // per-layer-embedding block, then the layer scalar
            matmul_bf16(h.data(), L, d, w.ple_gate, P, pg.data());
            for (int l = 0; l < L; ++l) {
                const float* pl = ple.data() + ((size_t)l * cfg_.layers + li) * P;
                for (int i = 0; i < P; ++i) pg[(size_t)l * P + i] = gelu_tanh(pg[(size_t)l * P + i]) * pl[i];
            }
            matmul_bf16(pg.data(), L, P, w.ple_proj, d, x.data());
            rmsnorm(x.data(), x.data(), L, d, w.ple_norm_f.data(), eps);
            for (size_t i = 0; i < h.size(); ++i) h[i] = (h[i] + x[i]) * w.scalar;
            tick("ple_block");
        }
        rmsnorm(h.data(), x.data(), L, d, final_norm_.data(), eps);
        std::vector<float> states;
        if (cfg_.read == "projected") { states.resize((size_t)L * cfg_.embedding_dim); matmul_bf16(x.data(), L, d, out_proj_, cfg_.embedding_dim, states.data()); }
        else states = x;
        tick("final");
        Response out;
        heads_.apply(r, states.data(), out);
        tick("heads");
        if (dump_states) out.states = states;
        out.seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        if (profile) for (auto& [k, v] : prof) fprintf(stderr, "profile %-12s %8.1f ms\n", k.c_str(), v * 1000);
        return out;
    }

  private:
    SafeTensors st_;
    Config cfg_;
    const uint16_t *embed_, *ple_proj_, *out_proj_;
    std::vector<float> ple_norm_, final_norm_;
    std::vector<LayerW> layers_;
    Heads heads_;
};

}  // namespace

std::unique_ptr<Engine> make_cpu_engine(const std::string& dir, int) { return std::make_unique<CpuEngine>(dir); }

}  // namespace kai
