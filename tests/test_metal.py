"""Optional tests of real Metal pooling contracts using CPU arrays, without model weights."""

import importlib.metadata
from types import SimpleNamespace

import numpy as np
import pytest


def test_metal_preserves_all_raw_token_states():
    mx = pytest.importorskip("mlx.core")
    pytest.importorskip("vllm_metal")
    if importlib.metadata.version("vllm-metal") != "0.32.0":
        pytest.skip("This adapter targets the pinned 0.32.0 contract")
    from vllm.config import PoolerConfig
    from vllm.pooling_params import PoolingParams
    from vllm_metal.v1.cache_policy import WorkerCachePlanner
    from vllm_metal.v1.pooling.backends.decoder import factory
    from vllm_metal.v1.pooling.contract import DecoderPoolingBatch, DecoderPoolingSpan
    from vllm_metal.v1.pooling.options import unsupported_pooling_option as original_options

    from armor.metal import enable_token_embeddings

    enable_token_embeddings()
    from vllm_metal.v1.pooling.options import unsupported_pooling_option

    config = SimpleNamespace(
        pooler_config=PoolerConfig(task="token_embed", use_activation=False),
        max_model_len=32,
    )
    profiled = []
    worker = SimpleNamespace(
        vllm_config=SimpleNamespace(
            model_config=config,
            cache_config=SimpleNamespace(kv_cache_memory_bytes=512 * 1024**2),
        ),
        model_runner=SimpleNamespace(
            scheduler_memory_reporting_mode=lambda: "paged_attention_layout_budget",
            profile_run=lambda: profiled.append(True),
        ),
    )
    assert WorkerCachePlanner(worker).determine_available_memory() == 512 * 1024**2
    assert profiled == [True]
    backend = factory.build_decoder_pooling_backend(
        SimpleNamespace(model=lambda ids: ids), config, None
    )
    parameters = PoolingParams(task="token_embed", use_activation=False, requires_token_ids=True)
    assert unsupported_pooling_option(parameters) is None
    assert original_options(PoolingParams(task="embed", use_activation=False)) is not None
    assert unsupported_pooling_option(PoolingParams(task="token_embed", step_tag_id=5)) is not None
    backend.validate_params(parameters)
    with pytest.raises(ValueError, match="unnormalized"):
        backend.validate_params(PoolingParams(task="token_embed", use_activation=True))
    values = np.arange(24, dtype=np.float32).reshape(1, 6, 4) * 2 + 1
    with mx.stream(mx.cpu):
        hidden = mx.array(values)
        spans = (
            DecoderPoolingSpan(0, 2, True, parameters),
            DecoderPoolingSpan(2, 4, True, parameters),
        )
        results = backend.pool_packed(hidden, DecoderPoolingBatch(spans))
        np.testing.assert_array_equal(results[0].numpy(), values[0, :2])
        np.testing.assert_array_equal(results[1].numpy(), values[0, 2:])
        pooler = backend.poolers_by_task["token_embed"]
        with pytest.raises(ValueError, match="unchunked"):
            pooler.pool_one(hidden, DecoderPoolingSpan(0, 2, False, parameters))
        with pytest.raises(ValueError, match="span"):
            pooler.pool_one(hidden, DecoderPoolingSpan(-1, 1, True, parameters))
