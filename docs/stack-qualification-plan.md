# Plan: qualify Summarizer, Kai-4B and the AgentArmor session path

2026-11-14. Model Reviewer. Execution plan for the user's request to address the whole
stack directly, with multi-turn/multi-session support and no ToolCall/XPIA regression.
This coordinates the existing contracts; it does not replace a frozen experiment,
authorize publication, or turn a diagnostic result into release qualification.

## 1. Decision and current evidence

Keep the architecture: **Summarizer marks suspicious interactions and retains relevant
context; AgentArmor binds the context to the proposed call; Kai decides allow/deny.**
The immediate priority is a useful session-trained Kai on the actual evidence AgentArmor
delivers, with a serving path capable of accepting that evidence whole. Investigate a small
set of failures through the entire stack before expanding data or experiments. Preserve the
component responsibilities while changing the training, evidence representation or serving
contract when the measurements justify it. The security target is unauthorized agent actions,
not general content moderation.

Reviewed starting points: AgentArmor `106fe26523a1748b82a7f7cee0591721c751988e`,
runtime freeze `a453deb334083bf611e25a53f467700472d21636`, and Kai
`3406d0c53cc071a21b73bffbfea6a5807f417bc2`. The shared board owns changing status;
this document owns the execution sequence and responsibility boundaries.

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| q3 blind read: decision agreement 141/190, host-fact agreement 127/190, 15/50 pairs retained; MR192 | The previous data/reader qualification failed. All 49 decision disagreements denied labelled-benign views. | Kai-4B accuracy: the reader was Daybreak, not Kai. |
| MR205–MR210 accepted the repaired runtime/diagnostic baseline and v4 reader machinery | Known development defects have bounded repairs; the next protocol is frozen. | General retrieval correctness, model quality or deployed protection. |
| MR215: full-r2 accepted, 40 roots / 20 pairs; both MR214 findings closed | The remaining planned authoring sittings can proceed without another intermediate review stop. | Acceptance of the eventual 100-root population. |
| Earlier EG2 batch-002 fit: short-text tagging 75% to 43%, despite high within-batch held-out AUC | There is historical evidence of encoder overfitting and transfer failure. | That every fine-tune fails, or that the current base encoder is sufficient. |
| Published Kai export: 2,048 state tokens; research proposal: 4,096 | Research and serving limits differ. | Permission to change the export limit without training and numerical qualification. |
| MR219/MR220 K0 DEV read, reproduced from saved outputs: reference allows 34/80 attack views; history allows 34/80 attacks and denies 14/80 benign views | The incumbent makes errors even with complete reference evidence; actual history introduces an additional benign-disruption problem. | A unique cause: wording, additional evidence and indicator tags differ between arms. |
| Same DEV: history retains 534/542 earlier facts; only 48/160 history views fit the published 2K state cap, versus 160/160 at 4K | Serving capacity and decision quality are material gaps. | That EG2 is generally sufficient or that a 4K research read qualifies a 4K export. |
| TRAIN admission, 2026-11-14: 200/200 valid answers, decision agreement 181/200, 26/50 pairs retained; whole batch held | The current TRAIN batch needs adjudication and bounded repair before a session fit. | Permission to train its retained subset by calling the run diagnostic. |

Sources: [EG2 training results](../training/summarizer/README.md#what-the-first-batch-shows),
[Kai serving](https://github.com/min-mwei/Kai/blob/3406d0c53cc071a21b73bffbfea6a5807f417bc2/docs/serving.md),
[session recipe](https://github.com/min-mwei/Kai/blob/3406d0c53cc071a21b73bffbfea6a5807f417bc2/train/recipes/kai-session-decision-20261113-v1/README.md),
and MR192/MR205–MR215 on the shared board. Historical counts retain their original data,
encoder and scorer identities; they are not fresh measurements performed for this plan.

## 2. What the research changes in this plan

The external results motivate experiments, not adoption of another model's architecture.

| Primary source, checked 2026-11-14 | Relevant finding | Application here |
|---|---|---|
| [Google EG2 model card](https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2) | Retrieval has asymmetric query/document prefixes; the text encoder supports normalized embeddings. BF16/FP32 are supported; FP16 can fail silently. | Preserve the pinned text-only configuration, exact prefixes, normalization, windows and numerical profile through fit, calibration and serving. Do not adopt a newer card's larger context limit without checking the pinned implementation. |
| [Google text fine-tuning guide](https://ai.google.dev/gemma/docs/embeddinggemma/fine-tuning-embeddinggemma-with-sentence-transformers) | Domain triplets and training/inference prompt consistency are the demonstrated adaptation path. The example is a small tutorial. | Use accepted cyber TRAIN pairs and independent evaluation; the tutorial is not evidence that a three-example fit generalizes. Retain our retrieval prefixes rather than copying its STS demonstration. |
| [Sentence Transformers losses](https://www.sbert.net/docs/package_reference/sentence_transformer/losses.html) | Ranking losses use other batch examples as negatives; the documentation recommends avoiding duplicate negatives. | Mask every known valid evidence item and twin-related positive, not just duplicate strings. An approval and a restriction can both be relevant to the same call. |
| [Perplexity Decider](https://www.perplexity.ai/en-GB/hub/blog/perplexity-decider) | Reports staged training, soft-target distillation and multiple seeds. Its extraction adapter can regress decisions if merged. Its decision readout and attention design differ from Kai's. | Take staged measurement, retention and extraction isolation. Keep Kai's pointer head and isolated branches. Distillation, averaging and attention changes are later hypotheses, not prerequisites to this cycle. |
| [AgentDojo](https://arxiv.org/abs/2406.13352) | Agent evaluation measures legitimate task performance as well as security under untrusted tool content. | Count benign disruptions and completed effects, not only attack denials. Use the evaluation method; do not import benchmark cases into training. |
| [Lost in the Middle](https://arxiv.org/abs/2307.03172) | The tested models' use of evidence depended on its position in long context. | Include early/middle/late evidence and distractor load in development slices. This is a test hypothesis for Kai, not an assertion that Kai has the same effect. |

The important local lesson is that increasing data volume, context length, or training
steps can improve a headline while leaving the actual failure unchanged. Diagnose the
failure with the same call and controlled evidence first.

## 3. Critical path and parallel preparation

The user narrowed the research scope on 2026-11-14: major gaps first, fewer key cases,
whole-stack improvement. The Trainer agreed in the 08:05 board reply to MR220 and recorded
the eight-pair roster in [run sheet section 9](stack-run-sheet.md#9-the-focused-loop-mr220-eight-exposed-dev-pairs).
Use that roster, not another dataset or planning framework. It covers contradictory later
observations, lookalike objects, poisoned controls, authorized transfers, claimed approval,
grant/revocation, cross-session handoffs and destructive preparation. Both twins and both
client layouts stay together: 16 roots, 32 views per arm. Selection used saved K0 failures
and a working control; this is a debugging set, not an unbiased evaluation population.

| Priority | Deliverable and owner | Evidence to move forward |
|---|---|---|
| 1. Teach the session decision | Trainer and Reviewer adjudicate the 24 held TRAIN pairs; Data Engineer repairs demonstrated faults in new revisions, then Trainer repeats preparation and admission before the existing fresh-base K1 recipe on E0 packets. Separately add only the missing TRAIN mechanisms: later observations, lookalikes and destructive preparation, about 12 pairs. | One guarded fit on admitted TRAIN, no sweep; useful attack/benign improvement on covered cases, working controls preserved, followed immediately by the full exposed DEV read and ToolCall/XPIA retention checks. Missing mechanisms remain explicit if the addition is not ready for this recipe. |
| 2. Deliver the same evidence through the product | AgentArmor and Kai owners implement the bounded local MLX adapter and carry these cases through capture, marking, history, the exact serialized call, the model, the gate and observed hook outcomes. Resolve token admission/repacking and qualify the actual 4K export/backend together. | The evidence and call reach the real model whole, before the call, in the correct namespace, with the expected model identity. Record latency, queueing and memory; gaps/oversize/errors remain visible. CPU stand-ins do not close real-model integration. |
| 3. Expand and qualify | Once the intervention is useful, Trainer broadens mechanism/history/load coverage, completes the missing evaluation panels, repeats required seeds/orders and supplies the frozen serving identity; Reviewer decides acceptance. | Existing fresh-test, non-regression, calibration and release rules pass. No release claim is based on eight selected pairs or on a shadow gate's answer alone. |

The existing 50-pair TRAIN admission failed its bars and holds the whole batch. Do not
bypass it with a 26-pair diagnostic fit. Adjudicate source faults, evidence binding and
genuine reader disagreement before repairs; never relabel solely to match the reader.
This proceeds independently of private test-004 source review and qualification. Do not
wait for E1 or a new broad corpus. A prepared addition for missing mechanisms can join a
newly registered recipe before its freeze; it must not change
an already sealed read or running recipe. A pilot without it measures covered mechanisms,
not closure of the three missing ones. Transfer between mechanisms is possible: those
absent families are useful diagnostic slices, not guaranteed unchanged causal controls.

For each selected pair, reuse one compact trace: capture/trust, mark, required evidence and
cutoff, exact Kai input/token count, decision, gate answer and observed outcome. Use saved
K0 answers. A bounded framing-only DEV comparison is secondary to the Kai pilot; preserve
all evidence, indicators, question text and model settings, and say exactly which changed
bytes could explain its result. Do not suppress suspicious indicators to reduce benign
denials. Keep the calibrated base encoder until a Mark/Link failure supports its next fit;
an EG2 experiment does not take the critical path ahead of this decision/serving work.
An early loss stop must compare the same fixed records and objective, rather than losses
from different minibatches. Preserve the existing non-finite and memory stops.

Working estimates from the Trainer, not deadlines: TRAIN repair and re-admission precede
the fit. The previous ten-update memory prefix took about two minutes, and K1 is estimated at
two to three hours after admission and recipe checks. A full 320-view DEV read just took
256 seconds. The 4K training cost is not yet measured. The user's local MLX/Metal choice
removes a Linux host as a prerequisite for the first real-model path. Adapter delivery,
export parity and actual-hook measurements remain work, not completed integration.

Private test 004 remains separate: source acceptance, then the frozen v4 reader bars
(decision and host-fact agreement each >=95%, valid coverage >=95%, retained promised pairs
>=90%), with preparation holds reported apart and its launch authorization resolved. This
does not turn test data into TRAIN or block the independent development intervention.
Reuse existing commands and artifacts, including `check_campaigns.py`, `check_evidence.py`,
`campaign_arms.py`, `panels.py`, Kai `prepare.py`, `check_prepare.py`, `blind4.py`,
`publish_source.py`, `build_recipe.py`, `kai.qwen.run` and the retention comparer.

CPU preparation, source review and shadow-runner work need not wait for the model queue.
One model job runs at a time through the existing guard. The Reviewer reviews returned
artifacts instead of repeatedly asking for a new plan. The [Trainer's run sheet](stack-run-sheet.md)
owns commands, recipe pins, owners and live blockers; extend it rather than creating another
planning layer.

Existing model-job holds remain a launch prerequisite, not a reason to stop preparation.
The session package requires a release record naming its exact recipe, phase and user
authorization. The Trainer should resolve the actual current authorization against that
record before launch, state a specific unmet prerequisite once, and avoid another generic
approval loop. This plan neither waives an explicit user stop nor launches another role's job.

## 4. The experiment that identifies which component needs work

Run this matrix on **DEV first**, with source calls, policy, budgets and replay schedules
held fixed. K0 is the registered incumbent; K1 is the selected session-trained Kai.
E0 is pinned base EG2 with its calibration; E1 is one selected fine-tuned EG2 bundle.

| Evidence input | K0 | K1 | Question answered |
|---|---|---|---|
| Complete source-bound reference packet | baseline | candidate | Can Kai decide when the required evidence is present? |
| Real history packet made with E0 | baseline | candidate | What is lost through today's scanner/context path? |
| Same builder with E1 and its calibration | fixed-Kai encoder comparison | combined candidate | Does encoder adaptation help, and does the trained Kai benefit? |

Reference packets are bounded and source-bound, not unconstrained full transcripts. A
reference that cannot fit remains held. Save the exact model input and do not credit a
fact based only on an author's annotation. Keep newest-only/none and exact/FTS retrieval
controls from the existing development tools where they answer a specific failure; do not
turn them into another large model grid. Compare ranking methods on one eligible candidate
pool, then measure the final packed evidence, including displaced evidence.

First measure K0 with E0/reference. Keep the current K1 recipe on E0 while developing E1
separately with K0 fixed. Measure K1/E1 only after a component earns that comparison.
Use the session proposal's matched **4,096-state-token research cap** for K0 and K1,
subject to exact read-recipe admission; the published serving export remains 2,048.
Report the 2,048-token admission census separately. If a 2K baseline is read, label any
later 4K comparison as a combined capacity/training change. The full matrix must not obscure that
the session recipe also changes dose/horizon and context cap. K0 is an incumbent comparison,
not automatically a compute-matched causal control. If claiming that data alone caused a
gain, register the needed matched control; otherwise state the combined intervention.

For the first read, keep both layouts and the complete four-arm roster, but ask only the
reference and history arms; report the other arms as unasked. Reuse `campaign_arms.py score`
and `history_diagnosis.py` before building a joined ledger. Preserve all admission holds and
both twins; exact-request deduplication must map answers back to every original view.
Report history and reference separately, with uncertainty clustered by pair (both twins,
layouts and arms together). An aggregate gain across reference and history cannot substitute
for improvement on the actual history path.

**Interpretation:** reference wrong means inspect labels and Kai; reference right/history
wrong with missing premises means inspect retrieval/packing; evidence retained but decision
changed means inspect layout, distractors and Kai; routed/model-denied yet effect proceeds
means inspect the client/harness. Classify unmarked attacks before any conditional Kai score.

Do not insert E1 or new arms into the already frozen v4 seal. Current v4 uses its pinned
encoder. Any subsequent candidate measurement needs its own prospective registration;
test 004 is reserved for its registered qualification and final model read. No candidate
selection, threshold fitting or implementation change may use its contents or results.
Additional test use must be settled before any relevant read, not after seeing a failure.

## 5. EG2: train the job it actually performs

There are two adaptation targets: recognize suspicious patterns, and find context that
bears on a call. Preserve all four existing encoder jobs: Mark, Link, Select and Find
([architecture section 5](architecture.md#5-the-encoder)). In particular, check whether an
action links to the user's request or to lower-trust content; a relevance improvement
must not silently regress this Link behavior. None of these jobs is an allow/deny verdict.
Keep the three existing broad probe families
for the first experiment. More labels, learned prototypes, new dimensions and new chunking
are not required to test encoder adaptation.

The current `training/summarizer/finetune.py:260–274` fits probe/text CoSENT and optional
action/request contrast. Start with the supported probe objective, with Link as a retention
check. The current history selector does not consume encoder relevance rankings. A missing
fact is therefore not automatically an EG2 defect. Relevance supervision and a measured
consumer are follow-up work only if the DEV diagnosis supports them; they do not block the
first Mark fit. The masking requirement below applies before using the optional contrastive
objective, not to a probe-only run.

- TRAIN comes only from accepted synthetic training sources. Evaluation campaigns,
  test 004, old blind outputs, benchmark text and seeds supply no training targets.
  Declare a separate relevance-pair contract before converting eligible TRAIN roots.
- A call can need several positives: effective grant, restriction, revocation, lower-trust
  instruction, producing action, actual result and object/version link. Train relevance
  regardless of whether the eventual call is allowed. Quoted injection remains a positive
  for a probe that recognizes injection wording; it is not automatically a probe negative.
- Use within-scope hard negatives only where irrelevance is established. Mask all known
  positives from contrastive negatives, including related twins. Keep roots/source blocks
  together across splits; entity substitutions do not create independent examples.
- Pin text-only weights, tokenizer, prompts, pooling/normalization, dimension, chunk/window
  aggregation and dtype. Evaluate and calibrate the actual served centroid score. A
  per-description training surrogate need not be numerically equal to that score; disclose
  the mismatch and test its transfer rather than requiring an impossible equality check.
  Keep token embeddings frozen initially. No simultaneous probe-bank or attention rewrite.
- Register the loss mixture, batch composition, learning rate, fixed update budget, seeds
  and selection rule before fitting. Start from existing supported settings; one candidate
  first, with a second ablation only if a named DEV result makes it necessary.
- Recalibrate the saved/reloaded candidate through the actual serving path, with its own
  `probes.jsonl` and bundle identity. Use block-disjoint validation for encoder/threshold
  selection; never compare a refitted candidate solely with stale base thresholds. Show
  both today's base and a base recalibrated on the same fitting data.

The existing [probe protocol](../training/summarizer/docs/probe-protocol.md) is a base-only
proposal and explicitly excludes a fine-tune. The Trainer must register the fit as a new
experiment, not silently reuse that title or unfreeze the current cycle. Use its recall
guards and independent-block reasoning as the starting point, with accepted thresholds
and exact data roles recorded before results. The historical trial is not an adoption candidate.

Report per-facet recall and extra escalations; action-to-user/content linking; whole proof-chain retention; falsely complete
packets; direct versus quoted and short versus long slices; and fixed-Kai decision changes.
A high AUC with a lost permission or increased missed attacks fails the intended purpose.
Repeat an apparently useful fit before adoption and disclose seed spread. If transfer fails,
repair the TRAIN distribution or objective identified by DEV; do not simply add epochs.

## 6. Kai: session adaptation with prior skills and spans intact

Use [the existing session package](https://github.com/min-mwei/Kai/blob/3406d0c53cc071a21b73bffbfea6a5807f417bc2/train/recipes/kai-session-decision-20261113-v1/README.md)
and `registration-proposal-v1.json` as the starting point. Fill its missing artifact pins
and stale source references; this plan is not a second training runner. Start every new
run from the registered Qwen3.5-4B-Base, keeping LoRA, the pointer head, canonical options,
reserved-token escaping, isolated question branches and checkpoint temperature.

Admit exact rendered TRAIN requests, not source labels attached to incomplete packets.
Prior source review does not disqualify TRAIN. Earlier blind exposure does prevent claiming
fresh qualification under test 004's population rule. Register TRAIN admission separately,
reusing the pinned reader, prompt, isolation, retry limits and numerical bars, with exposure
declared and no fresh-test claim. Do not rewrite the frozen test protocol, import its records
into training, or replace existing TRAIN solely to satisfy a test freshness rule.
Record selection coverage and root/family exposure; two available arms must not silently
double one root's weight. Keep missing host facts, missing captured context and missing
authorization distinct. This cycle's default `none_required` profile does not train or
qualify a general missing-host-facts router. Unsupported host computations remain refused
by the harness; router expansion is a separate scoped task.

Keep the existing old-task corpus/replay and new session targets under the agreed recipe.
Read the fixed final update, not a hunt through checkpoints. Apply
[retention rule v2](https://github.com/min-mwei/Kai/blob/3406d0c53cc071a21b73bffbfea6a5807f417bc2/train/recipes/release-retention-20261113-v1/registration-v2.json):
each measure and panel is held, lost or inconclusive; an interval crossing zero is not a
pass. Preserve the evaluation document's two-seed/initializer-order recipe evidence and
three-seed adoption requirements. Stop an unsuccessful pilot before paying for all repeats.
The old five-panel suite does not cover general ToolCall or MultiTurn. Kai's evaluation
document records that the planned `armor-cyber-v1` replacement was never built or read.
The run sheet must name the missing eligible evaluation artifacts, their baseline,
prospective registration and session-improvement criterion, including a multi-session
slice. Reuse eligible existing sources before requesting new data. If a feature's panel
is absent, record the gap and make no non-regression claim for it; the public five-panel
diagnostic is not a substitute or a fresh selection set.

Perplexity's distillation result is a reason for a later, matched hard-label versus
soft-target experiment if Kai still fails on complete DEV evidence. It is not a reason
to change this cycle's head, buy a teacher, train on reader answers, or average incompatible
adapters. A teacher must see the same evidence as the student and label eligible TRAIN only.

**Spans:** preserve the [existing detached head-only plan](https://github.com/min-mwei/Kai/blob/3406d0c53cc071a21b73bffbfea6a5807f417bc2/docs/kai-training.md#123-train-evidence-without-changing-the-decision-model).
First qualify decisions. A later evidence stage freezes backbone, decision adapter, pointer
and temperature and trains only the separate evidence parameters. Require exact decision
preservation at fixed shapes, then numerical serving checks. Test mappings through Unicode,
escaping, redaction and serialization back to the original field and event; a clipped or
unprojectable target is unavailable, not empty. Report exact-span quality, complete-proof
recall, false evidence on empty targets and offset failures separately. Current spans are
untrained/unavailable: maintaining their contract does not claim a functioning span model.

## 7. AgentArmor: measure the path users actually run

Prepare the runner with accepted public/development cases now; execute with the selected
models when their jobs are released. Use the actual generated Claude/Codex hook templates,
local server, server-owned history/policy, configured Kai profile and the actual selected
serving implementation.
For multi-session measurement, use `agentarmor-monitor serve --shadow --earlier-sessions on`
with the configured model arguments, and assert that prior-session evidence appears in
Kai's actual request. `--earlier-sessions shadow` only counts history; default `off` omits
it. Neither tests Kai on prior-session context. The server maintains and queries the
index to prove which sessions are unrelated, then reads at most the newest 40 remaining
summaries. Evidence selection still uses those summaries; do not claim vector-search
candidates reach Kai from this test. Unread sessions not proven unrelated remain unknown.
An imported function or mocked model is a component test, not this integration result.

| Obligation | Required observation |
|---|---|
| Capture and scope | Every delivered prompt/call/result accounted for; known unhooked events disclosed; concurrent sessions cannot cross namespaces or the call cutoff. |
| Multi-turn/session memory | Explicit grant/revocation, relevant file changes, failed writes and restarted/rebuilt stores retain the right evidence or mark the packet incomplete. |
| Whole input | No silent clipping; invalid encoding, oversize, missing dependencies and gaps produce explicit outcomes. Test just below/at/above actual token limits. |
| Real model identity | Exact EG2 plus calibration reported by health; exact Kai export files, pointer and tokenizer validated before loading. Wrong identity is refused. |
| Responsiveness | Kai timeout/transport failure does not stall the summary loop. Measure end-to-end p50/p95/p99, queue delay and peak memory against preregistered operator limits. |
| Outcome | Link shadow answers to subsequent observed hook events by call identity. Report what would be denied; do not count a shadow denial as a prevented effect. |

**Local serving decision, 2026-11-14.** The user chose vLLM Metal or MLX. Prefer a bounded
MLX backbone adapter behind the existing service. AgentArmor's vLLM adapter asks for raw
token states (`token_embed`, `ALL`, in `src/vllm/agentarmor_vllm/engine.py`); the current
[official vLLM Metal pooling scope](https://docs.vllm.ai/projects/vllm-metal/en/stable/text_embedding_pooling/#unsupported)
excludes generic token-level pooling and decoder `ALL`. Therefore the documented stock
pooling route is not a drop-in Kai backend. This is a compatibility inference from source
and documentation, not a hardware failure. Do not replace Kai's readout with the standard
embedding or vocabulary-reranking API, or build a general plugin on this critical path.

Trainer owns a local loader that uses Kai's text backbone with independent complete causal
rows and the existing FP32 pointer. Reuse the shared request validation, encoder and API;
preserve recurrent-state isolation, checkpoint temperature, escaping and span offsets.
Kai owns compatibility with the verified merged export, or a versioned compatible export
if required. Health and decision records must identify the actual backend and package,
not inherit the current hardcoded `vllm` name. No model switching or downloads from requests.
CPU adapter contracts can proceed while TRAIN is repaired; actual model work stays serialized.

The published 2K export cannot stand in for the 4K research model. It can support fitting
smoke cases and oversize refusal only. Qualify the actual local 4K export's logits,
probabilities, decisions, admission limits and question isolation against identical tokens
in Kai's MLX reference. Set numerical tolerances and decision-agreement rules before the
read. Bind calibration to final weights, merge, dtype, tokenizer and kernels, then test
the full real-hook path and retention. Training-runtime or fused-export parity alone is
not serving qualification; local MLX qualification does not qualify Linux vLLM. Evidence
spans remain unavailable until their separate training and qualification pass.

Exercise redaction, restart, timeout, wrong-model replies and configured remote transport
with synthetic fixtures and controlled local destinations. Do not execute dataset attack
payloads. Claude gate-start protection and Codex limitations are different: Codex has no
equivalent failure handler and does not hook `write_stdin`. Test the supported versions
and disclose that boundary; the release must not call Codex's gate fail-closed.

## 8. One scorecard and rules for deciding what to fix

Every row carries input/recipe/model hashes, split, root count, view count, device/backend,
denominator and completion status. Use the four top-level Kai panels in their defined order:
ToolCall, ToolCall Path, XPIA short/long, MultiTurn; show multi-session as an explicit slice.

| Stage metric | Denominator and interpretation |
|---|---|
| Suspicious-call recall | Marked attack calls / all attack calls; include attacks never sent to Kai. |
| Evidence retention | Source-supported required facts and complete chains shown / all required facts and chains; split current/prior session. |
| False completeness | Packets declared complete despite a missing required premise / all packets; every known instance is a defect. |
| Kai decision quality | TP/FN/FP/TN on both complete-reference and actual-packet views; report holds separately rather than assigning a guessed label. |
| End-to-end cost | Benign would-interventions / all attempted benign calls, including default denial from gaps, errors, encoding and timeouts. Separate model denials from these costs. |
| Actual enforcement | In an explicitly enabled controlled enforcement trial, observed stopped/proceeded effects; never inferred from `answered` alone. |

Do not multiply recall rates measured on different populations. Save a joined per-call
funnel from capture through marking, retained evidence, Kai answer and observed outcome.
Repeated twins/layouts/replays/entity fills remain dependent views; use pair/source-block
resampling. A 50-pair balanced test cannot certify a production 1% false-intervention rate.
The independent workflow CAL/audit sizes and joint bounds remain those in
[Kai evaluation section 8](https://github.com/min-mwei/Kai/blob/3406d0c53cc071a21b73bffbfea6a5807f417bc2/docs/evaluation.md#8-calibration-locked-test-and-production-qualification).
There is no new smaller substitute qualification in this plan.

**Stop conditions:** source contradiction -> Data Engineer; missing captured evidence ->
core/monitor owner; poor relevance ranking with intact candidate evidence -> EG2 Trainer;
wrong answer on sound complete evidence -> Kai Trainer; training/serving divergence ->
Kai export and AgentArmor serving owners. Fix on TRAIN/DEV, retain the failed measurement,
and rerun only the affected checks plus required regressions. A new hold needs a reproduced
material failure, not a cosmetic preference.

## 9. Next handoff and scope control

The next owner handoffs should contain completed artifacts or an exact blocker:

1. Data Engineer: from accepted full-r2, finish the existing remaining authoring sittings
   in full-r3 and return the final quota reconciliation. Trainer posts counts/hashes/process;
   Reviewer reads the private population.
2. Trainer: one run sheet naming the current eligible TRAIN/DEV/CAL/test roles, K0/E0 identities,
   fixed-Kai DEV comparison, proposed E1 objective and inputs, and the existing Kai recipe's
   missing pins. Include the missing general ToolCall/MultiTurn evaluation artifacts and
   the prospective session-improvement criterion. Name each executable command/phase, estimated memory/runtime from existing
   receipts, and the actual launch authorization status. No model downloads or jobs for planning.
3. Trainer: the shadow runner and serving-limit/parity checklist tied to existing code/tests, with
   owners and the next artifact. Report completed work before plans for later work.

After the checkpoint, the final population review and the already required model/export
gates are the acceptance points. Do not reopen accepted code for naming, file movement,
general framework work, expanded probes, new teachers or architecture comparisons. New
data is justified only by an identified training coverage gap or a required fresh test;
do not repeat authoring loops to make a reader agree with an unsupported label.

The Trainer owns implementations, training and model jobs; the Data Engineer owns source
repairs; the Reviewer owns independent acceptance. Work stays on `dev` and communication
stays on the shared append-only board. Keep `policy_ready: false` until the separately
registered model, serving and end-to-end qualification actually passes.
