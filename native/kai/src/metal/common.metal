// Portions adapted from GemmaEmbed, Copyright 2026 Peter Marelas, Apache-2.0. See infer/NOTICE.
#include <metal_stdlib>
using namespace metal;

struct GemmParams { uint M, N, K; float alpha; uint epilogue; };
struct RowParams { uint rows, width; float eps, scale; };
struct RopeNormParams { uint rows, heads, width; float eps; };
struct Range { uint begin, end; };
struct AttentionWork { uint query_begin, query_end; Range state, branch; };
struct AttentionParams { uint rows, heads, kv_heads, head_dim, window, global; };

inline float widen(ushort x) { return as_type<float>(uint(x) << 16); }
inline float opt_gelu(float x) {
    return 0.5f * x * (1 + precise::tanh(0.7978845608028654f * (x + 0.044715f * x * x * x)));
}

inline float epilogue(float x, float aux, uint mode) {
    if (mode == 1) return opt_gelu(aux) * x;
    if (mode == 2) return opt_gelu(x) * aux;
    return x;
}

kernel void fragment_layout(device float* out [[buffer(0)]], uint lane [[thread_index_in_simdgroup]]) {
    simdgroup_float8x8 x;
    uint r = ((lane >> 1) & 3) | ((lane >> 4) << 2);
    uint c = (lane & 1) * 2 + ((lane >> 3) & 1) * 4;
    x.thread_elements()[0] = float(r * 8 + c);
    x.thread_elements()[1] = float(r * 8 + c + 1);
    simdgroup_store(x, out, 8);
}

kernel void cast_bf16(device const float* in [[buffer(0)]], device bfloat* out [[buffer(1)]],
                      constant uint2& count [[buffer(2)]], uint i [[thread_position_in_grid]]) {
    if (i < count.y) out[i] = i < count.x ? bfloat(in[i]) : bfloat(0);
}

kernel void gemm_epilogue(device float* out [[buffer(0)]], device const float* aux [[buffer(1)]],
                          constant GemmParams& p [[buffer(2)]], uint i [[thread_position_in_grid]]) {
    if (i < p.M * p.N) out[i] = epilogue(out[i], p.epilogue ? aux[i] : 0, p.epilogue);
}
