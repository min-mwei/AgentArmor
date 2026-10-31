#include "execution.h"

#include <chrono>
#include <cstdlib>
#include <mutex>
#include <thread>

#if defined(__ARM_NEON)
#include <arm_neon.h>
#endif
#if defined(__APPLE__)
#include <dispatch/dispatch.h>
#endif
#ifdef KAI_ACCELERATE
#include <Accelerate/Accelerate.h>
#endif

namespace kai {
namespace {

template <class F>
void parallel_for(int n, F fn) {
#if defined(__APPLE__)
    dispatch_apply(n, dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^(size_t i) { fn(int(i)); });
#else
    for (int i = 0; i < n; ++i) fn(i);
#endif
}

float dot(const float* a, const float* b, int n) {
    int i = 0;
    float sum = 0;
#if defined(__ARM_NEON)
    float32x4_t s0 = vdupq_n_f32(0), s1 = s0, s2 = s0, s3 = s0;
    for (; i + 16 <= n; i += 16) {
        s0 = vfmaq_f32(s0, vld1q_f32(a + i), vld1q_f32(b + i));
        s1 = vfmaq_f32(s1, vld1q_f32(a + i + 4), vld1q_f32(b + i + 4));
        s2 = vfmaq_f32(s2, vld1q_f32(a + i + 8), vld1q_f32(b + i + 8));
        s3 = vfmaq_f32(s3, vld1q_f32(a + i + 12), vld1q_f32(b + i + 12));
    }
    sum = vaddvq_f32(vaddq_f32(vaddq_f32(s0, s1), vaddq_f32(s2, s3)));
#endif
    for (; i < n; ++i) sum += a[i] * b[i];
    return sum;
}

[[maybe_unused]] float gelu(float x) { return 0.5f * x * (1 + std::tanh(0.7978845608028654f * (x + 0.044715f * x * x * x))); }

void gelu_multiply(const float* gate, const float* factor, float* out, int count) {
    constexpr int block = 1024;
    parallel_for((count + block - 1) / block, [&](int task) {
        int begin = task * block;
        const int n = std::min(block, count - begin);
#ifdef KAI_ACCELERATE
        float arguments[block], values[block];
        for (int i = 0; i < n; ++i) {
            float x = gate[begin + i];
            arguments[i] = 0.7978845608028654f * (x + 0.044715f * x * x * x);
        }
        vvtanhf(values, arguments, &n);
        for (int i = 0; i < n; ++i) out[begin + i] = 0.5f * gate[begin + i] * (1 + values[i]) * factor[begin + i];
#else
        for (int i = 0; i < n; ++i) out[begin + i] = gelu(gate[begin + i]) * factor[begin + i];
#endif
    });
}

#if defined(__ARM_NEON)
template <int Lane>
void outer_lane(const uint16_t* w, const float32x4_t (&x)[4], float32x4_t (&sum)[4][2]) {
    uint16x8_t bits = vld1q_u16(w);
    float32x4_t lo = vreinterpretq_f32_u32(vshll_n_u16(vget_low_u16(bits), 16));
    float32x4_t hi = vreinterpretq_f32_u32(vshll_high_n_u16(bits, 16));
    for (int row = 0; row < 4; ++row) {
        sum[row][0] = vfmaq_laneq_f32(sum[row][0], lo, x[row], Lane);
        sum[row][1] = vfmaq_laneq_f32(sum[row][1], hi, x[row], Lane);
    }
}
#endif

// Eight output columns are contiguous for each K. Packing happens once, without widening the token table.
struct Matrix {
    int rows, cols;
    std::vector<uint16_t> packed;
    std::vector<float> floats;

    Matrix(const Tensor& t, bool blas) : rows(int(t.shape[0])), cols(int(t.shape[1])) {
        if (blas) {
            floats.resize(size_t(rows) * cols);
            for (size_t i = 0; i < floats.size(); ++i) floats[i] = bf16_to_f32(t.bf16()[i]);
            return;
        }
        packed.resize(size_t((rows + 7) / 8) * cols * 8);
        for (int n = 0; n < rows; ++n)
            for (int k = 0; k < cols; ++k)
                packed[(size_t(n / 8) * cols + k) * 8 + n % 8] = t.bf16()[size_t(n) * cols + k];
    }

    void apply(const float* x, float* y, int m, int outputs, int first = 0, float alpha = 1) const {
#ifdef KAI_ACCELERATE
        if (!floats.empty()) {
            cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasTrans, m, outputs, cols, alpha, x, cols,
                        floats.data() + size_t(first) * cols, cols, 0, y, outputs);
            return;
        }
#endif
        if (first % 8) throw std::runtime_error("packed matrix slice must start on eight columns");
        constexpr int block_m = 32;
        const int tiles_m = (m + block_m - 1) / block_m;
        const int tiles_n = (outputs + 7) / 8;
        const int tiles = tiles_m * tiles_n;
        // Hunch's eg2cpu uses flop-sized task groups and lane FMA to amortize scheduling and A loads.
        const uint64_t flops = uint64_t(2) * m * outputs * cols;
        static const unsigned threads = std::max(1u, std::thread::hardware_concurrency());
        const uint64_t target = std::max<uint64_t>(1, std::min<uint64_t>(3 * threads, flops >> 21));
        const int tasks = int(std::min<uint64_t>(tiles, target));
        auto apply_tile = [&](int tile) {
            int m0 = tile / tiles_n * block_m, n0 = tile % tiles_n * 8;
            int end = std::min(m0 + block_m, m), width = std::min(8, outputs - n0);
            const uint16_t* w = packed.data() + size_t((first + n0) / 8) * cols * 8;
            int row = m0;
#if defined(__ARM_NEON)
            for (; row + 4 <= end && width == 8; row += 4) {
                float32x4_t sums[4][2];
                for (auto& pair : sums) for (auto& sum : pair) sum = vdupq_n_f32(0);
                const float* xr = x + size_t(row) * cols;
                int k = 0;
                for (; k + 4 <= cols; k += 4) {
                    const float32x4_t inputs[] = {vld1q_f32(xr + k), vld1q_f32(xr + cols + k),
                                                 vld1q_f32(xr + 2 * cols + k), vld1q_f32(xr + 3 * cols + k)};
                    outer_lane<0>(w + size_t(k) * 8, inputs, sums);
                    outer_lane<1>(w + size_t(k + 1) * 8, inputs, sums);
                    outer_lane<2>(w + size_t(k + 2) * 8, inputs, sums);
                    outer_lane<3>(w + size_t(k + 3) * 8, inputs, sums);
                }
                for (; k < cols; ++k) {
                    uint16x8_t bits = vld1q_u16(w + size_t(k) * 8);
                    float32x4_t lo = vreinterpretq_f32_u32(vshll_n_u16(vget_low_u16(bits), 16));
                    float32x4_t hi = vreinterpretq_f32_u32(vshll_high_n_u16(bits, 16));
                    for (int r = 0; r < 4; ++r) {
                        sums[r][0] = vfmaq_n_f32(sums[r][0], lo, xr[r * cols + k]);
                        sums[r][1] = vfmaq_n_f32(sums[r][1], hi, xr[r * cols + k]);
                    }
                }
                float* out = y + size_t(row) * outputs + n0;
                for (int r = 0; r < 4; ++r) {
                    vst1q_f32(out + r * outputs, vmulq_n_f32(sums[r][0], alpha));
                    vst1q_f32(out + r * outputs + 4, vmulq_n_f32(sums[r][1], alpha));
                }
            }
#endif
            for (; row < end; ++row) {
                float sums[8] = {};
                for (int k = 0; k < cols; ++k)
                    for (int n = 0; n < width; ++n)
                        sums[n] += x[size_t(row) * cols + k] * bf16_to_f32(w[size_t(k) * 8 + n]);
                for (int n = 0; n < width; ++n) y[size_t(row) * outputs + n0 + n] = sums[n] * alpha;
            }
        };
        parallel_for(tasks, [&](int task) {
            const int end = int(int64_t(task + 1) * tiles / tasks);
            for (int tile = int(int64_t(task) * tiles / tasks); tile < end; ++tile) apply_tile(tile);
        });
    }
};

void norm(const float* x, float* y, int rows, int d, const float* w, float eps, float scale = 1) {
    parallel_for(rows, [&](int row) {
        const float* in = x + size_t(row) * d;
        float* out = y + size_t(row) * d;
        float inv = 1 / std::sqrt(dot(in, in, d) * scale * scale / d + eps);
        for (int i = 0; i < d; ++i) out[i] = in[i] * scale * inv * (w ? w[i] : 1);
    });
}

void norm_rope(float* x, int rows, int heads, const float* w, const int32_t* pos, const RopeTable& table, float eps) {
    int d = table.width;
    parallel_for(rows * heads, [&](int row) {
        float* in = x + size_t(row) * d;
        float inv = 1 / std::sqrt(dot(in, in, d) / d + eps);
        const float* cs = table.values.data() + size_t(pos[row / heads]) * d;
        for (int i = 0; i < d / 2; ++i) {
            float a = in[i] * inv * w[i], b = in[i + d / 2] * inv * w[i + d / 2];
            in[i] = a * cs[2 * i] - b * cs[2 * i + 1];
            in[i + d / 2] = b * cs[2 * i] + a * cs[2 * i + 1];
        }
    });
}

void residual(float* h, const float* t, const float* w, float* out, const float* next, int rows, int d, float eps, float scale = 1) {
    parallel_for(rows, [&](int row) {
        float* hr = h + size_t(row) * d;
        const float* tr = t + size_t(row) * d;
        float inv = 1 / std::sqrt(dot(tr, tr, d) / d + eps);
        for (int i = 0; i < d; ++i) hr[i] = (hr[i] + tr[i] * inv * w[i]) * scale;
        if (!out) return;
        float inv2 = 1 / std::sqrt(dot(hr, hr, d) / d + eps);
        float* dest = out + size_t(row) * d;
        for (int i = 0; i < d; ++i) dest[i] = hr[i] * inv2 * next[i];
    });
}

void attention(int rows, const BatchLayout& layout, const float* q, const float* k, const float* v, float* out,
               int heads, int kvh, int hd, bool global) {
    parallel_for(rows * heads, [&](int task) {
        const int row = task / heads, h = task % heads, kh = h / (heads / kvh);
        const float* qi = q + size_t(task) * hd;
        float* dest = out + size_t(task) * hd;
        std::fill(dest, dest + hd, 0);
        float maximum = -INFINITY;
        double denominator = 0;
        KeyRanges ranges = layout.keys(row, global);
        for (Range range : {ranges.state, ranges.branch}) {
            for (uint32_t begin = range.begin; begin < range.end; begin += 32) {
                const int count = int(std::min(begin + 32, range.end) - begin);
                float scores[32], block_max = -INFINITY;
                for (int j = 0; j < count; ++j) {
                    scores[j] = dot(qi, k + ((size_t(begin + j) * kvh + kh) * hd), hd);
                    block_max = std::max(block_max, scores[j]);
                }
                float next_max = std::max(maximum, block_max);
                float alpha = std::exp(maximum - next_max);
                for (int t = 0; t < hd; ++t) dest[t] *= alpha;
                denominator *= alpha;
                for (int j = 0; j < count; ++j) scores[j] -= next_max;
#ifdef KAI_ACCELERATE
                vvexpf(scores, scores, &count);
#else
                for (int j = 0; j < count; ++j) scores[j] = std::exp(scores[j]);
#endif
                for (int j = 0; j < count; ++j) {
                    float probability = scores[j];
                    denominator += probability;
                    const float* value = v + ((size_t(begin + j) * kvh + kh) * hd);
                    int t = 0;
#if defined(__ARM_NEON)
                    for (; t + 4 <= hd; t += 4)
                        vst1q_f32(dest + t, vfmaq_n_f32(vld1q_f32(dest + t), vld1q_f32(value + t), probability));
#endif
                    for (; t < hd; ++t) dest[t] += probability * value[t];
                }
                maximum = next_max;
            }
        }
        float inv = denominator > 0 ? float(1 / denominator) : 0;
        for (int t = 0; t < hd; ++t) dest[t] *= inv;
    });
}

#ifdef KAI_ACCELERATE
// Hunch blocks text attention into QK / softmax / PV products. Here the two key intervals preserve Kai's mask.
void attention_blocked(const BatchLayout& layout, const std::vector<AttentionWork>& work,
                       const float* q, const float* k, const float* v, float* out,
                       int heads, int kvh, int hd, bool global, std::vector<std::vector<float>>& scratch) {
    const int q_stride = heads * hd, kv_stride = kvh * hd;
    // Each head owns one reusable score tile. Accelerate controls parallelism inside each matrix product.
    parallel_for(heads, [&](int h) {
        const int kh = h / (heads / kvh);
        float* scores = scratch[h].data();
        for (const AttentionWork& tile : work) {
            const int rows = int(tile.query_end - tile.query_begin);
            const Range ranges[] = {tile.keys.state, tile.keys.branch};
            const int keys = int(ranges[0].end - ranges[0].begin + ranges[1].end - ranges[1].begin);
            const float* query = q + size_t(tile.query_begin) * q_stride + h * hd;
            float* dest = out + size_t(tile.query_begin) * q_stride + h * hd;
            int column = 0;
            for (Range range : ranges) {
                const int count = int(range.end - range.begin);
                if (!count) continue;
                cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasTrans, rows, count, hd, 1, query, q_stride,
                            k + size_t(range.begin) * kv_stride + kh * hd, kv_stride, 0, scores + column, keys);
                column += count;
            }
            for (int i = 0; i < rows; ++i) {
                const KeyRanges valid = layout.keys(tile.query_begin + i, global);
                const Range allowed[] = {valid.state, valid.branch};
                float* row = scores + size_t(i) * keys;
                float maximum = -INFINITY;
                column = 0;
                for (int interval = 0; interval < 2; ++interval)
                    for (uint32_t key = ranges[interval].begin; key < ranges[interval].end; ++key, ++column) {
                        if (key < allowed[interval].begin || key >= allowed[interval].end) row[column] = -INFINITY;
                        maximum = std::max(maximum, row[column]);
                    }
                for (int j = 0; j < keys; ++j) row[j] -= maximum;
                vvexpf(row, row, &keys);
                double sum = 0;
                for (int j = 0; j < keys; ++j) sum += row[j];
                const float inv = float(1 / sum);
                for (int j = 0; j < keys; ++j) row[j] *= inv;
            }
            column = 0;
            for (Range range : ranges) {
                const int count = int(range.end - range.begin);
                if (!count) continue;
                cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasNoTrans, rows, hd, count, 1, scores + column, keys,
                            v + size_t(range.begin) * kv_stride + kh * hd, kv_stride, column ? 1 : 0, dest, q_stride);
                column += count;
            }
        }
    });
}
#endif

class CpuOptimized : public Engine {
  public:
    explicit CpuOptimized(const std::string& dir) : weights_(dir) {
        std::string mode = std::getenv("KAI_CPU_MATMUL") ? std::getenv("KAI_CPU_MATMUL") : "auto";
        if (mode != "auto" && mode != "neon" && mode != "accelerate") throw std::runtime_error("invalid KAI_CPU_MATMUL");
#ifdef KAI_ACCELERATE
        bool blas = mode != "neon";
#else
        if (mode == "accelerate") throw std::runtime_error("Accelerate was not built");
        bool blas = false;
#endif
        std::string attn = std::getenv("KAI_CPU_ATTENTION") ? std::getenv("KAI_CPU_ATTENTION") : "online";
        if (attn != "online" && attn != "blocked") throw std::runtime_error("invalid KAI_CPU_ATTENTION");
        blocked_attention_ = attn == "blocked";
#ifndef KAI_ACCELERATE
        if (blocked_attention_) throw std::runtime_error("blocked attention requires Accelerate");
#endif
        auto cache = [&](const Tensor* t) { matrices_.try_emplace(t, *t, blas); };
        cache(weights_.ple_proj);
        if (weights_.config.read == "projected") cache(weights_.output);
        for (const auto& w : weights_.layers)
            for (const Tensor* t : {w.q, w.k, w.v, w.o, w.gate, w.up, w.down, w.ple_gate, w.ple_proj}) cache(t);
    }

    std::string name() const override { return "cpu-opt"; }

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
        const Config& c = weights_.config;
        const BatchLayout layout(requests, c, true);
        const int n = int(layout.ids.size()), d = c.hidden, p = c.ple;
        reserve(n);
        std::vector<AttentionWork> local_work, global_work;
        if (blocked_attention_) { local_work = layout.work(32, false); global_work = layout.work(32, true); }
        const int positions = *std::max_element(layout.pos.begin(), layout.pos.end()) + 1;
        local_rope_.reserve(positions, c.head_dim, c.theta_sliding);
        global_rope_.reserve(positions, c.global_head_dim, c.theta_global);
        float scale = std::sqrt(float(d));
        for (int row = 0; row < n; ++row)
            for (int j = 0; j < d; ++j)
                initial_[size_t(row) * d + j] = bf16_to_f32(weights_.embed->bf16()[size_t(layout.ids[row]) * d + j]) * scale;
        std::copy_n(initial_.data(), size_t(n) * d, h_.data());
        norm(h_.data(), x_.data(), n, d, weights_.layers[0].in_norm.data(), c.eps);
        for (int li = 0; li < c.layers; ++li) {
            const LayerWeights& w = weights_.layers[li];
            const RopeTable& rope = w.global ? global_rope_ : local_rope_;
            project(x_, w.q, q_, n, c.heads * w.hd);
            project(x_, w.k, k_, n, w.kvh * w.hd);
            project(x_, w.v, v_, n, w.kvh * w.hd);
            norm_rope(q_.data(), n, c.heads, w.q_norm.data(), layout.pos.data(), rope, c.eps);
            norm_rope(k_.data(), n, w.kvh, w.k_norm.data(), layout.pos.data(), rope, c.eps);
            norm(v_.data(), v_.data(), n * w.kvh, w.hd, nullptr, c.eps);
#ifdef KAI_ACCELERATE
            if (blocked_attention_)
                attention_blocked(layout, w.global ? global_work : local_work, q_.data(), k_.data(), v_.data(), att_.data(),
                                  c.heads, w.kvh, w.hd, w.global, attention_scores_);
            else
#endif
                attention(n, layout, q_.data(), k_.data(), v_.data(), att_.data(), c.heads, w.kvh, w.hd, w.global);
            project(att_, w.o, tmp_, n, d);
            residual(h_.data(), tmp_.data(), w.post_attn.data(), x_.data(), w.pre_ffw.data(), n, d, c.eps);
            project(x_, w.gate, gate_, n, c.intermediate);
            project(x_, w.up, up_, n, c.intermediate);
            gelu_multiply(gate_.data(), up_.data(), up_.data(), n * c.intermediate);
            project(up_, w.down, tmp_, n, d);
            residual(h_.data(), tmp_.data(), w.post_ffw.data(), nullptr, nullptr, n, d, c.eps);
            matrices_.at(weights_.ple_proj).apply(initial_.data(), ple_.data(), n, p, li * p, 1 / scale);
            norm(ple_.data(), ple_.data(), n, p, weights_.ple_norm.data(), c.eps);
            project(h_, w.ple_gate, gate_, n, p);
            gelu_multiply(gate_.data(), ple_.data(), gate_.data(), n * p);
            project(gate_, w.ple_proj, tmp_, n, d);
            const float* next = li + 1 < c.layers ? weights_.layers[li + 1].in_norm.data() : weights_.final_norm.data();
            residual(h_.data(), tmp_.data(), w.ple_norm.data(), x_.data(), next, n, d, c.eps, w.scalar);
        }
        const float* states = x_.data();
        if (c.read == "projected") { project(x_, weights_.output, states_, n, c.embedding_dim); states = states_.data(); }
        BatchResponse result;
        for (size_t i = 0; i < requests.size(); ++i) {
            Response out;
            const float* row = states + size_t(layout.offsets[i]) * c.d_read();
            weights_.heads.apply(*requests[i], row, out, true);
            if (dump_states) out.states.assign(row, row + requests[i]->ids.size() * c.d_read());
            result.responses.push_back(std::move(out));
        }
        result.seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        for (auto& out : result.responses) out.seconds = result.seconds;
        return result;
    }

    Weights weights_;
    std::map<const Tensor*, Matrix> matrices_;
    std::mutex mutex_;
    int capacity_ = 0;
    bool blocked_attention_ = false;
    RopeTable local_rope_, global_rope_;
    std::vector<float> initial_, h_, x_, tmp_, q_, k_, v_, att_, gate_, up_, ple_, states_;
    std::vector<std::vector<float>> attention_scores_;

    void project(const std::vector<float>& x, const Tensor* w, std::vector<float>& y, int rows, int outputs) {
        matrices_.at(w).apply(x.data(), y.data(), rows, outputs);
    }

    void reserve(int rows) {
        if (rows <= capacity_) return;
        capacity_ = (rows + 63) / 64 * 64;
        const Config& c = weights_.config;
        size_t qdim = 0, kvdim = 0;
        for (const auto& w : weights_.layers) {
            qdim = std::max(qdim, size_t(c.heads) * w.hd); kvdim = std::max(kvdim, size_t(w.kvh) * w.hd);
        }
        for (auto* buf : {&initial_, &h_, &x_, &tmp_}) buf->resize(size_t(capacity_) * c.hidden);
        q_.resize(capacity_ * qdim); att_.resize(capacity_ * qdim);
        k_.resize(capacity_ * kvdim); v_.resize(capacity_ * kvdim);
        gate_.resize(size_t(capacity_) * std::max(c.intermediate, c.ple)); up_.resize(size_t(capacity_) * c.intermediate);
        ple_.resize(size_t(capacity_) * c.ple); states_.resize(size_t(capacity_) * c.d_read());
        if (blocked_attention_) {
            attention_scores_.resize(c.heads);
            for (auto& scores : attention_scores_) scores.resize(size_t(32) * capacity_);
        }
    }
};

}  // namespace

std::unique_ptr<Engine> make_cpu_optimized_engine(const std::string& dir) { return std::make_unique<CpuOptimized>(dir); }

}  // namespace kai
