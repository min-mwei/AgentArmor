#include <metal_stdlib>
#include <metal_tensor>
#include <MetalPerformancePrimitives/MetalPerformancePrimitives.h>
using namespace metal;
using namespace mpp::tensor_ops;

struct TensorGemmParams { uint M, N, K; float alpha; uint epilogue; };

// Mixed fp32/bf16 and bf16/bf16 are separate candidates. Bounds remain dynamic at row tails.
template <typename T>
kernel void gemm_tensor(device T* x [[buffer(0)]], device bfloat* w [[buffer(1)]],
                         device float* y [[buffer(2)]], constant TensorGemmParams& p [[buffer(3)]],
                         uint2 group [[threadgroup_position_in_grid]]) {
    // MPP requires unqualified tensor element types; both source views remain read-only in this kernel.
    auto a = tensor<device T, dextents<int, 2>, tensor_inline>(x, dextents<int, 2>(p.K, p.M));
    auto b = tensor<device bfloat, dextents<int, 2>, tensor_inline>(w, dextents<int, 2>(p.K, p.N));
    auto dest = tensor<device float, dextents<int, 2>, tensor_inline>(y, dextents<int, 2>(p.N, p.M));
    auto as = a.slice(0, int(group.y) * 64);
    auto bs = b.slice<dynamic_extent, 64>(0, int(group.x) * 64);
    constexpr auto desc = matmul2d_descriptor(64, 64, dynamic_length_v<int>, false, true);
    matmul2d<desc, execution_simdgroups<4>> op;
    auto result = op.get_destination_cooperative_tensor<decltype(as), decltype(bs), float>();
    op.run(as, bs, result);
    for (uint i = 0; i < result.get_capacity(); ++i) result[i] *= p.alpha;
    result.store(dest.slice(int(group.x) * 64, int(group.y) * 64));
}

template [[host_name("gemm_tensor_fp32")]] kernel void gemm_tensor<float>(
    device float*, device bfloat*, device float*, constant TensorGemmParams&, uint2);
template [[host_name("gemm_tensor_bf16")]] kernel void gemm_tensor<bfloat>(
    device bfloat*, device bfloat*, device float*, constant TensorGemmParams&, uint2);
