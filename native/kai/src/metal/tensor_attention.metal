// Tensor fragment mapping and MMA adapted from MLX, Copyright 2025 Apple Inc., Apache-2.0.
// Kai uses exact state/branch intervals and splits head dimensions without repeating QK. See infer/NOTICE.
using FloatFragment = vec<float, 8>;

struct TensorKeyRange { uint begin, end; };
struct TensorAttentionWork { uint query_begin, query_end; TensorKeyRange state, branch; };
struct TensorAttentionParams { uint rows, heads, kv_heads, head_dim, window, global; };

inline uint2 tensor_coordinate(uint lane) {
    uint quad = lane >> 2;
    return uint2(((quad & 2) | (lane & 1)) * 4, (quad & 4) | ((lane >> 1) & 3));
}

template <typename T>
inline vec<T, 8> tensor_load16(device const float* x, uint row, uint col, uint stride, uint end, uint lane) {
    uint2 c = tensor_coordinate(lane);
    vec<T, 8> result;
#pragma clang loop unroll(full)
    for (uint i = 0; i < 2; ++i)
#pragma clang loop unroll(full)
        for (uint j = 0; j < 4; ++j) {
            uint r = row + c.y + i * 8;
            result[i * 4 + j] = r < end ? T(x[ulong(r) * stride + col + c.x + j]) : T(0);
        }
    return result;
}

template <bool TRANSPOSE_B, typename A, typename B>
inline void tensor_mma16(thread FloatFragment& c0, thread FloatFragment& c1, vec<A, 8> a, vec<B, 8> b0, vec<B, 8> b1) {
    constexpr auto desc = matmul2d_descriptor(16, 32, 16, false, TRANSPOSE_B, false,
                                            matmul2d_descriptor::mode::multiply_accumulate);
    matmul2d<desc, execution_simdgroup> op;
    auto left = op.template get_left_input_cooperative_tensor<A, B, float>();
    auto right = op.template get_right_input_cooperative_tensor<A, B, float>();
    auto dest = op.template get_destination_cooperative_tensor<remove_addrspace_t<decltype(left)>,
                                                              remove_addrspace_t<decltype(right)>, float>();
#pragma clang loop unroll(full)
    for (uint i = 0; i < 8; ++i) {
        left[i] = a[i]; right[i] = b0[i]; right[i + 8] = b1[i];
        dest[i] = c0[i]; dest[i + 8] = c1[i];
    }
    op.run(left, right, dest);
#pragma clang loop unroll(full)
    for (uint i = 0; i < 8; ++i) { c0[i] = dest[i]; c1[i] = dest[i + 8]; }
}

kernel void tensor_fragment_layout(device float* out [[buffer(0)]], uint lane [[thread_index_in_simdgroup]]) {
    uint2 c = tensor_coordinate(lane);
    FloatFragment a, b0, b1, y0 = FloatFragment(0), y1 = FloatFragment(0);
#pragma clang loop unroll(full)
    for (uint i = 0; i < 8; ++i) {
        uint row = c.y + (i / 4) * 8, col = c.x + i % 4;
        a[i] = row == col ? 1 : 0;
        b0[i] = row * 32 + col; b1[i] = row * 32 + col + 16;
    }
    tensor_mma16<false>(y0, y1, a, b0, b1);
#pragma clang loop unroll(full)
    for (uint i = 0; i < 8; ++i) {
        uint row = c.y + (i / 4) * 8, col = c.x + i % 4;
        out[row * 32 + col] = y0[i]; out[row * 32 + col + 16] = y1[i];
    }
}

template <int HD, typename T>
kernel void attention_tensor(device const float* Q [[buffer(0)]], device const float* K [[buffer(1)]],
                             device const float* V [[buffer(2)]], device float* O [[buffer(3)]],
                             device const TensorAttentionWork* work [[buffer(4)]], device const int* pos [[buffer(5)]],
                             constant TensorAttentionParams& p [[buffer(6)]],
                             uint2 group [[threadgroup_position_in_grid]], uint shard [[simdgroup_index_in_threadgroup]],
                             uint lane [[thread_index_in_simdgroup]]) {
    constexpr uint WIDTH = 128, SHARDS = HD / WIDTH;
    threadgroup float partial[SHARDS * 16 * 32];
    TensorAttentionWork w = work[group.x];
    uint2 c = tensor_coordinate(lane);
    uint qs = p.heads * HD, ks = p.kv_heads * HD, kh = group.y / (p.heads / p.kv_heads);
    uint d0 = shard * WIDTH;
    device const float* q = Q + group.y * HD;
    device const float* k = K + kh * HD;
    device const float* v = V + kh * HD;
    FloatFragment output[WIDTH / 16];
#pragma clang loop unroll(full)
    for (uint i = 0; i < WIDTH / 16; ++i) output[i] = FloatFragment(0);
    float2 maximum(-INFINITY), denominator(0);
    uint end0 = w.state.begin < w.state.end ? (w.state.end + 31) / 32 * 32 : 0;
    for (uint interval = 0; interval < 2; ++interval) {
        TensorKeyRange range = interval == 0 ? w.state : w.branch;
        uint begin = range.begin / 32 * 32;
        if (interval == 1) begin = max(begin, end0);
        uint end = range.begin < range.end ? (range.end + 31) / 32 * 32 : begin;
        for (uint kb = begin; kb < end; kb += 32) {
            FloatFragment scores[2] = {FloatFragment(0), FloatFragment(0)};
#pragma clang loop unroll_count(2)
            for (uint dc = 0; dc < WIDTH; dc += 16) {
                auto a = tensor_load16<T>(q, w.query_begin, d0 + dc, qs, w.query_end, lane);
                auto b0 = tensor_load16<T>(k, kb, d0 + dc, ks, p.rows, lane);
                auto b1 = tensor_load16<T>(k, kb + 16, d0 + dc, ks, p.rows, lane);
                tensor_mma16<true>(scores[0], scores[1], a, b0, b1);
            }
            threadgroup_barrier(mem_flags::mem_threadgroup);
#pragma clang loop unroll(full)
            for (uint i = 0; i < 8; ++i) {
                uint at = (shard * 16 + c.y + i / 4 * 8) * 32 + c.x + i % 4;
                partial[at] = scores[0][i]; partial[at + 16] = scores[1][i];
            }
            threadgroup_barrier(mem_flags::mem_threadgroup);
#pragma clang loop unroll(full)
            for (uint j = 0; j < 2; ++j)
#pragma clang loop unroll(full)
                for (uint i = 0; i < 8; ++i) {
                    uint row = c.y + i / 4 * 8, key = kb + j * 16 + c.x + i % 4;
                    float score = 0;
#pragma clang loop unroll(full)
                    for (uint s = 0; s < SHARDS; ++s)
                        score += partial[(s * 16 + row) * 32 + j * 16 + c.x + i % 4];
                    uint qi = w.query_begin + row;
                    bool valid = qi < w.query_end && key < p.rows &&
                        ((key >= w.state.begin && key < w.state.end) || (key >= w.branch.begin && key < w.branch.end));
                    if (valid && !p.global) valid = uint(abs(pos[qi] - pos[key])) <= p.window;
                    scores[j][i] = valid ? score : -INFINITY;
                }
#pragma clang loop unroll(full)
            for (uint row = 0; row < 2; ++row) {
                float block_max = -INFINITY;
#pragma clang loop unroll(full)
                for (uint j = 0; j < 2; ++j)
#pragma clang loop unroll(full)
                    for (uint col = 0; col < 4; ++col) block_max = max(block_max, scores[j][row * 4 + col]);
                block_max = max(block_max, simd_shuffle_xor(block_max, 1));
                block_max = max(block_max, simd_shuffle_xor(block_max, 8));
                float next_max = max(maximum[row], block_max);
                float alpha = next_max == -INFINITY ? 1 : precise::exp(maximum[row] - next_max), sum = 0;
#pragma clang loop unroll(full)
                for (uint j = 0; j < 2; ++j)
#pragma clang loop unroll(full)
                    for (uint col = 0; col < 4; ++col) {
                        float value = next_max == -INFINITY ? 0 : precise::exp(scores[j][row * 4 + col] - next_max);
                        scores[j][row * 4 + col] = value; sum += value;
                    }
                sum += simd_shuffle_xor(sum, 1); sum += simd_shuffle_xor(sum, 8);
                denominator[row] = denominator[row] * alpha + sum; maximum[row] = next_max;
#pragma clang loop unroll(full)
                for (uint j = 0; j < WIDTH / 16; ++j)
#pragma clang loop unroll(full)
                    for (uint col = 0; col < 4; ++col) output[j][row * 4 + col] *= alpha;
            }
#pragma clang loop unroll(full)
            for (uint dc = 0; dc < WIDTH; dc += 32)
#pragma clang loop unroll(full)
                for (uint j = 0; j < 2; ++j) {
                    auto b0 = tensor_load16<T>(v, kb + j * 16, d0 + dc, ks, p.rows, lane);
                    auto b1 = tensor_load16<T>(v, kb + j * 16, d0 + dc + 16, ks, p.rows, lane);
                    tensor_mma16<false>(output[dc / 16], output[dc / 16 + 1], scores[j], b0, b1);
                }
        }
    }
#pragma clang loop unroll(full)
    for (uint j = 0; j < WIDTH / 16; ++j)
#pragma clang loop unroll(full)
        for (uint i = 0; i < 8; ++i) {
            uint row = w.query_begin + c.y + i / 4 * 8;
            if (row < w.query_end)
                O[ulong(row) * qs + group.y * HD + d0 + j * 16 + c.x + i % 4] =
                    denominator[i / 4] > 0 ? output[j][i] / denominator[i / 4] : 0;
        }
}

#define TENSOR_ATTENTION(NAME, HD, TYPE) \
template [[host_name(NAME)]] kernel void attention_tensor<HD, TYPE>( \
    device const float*, device const float*, device const float*, device float*, device const TensorAttentionWork*, \
    device const int*, constant TensorAttentionParams&, uint2, uint, uint);
TENSOR_ATTENTION("attention_tensor_256", 256, float)
TENSOR_ATTENTION("attention_tensor_512", 512, float)
TENSOR_ATTENTION("attention_tensor_bf16_256", 256, bfloat)
TENSOR_ATTENTION("attention_tensor_bf16_512", 512, bfloat)
