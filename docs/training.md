# Kai training and serving environments

AgentArmor serves Kai; the [Kai repository](https://github.com/min-mwei/Kai) owns
training, data admission, checkpoint selection and export qualification. Its
[training guide](https://github.com/min-mwei/Kai/blob/dev/docs/kai-training.md)
defines the registered model and environment. Generic language-model fine-tuning
does not produce Kai's pointer decision head.

## Dependencies

The extras in [pyproject.toml](../src/vllm/pyproject.toml) isolate serving from
optional training tools. vLLM runs inference; Transformers/PEFT and MLX-LM perform
training. A dependency upgrade in a training environment must not change serving.

| Extra | Platform | Pinned core dependencies |
|---|---|---|
| `serve` | Supported Linux vLLM host | vLLM 0.33.0, Transformers 5.17.0, HF Hub 1.32.0; vLLM selects Torch 2.14.0 |
| `train-transformers` | Linux GPU training host | Torch 2.14.0, Transformers 5.17.0, PEFT 0.21.2, Accelerate 1.15.0, Datasets 5.1.0, HF Hub 1.32.0 |
| `train-mlx` | Apple silicon | MLX/MLX-Metal 0.32.4, MLX-LM 0.32.2, Transformers 5.17.0, Datasets 5.1.0, HF Hub 1.32.0 |

Use Python 3.11–3.13. Install only the relevant environment on each host:

```sh
# Linux serving host
python3 -m venv .venv-serve
.venv-serve/bin/pip install './src/vllm[serve]'

# Separate Transformers experiment environment
python3 -m venv .venv-train-transformers
.venv-train-transformers/bin/pip install './src/vllm[train-transformers]'

# Separate Apple silicon experiment environment
python3 -m venv .venv-train-mlx
.venv-train-mlx/bin/pip install './src/vllm[train-mlx]'
```

The optional extras supply tools, not a registered Kai recipe. Kai's active
training environment has its own version and numerical constraints. Do not
replace it with these experiment extras or assume that their LoRA configuration,
loss, adapter format or forward computation matches Kai. Follow the trainer's
resource guard, input coverage and evaluation requirements.

## Publishing Kai for serving

Kai-4B uses Qwen3.5-4B-Base with trained adapters and a pointer head. The trainer
publishes a merged BF16 backbone, FP32 pointer head and hash manifest under
`vllm/` in [min-h-wei/Kai-4B](https://huggingface.co/min-h-wei/Kai-4B).
The [export contract](../src/vllm/README.md#kai-export-contract) defines the required
files, tokenizer identity, temperature, pause layout and context limits.

Preserve the exact trained decision computation. Exporting or merging changes
numerical behavior and needs a comparison against the training scorer. Record
probability errors and decision changes on the same complete inputs before
selecting serving thresholds. Model files, adapters and training logs stay outside
this repository; do not train into or modify a cached HF snapshot.

Launch `agentarmor-vllm --kai-revision "<HF_COMMIT_SHA>"` with the published commit.
The server downloads and verifies the export before model allocation. It never
merges adapters, launches training or switches models during a request.
`policy_ready: false` remains until numerical and application-level qualification
passes. The lightweight hook CLI requires neither a model nor a GPU runtime.
