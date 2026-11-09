> Historical Kai 0.8B v0 results. For the current Kai-4B comparison with Kev-4B and OpenAI Decisions, see the
> [benchset](../benchset/README.md); every number there is reproducible with `python3 benchset/score.py`.

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

Sources: the trainer's v0 development manifest (`v0-manifest.json`) records scores, cutoff procedures,
source-row hashes, and bootstrap details; the release summary (`release.json`) records both models' counts and
cutoffs; the merged-model manifest (`manifest.json`) records the trainer's native BF16 parity read. These v0 files
were published in the retired `min-h-wei/Kai` repository at revision `d0d6a569`, which was removed from Hugging Face
on 2026-11-08; the trainer keeps a hash-verified local archive of every revision. The published provenance omits
personal filesystem prefixes; artifact hashes are retained.

These development panels informed checkpoint selection. There is no locked-test,
general multi-turn, or deployment-wide accuracy claim. These historical results
do not qualify the current Kai vLLM integration. The [serving contracts](../src/vllm/README.md)
test weight-free mechanics; model accuracy, evidence, parity, and latency still require qualification.
