# Renderer migration references

`renderer_migration.json` captures two synthetic outputs from the retired
Python renderer at commit `466a966`, before `armor/` was removed. The source
remains available in Git history. No production Python package is needed to
consume these records.

The tool-call example preserves JSON quoting of forged roles and non-ASCII
content. The contextual example preserves code-point offsets across an emoji,
a combining character, and an escaped source name. These are byte/offset
compatibility references, not proof that the legacy renderer is safe or that
the contextual profile is calibrated. They contain no model outputs or cutoffs.

The future native renderer should consume these fixtures when it claims legacy
compatibility. A richer trust/event format requires a new profile. The Kai
trainer must still provide the full tokenizer/layout/readout golden set and
CPU/Metal parity fixtures specified in
[Kai_requirements.md](https://github.com/min-mwei/Kai/blob/dev/docs/Kai_requirements.md#native-tokenizerencoding-golden-fixtures).
