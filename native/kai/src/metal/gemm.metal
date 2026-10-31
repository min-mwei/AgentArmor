// Adapted from GemmaEmbed GEMM.metal, Copyright 2026 Peter Marelas, Apache-2.0. See infer/NOTICE.
// fp32 activations and accumulators, bf16 weights. Padding is local to each tile.
template <int BM, int BN, int BK, int SG>
kernel void gemm_matrix(device const float* X [[buffer(0)]], device const ushort* W [[buffer(1)]],
                        device float* Y [[buffer(2)]], constant GemmParams& p [[buffer(3)]],
                        device const float* aux [[buffer(4)]], uint2 group [[threadgroup_position_in_grid]],
                        uint tid [[thread_index_in_threadgroup]], uint sg [[simdgroup_index_in_threadgroup]]) {
    constexpr int LD = BK + 4, SN = BN / SG, TM = BM / 8, TN = SN / 8;
    constexpr int STAGE = (BM + BN) * LD;
    constexpr int OUT_LD = BN + 4;
    // Store eight rows at a time, reusing the operand stage after the final product.
    static_assert(STAGE >= 8 * OUT_LD, "output stage must fit");
    threadgroup float stage[STAGE];
    threadgroup float* xs = stage;
    threadgroup float* ws = stage + BM * LD;
    uint m0 = group.y * BM, n0 = group.x * BN;
    simdgroup_float8x8 acc[TM][TN];
#pragma clang loop unroll(full)
    for (int i = 0; i < TM; ++i)
#pragma clang loop unroll(full)
        for (int j = 0; j < TN; ++j) acc[i][j] = simdgroup_float8x8(0.0f);
    for (uint k0 = 0; k0 < p.K; k0 += BK) {
        threadgroup_barrier(mem_flags::mem_threadgroup);
        for (uint i = tid; i < BM * BK; i += SG * 32) {
            uint row = i / BK, k = i % BK;
            xs[row * LD + k] = m0 + row < p.M && k0 + k < p.K ? X[ulong(m0 + row) * p.K + k0 + k] : 0;
        }
        for (uint i = tid; i < BN * BK; i += SG * 32) {
            uint row = i / BK, k = i % BK;
            ws[row * LD + k] = n0 + row < p.N && k0 + k < p.K ? widen(W[ulong(n0 + row) * p.K + k0 + k]) : 0;
        }
        threadgroup_barrier(mem_flags::mem_threadgroup);
#pragma clang loop unroll(full)
        for (int kk = 0; kk < BK; kk += 8) {
            simdgroup_float8x8 a[TM], b[TN];
#pragma clang loop unroll(full)
            for (int i = 0; i < TM; ++i) simdgroup_load(a[i], xs + i * 8 * LD + kk, LD);
#pragma clang loop unroll(full)
            for (int j = 0; j < TN; ++j) simdgroup_load(b[j], ws + (sg * SN + j * 8) * LD + kk, LD, ulong2(0), true);
#pragma clang loop unroll(full)
            for (int i = 0; i < TM; ++i)
#pragma clang loop unroll(full)
                for (int j = 0; j < TN; ++j) simdgroup_multiply_accumulate(acc[i][j], a[i], b[j], acc[i][j]);
        }
    }
#pragma clang loop unroll(full)
    for (int i = 0; i < TM; ++i) {
        threadgroup_barrier(mem_flags::mem_threadgroup);
#pragma clang loop unroll(full)
        for (int j = 0; j < TN; ++j) simdgroup_store(acc[i][j], stage + sg * SN + j * 8, OUT_LD);
        threadgroup_barrier(mem_flags::mem_threadgroup);
        for (uint u = tid; u < 8 * BN; u += SG * 32) {
            uint row = m0 + i * 8 + u / BN, col = n0 + u % BN;
            if (row < p.M && col < p.N) {
                ulong index = ulong(row) * p.N + col;
                Y[index] = epilogue(stage[(u / BN) * OUT_LD + u % BN] * p.alpha, p.epilogue ? aux[index] : 0, p.epilogue);
            }
        }
    }
}

#define GEMM(NAME, M, N, K, S) \
template [[host_name(NAME)]] kernel void gemm_matrix<M, N, K, S>( \
    device const float*, device const ushort*, device float*, constant GemmParams&, device const float*, uint2, uint, uint);
GEMM("gemm_8x32", 8, 32, 32, 2)
GEMM("gemm_32x64", 32, 64, 32, 4)
GEMM("gemm_64x64", 64, 64, 32, 4)
