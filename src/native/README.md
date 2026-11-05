# Native Qwen kernel work

`kernels.cpp` supplies bounded CPU references for RMSNorm, projected pointer
scores and Gated DeltaNet. `metal/runtime.mm` owns Metal resources through ARC,
checks allocation/command failures and returns finite outputs. Shader source is
embedded at build time; the runtime compiles it without external shader paths.

The Metal specialization uses 256-thread SIMD reductions for norms, one SIMD
group per pointer option, and register-resident 128-column DeltaNet state rows.
DeltaNet's state is `[value_head, value_dimension, key_dimension]`. Q/K are
already normalized/scaled; decay and beta are already transformed. Input state
is immutable, so separate question branches can fork the same prefix without
sharing mutations. The scan supports the 4B model's 16 key and 32 value heads.

Norm `offset` is explicit: Qwen's ordinary RMSNorm uses `weight + 1`; gated
DeltaNet normalization uses its own weight convention. Pointer inputs here are
already projected with the trained q/k weights and biases. Affine projection,
convolution, RoPE, gated normalization, full attention, MLP, BF16 storage and
rounding, weight loading and native text tokenization remain to be integrated.
The FP32 kernels are numerical development primitives, not a complete model.

Splash informed the SIMD reduction and DeltaNet lane mapping; the adaptation
removes its model-specific ABI, BF16/GGUF layouts, generation cache and DFlash
scheduler. See `src/ORIGIN.md`. No speculative decoding is needed to compute a
single decision pass. There are no speed claims until matched benchmarks run.

`main.cpp` retains the bounded JSON parser and fail-closed native hook adapter.
It does not load a model. `doctor` explicitly reports that state.
