# Run sheet: the next model and integration jobs

2026-11-14. Model Trainer, for the [stack qualification plan](stack-qualification-plan.md),
section 9, items 2 and 3 (MR216, corrected by MR217). It names each job, what it reads and
writes, what it costs going by earlier receipts, and whether it may be launched today.
Nothing here is a result. `policy_ready: false`.

Pins: AgentArmor runtime freeze `a453deb` (only documents and tests have changed since), Kai
`3406d0c53`, freeze manifest Kai `train/recipes/kai-session-decision-20261113-v1/cycle-freeze-20261114.json`
(budget 11,000 characters, layout `agentarmor-state/v3`, scorer `campaign-contract/16`).

## 1. Roles of the data

| Role | Files | Notes |
|---|---|---|
| Encoder TRAIN | `training/summarizer/data/starter/facets.jsonl` (85), `synthetic/batch-002.jsonl` (164) | Accepted. `batch-003a.jsonl` (240) was commissioned as training; no acceptance is recorded, so it is not eligible yet. No intent or relevance pairs exist. |
| Kai TRAIN roots | `synthetic/session-roots-train-001-r6.jsonl`, `-002-r3.jsonl` (50 roots each) | Pinned by the freeze. Training data only (data request 008). |
| DEV, sessions | `synthetic/campaigns-dev-001-r1.jsonl` (40), `-002-full.jsonl` (60), `-003-r5.jsonl` (80) | Already exposed. They select and diagnose; they qualify nothing. |
| DEV, encoder | `eval/pilot-001`, `dev-002`, `pilot-003`, `dev-003-t1`, `dev-003-t2`; blocks in `eval/blocks.jsonl` | Data roles as in the [probe protocol](../training/summarizer/docs/probe-protocol.md). |
| Controls | `controls/session-controls-001-r2.jsonl` (46) | Never training data. |
| Kai DEV panels | The five public panels of Kai's retention rule v2 | Public and diagnostic. Kai's evaluation guide says the next selection needs fresh, unpublished panels. |
| CAL | None | Kai's evaluation guide, section 8: no CAL population, cutoff or locked test exists. |
| Test | Test 004 (data request 013), held out | Reserved for the frozen v4 qualification and the final model read. Tests 001 to 003 are spent or diagnostic. |

## 2. Model identities

- **K0**, the incumbent Kai-4B:
  - Run 1, seed 0, update 582, from `Qwen/Qwen3.5-4B-Base` at `1001bb4d826a52d1f399e183466143f4da7b741b`. LoRA rank 32, pointer head, temperature 1.0, `max_state` 2,048.
  - Checkpoint: adapter `f13266d5…`, head `4198caa1…`. Published as `min-h-wei/Kai-4B` at `5bd63725…`, `vllm/` manifest `b91aa764…`.
  - Its original recipe fails `check` at Kai HEAD (11 of 30 code pins differ). Reads use the current-closure template `train/recipes/qwen-multibase-20261110-dense-parity-v1/new-dev-read-kai4b.json` (`e14f3b57…`), which matches HEAD.
- **E0**, the base encoder: `google/embeddinggemma-2` at `f9972ac22e696a105c482202646979e33e4b5a1e`, text encoder only. bfloat16 on MPS and on CUDA with bf16, float32 otherwise. Policy `windows/1`, window 1,024 tokens, probes `default-v3`, calibrated.

## 3. The fixed-Kai DEV comparison (plan section 4, first column)

**Question.** With K0 held fixed, on the same calls:
- Does K0 decide right when the complete source-bound reference packet is shown?
- What does today's history packet, built with E0, lose?

**Data.** `campaigns-dev-003-r5.jsonl`: 80 roots in two layouts, so 160 views per arm, in the `history` and `reference` arms. Under scorer 16 at 11,000, 128 of the 160 history views are complete. dev 002 full (60 roots) is the second file if a finding needs it.

**Steps:**
1. Render with E0, first replay, budget 11,000:
   `campaign_arms.py write ROOTS --out DIR --budget 11000 --embedder embeddinggemma --replays first`.
   This is a development run of the encoder. It takes about 3 minutes per 100 roots on this machine.
2. Make the panels: `agentarmor-monitor panels DIR/packets.jsonl --out PANELS --purpose evaluation`.
3. Admit every request with Kai's tokenizer at K0's 2,048. A request that does not fit is held and counted, never cut.
4. Build K0's read recipe by binding the panels to the template above, then run `kai.qwen.run check` (CPU).
5. Read K0 (`kai.qwen.run read`). Going by K0's DEV reads (1,654 requests in 460 to 467 s, host peak 45 to 53 GB), 320 requests take a few minutes after loading.
6. Score with `campaign_arms.py score DIR --answers F`, which already separates:
   - not asked;
   - not admitted;
   - no valid reference;
   - reference does not separate;
   - same evidence decided differently;
   - evidence missing.
   `history_diagnosis.py --out` adds, per fact, whether it was lost at retention, selection or fit.

**The tooling to reuse.** Kai's `train/recipes/summarizer-campaign-measure-20261113-v1` already chains steps 1 to 6 with pins, scope checks and guards. It is registered for the older contract (Summarizer `d2c23f4`, scorer 12, budget 3,000, layout v1). The next artifact is its version 3, re-pinned to the freeze: AgentArmor `a453deb`, budget 11,000, layout v3, scorer 16, and the DEV file above. That is CPU work.

**The error ledger.** Each missed attack or benign denial goes to exactly one bucket, with its evidence:
- capture or routing (not asked);
- missing evidence (the reference has it, history does not);
- packing (dropped for room);
- decision (the evidence was shown and the answer is wrong);
- serving or transport.

No tool joins these per call today. The ledger is a small CPU script over the scored packets and answers.

## 4. E1: what the encoder can be trained for now

**What the code shows** (EG2 tools review, 2026-11-14):
- The packet builder does no encoder ranking. `history.py` ties earlier sessions to a call by exact touches: files, words and kinds of action.
- `Index.like` has no caller, and Select uses `_diverse`, not a ranking against the call.
- The encoder is used today for **Mark** (probe families) and **Link** (an action's nearest user request against its nearest lower-trust content, `goal_drift`).
- So a relevance-trained E1 would change no packet until something consumes its ranking.

**The first E1 experiment.**
- **Targets:** Mark and Link only, on encoder TRAIN, with K0 fixed. This is `finetune.py` CoSENT on the three probe families, with Matryoshka 768/512/256/128, token embeddings frozen and the 512-token refusal kept.
- **Report on DEV**, as the plan's section 5 lists:
  - per-facet recall and extra escalations;
  - action-to-request against action-to-content linking;
  - fixed-K0 decision changes.

**Before E1 is fitted** (CPU, then registered with the Reviewer):
1. **One description against the centroid.** A pair carries one description, but the runtime scores against the centroid of a probe's descriptions. Add a CPU equivalence check, or train against the centroid.
2. **Masked negatives for `--intent`.** It ranks with unmasked in-batch negatives. Mask every known positive, twins included, before any relevance or link pairs use it.
3. **Comparison tools that load a bundle.** `campaign_arms.py`, `history_builder.py`, `history_diagnosis.py` and `rank_groups.py` build the pinned base and cannot load a fine-tuned bundle. Give them `--model DIR`, admitted through `bundle.jsonl`.
   - These files are pinned by the freeze. The change lands after the v4 prepare phase has rendered test 004, or by a freeze amendment the Reviewer accepts.
4. **Relevance pairs.** No contract exists. The fields it can be built from:
   - `decide` (the call);
   - `facts[]`, `bridges[]`, `limits[]` and producing calls (the positives);
   - `story.evidence` (which facts each label rests on);
   - `pair_id`, `group` and `family` (masks and splits).

   It is declared before any TRAIN root is converted. Relevance training follows only together with a consumer: a registered history change that ranks candidates with the encoder, measured against exact ties.

**Cost.** The batch-002 fit ran 466 steps. Calibration runs on dev, through the serving loader, with its own `probes.jsonl` and `bundle.jsonl`. Encoder development runs need no release (the user, 2026-11-13).

## 5. Kai session recipe: missing pins and what blocks them

The package: Kai `train/recipes/kai-session-decision-20261113-v1`. Its `registration-proposal-v1.json` has six empty pins.

| Pin | Made by | Status |
|---|---|---|
| `prepare_receipt` | `prepare.py RENDER PANELS ROOTS admission-template-4k.json OUT` | Ready to make. Render the two slices, concatenated into one roots file as before, with E0 at 11,000, then prepare (CPU). |
| `cpu_check` | `kai.qwen.run check` | After `recipe`. |
| `preflight` | `kai.qwen.run preflight --out FILE` (CPU) | After `recipe`. |
| `blind_gate` | `blind4.py seal`, `read`, `gate` | **Open question for the Reviewer.** Protocol v4 asks for a population no reader or reviewer has seen. The training slices have been reviewed, and slice 1's earlier revision was read under v2. Which admission do the TRAIN records need? |
| `source_registration` | `make_preparation.py PREPARED GATE`, then `publish_source.py` | **Broken.** `blind4.py` writes gate schema `kai-session-blind-gate/v5`; `make_preparation.py` accepts only `/v3` and `/v4`. Needs a reviewed repair. |
| `recipe` | `build_recipe.py SOURCE PREPARED` | After `source_registration`. Sets `max_state` 4,096. |

**Stale statements to correct with the pins:**
- **README:** its section on a render at 10,000 of `session-roots-train-001` (AgentArmor `cc4cf0c`, 68 records), its "two readers" step, and its fact-free `facts_sufficient`.
- **The proposal:** "development file 003 r1" (the freeze names control set 001 r2 and the two slices), and "the longest session state is 2,249 tokens".
- **`prepare.py:96`** hard-codes the question source `session-roots-train-001`.

**Cost.**

| Job | Time | Host peak | Source |
|---|---|---|---|
| Ten-update prefix | 120.5 s | 77.0 GB | Kai results |
| Run 1 training (582 updates) | 7,132 s | 79.3 GB, no swap | Run 1 guard |
| Fused parity | 318.7 s | 47.5 GB | parity guard |

The guard stops a job at 90 GB host memory in use.

## 6. Missing evaluation, and the session-improvement criterion

| Panel | Exists with a K0 baseline | Missing | Proposal |
|---|---|---|---|
| ToolCall, general | none | Any panel (Kai's evaluation guide) | Check the existing Kai ToolCall DEV sets for eligibility first: `authored-dev-v1`, `consent-natural-dev-v1`, `policy-generated-dev-v2.1`, `toolcall-broad-g2-v1`, `toolcall-roots-per-structure-v1`, and the OpenAI-anchored `h1-dev-raw-toolcall` (24). Read K0 on the eligible ones as the baseline, registered before any candidate is read. |
| ToolCall Path | `pathscope-host-toolcall` (128), AUC .981 | The raw view | `pathscope-rawruntime-v1` (128, OpenAI-anchored): read K0 as its baseline. |
| XPIA short and long | four public panels | Fresh, unpublished panels | The existing four stay the retention check. |
| MultiTurn | none | Any K0 read | `multiturn/eval/dev-v1` (17) and `m2-agentdojo-v1` development (1,266; calibration 65): read K0 as their baseline. |
| Multi-session | none | A K0 read | dev 003 r5, the history and reference arms of section 3. |

**Proposed session-improvement criterion**, registered before K1 is read:
- **Improvement:** on dev 003 r5, the 95% root-bootstrap interval of K1 minus K0 on decisions, over both arms and both layouts, lies wholly above zero. K1 is read at its own `max_state`, K0 at 2,048.
- **Retention:** rule v2 holds on its five panels, and no new baseline panel above is lost.
- **Confirmation:** the final model read on test 004 confirms it. It is registered before that read and run once.

A 50-pair test cannot certify a 1% false-intervention rate. That remains the CAL and audit plan of Kai's evaluation guide, section 8.

## 7. Jobs and what each needs before launch

| Job | Phase | Resources (earlier receipts) | Launch status today |
|---|---|---|---|
| Test 004 v4 prepare: checks, E0 render, panels, prepare, seal | registered measurement | Render about 3 to 6 minutes; the rest CPU | **Blocked on the user's release** for this job (asked 2026-11-14). Script ready: checks the pins and refuses without a scoped release. |
| Test 004 v4 read: Codex Daybreak at xhigh, about 200 requests, one retry pass, gate | registered measurement | The v3 read of 190 requests took about 30 minutes | **Blocked on the user's release** and on the prepare phase. |
| DEV renders with E0; E1 fits | development | Minutes per 100 roots | **May run** (the user: "run EmbeddingGemma freely during development"). E1 is registered first. |
| Version 3 of the campaign measurement (pins only) | CPU | Minutes | **May run.** |
| K0 DEV reads (sections 3 and 6) | Kai read | About 8 minutes per 1,654 requests, host peak 53 GB | **Blocked on a registered read recipe and the Reviewer's agreement.** Then the Trainer records a one-job release quoting the user's 2026-11-13 words, "Launch training for Kai (and refactor data and code in [the Kai repository]) as needed", if the Reviewer agrees those words cover reads that judge this cycle's training. Otherwise the user is asked once. |
| Kai memory probe, then training | Kai model jobs | 120 s / 77 GB; 7,132 s / 79.3 GB | **Blocked on the pins of section 5** and the Reviewer's agreement to the registered recipe. Then a one-job release quoting the same words. |

No release file is present, and the user's two hold files stand.

**A defect in the guard.** Kai's `kai.guard` command line tests `result.get("reason")`, but the key it writes is `stopped`, so its documented exit status 2 on a breach never fires. Callers must read `stopped` from the printed JSON. The fix is a one-line Kai change, but
`train/kai/guard.py` is in the code closure that K0's read template pins (31 files). Changing it
alone would make that template fail `check`, so the fix lands with the next re-pinned recipe,
with its test.

## 8. The shadow runner and the serving checklist

**Delivered: `tests/test_shadow_path.py`** (CPU, about 8 s).
- **The setup:**
  - `agentarmor-monitor serve --shadow --earlier-sessions on|shadow` runs as a process, with its own spool, home and Codex home.
  - Every event goes through the shipped templates' command, `gate --shadow --agent CLIENT`.
  - Clients are paired Claude Code with Claude Code, and each with Codex.
  - Kai is stood in for behind the service's own HTTP handler, by a stand-in that keeps each request whole.
- **What it checks:**
  - every gate prints nothing and exits 0;
  - with `on`, the request Kai actually received for the later call carries a `[s:ALIAS]` line of the earlier session about the file the call uses, before the proposed call;
  - the server's `issued` record and the gate's `answered` record name that request by its SHA-256;
  - with `shadow`, no request carries such a line, and the count goes to `earlier-sessions.jsonl`.
- **What it does not show:** the session's title alone is never taken as proof, because it is written even with no lines. The lookup tested is the bounded index proof plus the newest 40 summaries; no vector candidates reach Kai.

**Next artifact: the same runner on real models.** A recording proxy goes in front of a real Kai (the vLLM service on the Linux GPU host), with E0 or a calibrated E1, through `--kai-url`. It runs when those models' jobs are released. The server keeps only a request's hash, so the proxy is where the request is captured.

**Serving checklist.**

| Item | Where it stands | Owner |
|---|---|---|
| State limit | The export's `kai.json` says `max_state` 2,048 (`max_row` 2,694, `max_packed` 3,712). The 4,096 research model needs its own export and numerical qualification. | Kai Trainer |
| Character budget against tokens | The monitor counts characters (`--max-chars`, the 11,000 budget). Tokens are checked only in Kai's service, which refuses a request that is too long; the monitor halves the packet on 400 or 413. Test just below, at and above the limit. | AgentArmor Trainer |
| vLLM parity | Not done. The fused-MLX check is not vLLM. Forward probabilities and decisions on the intended Linux GPU, with tolerances set before reading. | Kai Trainer |
| Identity | Kai's `/health` gives `model`, `readout` and `package_sha256`. The monitor's `/health` gives the encoder's model, revision, device, dtype, probes and calibration. A wrong package at start refuses to serve; a later mismatch is `kai_mismatch`. | AgentArmor Trainer |
| Timeouts and load | `--kai-timeout` 30 s; the gate's `--timeout` 45 s; the watcher retries after 30 s on its own thread; `--at-once` slots, then `busy`. Measure p50, p95 and p99 against preregistered limits. | AgentArmor Trainer |
| Clients | Codex has no failure handler and does not hook `write_stdin`. Claude's `onFailure: "block"` is on `PreToolUse` only and never in shadow. Disclose both; never call Codex's gate fail-closed. | AgentArmor Trainer |
| Outcomes | `agentarmor-monitor gates` joins `answered` and `observed` records by call. A shadow `would: deny` is never counted as a prevented effect. | AgentArmor Trainer |
