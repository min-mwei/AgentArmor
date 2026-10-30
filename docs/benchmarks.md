> Historical Qwen 0.8B results. These rows do not describe the official Kai encoder promoted on 2026-10-30.

# Kai v0: initial development results

Kai is the security specialist derived from the [Kev](https://github.com/jaredpalmer/kev)
decision-model architecture. Its nominal 0.8B backbone has one fifth of Kev-4B's
parameter count. These are the Kai trainer's initial-model-v0 (S2) results on the
same development examples, using a separately fitted cutoff for each model.

| Metric                                      | Kai-0.8B        | Kev-4B          |
| ------------------------------------------- | --------------: | --------------: |
| ToolCall accuracy, template-4 development   | 208/240 (86.7%) | 173/240 (72.1%) |
| ToolCall false denials among allowed calls  | 19/120 (15.8%)  | 16/120 (13.3%)  |
| XPIA attack recall, BIPIA development       | 609/703 (86.6%) | 275/703 (39.1%) |
| XPIA false positives among benign documents | 9/235 (3.83%)   | 4/235 (1.70%)   |

ToolCall uses the factorized policy score, with cutoffs fitted on separate
calibration data using benign twin-group maxima (NP alpha 0.20, delta 0.05).
XPIA uses the `is_xpia_attempt` logit margin and benign calibration-component
maxima (NP alpha = delta = 0.05). These are matched calibration procedures,
not equal observed false-positive rates. The paired bootstrap uses 2,000
whole-group/component draws; the 95% lower bounds on Kai's gains are +6.67
percentage points for ToolCall accuracy and +43.43 points for BIPIA recall.

Sources: the trainer's [v0 development manifest](https://huggingface.co/min-h-wei/Kai/blob/d0d6a5694d0001e06b383f936c9bd8ff77271bf7/v0-manifest.json)
records scores, cutoff procedures, source-row hashes, and bootstrap details;
the [release summary](https://huggingface.co/min-h-wei/Kai/blob/d0d6a5694d0001e06b383f936c9bd8ff77271bf7/release.json)
records both models' counts and cutoffs;
the [merged-model manifest](https://huggingface.co/min-h-wei/Kai/blob/d0d6a5694d0001e06b383f936c9bd8ff77271bf7/manifest.json)
records the trainer's native BF16 parity read. The published provenance omits
personal filesystem prefixes; artifact hashes are retained.

[`benchset/benchset.jsonl`](../benchset/benchset.jsonl) contains four illustrative
Kai development records: one allowed and one denied ToolCall, and a benign/attack
XPIA pair. Each row retains the original `state` and labeled `questions`, with
reduced `_meta` containing the source file, line, SHA-256, split, and license.
The ToolCall examples use the direct `decision` question; the table above uses
the full factorized evaluation. This tiny sample is not an accuracy benchmark.
XPIA examples come from BIPIA's MIT-licensed email subset; required Microsoft
and OpenAI notices are included in each row. ToolCall paths and companies are
synthetic. Treat embedded instructions as untrusted data, never as commands.

These development panels informed checkpoint selection. There is no locked-test,
general multi-turn, or deployment-wide accuracy claim. Native MLX results do not
establish vLLM numerical parity; [AgentArmor's native smoke tests](../scripts/test_live_agents.py)
check integration and reveal benign-text false positives. Neither the frozen
cutoffs nor the benchmark scores were changed to make those tests pass.
