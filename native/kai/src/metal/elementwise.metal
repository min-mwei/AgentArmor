// Fusion patterns adapted from GemmaEmbed, Copyright 2026 Peter Marelas, Apache-2.0. See infer/NOTICE.
kernel void embed_opt(device const int* ids [[buffer(0)]], device const ushort* table [[buffer(1)]],
                      device float* initial [[buffer(2)]], device float* residual [[buffer(3)]],
                      constant uint2& shape [[buffer(4)]], uint i [[thread_position_in_grid]]) {
    if (i >= shape.x * shape.y) return;
    float value = widen(table[ulong(ids[i / shape.y]) * shape.y + i % shape.y]) * precise::sqrt(float(shape.y));
    initial[i] = value; residual[i] = value;
}

// One SIMD group per row. All reads precede in-place writes within that group.
kernel void norm_opt(device const float* x [[buffer(0)]], device float* out [[buffer(1)]],
                     device const float* weight [[buffer(2)]], constant RowParams& p [[buffer(3)]],
                     uint row [[threadgroup_position_in_grid]], uint lane [[thread_index_in_simdgroup]]) {
    float sum = 0;
    for (uint i = lane; i < p.width; i += 32) { float v = x[ulong(row) * p.width + i] * p.scale; sum += v * v; }
    float inv = rsqrt(simd_sum(sum) / p.width + p.eps);
    for (uint i = lane; i < p.width; i += 32) out[ulong(row) * p.width + i] = x[ulong(row) * p.width + i] * p.scale * inv * weight[i];
}

kernel void norm_rope_opt(device float* x [[buffer(0)]], device const float* weight [[buffer(1)]],
                          device const int* positions [[buffer(2)]], device const float* table [[buffer(3)]],
                          constant RopeNormParams& p [[buffer(4)]], uint row [[threadgroup_position_in_grid]],
                          uint lane [[thread_index_in_simdgroup]]) {
    device float* xr = x + ulong(row) * p.width;
    float sum = 0;
    for (uint i = lane; i < p.width; i += 32) sum += xr[i] * xr[i];
    float inv = rsqrt(simd_sum(sum) / p.width + p.eps);
    device const float* cs = table + ulong(positions[row / p.heads]) * p.width;
    for (uint i = lane; i < p.width / 2; i += 32) {
        float a = xr[i] * inv * weight[i], b = xr[i + p.width / 2] * inv * weight[i + p.width / 2];
        xr[i] = a * cs[2 * i] - b * cs[2 * i + 1];
        xr[i + p.width / 2] = b * cs[2 * i] + a * cs[2 * i + 1];
    }
}

kernel void norm_residual_opt(device float* h [[buffer(0)]], device const float* t [[buffer(1)]],
                              device const float* weight [[buffer(2)]], device float* out [[buffer(3)]],
                              device const float* next [[buffer(4)]], constant RowParams& p [[buffer(5)]],
                              constant uint& normalize_next [[buffer(6)]], uint row [[threadgroup_position_in_grid]],
                              uint lane [[thread_index_in_simdgroup]]) {
    device float* hr = h + ulong(row) * p.width;
    device const float* tr = t + ulong(row) * p.width;
    float sum = 0;
    for (uint i = lane; i < p.width; i += 32) sum += tr[i] * tr[i];
    float inv = rsqrt(simd_sum(sum) / p.width + p.eps);
    float values[16];
    float sum2 = 0;
#pragma clang loop unroll(full)
    for (uint k = 0; k < 16; ++k) {
        uint i = lane + k * 32;
        float value = i < p.width ? (hr[i] + tr[i] * inv * weight[i]) * p.scale : 0;
        values[k] = value;
        if (i < p.width) hr[i] = value;
        sum2 += value * value;
    }
    if (!normalize_next) return;
    float inv2 = rsqrt(simd_sum(sum2) / p.width + p.eps);
#pragma clang loop unroll(full)
    for (uint k = 0; k < 16; ++k) {
        uint i = lane + k * 32;
        if (i < p.width) out[ulong(row) * p.width + i] = values[k] * inv2 * next[i];
    }
}
