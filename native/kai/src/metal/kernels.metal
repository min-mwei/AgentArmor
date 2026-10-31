// Metal kernels for the EmbeddingGemma-2 decision model (infer/src/metal.mm drives them). Activations are float32,
// weights bf16 (widened on load). Each kernel mirrors one operation of train/kai/model.py.
#include <metal_stdlib>
using namespace metal;

inline float bf16_to_f32(ushort h) { return as_type<float>((uint)h << 16); }
inline float gelu_tanh(float x) { return 0.5f * x * (1.0f + precise::tanh(0.7978845608028654f * (x + 0.044715f * x * x * x))); }

// ---------------------------------------------------------------------------------------------------- embeddings
struct EmbedParams { uint L, d; float scale; };

kernel void embed(device const int* ids [[buffer(0)]], device const ushort* table [[buffer(1)]], device float* out [[buffer(2)]],
                  constant EmbedParams& p [[buffer(3)]], uint gid [[thread_position_in_grid]]) {
    if (gid >= p.L * p.d) return;
    uint l = gid / p.d, i = gid % p.d;
    out[gid] = bf16_to_f32(table[(ulong)ids[l] * p.d + i]) * p.scale;
}

// ---------------------------------------------------------------------------------------------------- matmul
// y[M, N] = alpha * x[M, K] . W[N, K]^T ; W bf16 row-major. 64 x 64 output tiles, 16 x 16 threads, 4 x 4 outputs each.
struct MatmulParams { uint M, N, K; float alpha; };
#define TM 64
#define TN 64
#define TK 16

kernel void matmul_bf16(device const float* x [[buffer(0)]], device const ushort* W [[buffer(1)]], device float* y [[buffer(2)]],
                        constant MatmulParams& p [[buffer(3)]], uint2 tg [[threadgroup_position_in_grid]],
                        uint2 tid [[thread_position_in_threadgroup]]) {
    threadgroup float As[TK][TM + 1];
    threadgroup float Bs[TK][TN + 1];
    const uint m0 = tg.y * TM, n0 = tg.x * TN;
    const uint tx = tid.x, ty = tid.y, t = ty * 16 + tx;   // 256 threads
    float acc[4][4] = {{0}};
    for (uint k0 = 0; k0 < p.K; k0 += TK) {
        // each thread stages 4 elements of A and 4 of B: element e = t + i*256 of the 64x16 tile, laid out [row][k]
        for (uint i = 0; i < 4; ++i) {
            uint e = t + i * 256, row = e / TK, kk = e % TK;
            uint m = m0 + row, n = n0 + row, k = k0 + kk;
            As[kk][row] = (m < p.M && k < p.K) ? x[(ulong)m * p.K + k] : 0.0f;
            Bs[kk][row] = (n < p.N && k < p.K) ? bf16_to_f32(W[(ulong)n * p.K + k]) : 0.0f;
        }
        threadgroup_barrier(mem_flags::mem_threadgroup);
        for (uint kk = 0; kk < TK; ++kk) {
            float a[4], b[4];
            for (uint i = 0; i < 4; ++i) { a[i] = As[kk][ty * 4 + i]; b[i] = Bs[kk][tx * 4 + i]; }
            for (uint i = 0; i < 4; ++i) for (uint j = 0; j < 4; ++j) acc[i][j] = fma(a[i], b[j], acc[i][j]);
        }
        threadgroup_barrier(mem_flags::mem_threadgroup);
    }
    for (uint i = 0; i < 4; ++i) {
        uint m = m0 + ty * 4 + i;
        if (m >= p.M) continue;
        for (uint j = 0; j < 4; ++j) {
            uint n = n0 + tx * 4 + j;
            if (n < p.N) y[(ulong)m * p.N + n] = acc[i][j] * p.alpha;
        }
    }
}

// Simdgroup-matrix variant: 64 x 64 output tile per threadgroup of 4 simdgroups (128 threads); each simdgroup owns a 32 x 32
// sub-tile as 4 x 4 simdgroup_float8x8 accumulators. A (fp32 activations) and B (bf16 weights widened to fp32) are staged
// in threadgroup memory per 16-wide K slab; B is staged n-major (as W is stored) and loaded transposed. fp32 throughout,
// so the result matches the scalar kernel to accumulation order. Threadgroup memory stays at 9 KB (As, Bs and a 256 B
// per-simdgroup store scratch) so several threadgroups share a core.
#define SG_TK 16
kernel void matmul_sgm(device const float* x [[buffer(0)]], device const ushort* W [[buffer(1)]], device float* y [[buffer(2)]],
                       constant MatmulParams& p [[buffer(3)]], uint2 tg [[threadgroup_position_in_grid]],
                       uint t [[thread_index_in_threadgroup]], uint sg [[simdgroup_index_in_threadgroup]],
                       uint lane [[thread_index_in_simdgroup]]) {
    threadgroup float As[TM * SG_TK];        // [m][k]
    threadgroup float Bs[TN * SG_TK];        // [n][k]
    threadgroup float Ss[4][64];             // one 8 x 8 block per simdgroup for the bounds-checked store
    const uint m0 = tg.y * TM, n0 = tg.x * TN;
    const uint sm = (sg / 2) * 32, sn = (sg % 2) * 32;   // the simdgroup's sub-tile origin
    simdgroup_float8x8 acc[4][4];
    for (uint i = 0; i < 4; ++i) for (uint j = 0; j < 4; ++j) acc[i][j] = simdgroup_float8x8(0.0f);
    for (uint k0 = 0; k0 < p.K; k0 += SG_TK) {
        for (uint i = 0; i < 8; ++i) {                      // 1024 elements of A and of B, 8 per thread
            uint e = t + i * 128, row = e >> 4, kk = e & 15, k = k0 + kk;
            uint m = m0 + row, n = n0 + row;
            As[e] = (m < p.M && k < p.K) ? x[(ulong)m * p.K + k] : 0.0f;
            Bs[e] = (n < p.N && k < p.K) ? bf16_to_f32(W[(ulong)n * p.K + k]) : 0.0f;
        }
        threadgroup_barrier(mem_flags::mem_threadgroup);
        for (uint kk = 0; kk < SG_TK; kk += 8) {
            simdgroup_float8x8 a[4], b[4];
            for (uint i = 0; i < 4; ++i) simdgroup_load(a[i], As + (sm + i * 8) * SG_TK + kk, SG_TK);
            for (uint j = 0; j < 4; ++j) simdgroup_load(b[j], Bs + (sn + j * 8) * SG_TK + kk, SG_TK, ulong2(0, 0), true);
            for (uint i = 0; i < 4; ++i) for (uint j = 0; j < 4; ++j) simdgroup_multiply_accumulate(acc[i][j], a[i], b[j], acc[i][j]);
        }
        threadgroup_barrier(mem_flags::mem_threadgroup);
    }
    const bool inside = (m0 + TM <= p.M) && (n0 + TN <= p.N);
    for (uint i = 0; i < 4; ++i) for (uint j = 0; j < 4; ++j) {
        const uint mb = m0 + sm + i * 8, nb = n0 + sn + j * 8;
        if (inside && p.alpha == 1.0f) {
            simdgroup_store(acc[i][j], y + (ulong)mb * p.N + nb, p.N);
        } else {
            simdgroup_store(acc[i][j], Ss[sg], 8);
            simdgroup_barrier(mem_flags::mem_threadgroup);
            for (uint e = lane; e < 64; e += 32) {
                uint m = mb + (e >> 3), n = nb + (e & 7);
                if (m < p.M && n < p.N) y[(ulong)m * p.N + n] = Ss[sg][e] * p.alpha;
            }
            simdgroup_barrier(mem_flags::mem_threadgroup);
        }
    }
}

// bf16-input variant: A rounded to bfloat when staged, B as stored, bfloat 8x8 fragments, float accumulation (the
// hardware matrix path and the numerics of the bf16 training forward). Selected with KAI_MATMUL=bf16.
kernel void matmul_sgm_bf16(device const float* x [[buffer(0)]], device const ushort* W [[buffer(1)]], device float* y [[buffer(2)]],
                            constant MatmulParams& p [[buffer(3)]], uint2 tg [[threadgroup_position_in_grid]],
                            uint t [[thread_index_in_threadgroup]], uint sg [[simdgroup_index_in_threadgroup]],
                            uint lane [[thread_index_in_simdgroup]]) {
    threadgroup bfloat As[TM * 32];          // [m][k], 32-wide K slab
    threadgroup bfloat Bs[TN * 32];          // [n][k]
    threadgroup float Ss[4][64];
    const uint m0 = tg.y * TM, n0 = tg.x * TN;
    const uint sm = (sg / 2) * 32, sn = (sg % 2) * 32;
    simdgroup_float8x8 acc[4][4];
    for (uint i = 0; i < 4; ++i) for (uint j = 0; j < 4; ++j) acc[i][j] = simdgroup_float8x8(0.0f);
    for (uint k0 = 0; k0 < p.K; k0 += 32) {
        for (uint i = 0; i < 16; ++i) {                     // 2048 elements of A and of B, 16 per thread
            uint e = t + i * 128, row = e >> 5, kk = e & 31, k = k0 + kk;
            uint m = m0 + row, n = n0 + row;
            As[e] = (m < p.M && k < p.K) ? bfloat(x[(ulong)m * p.K + k]) : bfloat(0.0f);
            Bs[e] = (n < p.N && k < p.K) ? as_type<bfloat>(W[(ulong)n * p.K + k]) : bfloat(0.0f);
        }
        threadgroup_barrier(mem_flags::mem_threadgroup);
        for (uint kk = 0; kk < 32; kk += 8) {
            simdgroup_bfloat8x8 a[4], b[4];
            for (uint i = 0; i < 4; ++i) simdgroup_load(a[i], As + (sm + i * 8) * 32 + kk, 32);
            for (uint j = 0; j < 4; ++j) simdgroup_load(b[j], Bs + (sn + j * 8) * 32 + kk, 32, ulong2(0, 0), true);
            for (uint i = 0; i < 4; ++i) for (uint j = 0; j < 4; ++j) simdgroup_multiply_accumulate(acc[i][j], a[i], b[j], acc[i][j]);
        }
        threadgroup_barrier(mem_flags::mem_threadgroup);
    }
    const bool inside = (m0 + TM <= p.M) && (n0 + TN <= p.N);
    for (uint i = 0; i < 4; ++i) for (uint j = 0; j < 4; ++j) {
        const uint mb = m0 + sm + i * 8, nb = n0 + sn + j * 8;
        if (inside && p.alpha == 1.0f) {
            simdgroup_store(acc[i][j], y + (ulong)mb * p.N + nb, p.N);
        } else {
            simdgroup_store(acc[i][j], Ss[sg], 8);
            simdgroup_barrier(mem_flags::mem_threadgroup);
            for (uint e = lane; e < 64; e += 32) {
                uint m = mb + (e >> 3), n = nb + (e & 7);
                if (m < p.M && n < p.N) y[(ulong)m * p.N + n] = Ss[sg][e] * p.alpha;
            }
            simdgroup_barrier(mem_flags::mem_threadgroup);
        }
    }
}

// ---------------------------------------------------------------------------------------------------- rmsnorm
// One threadgroup (256 threads) per row of x [M, d]; weight optional (has_weight); in place allowed (x == y).
struct NormParams { uint M, d; float eps; uint has_weight; };

kernel void rmsnorm(device const float* x [[buffer(0)]], device float* y [[buffer(1)]], device const float* w [[buffer(2)]],
                    constant NormParams& p [[buffer(3)]], uint row [[threadgroup_position_in_grid]],
                    uint tid [[thread_position_in_threadgroup]], uint simd_lane [[thread_index_in_simdgroup]],
                    uint simd_id [[simdgroup_index_in_threadgroup]]) {
    threadgroup float partial[8];
    device const float* r = x + (ulong)row * p.d;
    float ss = 0.0f;
    for (uint i = tid; i < p.d; i += 256) ss += r[i] * r[i];
    ss = simd_sum(ss);
    if (simd_lane == 0) partial[simd_id] = ss;
    threadgroup_barrier(mem_flags::mem_threadgroup);
    float total = 0.0f;
    for (uint i = 0; i < 8; ++i) total += partial[i];
    float inv = rsqrt(total / (float)p.d + p.eps);
    device float* o = y + (ulong)row * p.d;
    for (uint i = tid; i < p.d; i += 256) o[i] = r[i] * inv * (p.has_weight ? w[i] : 1.0f);
}

// ---------------------------------------------------------------------------------------------------- rope
// Rotate-half RoPE in place on x [L, H, D] with positions pos [L]; one thread per (l, h, i < D/2).
struct RopeParams { uint L, H, D; float theta; };

kernel void rope(device float* x [[buffer(0)]], device const int* pos [[buffer(1)]], constant RopeParams& p [[buffer(2)]],
                 uint gid [[thread_position_in_grid]]) {
    uint half_d = p.D / 2;
    if (gid >= p.L * p.H * half_d) return;
    uint i = gid % half_d, lh = gid / half_d, h = lh % p.H, l = lh / p.H;
    float inv = 1.0f / precise::pow(p.theta, (2.0f * (float)i) / (float)p.D);
    float a = (float)pos[l] * inv, c = precise::cos(a), s = precise::sin(a);
    device float* v = x + ((ulong)l * p.H + h) * p.D;
    float x1 = v[i], x2 = v[i + half_d];
    v[i] = x1 * c - x2 * s;
    v[i + half_d] = x2 * c + x1 * s;
}

// ---------------------------------------------------------------------------------------------------- attention
// One simdgroup (32 lanes) per query, 8 queries (one head) per threadgroup, so the eight simdgroups read the same key and
// value rows through the cache. Keys are visited 32 at a time (one per lane) with an online softmax; the mask is computed
// from seg (block: key in the state, or the same block as the query) and pos (|pos_i - pos_j| <= window on sliding
// layers). Scale 1. q [L, H, hd], k / v [L, KVH, hd], out [L, H, hd]; hd <= 512.
struct AttnParams { uint L, H, KVH, hd, window, is_global, full; };
#define ATT_Q 8

kernel void attention(device const float* q [[buffer(0)]], device const float* k [[buffer(1)]], device const float* v [[buffer(2)]],
                      device float* out [[buffer(3)]], device const int* seg [[buffer(4)]], device const int* pos [[buffer(5)]],
                      constant AttnParams& p [[buffer(6)]], uint2 tg [[threadgroup_position_in_grid]],
                      uint lane [[thread_index_in_simdgroup]], uint simd_id [[simdgroup_index_in_threadgroup]]) {
    threadgroup float qs[ATT_Q][512];
    const uint h = tg.y, i = tg.x * ATT_Q + simd_id, hd = p.hd, kvh = h / (p.H / p.KVH);
    if (i >= p.L) return;
    device const float* qi = q + ((ulong)i * p.H + h) * hd;
    for (uint t = lane; t < hd; t += 32) qs[simd_id][t] = qi[t];
    simdgroup_barrier(mem_flags::mem_threadgroup);
    threadgroup const float* qv = qs[simd_id];
    const int seg_i = seg[i], pos_i = pos[i];
    float acc[16];
    for (uint a = 0; a < 16; ++a) acc[a] = 0.0f;
    float m_run = -INFINITY, l_run = 0.0f;
    for (uint c0 = 0; c0 < p.L; c0 += 32) {
        uint j = c0 + lane;
        float sc = -INFINITY;
        if (j < p.L) {
            bool ok = (seg[j] == 0 || seg[j] == seg_i || (p.full && seg_i == 0)) && (p.is_global || (uint)abs(pos_i - pos[j]) <= p.window);
            if (ok) {
                device const float* kj = k + ((ulong)j * p.KVH + kvh) * hd;
                float d = 0.0f;
                for (uint t = 0; t < hd; t += 4) d += qv[t] * kj[t] + qv[t + 1] * kj[t + 1] + qv[t + 2] * kj[t + 2] + qv[t + 3] * kj[t + 3];
                sc = d;
            }
        }
        float cm = simd_max(sc);
        if (cm == -INFINITY) continue;                       // uniform across the simdgroup
        float m_new = max(m_run, cm);
        float rescale = (m_run == -INFINITY) ? 0.0f : precise::exp(m_run - m_new);
        for (uint a = 0; a < 16; ++a) acc[a] *= rescale;
        float pr = (sc == -INFINITY) ? 0.0f : precise::exp(sc - m_new);
        l_run = l_run * rescale + simd_sum(pr);
        m_run = m_new;
        uint n = min(32u, p.L - c0);
        for (uint jj = 0; jj < n; ++jj) {
            float pj = simd_shuffle(pr, jj);
            if (pj == 0.0f) continue;
            device const float* vj = v + ((ulong)(c0 + jj) * p.KVH + kvh) * hd;
            for (uint a = 0; a < 16; ++a) { uint t = lane + a * 32; if (t < hd) acc[a] = fma(pj, vj[t], acc[a]); }
        }
    }
    device float* o = out + ((ulong)i * p.H + h) * hd;
    float inv = (l_run > 0.0f) ? 1.0f / l_run : 0.0f;
    for (uint a = 0; a < 16; ++a) { uint t = lane + a * 32; if (t < hd) o[t] = acc[a] * inv; }
}

// ---------------------------------------------------------------------------------------------------- attention (bf16 matrices)
// Flash attention on simdgroup matrices: q, k, v as bfloat (rows padded to a multiple of 8 with zeros), float
// accumulation, probabilities rounded to bfloat for the P.V product (the numerics of the bf16 training forward). One
// threadgroup = 4 simdgroups = 32 queries of one head; a simdgroup owns 8 queries and walks the keys 8 at a time:
// pass 1 collects each row's max and sum (online), pass 2 recomputes the scores and accumulates P.V with the final
// statistics, so no accumulator rescaling is needed. 8 x 8 tiles pass through threadgroup memory for the mask and
// softmax; lane l owns row l / 4 and columns 2 (l % 4), 2 (l % 4) + 1 of a tile.
struct AttnMmaParams { uint L, H, KVH, hd, window, is_global, full; };
#define MMA_Q 8

inline void score_tile(thread simdgroup_float8x8& acc, device const bfloat* qb, device const bfloat* kb, uint hd, uint qstride, uint kstride) {
    acc = simdgroup_float8x8(0.0f);
    for (uint t = 0; t < hd; t += 8) {
        simdgroup_bfloat8x8 a, b;
        simdgroup_load(a, qb + t, qstride);
        simdgroup_load(b, kb + t, kstride, ulong2(0, 0), true);
        simdgroup_multiply_accumulate(acc, a, b, acc);
    }
}

kernel void attention_mma(device const bfloat* q [[buffer(0)]], device const bfloat* k [[buffer(1)]], device const bfloat* v [[buffer(2)]],
                          device float* out [[buffer(3)]], device const int* seg [[buffer(4)]], device const int* pos [[buffer(5)]],
                          constant AttnMmaParams& p [[buffer(6)]], uint2 tg [[threadgroup_position_in_grid]],
                          uint lane [[thread_index_in_simdgroup]], uint sg [[simdgroup_index_in_threadgroup]]) {
    threadgroup float Ss[4][64];
    threadgroup bfloat Ps[4][64];
    const uint h = tg.y, i0 = (tg.x * 4 + sg) * MMA_Q, hd = p.hd, kvh = h / (p.H / p.KVH);
    if (i0 >= p.L) return;
    const uint qstride = p.H * hd, kstride = p.KVH * hd;
    const uint r = lane / 4, c0 = 2 * (lane % 4);
    const uint i = i0 + r;
    const bool i_ok = i < p.L;
    const int seg_i = i_ok ? seg[i] : 0, pos_i = i_ok ? pos[i] : 0;
    device const bfloat* qrow = q + ((ulong)i0 * p.H + h) * hd;
    threadgroup float* ss = Ss[sg];
    threadgroup bfloat* ps = Ps[sg];
    simdgroup_float8x8 acc;
    float m_r = -INFINITY, l_r = 0.0f;
    // pass 1: row statistics
    for (uint j0 = 0; j0 < p.L; j0 += 8) {
        bool ok[2];
        for (uint e = 0; e < 2; ++e) {
            uint j = j0 + c0 + e;
            ok[e] = i_ok && j < p.L && (seg[j] == 0 || seg[j] == seg_i || (p.full && seg_i == 0)) && (p.is_global || (uint)abs(pos_i - pos[j]) <= p.window);
        }
        if (!simd_any(ok[0] || ok[1])) continue;
        score_tile(acc, qrow, k + ((ulong)j0 * p.KVH + kvh) * hd, hd, qstride, kstride);
        simdgroup_store(acc, ss, 8);
        simdgroup_barrier(mem_flags::mem_threadgroup);
        float s0 = ok[0] ? ss[r * 8 + c0] : -INFINITY, s1 = ok[1] ? ss[r * 8 + c0 + 1] : -INFINITY;
        float mb = max(s0, s1);
        mb = max(mb, simd_shuffle_xor(mb, 1)); mb = max(mb, simd_shuffle_xor(mb, 2));
        if (mb != -INFINITY) {
            float m_new = max(m_r, mb);
            float ps0 = (s0 == -INFINITY) ? 0.0f : precise::exp(s0 - m_new), ps1 = (s1 == -INFINITY) ? 0.0f : precise::exp(s1 - m_new);
            float sum = ps0 + ps1;
            sum += simd_shuffle_xor(sum, 1); sum += simd_shuffle_xor(sum, 2);
            l_r = (m_r == -INFINITY ? 0.0f : l_r * precise::exp(m_r - m_new)) + sum;
            m_r = m_new;
        }
        simdgroup_barrier(mem_flags::mem_threadgroup);
    }
    const float inv = (l_r > 0.0f) ? 1.0f / l_r : 0.0f;
    // pass 2: probabilities and P.V, 256 dims of the head at a time
    for (uint d0 = 0; d0 < hd; d0 += 256) {
        const uint nf = min(32u, (hd - d0) / 8);
        simdgroup_float8x8 o[32];
        for (uint f = 0; f < 32; ++f) o[f] = simdgroup_float8x8(0.0f);
        for (uint j0 = 0; j0 < p.L; j0 += 8) {
            bool ok[2];
            for (uint e = 0; e < 2; ++e) {
                uint j = j0 + c0 + e;
                ok[e] = i_ok && j < p.L && (seg[j] == 0 || seg[j] == seg_i || (p.full && seg_i == 0)) && (p.is_global || (uint)abs(pos_i - pos[j]) <= p.window);
            }
            if (!simd_any(ok[0] || ok[1])) continue;
            score_tile(acc, qrow, k + ((ulong)j0 * p.KVH + kvh) * hd, hd, qstride, kstride);
            simdgroup_store(acc, ss, 8);
            simdgroup_barrier(mem_flags::mem_threadgroup);
            ps[r * 8 + c0] = bfloat(ok[0] ? precise::exp(ss[r * 8 + c0] - m_r) * inv : 0.0f);
            ps[r * 8 + c0 + 1] = bfloat(ok[1] ? precise::exp(ss[r * 8 + c0 + 1] - m_r) * inv : 0.0f);
            simdgroup_barrier(mem_flags::mem_threadgroup);
            simdgroup_bfloat8x8 pf;
            simdgroup_load(pf, ps, 8);
            device const bfloat* vb = v + ((ulong)j0 * p.KVH + kvh) * hd + d0;
            for (uint f = 0; f < 32; ++f) {
                if (f >= nf) break;
                simdgroup_bfloat8x8 b;
                simdgroup_load(b, vb + f * 8, kstride);
                simdgroup_multiply_accumulate(o[f], pf, b, o[f]);
            }
            simdgroup_barrier(mem_flags::mem_threadgroup);
        }
        device float* orow = out + ((ulong)i0 * p.H + h) * hd + d0;
        for (uint f = 0; f < 32; ++f) { if (f >= nf) break; simdgroup_store(o[f], orow + f * 8, qstride); }
    }
}

// y[n] = bfloat(x[n])
struct CastParams { uint n; };
kernel void to_bf16(device const float* x [[buffer(0)]], device bfloat* y [[buffer(1)]], constant CastParams& p [[buffer(2)]],
                    uint gid [[thread_position_in_grid]]) {
    if (gid < p.n) y[gid] = bfloat(x[gid]);
}

// ---------------------------------------------------------------------------------------------------- elementwise
struct GeluParams { uint n; };
kernel void gelu_mul(device float* g [[buffer(0)]], device const float* u [[buffer(1)]], constant GeluParams& p [[buffer(2)]],
                     uint gid [[thread_position_in_grid]]) {
    if (gid < p.n) g[gid] = gelu_tanh(g[gid]) * u[gid];
}

// g [L, P] = gelu(g) * ple[l, layer, :] with ple [L, layers, P]
struct PleParams { uint L, P, layers, layer; };
kernel void gelu_mul_ple(device float* g [[buffer(0)]], device const float* ple [[buffer(1)]], constant PleParams& p [[buffer(2)]],
                         uint gid [[thread_position_in_grid]]) {
    if (gid >= p.L * p.P) return;
    uint l = gid / p.P, i = gid % p.P;
    g[gid] = gelu_tanh(g[gid]) * ple[((ulong)l * p.layers + p.layer) * p.P + i];
}

// h = (h + x) * scale
struct AddParams { uint n; float scale; };
kernel void add_scale(device float* h [[buffer(0)]], device const float* x [[buffer(1)]], constant AddParams& p [[buffer(2)]],
                      uint gid [[thread_position_in_grid]]) {
    if (gid < p.n) h[gid] = (h[gid] + x[gid]) * p.scale;
}
