"""Add unnormalized decoder token embeddings to the vllm-metal 0.32 pooling API.

Installed only in the AgentArmor process, before its single-process vLLM engine.
Upstream's LAST-token embedding and reranker paths retain their original behavior.
"""

import importlib.metadata


def enable_token_embeddings():
    version = importlib.metadata.version("vllm-metal")
    if version.split("+", 1)[0] != "0.32.0":
        raise RuntimeError(
            "AgentArmor's Metal token-embedding adapter requires "
            f"vllm-metal 0.32.0; found {version}"
        )
    import mlx.core as mx
    from vllm_metal.tensor_bridge import mlx_to_torch
    from vllm_metal.v1 import model_lifecycle
    from vllm_metal.v1.cache_policy import WorkerCachePlanner
    from vllm_metal.v1.pooling import options, validation
    from vllm_metal.v1.pooling.backends.decoder import factory
    from vllm_metal.v1.pooling.backends.decoder.runtime import (
        DecoderModelView,
        MetalDecoderPoolingBackend,
    )
    from vllm_metal.v1.pooling.validation import PoolingConfigView

    if getattr(factory, "_agentarmor_token_embeddings", False):
        return
    original = factory.build_decoder_pooling_backend
    original_options = options.unsupported_pooling_option
    original_memory = WorkerCachePlanner.determine_available_memory

    def cache_memory(planner):
        config = planner._worker.vllm_config
        budget = config.cache_config.kv_cache_memory_bytes
        pooler = config.model_config.pooler_config
        if pooler is None or pooler.task != "token_embed" or budget is None:
            return original_memory(planner)
        if planner._worker.model_runner.scheduler_memory_reporting_mode() != (
            "paged_attention_layout_budget"
        ):
            raise RuntimeError("Kai Metal cache budgeting requires the upstream cache layout")
        # Metal 0.32 ignores vLLM's explicit byte budget. Match the core worker
        # contract instead of allocating a percentage of all shared memory.
        planner._worker.model_runner.profile_run()
        return budget

    def unsupported_option(parameters):
        if parameters.task != "token_embed":
            return original_options(parameters)
        # Upstream rejects ALL-token output and activation=False. Those are
        # precisely Kai's semantics; keep every other upstream option check.
        checked = parameters.clone()
        checked.requires_token_ids = False
        checked.use_activation = None
        return original_options(checked)

    class TokenPooler:
        task = "token_embed"

        def is_supported(self):
            return True

        def pool_one(self, hidden_states, span):
            if not span.is_complete:
                raise ValueError("Kai token embeddings require a complete, unchunked prompt")
            if hidden_states.ndim != 3 or hidden_states.shape[0] != 1:
                raise ValueError("Expected [1, tokens, hidden] from Metal")
            if (
                span.start_row < 0
                or span.num_tokens < 1
                or span.start_row + span.num_tokens > hidden_states.shape[1]
            ):
                raise ValueError("Invalid Metal token span")
            values = hidden_states[0, span.start_row : span.start_row + span.num_tokens]
            if values.shape[0] != span.num_tokens:
                raise ValueError("Metal did not return every prompt token")
            return mlx_to_torch(values.astype(mx.float32), device="cpu").detach().clone()

    class TokenBackend(MetalDecoderPoolingBackend):
        def validate_params(self, parameters):
            if parameters.task != "token_embed":
                raise ValueError("Kai Metal runner supports token_embed only")
            if parameters.dimensions is not None:
                raise ValueError("Kai's pointer head requires all hidden dimensions")
            if (
                self.config.pooler_config.use_activation is not False
                or parameters.use_activation is True
            ):
                raise ValueError("Kai requires unnormalized hidden states")

    def build(model, model_config, tokenizer):
        if model_config.pooler_config.task != "token_embed":
            return original(model, model_config, tokenizer)
        config = PoolingConfigView(model_config)
        if model_config.max_model_len < 1:
            raise ValueError("Invalid Metal context length")
        return TokenBackend(config, DecoderModelView(model), (TokenPooler(),))

    factory.build_decoder_pooling_backend = build
    model_lifecycle.build_decoder_pooling_backend = build
    options.unsupported_pooling_option = unsupported_option
    validation.unsupported_pooling_option = unsupported_option
    WorkerCachePlanner.determine_available_memory = cache_memory
    factory._agentarmor_token_embeddings = True
