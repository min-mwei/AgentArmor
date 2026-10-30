# Kai service and agent hooks

AgentArmor serves the official Kai encoder. The active backend is `kai`, using the canonical MLX model, tokenizer,
pointer head and evidence decoder from the Kai source repository. `stub` is a deny-only smoke backend. There is no
Qwen/vLLM or reference-server fallback. The earlier service guide is archived in docs/history/server-qwen-v0.md.

## GPU setup

The model runtime currently requires Apple Silicon and Python 3.13. Hook/API-only development works without MLX.

```sh
python3 install.py --backend kai --kai-source /path/to/Kai
# API and hook tests only:
python3 install.py --backend cpu
```

Use the installed environment to run `agentarmor doctor`. No weights load during diagnostics. The trainer must choose
and freeze the serving checkpoint(s), exact typed questions, renderer profiles and calibrated cutoffs. Do not apply old
0.8B thresholds or a benchmark-fitted threshold to the encoder.

## Bind a deployment package

`python -m armor.package` is CPU-only. It hashes existing artifacts and never fits a cutoff or copies weights. The
contract JSON has `toolcall` and `xpia` objects, each with `question`, probability `cutoff`, `profile` and optional
`evidence_threshold`. ToolCall uses the registered choice question with `allow` and `deny`; XPIA uses its registered
noul question. Supported profiles are `armor-toolcall-v1` and either `xpia-document-v0` or `xpia-context-v1`.

```sh
python -m armor.package --base /path/to/embeddinggemma-snapshot \
  --toolcall-run /path/to/toolcall-run --xpia-run /path/to/xpia-run \
  --contract /path/to/trainer-registered-contract.json --out .artifacts/kai-package
```

The resulting `kai-encoder-v1` manifest binds base weights/config/tokenizer, checkpoint weights/meta, questions,
cutoffs, evidence thresholds, model-code hashes and numerical dependency versions. The model loader refuses a
changed file, runtime mismatch, unsupported mask/grounding profile, or a scan renderer different from the registration.
Build the package in the same runtime used to serve and qualify it. `agentarmor inspect` verifies files, not model quality.

Set `ARMOR_CANDIDATE_PACKAGE` and optionally `ARMOR_PACKAGE_SHA256` to that artifact. `ARMOR_SCAN_PROFILE` must match
its XPIA profile. Keep credentials in the environment, never in a package or source file. A package is experimental
until the trainer's checkpoint/parity/calibration/span/latency qualification passes.

```sh
# Run from an environment with AgentArmor and official Kai installed.
python -m kai.guard run .artifacts/kai-service.log -- \
  python -m armor.cli serve --backend kai --package .artifacts/kai-package
```

The guard owns the machine-wide GPU lock and memory/swap limits. Model execution is serialized inside the service.
The service never downloads a model silently. Stop it before the trainer resumes training.

## HTTP interface

- `GET /healthz`, `GET /readyz`, `GET /v1/models`: process/model readiness.
- `POST /v1/systemone`: supplied `state` and typed `questions`, returning probabilities, diagnostic evidence and zero
  generated tokens. Mixed routed ToolCall/XPIA questions are refused. The served model alias is `kai`.
- `POST /v1/guard`: `{agent, policy, task, tool_name, tool_input, history?}`. Policy is JSON text containing
  `environment` and rules with explicit `id`, `tier` and `description`. The request builder emits Armor text. Optional
  history is chronological `{role, content}` turns. Rules may be empty when the operator sets no additional rule.
- `POST /v1/scans`, `GET /v1/scans/{id}`, `GET /v1/scans/{id}/result`: bounded asynchronous scans.

Tool guards use the package's direct `decision` question and deny when `p(deny)` is strictly above its registered
cutoff. They do not invoke the earlier process-question composite. State contains the initial task followed by the
supplied history and proposed call. Tool content stays lower trust and is quoted so embedded role labels do not become
new transcript turns. An allow abstains from the coding agent's existing permission flow; errors explicitly deny.

`history` accepts system, developer, user, assistant and tool roles. Native hooks pass supported Claude/Codex transcript
messages, retaining tool results and user cancellations without repeating dual-logged Codex authorizations. Unsupported
non-text content or unreadable history refuses the check. Inputs that exceed the checkpoint's complete state/branch or
packed token limits are refused without truncation. Long real sessions therefore need a separately qualified context
profile; this implementation does not silently summarize authorization history.

## Asynchronous scans

`xpia-document-v0` evaluates the submitted text only. Policy/task fields remain metadata for that profile.
`xpia-context-v1` renders policy, task, supplied history and the lower-trust source with source-offset mappings; it needs
its own registered cutoff and question. Evidence uses half-open Unicode code-point offsets, and contextual results map
locations back to policy/task/history/text. Diagnostic spans are not a proof-quality claim.

Scans cover every input chunk, with overlap and no silent clipping. Chunk/context overflow fails the job, not a clean
verdict. Background warnings are advisory and cannot undo an action that already completed. Multi-turn protection
requires the relevant history and separately measured coverage.

## Verification

```sh
ruff check .
ruff format --check .
pytest
```

CPU tests use pinned dummy artifacts and fake model outputs. They check the real request/serialization/decision paths,
file and runtime identity, Unicode evidence, history trust and overflow behavior. Actual model qualification is a
trainer-owned guarded `python -m armor.validation` run on a registered `agentarmor-kai-parity-v1` fixture. It requires
probability and decision parity, valid evidence offsets and retained repeat/latency rows. CPU success does not replace
that read. The native Kai C++ service still exposes encoded tensors; this MLX adapter is the reference integration
for later native-backend qualification.
