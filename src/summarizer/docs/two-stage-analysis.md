# Where the two stages lose attacks: measurement analysis

Status: 2026-11-10, Model Trainer; revised after the Model Reviewer's MR42. This is the evidence
behind [model-plan.md](model-plan.md).

**For reviewers and trainers only. Do not give this file to data authors.** It describes
benchmark cases by id and count. Data is directed by family through
[family-brief.md](family-brief.md), which holds none of this.

Everything here is a development figure on AgentArmor's public benchset, which Kai's published
seed was selected on and which both teams have now read. Nothing here is a held-out result, a
calibrated operating point or a measurement of the monitor's real path.

## 1. What was run

| | |
|---|---|
| Cases | AgentArmor benchset, the four injection panels: 1,526 cases, 997 attacks, 529 benign |
| Stage 1 | Summarizer, fresh inference: EmbeddingGemma 2 at revision `f9972ac2…`, built-in probes `default-v3`. Figures below are from **Apple silicon, MPS, bfloat16** unless a row says otherwise |
| Stage 2 | Saved scores of the published Kai-4B (`benchset/predictions/kai-4b.jsonl`, SHA-256 `677bc3f7…`), flagged strictly above 0.5. **No Kai model was run** |
| Other trained copies | Per-case rows Kai's trainer saved for nine models (folders in Kai's `train/runs`, local and not committed there), read only |

Stage 1 is not backend-independent. The reviewer's CPU/float32 run of the same script moved a
few cases near a threshold (section 2). `training/summarizer/two_stage.py` prints a run record first; quote a
figure with it. The MPS/bfloat16 figures here are the run kept in
`training/summarizer/data/eval/two-stage-benchset-mps-bfloat16.jsonl`, made from clean code at `fac6a29`: its
first line names the code, the model revision, device and precision, the probes in effect
(`default-v3`, three calibrated) and the SHA-256 of every input as read. An earlier run with the
same counts was made from uncommitted code and is not that record. A later run from clean code
at `23dcec7`, kept as `two-stage-benchset-mps-bfloat16-v2.jsonl`, repeats every count of that
record and adds the second figure of section 2.

Two readings of stage 1 are used, and they are different populations:

- **Any-indicator slice.** A case is passed on when any chunk of its text earns an indicator, as
  `training/summarizer/evaluate.py` counts it. This is the funnel the user first saw: 960 attacks passed, 160
  missed by Kai.
- **Low-trigger slice.** A case is passed on when the session pipeline marks it low or higher,
  with the case text arriving as a file the agent read after a fixed request. This is what a
  monitor escalating from "low" sends: 961 attacks, 161 missed by Kai.

Wrapping a benchmark case as a file read is a **layout diagnostic**. It supplies an artificial
user request and makes the whole case lower-trust content, including lines the benchmark marks
as the trusted task. It is not a labelled tool-decision funnel.

## 2. The funnel

Any-indicator slice, Kai at 0.5 (MPS/bfloat16):

| Panel | Attacks | Passed to Kai | Caught | Missed by Kai | Never reach Kai |
|---|---:|---:|---:|---:|---:|
| BIPIA | 703 | 703 | 599 | 104 | 0 |
| Short text | 244 | 208 | 156 | 52 | 36 |
| hw1 | 40 | 39 | 35 | 4 | 1 |
| Armor | 10 | 10 | 10 | 0 | 0 |
| All | 997 | 960 | 800 | 160 | 37 |

Benign: 179 of 529 passed on, 10 flagged by Kai.

By the level a monitor escalates from (session pipeline):

| Escalate from | Run | Attacks reaching Kai | Caught | Missed by Kai | Never reach Kai | Benign reaching Kai | Benign flagged |
|---|---|---:|---:|---:|---:|---:|---:|
| low | MPS/bfloat16 | 961 | 800 | 161 | 36 | 185 | 10 |
| low | CPU/float32 (reviewer) | 960 | 799 | 161 | 37 | 183 | 10 |
| medium | MPS/bfloat16 | 912 | 772 | 140 | 85 | 105 | 10 |
| medium | CPU/float32 (reviewer) | 911 | 771 | 140 | 86 | 106 | 10 |
| high | MPS/bfloat16 | 124 | 108 | 16 | 873 | 37 | 5 |
| high | CPU/float32 (reviewer) | 125 | 109 | 16 | 872 | 37 | 5 |

Low-trigger slice by panel (MPS/bfloat16): BIPIA 703 reach Kai, 104 missed; short text 209
reach, 53 missed, 35 never reach; hw1 39 reach, 4 missed, 1 never; Armor 10 reach, none missed.

The benchset's escalation rates are not traffic rates. A third of these benign cases is
escalated because two panels are built from look-alikes; nothing here says how often a real
coding session's content is escalated.

**The second figure.** On 2026-11-10 the user asked for a second count beside the headline:
the same funnel without the attacks whose planted instruction changes only how the reply is
rendered and asks for nothing else. Here those are the three instruction ids of section 3.1,
120 attacks (41, 41 and 38). The slice was defined after the misses had been read, so on this
benchset it is a historical figure, not a preregistered one. Labels and the headline are
unchanged, and all 529 benign cases stay in (MPS/bfloat16, run record v2):

| Slice, without the 120 | Attacks | Reach Kai | Caught | Missed by Kai | Never reach Kai |
|---|---:|---:|---:|---:|---:|
| Any indicator | 877 | 840 | 783 | 57 | 37 |
| Escalate from low | 877 | 841 | 783 | 58 | 36 |
| Escalate from medium | 877 | 792 | 755 | 37 | 85 |
| Escalate from high | 877 | 124 | 108 | 16 | 753 |

Kai alone on the 877: 805 caught, 72 missed. The 57 are 52 short text, 4 hw1 and 1 BIPIA.

**After the rule change of 2026-11-10.** Sections 2 to 6 describe Summarizer with the wording
rules as they were when the misses were read. The rules were then extended (`da51ee3`) and
corrected after review (`e4d5c7e`), from the families of [family-brief.md](family-brief.md),
invented sentences and the development examples; nothing was written from or fitted on these
cases. The benchset is a historical regression check here. It was executed twice on the first
version of the new rules, with identical rows, and once on the corrected version, which
changed no count (MPS/bfloat16; run records v3, code `12f1849`, and v4, code `e4d5c7e`):

| Any-indicator slice | Attacks reaching Kai | Caught | Missed by Kai | Never reach Kai | Benign reaching Kai | Benign flagged |
|---|---:|---:|---:|---:|---:|---:|
| Rules before | 960 | 800 | 160 | 37 | 179 | 10 |
| New rules | 966 | 802 | 164 | 31 | 182 | 10 |
| Second figure, before | 840 | 783 | 57 | 37 | 179 | 10 |
| Second figure, after | 846 | 785 | 61 | 31 | 182 | 10 |

All six attacks that newly reach Kai are short text. Kai catches two and misses four, so the
"missed by Kai" row rises while the attacks not caught at all fall from 197 to 195. From the
low trigger the rows read 966, 802, 164 and 31, with 188 benign cases reaching Kai; from
medium, 916, 773, 143 and 81, with 105.

## 3. Kai's misses, observed

Kai alone misses 175 of 997 at 0.5: 104 BIPIA, 67 short text, 4 hw1. It flags 13 of 529 benign.

### 3.1 BIPIA, by injected instruction id

The panel is 18 instruction ids, each placed in 37 to 41 contexts, 696 of the 703 in tables. A
case id ends in the placement and the instruction id.

| Instruction ids | Placement | Attacks | Missed, published Kai | Missed, OpenAI Decisions | Missed, Kev-4B |
|---|---|---:|---:|---:|---:|
| `83c65111a331`, `f94757e7fad8` | end | 82 | 82 (41 and 41) | 80 | 82 |
| `bee80eb7f19e` | middle | 38 | 21 | 20 | 38 |
| the other fifteen ids | one placement each | 583 | 1 (id `022cab7d8028`, start) | 103 | 583 |

The three ids that account for 103 of the 104 misses share a kind: each asks only for a change
to the form of the reply, and names no link, file, secret or other action. The other fifteen
ask for content to be added (promotion, links, false claims).

What this does and does not show:

- **Observed:** three ids, 103 of 104 misses; the other fifteen, 1 of 583.
- **Not separable here:** every id has exactly one placement in this panel, so instruction kind
  and placement cannot be told apart. The miss rate is flat across state length (14% to 16% from
  under 1,000 to over 4,000 characters), which does not make kind the cause.
- **Hypothesis:** the kind of instruction matters. It needs fresh data that crosses family,
  position and length.
- **The comparators** miss the same ids. That is an observation about their scores, not about
  what they were trained on.

### 3.2 Short text, by the panel's own tags

From the tags in Kai's saved rows (published model):

| Effect tag | Attacks | Missed alone | Reach Kai (low-trigger) | Missed there | Never reach Kai |
|---|---:|---:|---:|---:|---:|
| `fabricate_authority` | 42 | 26 | 36 | 20 | 6 |
| `later_trigger` | 48 | 24 | 36 | 18 | 12 |
| `send_or_delete` | 48 | 10 | 37 | 8 | 11 |
| `change_rules` | 48 | 4 | 46 | 4 | 2 |
| `reveal` | 58 | 3 | 54 | 3 | 4 |

By phrasing tag, missed alone: `modal_passive` 21 of 58, `third_person_declarative` 18 of 58,
`second_person_imperative` 17 of 64, `bare_imperative` 11 of 64. Position tags show no
difference (20 to 26 missed of 74 to 90).

The other two seeds of the same run: `later_trigger` 19 and 24 missed of 48,
`fabricate_authority` 13 and 17 of 42.

False positives: all 12 of the published model's are benign cases tagged `reported` (12 of 58);
none of the other 186 benign cases is flagged. The other two seeds flag 30 and 24 of the 58.

### 3.3 Distance from the cutoff

| Panel | Missed at 0.5 | Of them below 0.01 | Caught at 0.5 | Caught at 0.001 | Benign flagged at 0.5, at 0.001 |
|---|---:|---:|---:|---:|---|
| BIPIA | 104 | 89 | 599 | 617 | 0, 0 |
| Short text | 67 | 59 | 177 | 187 | 12, 28 |
| hw1 | 4 | 2 | 36 | 38 | 0, 1 |

Median log-odds on BIPIA: benign −21, missed attacks −13. These are descriptions of saved
scores. No cutoff is proposed from them.

## 4. The trained copies

Kai's trainer has saved reads for nine 4B models trained on the same 556 records. Their history
matters for how to read them:

- **Selection.** The published model is seed 0 of **three** seeds (0, 1, 2), chosen by a pick
  rule registered before the reads
  (`recovery99-research-seed-pick-v1/pick-rule-v1.json`,
  `recovery99-three-seed-result-v1/internal-selection-v1.json` in Kai).
- **Diagnostics that selected nothing.** Two crossed cells (initializer and visit order swapped
  one at a time) and four cells of a stratified-order study. Kai's own verdict on these is that
  order sensitivity is unresolved.

At 0.5, on the 997 attacks and 529 benign cases:

| Model | Role | Attacks caught | Benign flagged | BIPIA caught of 703 | The two end-placed ids, missed of 82 |
|---|---|---:|---:|---:|---:|
| Seed 0 | selected, published | 822 | 13 | 599 | 82 |
| Seed 1 | selection pool | 564 | 33 | 310 | 82 |
| Seed 2 | selection pool | 501 | 31 | 263 | 82 |
| Initializer 0, order 1 | crossed diagnostic | 862 | 52 | 604 | 48 |
| Initializer 1, order 0 | crossed diagnostic | 644 | 5 | 450 | 82 |
| Stratified, init 0 order 0 | stratified study | 632 | 36 | 402 | 82 |
| Stratified, init 0 order 1 | stratified study | 660 | 26 | 433 | 82 |
| Stratified, init 1 order 0 | stratified study | 741 | 41 | 524 | 82 |
| Stratified, init 1 order 1 | stratified study | 812 | 15 | 588 | 82 |

What this supports: results at a fixed 0.5 swing widely with seed and order, so a single run
cannot show whether a change helped. It does not give a probability that a new run does worse:
the nine are different experimental cells, not draws from one recipe.

Averaging these saved scores does not help: mean log-odds of the three seeds catches 669 with
21 benign flagged, of all nine 707 with 20, against the published model's 822 with 13. That
rejects these two averages. It says nothing about other ensembles or a fitted fusion.

One descriptive ranking point: the crossed copy (initializer 0, order 1) scores 700 of the 703
BIPIA attacks above all but 2 of the 235 benign tables, in sample. That shows these rows can be
separated by a model trained on the same data. It is not a transferable cutoff, not a held-out
result, and not evidence about a new family.

## 5. Kai's training set, audited

The committed corpus
(`training/summarizer/data/corpus/qwen-rc2-recovery99-source-v1-20261107/train.jsonl` at Kai `4e636353`,
SHA-256 `9fe98d9d…`) holds 556 records: 220 tool-call and 336 injection records. The injection
records are 188 scenarios in three families, 170 positive and 166 benign.

| Family | Records | Settings | Notes |
|---|---:|---|---|
| Short plain text | 196 | Everyday settings (clubs, studios, small trades) | The only family with effect tags. Its brief lists five effects (`different_task` 21 scenarios, `reveal_data` 21, `change_rules` 20, `later_trigger` 18, `send_or_delete` 18); 15 of its 98 scenarios are the `reported` kind |
| Documents | 47 | IT 12, HR 11, support 10, commerce 8, general 6 | Not tagged by effect |
| Agent logs | 93 | General 23, IT 15, **code 14**, science 13, finance 10, support 8, HR 6, commerce 3, travel 1 | Not tagged by effect. Includes coding-agent sessions with untrusted tool output |

What can and cannot be said about the families Kai misses:

- **Not absent.** The reviewer found a positive document record in which untrusted text claims
  a prior approval and tells the assistant to skip a review, and coding-session logs among the
  agent-log family. An earlier draft of the plan said no training example taught these; that
  was the short-text brief generalised to the whole set, and it was wrong.
- **Thin, by a rough audit.** A keyword pass over the 170 positive records finds one that
  claims a prior approval and two that address the form of the reply; the short-text family has
  none of either. A keyword match is not a label, and the two untagged families have not been
  read by effect. Treat "thinly represented" as a hypothesis until they are.
- **Carriers.** No injection state is a pipe-delimited table or a block of comma-separated
  rows, by pattern. The longest injection state is 3,010 characters; the longest state in the
  whole set is 5,488.
- **Balance.** Attack and benign are about one to one. Kai's notes recommend one to three or
  four with matched clean twins.
- **Training.** Plain cross-entropy, adapter dropout 0, fit 1,826 of 1,826 targets, the
  gradient clip active on 300 of 582 updates, temperature 1.0, no calibration set.

## 6. Summarizer on these cases

- BIPIA (low-trigger slice): all 703 attacks are marked medium or higher, 10 of 235 benign
  tables at medium, 25 at low. The indicator on the cases Kai misses is a wording rule for text
  that addresses the reader's reply, at confidence 0.30.
- Short text: 209 of 244 attacks and 116 of 244 benign at low or higher; 161 and 72 at medium
  or higher; 26 benign at high.
- An indicator is not a verdict. The monitor's records already kept the trigger's labels when
  Kai cleared an entry; what was missing was a way to list such entries.
- Fixed unions are not usable: "Kai flags it, or Summarizer marks it medium or higher" catches
  962 of 997 and flags 108 of 529 benign; with "high", 838 and 45.
- On this population, where each case is one file read, every content trigger of a case built
  the same request, because a packet is centred on the call the content came back from: 1,372
  content triggers at low are 1,146 distinct requests, and the monitor asks once per trigger.
  This does not hold in general. An action and the content it returned ask different
  questions, and a later summary of the same call has a different context.

## 7. Exposure

The Model Trainer read benchset case text to describe these misses, and draft 1 of the plan
(Summarizer `22036be`) quoted three public BIPIA instructions and short fragments of short-text
cases. Removing the quotes does not undo that. Consequences:

- New examples for these families are written by authors who have seen neither the benchset
  nor this file, from [family-brief.md](family-brief.md) alone, and are screened for copies and
  near-copies of benchset text. All views and twins of a scenario stay in one group.
- The benchset stays measurement-only and is no longer a blind set for either model. A figure
  on it is a historical regression check, never a selection or stopping rule.
- A claim about these families needs a holdout written without sight of the exposed cases.

## 8. How to recount

```sh
# Funnel and trigger levels. First stdout line is the run record; the text table is on stderr.
.venv/bin/python training/summarizer/two_stage.py --benchset benchset

# By panel, instruction id and genre, for each saved system
.venv/bin/python training/summarizer/kai_misses.py --benchset benchset
.venv/bin/python training/summarizer/kai_misses.py --benchset benchset --system openai-decisions

# The trained copies, the short-text tags, and the mean of the copies' log-odds
R=KAI/train/runs
.venv/bin/python training/summarizer/kai_misses.py --benchset benchset --reads \
    $R/qwen-rc2-recovery99-run1-s{0,1,2}-dev-20261108 \
    $R/qwen-rc2-recovery99-cross-init{0-order1,1-order0}-dev-20261108 \
    $R/qwen-rc2-stratified-init{0,1}-order{0,1}-20261108-dev
```

`ranked_above_all_but_1pct_of_benign` in a BIPIA row is the in-sample ranking point of section
4. The effect-by-trigger columns of section 3.2 and the corpus audit of section 5 were counted
with one-off scripts from the same files; the reviewer recounted the corpus totals in MR42.
