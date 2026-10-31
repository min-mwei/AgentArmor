// Adapted from GemmaEmbed Attention.metal, Copyright 2026 Peter Marelas, Apache-2.0. See infer/NOTICE.
// Kai adds state/branch intervals, restarted positions and bounds on every partial tile.
constant int OPT_KEYS = 32, OPT_DC = 64, OPT_LD = 68;

inline void stage_attention(threadgroup float* tile, device const float* x, uint row0, uint stride,
                            uint col, uint rows, uint tid, uint threads) {
    for (uint i = tid; i < OPT_KEYS * OPT_DC; i += threads) {
        uint r = i / OPT_DC, c = i % OPT_DC;
        tile[r * OPT_LD + c] = row0 + r < rows ? x[ulong(row0 + r) * stride + col + c] : 0;
    }
}

template <int DS, bool REGISTER_KEYS>
kernel void attention_online(device const float* Q [[buffer(0)]], device const float* K [[buffer(1)]],
                              device const float* V [[buffer(2)]], device float* O [[buffer(3)]],
                              device const AttentionWork* work [[buffer(4)]], device const int* pos [[buffer(5)]],
                              constant AttentionParams& p [[buffer(6)]], constant uint& queries [[buffer(7)]],
                              uint3 group [[threadgroup_position_in_grid]], uint tid [[thread_index_in_threadgroup]],
                              uint sg [[simdgroup_index_in_threadgroup]], uint lane [[thread_index_in_simdgroup]]) {
    threadgroup float tile[OPT_KEYS * OPT_LD];
    const AttentionWork w = work[group.x];
    const uint hd = p.head_dim, qs = p.heads * hd, ks = p.kv_heads * hd;
    const uint h = group.y, kh = h / (p.heads / p.kv_heads), d0 = group.z * DS;
    const uint q0 = w.query_begin + sg * 8;
    const uint r = ((lane >> 1) & 3) | ((lane >> 4) << 2);
    const uint c0 = (lane & 1) * 2 + ((lane >> 3) & 1) * 4;
    const uint qi = q0 + r;
    const int position = qi < w.query_end ? pos[qi] : 0;
    device const float* q = Q + ulong(q0) * qs + h * hd;
    device const float* k = K + kh * hd;
    device const float* v = V + kh * hd + d0;
    simdgroup_float8x8 output[DS / 8];
#pragma clang loop unroll(full)
    for (int c = 0; c < DS / 8; ++c) output[c] = simdgroup_float8x8(0.0f);
    float maximum = -INFINITY, denominator = 0;
    uint end0 = w.state.begin < w.state.end ? (w.state.end + 31) / 32 * 32 : 0;
    for (uint interval = 0; interval < 2; ++interval) {
        Range range = interval == 0 ? w.state : w.branch;
        uint begin = range.begin / 32 * 32;
        if (interval == 1) begin = max(begin, end0);
        uint end = range.begin < range.end ? (range.end + 31) / 32 * 32 : begin;
        for (uint kb = begin; kb < end; kb += OPT_KEYS) {
            simdgroup_float8x8 scores[4];
#pragma clang loop unroll(full)
            for (int j = 0; j < 4; ++j) scores[j] = simdgroup_float8x8(0.0f);
            for (uint dc = 0; dc < hd; dc += OPT_DC) {
                if (!REGISTER_KEYS) {
                    threadgroup_barrier(mem_flags::mem_threadgroup);
                    stage_attention(tile, k, kb, ks, dc, p.rows, tid, queries / 8 * 32);
                    threadgroup_barrier(mem_flags::mem_threadgroup);
                }
#pragma clang loop unroll(full)
                for (int dd = 0; dd < OPT_DC; dd += 8) {
                    simdgroup_float8x8 a;
                    simdgroup_load(a, q + dc + dd, qs);
#pragma clang loop unroll(full)
                    for (int j = 0; j < 4; ++j) {
                        simdgroup_float8x8 b;
                        if (REGISTER_KEYS) {
                            uint key = kb + j * 8 + c0;
                            b.thread_elements()[0] = key < p.rows ? k[ulong(key) * ks + dc + dd + r] : 0;
                            b.thread_elements()[1] = key + 1 < p.rows ? k[ulong(key + 1) * ks + dc + dd + r] : 0;
                        } else {
                            simdgroup_load(b, tile + j * 8 * OPT_LD + dd, OPT_LD, ulong2(0), true);
                        }
                        simdgroup_multiply_accumulate(scores[j], a, b, scores[j]);
                    }
                }
            }
            float block_max = -INFINITY;
#pragma clang loop unroll(full)
            for (int j = 0; j < 4; ++j)
#pragma clang loop unroll(full)
                for (int e = 0; e < 2; ++e) {
                    uint key = kb + j * 8 + c0 + e;
                    bool valid = qi < w.query_end && key < p.rows &&
                        ((key >= w.state.begin && key < w.state.end) || (key >= w.branch.begin && key < w.branch.end));
                    if (valid && !p.global) valid = uint(abs(position - pos[key])) <= p.window;
                    float value = valid ? scores[j].thread_elements()[e] : -INFINITY;
                    scores[j].thread_elements()[e] = value;
                    block_max = max(block_max, value);
                }
            block_max = max(block_max, simd_shuffle_xor(block_max, 1));
            block_max = max(block_max, simd_shuffle_xor(block_max, 8));
            float next_max = max(maximum, block_max), alpha = 1, sum = 0;
            if (next_max == -INFINITY) {
#pragma clang loop unroll(full)
                for (int j = 0; j < 4; ++j) scores[j] = simdgroup_float8x8(0.0f);
            } else {
                alpha = precise::exp(maximum - next_max);
#pragma clang loop unroll(full)
                for (int j = 0; j < 4; ++j)
#pragma clang loop unroll(full)
                    for (int e = 0; e < 2; ++e) {
                        float probability = precise::exp(scores[j].thread_elements()[e] - next_max);
                        scores[j].thread_elements()[e] = probability;
                        sum += probability;
                    }
            }
            sum += simd_shuffle_xor(sum, 1);
            sum += simd_shuffle_xor(sum, 8);
            denominator = denominator * alpha + sum;
            maximum = next_max;
#pragma clang loop unroll(full)
            for (int c = 0; c < DS / 8; ++c) {
                output[c].thread_elements()[0] *= alpha;
                output[c].thread_elements()[1] *= alpha;
            }
#pragma clang loop unroll(full)
            for (int dc = 0; dc < DS; dc += OPT_DC) {
                threadgroup_barrier(mem_flags::mem_threadgroup);
                stage_attention(tile, v, kb, ks, dc, p.rows, tid, queries / 8 * 32);
                threadgroup_barrier(mem_flags::mem_threadgroup);
#pragma clang loop unroll(full)
                for (int j = 0; j < 4; ++j)
#pragma clang loop unroll(full)
                    for (int dd = 0; dd < OPT_DC; dd += 8) {
                        simdgroup_float8x8 value;
                        simdgroup_load(value, tile + j * 8 * OPT_LD + dd, OPT_LD);
                        const int c = (dc + dd) / 8;
                        simdgroup_multiply_accumulate(output[c], scores[j], value, output[c]);
                    }
            }
        }
    }
    if (qi >= w.query_end) return;
    float inv = denominator > 0 ? 1 / denominator : 0;
    device float* dest = O + ulong(qi) * qs + h * hd + d0 + c0;
#pragma clang loop unroll(full)
    for (int c = 0; c < DS / 8; ++c)
        *(device float2*)(dest + c * 8) = float2(output[c].thread_elements()[0], output[c].thread_elements()[1]) * inv;
}

#define ATTENTION(NAME, DS, REG) \
template [[host_name(NAME)]] kernel void attention_online<DS, REG>( \
    device const float*, device const float*, device const float*, device float*, device const AttentionWork*, \
    device const int*, constant AttentionParams&, constant uint&, uint3, uint, uint, uint);
ATTENTION("attention_128", 128, false)
ATTENTION("attention_256", 256, false)
ATTENTION("attention_512", 512, false)
ATTENTION("attention_128_register", 128, true)
ATTENTION("attention_256_register", 256, true)
ATTENTION("attention_512_register", 512, true)
