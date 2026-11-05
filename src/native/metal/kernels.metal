// SPDX-License-Identifier: Apache-2.0
// RMS reduction and GDN lane mapping adapted from Splash, commit
// 031a25a6125af2d7acf7096580afe56eb33d65bc. See src/ORIGIN.md and src/NOTICE.
#include <metal_stdlib>
using namespace metal;

// 256 threads per row, two-stage SIMD reduction; no device atomics.
kernel void armor_rms(device const float* x [[buffer(0)]],
                      device const float* w [[buffer(1)]], device float* y [[buffer(2)]],
                      constant uint& width [[buffer(3)]], constant float& eps [[buffer(4)]],
                      constant float& offset [[buffer(5)]],
                      uint row [[threadgroup_position_in_grid]], uint tid [[thread_index_in_threadgroup]],
                      uint lane [[thread_index_in_simdgroup]], uint sg [[simdgroup_index_in_threadgroup]]) {
    threadgroup float sums[8];
    float sum=0;
    for (uint c=tid; c<width; c+=256) sum+=x[ulong(row)*width+c]*x[ulong(row)*width+c];
    sum=simd_sum(sum);
    if (lane==0) sums[sg]=sum;
    threadgroup_barrier(mem_flags::mem_threadgroup);
    if (tid==0) {
        float total=0;
        for (uint i=0; i<8; ++i) total+=sums[i];
        sums[0]=rsqrt(total/float(width)+eps);
    }
    threadgroup_barrier(mem_flags::mem_threadgroup);
    for (uint c=tid; c<width; c+=256) y[ulong(row)*width+c]=(x[ulong(row)*width+c]*sums[0])*(w[c]+offset);
}

// One SIMD group per option. Query/keys have already passed through Kai's affine heads.
kernel void armor_pointer(device const float* q [[buffer(0)]], device const float* k [[buffer(1)]],
                          device float* y [[buffer(2)]], constant uint& dim [[buffer(3)]],
                          uint option [[threadgroup_position_in_grid]], uint lane [[thread_index_in_simdgroup]]) {
    float sum=0;
    for (uint c=lane; c<dim; c+=32) sum+=q[c]*k[ulong(option)*dim+c];
    sum=simd_sum(sum);
    if (lane==0) y[option]=sum*rsqrt(float(dim));
}

// Specialized Qwen3.5 head dimension 128. A group covers 16 value rows;
// eight lanes hold one state row (16 columns each) in registers through the scan.
// FP32 is a development reference; BF16 rounding parity with the trainer is unqualified.
struct DeltaShape { uint tokens, key_heads, value_heads; };
kernel void armor_delta(device const float* q [[buffer(0)]], device const float* k [[buffer(1)]],
                        device const float* v [[buffer(2)]], device const float* decay [[buffer(3)]],
                        device const float* beta [[buffer(4)]], device const float* initial [[buffer(5)]],
                        device float* state [[buffer(6)]], device float* output [[buffer(7)]],
                        constant DeltaShape& shape [[buffer(8)]], uint group [[threadgroup_position_in_grid]],
                        uint tid [[thread_index_in_threadgroup]], uint lane [[thread_index_in_simdgroup]]) {
    const uint head=group/8, row=(group%8)*16+tid/8, col=(lane%8)*16;
    const ulong base=(ulong(head)*128+row)*128+col;
    float s[16];
    for (uint c=0; c<16; ++c) s[c]=initial[base+c];
    const uint kh=head/(shape.value_heads/shape.key_heads);
    for (uint t=0; t<shape.tokens; ++t) {
        const ulong qb=(ulong(t)*shape.key_heads+kh)*128+col;
        float memory=0;
        for (uint c=0; c<16; ++c) { s[c]*=decay[t*shape.value_heads+head]; memory+=s[c]*k[qb+c]; }
        for (uint step=1; step<8; step*=2) memory+=simd_shuffle_xor(memory,step);
        const ulong vi=(ulong(t)*shape.value_heads+head)*128+row;
        const float delta=(v[vi]-memory)*beta[t*shape.value_heads+head];
        float value=0;
        for (uint c=0; c<16; ++c) { s[c]+=k[qb+c]*delta; value+=s[c]*q[qb+c]; }
        for (uint step=1; step<8; step*=2) value+=simd_shuffle_xor(value,step);
        if (lane%8==0) output[vi]=value;
    }
    for (uint c=0; c<16; ++c) state[base+c]=s[c];
}
