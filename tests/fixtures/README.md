# Renderer migration references

`renderer_migration.json` captures two synthetic outputs from the retired
Python renderer at commit `466a966`, before `armor/` was removed. That source is not included in this repository snapshot.
No retired Python package is needed to consume these records.

The tool-call example preserves JSON quoting of forged roles and non-ASCII
content. The contextual example preserves code-point offsets across an emoji,
a combining character, and an escaped source name. These are byte/offset
compatibility references, not proof that the legacy renderer is safe or that
the contextual profile is calibrated. They contain no model outputs or cutoffs.

Any renderer claiming legacy compatibility should consume these fixtures. A richer trust/event format requires a new profile. The Kai
trainer must still provide the full tokenizer/layout/readout golden set and
model-backed parity fixtures for a proposed serving profile.

`qwen_encoding.json` was generated from Kai's unmodified `train/kai/api.py` and
`train/kai/qwen/encode.py` at `25a73b0fd2bb1d8aced73bb7de5937c0e58f5a59`.
It uses synthetic text and a character tokenizer defined in `test_vllm_baseline.py`.
It freezes nested rendering, canonical Choice order, Noul/Score options, Unicode,
delimiter escaping, pause/no-pause layouts and branch readout offsets. It verifies
source-port fidelity, not the real Qwen tokenizer or model.
