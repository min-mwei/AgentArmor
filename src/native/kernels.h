#pragma once
#include <cstddef>
#include <memory>
#include <vector>

namespace armor {
using Floats = std::vector<float>;
struct DeltaResult { Floats output, state; };
// Inputs are post-convolution, normalized/scaled Q/K, and transformed decay/beta.
// State layout [value_head, value_dim, key_dim]; output [token, value_head, dim].
struct DeltaInput {
    Floats q, k, v, decay, beta, state;
    size_t tokens, key_heads, value_heads, dim;
};
void validate_norm(const Floats&, const Floats&, size_t width, float epsilon, float offset);
void validate_pointer(const Floats& query, const Floats& keys);
void validate_delta(const DeltaInput&);
Floats rms_norm(const Floats&, const Floats&, size_t width, float epsilon, float offset);
// Projected q(h_decide), k(h_options), including trained biases. FP32 readout.
Floats pointer_logits(const Floats& query, const Floats& keys);
DeltaResult delta_scan(const DeltaInput&);
#ifdef AGENTARMOR_HAS_METAL
class MetalKernels {
    struct Impl;
    std::unique_ptr<Impl> impl_;
public:
    MetalKernels();
    ~MetalKernels();
    MetalKernels(const MetalKernels&) = delete;
    MetalKernels& operator=(const MetalKernels&) = delete;
    Floats rms_norm(const Floats&, const Floats&, size_t width, float epsilon, float offset);
    Floats pointer_logits(const Floats& query, const Floats& keys);
    DeltaResult delta_scan(const DeltaInput&);
};
#endif
} // namespace armor
