# Plan: qualify Summarizer, Kai-4B and the AgentArmor session path

Updated 2026-11-15, Model Reviewer, after MR291–MR295. This replaces the November 14
session-fit-first sequence. It coordinates existing contracts and the Trainer's
[run sheet](stack-run-sheet.md); it does not replace registered experiments or authorize
publication. Security means authorized agent actions, not general content moderation.

## 1. Decision and current evidence

**Finish the fixed-K0 whole-stack measurement and the real span path. Session decision
fine-tuning has stopped for this release.** K0 remains a research baseline, not a qualified
enforcement model. Summarizer marks suspicious interactions and retains relevant history;
AgentArmor binds that history, policy and the pending call; Kai decides allow/deny.

Reviewed code: AgentArmor `57553bb72ae65d897b9398272bef86bd3987b767`, Kai
`2d801391f93df12a66967decedffdf5ab26c6473`. The board records changing status. Current
measurements and identities live in [Kai results](https://github.com/min-mwei/Kai/blob/2d801391f93df12a66967decedffdf5ab26c6473/docs/results.md)
and the recipes referenced there. Do not interpret old plan entries as new launch requests.

| Evidence | Conclusion and remaining gap |
|---|---|
| K6 learned 19/20 dosed primary-hard TRAIN pairs; unseen screen: K0/K6 unsafe allows 13/10 of 31, benign denials 2/5 of 31 | The fixed go/no-go failed. Learning TRAIN does not establish useful transfer; no further dose/schedule/seed ladder under this hypothesis. |
| K6 history DEV: benign denials 28/40 versus K0 7/40; policy ToolCall 89/120 versus 74/120 | Material retention/operating-point failure. Neither checkpoint's baseline error rate is a release acceptance bar. |
| MR293, saved screen probabilities: AUC 0.745057 → 0.843913; no common decision cutoff preserves K0 TP/FP on six checked panels | Some ranking signal exists. A global threshold change cannot repair the checked operating-point requirements. This post-hoc DEV diagnostic selects no threshold. |
| Reported TRAIN pipeline changes: lookalike asks 4→16/20; same-session 58→80/80; false completeness 24→4/64; twin route splits 16→4 | Component progress on training cases. These counts do not establish attack prevention, complete evidence, or fresh-population generalization. Finish the old/new comparison with K0. |
| Adjudicated held span proofs gold-covered: XPIA 20/28, decision 3/31; exact proof sets 10/28 and 0/31; false evidence 0/12 certified-empty XPIA questions | Injection extraction transfers to some unseen pairs; decision evidence remains inadequate. Most held families also occur in TRAIN. |
| Same-feature span scorer parity 317/317; corrected fresh decision-fitted read 62/62 comparable versus 61/62 before | Numerical progress at two bounded stages. Neither count is loader→API→monitor parity. Eight fresh-read targets remain unavailable. |
| MR294: nine saved live-MLX cases match strict intervals/edges; seven projected readings match; three reference states retain unknown-order errors in the monitor record | Bounded service/client/record integration progress, recounted from saved results. The actual-session replay regenerating the map in `escalate.detect()` remains outstanding; the helper closure was not pinned by the completed launcher. |
| MR295: all 145 requests in the fresh DEV blind read use paraphrased decision/XPIA questions rather than the registered specifications | Agreement is diagnostic under that alternate prompt. Correct canonical question binding and source defects before one final qualification read; do not regenerate the whole population or delay the existing frozen K0 system comparison. |

K0 allowing 13/31 attacks on the screen is an observed input-specific result. Audit whether
each required fact reached it before attributing the miss to the decision model. A miss
on sound complete evidence blocks enforcement; keeping K0 does not waive that problem.

## 2. What the research changes in this plan

Primary sources reviewed 2026-11-15. The applications below are design inferences to test
locally, not guarantees that another paper's method will work for Kai.

| Source | Finding and bounded application |
|---|---|
| [Perplexity Decider](https://www.perplexity.ai/en-GB/hub/blog/perplexity-decider) | Reports staged specialization, soft-target distillation and held-out evaluation. Its extraction adapter regresses decisions when merged. Preserve Kai's decision path and separate evidence stage; optional distillation needs a distinct hypothesis. Do not copy its vocabulary-derived head or attention design into Kai. |
| [AgentDojo](https://arxiv.org/abs/2406.13352) | Measures benign utility, utility under attack and attacker effects. Count legitimate task completion and unsafe side effects as well as gate answers. Use the evaluation principle; benchmark cases remain measurement-only. |
| [CaMeL](https://arxiv.org/abs/2503.18813) | Separates control/data flow and carries provenance in a trusted execution layer. Keep authority, source and action binding in AgentArmor's server-owned record. This does not propose its interpreter or confer its security guarantees on our stack. |
| [Counterfactually augmented data](https://arxiv.org/abs/1909.12434) | Coherent minimal label-changing edits reduced spurious reliance in the studied NLP tasks. Use same-call cyber twins differing in one decisive premise, alongside label-preserving variations; verify the premise from source. |
| [Group-shift generalization](https://arxiv.org/abs/1911.08731) | Strong average performance can hide poor group generalization; fitting groups perfectly does not solve it. Report root/family-level transfer and worst mechanism slices. Another optimizer is not the first remedy. |
| [Lost in the Middle](https://arxiv.org/abs/2307.03172) | Evidence position affected the studied models. Diagnose length/position effects on existing eligible cases, preserving chronology. Do not claim this is Kai's cause without a controlled comparison. |
| [ERASER](https://aclanthology.org/2020.acl-main.408/) and [Goodhart's law for explanation benchmarks](https://aclanthology.org/2024.findings-eacl.88/) | Rationale overlap and decision faithfulness are different; deletion-based scores can be inflated through distribution shift. Source-valid span coverage is required, but is not proof the decision used those spans. Use coherent premise changes, not arbitrary word deletion, for decision diagnostics. |
| [Calibration of neural networks](https://proceedings.mlr.press/v70/guo17a.html) | Confidence and accuracy are distinct; temperature scaling preserves argmax. Examine saved score ordering before another fit. Global monotone recalibration cannot create an operating point absent from that ordering or restore missing evidence. |

Do not add a new teacher, model family, attention architecture, optimizer study or broad
benchmark campaign merely because it appears in this research. Spend the next model
minute only on a question whose answer changes the next action.

## 3. Critical path and parallel preparation

Use the existing [eight exposed debug pairs](stack-run-sheet.md#9-the-focused-loop-mr220-eight-exposed-dev-pairs)
and the already registered broader DEV populations. The eight pairs are a debugging set,
not independent qualification. Preserve twins, layouts and original population identities.

| Order | Owner and deliverable | Exit evidence |
|---|---|---|
| 1. Measure the actual intervention | Trainer: unchanged K0 and encoder/calibration, old/new Summarizer/monitor commits, exact request joins and complete roster accounting. Reviewer audits source completeness and results. | Joined per-call outcomes, matched denominators and changed cases; distinguish unasked, incomplete, errors, pending answers and complete-model allow/deny. Separate model decisions, gate behavior and observed effects. |
| 2. Close the span path | Trainer: finish the actual-session replay with the span-enabled MLX artifact and a map regenerated by the monitor, including span-off/on comparison. Reviewer checks identity, numerical scope, offsets and provenance. | Actual integration receipt; unchanged decisions at fixed shapes; correct ranges/source binding or explicit unavailable/error. Saved-feature parity alone does not pass. |
| 3. Diagnose residuals | Trainer/Reviewer: classify the existing small roster's remaining failures. Data Engineer: finish the current fresh 24-pair DEV qualification and audit eligible TRAIN proof units. | Each residual has a missing premise, invalid target, representation issue or sound complete-input model error, with a concrete next intervention. New authoring only fills demonstrated gaps. |

CPU source/projection checks and data qualification can proceed while the existing guarded
model read runs. One model job uses the machine at a time. Reuse saved outputs for identical
requests under the same model/numerical identity; read only changed or missing requests.
Do not repeatedly run unchanged panels or create a new runner. Existing recipe closure,
preflight, launch authorization and guard requirements remain in force. Routine work
inside an agreed scope needs no new board approval at each command; substantive changes
and completed qualification artifacts remain review points.


Before any blind or model read, compare the **emitted** question instructions, types, option
keys and full criterion text against the canonical registered specifications. Agreement
between an author manifest and a prior blind-input copy is not that check. Keep source
labels out of blinded requests, and bind each answer to the state and exact question it
was asked. A paraphrase cannot reuse the old answer as a qualification result. Finalize
known source/render corrections first, then read the final deduplicated requests once.

The current DEV source rulings distinguish inert quotations from active redirection,
ordinary artifact-derived arguments from adopted authority, and conditional grants from
permission to execute after a failed check. Keep disclosure, destructive scope and injection
as separate questions. Preserve genuine native tool output, including empty or repeated
success statuses; do not invent prose to evade a diversity checker. An omitted active
attempt can invalidate a full-history XPIA label while the current call's decision proof
is intact: retain source truth, observed-state answers and question-specific coverage
separately. Frozen omission counts are not silently redefined after reading predictions.

**Planning targets, not commitments:** return the existing system comparison and residual
ledger in the current/next working session; aim for bounded span integration and diagnosed
pipeline repairs in the following working day. A further span-head/data experiment is a
conditional next one-to-two-day task, not already scheduled. Re-estimate from its receipt.
There is no defensible enforcement-release date while complete-input attack misses or
required decision-proof quality remain unresolved. Four or five days may be an engineering
candidate estimate; it is not a security qualification promise. A shadow-only diagnostic
candidate is a different outcome and retains `policy_ready: false`.

## 4. The experiment that identifies which component needs work

Keep K0, the calibrated encoder, questions/options, policies, call identities, budgets and
replay schedule fixed. Start with these three views; there is no K0/K1/E0/E1 grid to run now.

| View | What it answers |
|---|---|
| Old real pipeline packet | What did the system previously send or fail to send? |
| New real pipeline packet | Did capture, marking and evidence changes improve the same calls? |
| Source-bound complete reference, when needed for a residual | Can K0 decide with the relevant facts present and correctly attributed? |

A reference is bounded and source-bound, not an answer-key explanation or an unlimited
transcript. If it cannot fit, hold it. The actual state bytes, model identity, read recipe,
question set and numeric profile identify a reusable answer. Deduplication maps back to
every original root/view. Re-rendering invalidates only changed asks, but every call remains
in the roster. Preserve legacy rendering identities; do not relabel old outputs as new.

Read the existing eight debug pairs and every changed complete→allow result against the
source: effective user grant/limit; source/trust; latest relevant condition/result; matching
object/version/destination; proposed call; pre-call chronology; retained coverage. The
packet's own `ready` flag is not an independent fact-retention check. Keep observed results
apart from claimed success. Preserve tool facts that resolve an explicit user condition
without promoting the tool's words into user authority.

**Interpretation:** unasked attacks are scanner/routing misses; omitted premises are
capture/selection/packing defects; unsupported labels go to source adjudication; a complete
reference that is right while a complete runtime view is wrong motivates a bounded layout/
distractor diagnostic. A wrong answer on sound complete evidence is a Kai residual.
Training/serving disagreement goes to numerical integration. A gate denial followed by
the prohibited effect goes to the client path. None is repaired by changing another
component's headline metric.

A replay through a stand-in model, joined to saved real-K0 answers for exact requests,
estimates component-composed behavior. Label it that way; it does not measure live model
transport, concurrency, latency or whether the client actually stopped an effect. An
unasked benign call can validly abstain; report that separately from a Kai allow. An
incomplete/error denial has a security consequence and a benign cost, but is not a correct
Kai decision. Use source-checked completeness for the complete-input error slice.

Keep test 004 and other sealed populations under their existing registrations, outside
selection and training. New DEV exposure is recorded permanently. Existing checks,
`campaign_arms.py`, `history_diagnosis.py` and the current scorecard are sufficient; extend
only what is needed to account for all calls and explain the residuals.

## 5. EG2: train the job it actually performs

This is conditional follow-up, not the current queue. Keep the calibrated encoder fixed
while measuring the pipeline repairs. A deterministic history omission is not evidence
that another EG2 fit is needed.

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

## 6. Kai: residual-driven training and complete decision evidence

The current decision-fit cycle is closed. Preserve K0, K6 and their saved diagnostics;
never change the failed rule retrospectively. The owning
[training process](https://github.com/min-mwei/Kai/blob/dev/docs/kai-training.md#13-design-process-for-a-training-cycle)
already specifies admission, hardness, root-capped dose, learnability, retention and
repeats. Apply it; do not add another approval process.

### 6.1 What would justify a future decision experiment

Require a persistent, source-adjudicated failure on complete eligible input and a distinct
falsifiable intervention. Decide among representation, supervision and interference from
evidence, not another dose variant. A small source-faithful view comparison can diagnose
representation; it must preserve authority, chronology, indicators and whole-call coverage.
Do not make production decisions from an annotated answer-key view.

MR293's CPU diagnosis rules out one cheap alternative on the existing outputs. At deny
cut `t`, K6 must satisfy `0.9906209685338778 < t <= 0.9983950716449809` to match K0's
campaign-history TP/FP, but `0.16360546846058788 < t <= 0.7573977740210421` to match
ToolCall Path. These intervals do not intersect. Across the six checked decision panels
there is likewise no common cutoff. Per-panel post-hoc cutoffs, selected on exposed DEV,
are not a calibration solution or permission for a family-specific router. This says
nothing about the impossibility of learning a better model.

For new eligible TRAIN, count primary-hard, auxiliary-hard, view-mismatch and solved roots
before assigning dose. Local-rule losses can carry signal even when the primary answer
is already correct; record both. Target unsolved mechanisms, while retaining useful solved
benign and legacy controls. Freeze renderer, eligible data, masked objective, root weights,
replay and visit order before fitting. A changed renderer, data distribution and schedule
are a bundled intervention unless controlled separately. One bounded candidate first;
stop at failed TRAIN learnability or the registered DEV/retention rule. Existing two-seed
recipe and three-seed adoption requirements apply only after a useful candidate warrants
them. No decision job is released by this plan.

Data Engineer should first finish existing DEV qualification and audit TRAIN source/proof
units. Further authoring follows the residual ledger, not a volume quota. Pair the same
proposed action with one decisive change in grant/revocation, condition/result, object or
destination binding, or source authority. Also vary irrelevant names/wording without
changing the answer. Keep realistic authorized transfers, credential access and control
maintenance as benign cases. Equal total labels are insufficient: balance unique roots
and effective loss mass by mechanism, class, question and layout. Do not select only hard
attacks. Keep source families/roots/twins/descendants together and distinguish new-family
transfer from new instances of familiar families. Authors receive abstract mechanisms and
TRAIN defects, never exposed evaluation examples or saved benchmark answers.

Soft-target distillation, replay/interference objectives and extraction adapters remain
conditional research choices. A teacher sees the same complete eligible TRAIN input,
supplies canonical-option probabilities and passes source adjudication; its confidence is
not ground truth. No teacher purchase, external data disclosure or architecture change is
a prerequisite to completing the current work.

### 6.2 Spans: learn the proof, preserve the decision

Use the existing detached head and cached features on frozen K0. No second session
checkpoint needs a span head in this cycle. Keep backbone, decision adapter, pointer and
temperature unchanged. Separate original-character gold, registered wrapper projection,
representability, prediction and the source map. The 317/317 same-feature comparison and
62/62 comparable fresh decision-fitted result are bounded arithmetic checks; complete the
actual serving path before widening their claim.

Prioritize condition/result, scope and call-argument units. The held decision-role check
found only 1/26 condition units exact, compared with 43/60 fitted units; structured argument
values are also missed in fitted data. These facts distinguish transfer from fitting
failures but do not isolate their cause. Check source validity, token/projection bounds,
length/candidate limits, loss/negative construction and decoder budgets on existing TRAIN
failures before another full feature pass or a larger adapter. If an experiment is needed,
a bounded TRAIN-only diagnostic on cached features must answer one such question first.

A valid decision proof may need a grant/limit, a later observation satisfying or defeating
its condition, the object/version and the current call's argument. Label every necessary
source occurrence and explicit acceptable alternatives before fitting. A tool's approval
claim is lower-trust content. Returned is not completed; missing is not empty. A benign
allow often needs positive evidence; only a certified no-evidence target is empty.
Unprojectable or clipped targets are unavailable. Do not repair exposed held gold into
training or change the wrapper rule to improve a held score.

MR294's source audit keeps the structured-value targets: all 28 checked source units
(12 fitted, 16 held) retain exact whole values in both the session line and the proposed
call, 56 copies total. Escaping surrounding JSON punctuation does not make an unchanged,
source-bound value ineligible. Actual value rewriting follows the existing unavailable
path. Nor can an observation of a marker in one file replace evidence that it occurs in
the outgoing artifact, or an earlier status check replace a required subsequent check.
R1 counts copies of the same source unit; equal words in another event are not that unit.

Keep strict decoded intervals and optional projected interpretations separate. The live
sample used saved maps; the pending session replay must regenerate them and preserve the
cutoff and source identity. Pin its helper code and input/expected identities in the
existing job registration. A package's `quote+call` interpretation is not an identical
training convention for older slice-1 targets that permitted only `quote`; disclose and
resolve that comparison scope without altering exposed held gold.

Report original and projected exact spans, complete gold-covered proofs, exact proof sets,
false evidence on certified-empty targets, extras and unavailable cases by role/family/
placement. Correct offsets, useful proof coverage and decision faithfulness are distinct.
Do not describe highlighted text as the model's causal reasoning. Leave thresholds and
budgets at their registered values until disjoint calibration; a source-verified coherent
premise contrast is more informative than deleting arbitrary words from a rationale.

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

**One active MLX stack, user decision 2026-11-15.** Use the same exact versions for Kai
training, reads and export, and for AgentArmor serving: [MLX 0.32.4](https://pypi.org/project/mlx/0.32.4/),
[MLX Metal 0.32.4](https://pypi.org/project/mlx-metal/0.32.4/) and
[MLX-LM 0.32.2](https://pypi.org/project/mlx-lm/0.32.2/), verified as the latest stable
releases that day. Separate purpose-specific environments may use these same pins;
an older library stack is not an active fallback. Trainer migrates the active dependency
locks, environment checks and new recipes after any in-flight job finishes. Keep old
recipes, locks and outputs as historical records, reproducible from their Git revision.

This supersedes MR272's proposed additional old-environment diagnostic. The migration
sequence was a new numerical profile, eight diagnostic tuples, then the original 320
parity comparisons on the unified stack with the same tolerance. Reuse completed valid
receipts; do not repeat this sequence unless a relevant artifact or numerical path changes.
Record any change from saved old-stack answers separately; a new reference is versioned
and does not turn a failed historical comparison into a pass. Preserve the pointer,
question isolation, temperature, escaping and span offsets, retain ToolCall/XPIA
results under their measured identities, and complete the outstanding whole-stack shadow replay. Identical library versions alone
do not establish identical kernels, merge numerics, model quality or release readiness.

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
not serving qualification; local MLX qualification does not qualify Linux vLLM. Span
training has begun; a fitted research head remains unqualified until its quality and the
actual serving path pass their separately registered checks.

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

For scale only, zero failures in 12 independent trials has a one-sided 95% binomial upper
bound of `1 - 0.05**(1/12) = 22.1%`; zero in 299 would put that bound below 1% for one
prespecified rate under independent representative sampling. Repeated layouts/twins are
not such independent trials, and joint production qualification has additional conditions.
The span set's 0/12 is therefore useful diagnostic evidence, not a 1% guarantee.

**Stop conditions:** source contradiction -> Data Engineer; missing captured evidence ->
core/monitor owner; poor relevance ranking with intact candidate evidence -> EG2 Trainer;
wrong answer on sound complete evidence -> Kai Trainer; training/serving divergence ->
Kai export and AgentArmor serving owners. Fix on TRAIN/DEV, retain the failed measurement,
and rerun only the affected checks plus required regressions. A new hold needs a reproduced
material failure, not a cosmetic preference.

## 9. Next handoff and scope control

Return completed artifacts or the concrete blocker, in one handoff:

1. Trainer: old/new fixed-K0 per-call scorecard and the small source-audited residual ledger;
   identify reused versus new model reads, actual complete-input misses and benign costs.
2. Trainer: one real span package→API→client→monitor receipt with actual decision, numerical,
   offset, provenance and latency/memory checks. Preserve the bounded scope of earlier parity.
3. Data Engineer: current fresh DEV qualification counts and source-based holds; eligible
   TRAIN proof-role audit and only the missing mechanisms requiring new authoring.

No new checkpoint family, broad GPU sweep, duplicate framework or pipeline refactor is
implied. Review the decision-changing outputs, not every command. Reopen an accepted area
only for a reproduced material defect. Keep the security mechanism, authorized user task
and observable outcome in every result; do not substitute packet counts for system quality.

Trainer owns implementation, recipes, artifacts and model jobs; Data Engineer owns source
and annotation repairs; Reviewer independently accepts results and checks claims. Work
stays on `dev`. The shared append-only board is outside Git and is not committed. Durable
facts go in their owning documents, not another planning layer. Keep `policy_ready: false`
until model, serving and end-to-end qualification pass. No release date is inferred from
a completed engineering task or a successful head fit.
