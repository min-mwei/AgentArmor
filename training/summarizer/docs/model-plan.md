# Model improvement plan: Summarizer, Kai and AgentArmor

Status: draft 2, 2026-11-10, written by the Model Trainer after a review of the three
repositories (Summarizer `dev`; AgentArmor `main` with the monitor, uncommitted then; Kai `dev`,
read only) and revised after the Model Reviewer's MR42. It answers one request from the user:
review the three and plan the model work, above all to cut **"missed by Kai: 160 (16.0%)"**.

Later on 2026-11-10 the user answered all three decisions in section 8. The plan was posted
on Kai's message board. The monitor was committed and pushed as AgentArmor `2cedf07`, which the
user then had moved to AgentArmor's `dev` branch and taken off `main` for now. Reply-form
instructions are to be trained for and reported as a second figure. The user also set the
order of Kai's next data (tool calls first, with all bases covered) and had Summarizer's `dev`
merged into `main` (`f0c4a8c`).

Three files, three audiences:

- this plan;
- [two-stage-analysis.md](two-stage-analysis.md), the measurements behind it, for reviewers and
  trainers only;
- [family-brief.md](family-brief.md), the only part that goes to data authors.

No Kai model was run. Every figure is a development figure on a public benchmark that Kai's
published seed was selected on. Observations and hypotheses are kept apart below.

## 1. In short

**What the 160 are** (any-indicator slice: the attacks Summarizer passes on by any indicator,
scored by the published Kai at its 0.5 default).

| Panel | Missed by Kai | What was observed |
|---|---:|---|
| BIPIA (instructions planted in data tables) | 104 | 103 are three of the panel's eighteen instructions. All three ask only for a change to the form of the reply. The other fifteen instructions are missed once in 583 |
| Short text | 52 | By the panel's own tags, misses concentrate in two effects: a claimed approval or authority, and an instruction tied to a later trigger |
| Hand-written (hw1) | 4 | Instructions addressed to a role inside a business document |
| **Total** | **160** | |

**What else was observed.**

1. **They are confident misses.** 148 of Kai's 171 misses on the two large panels score below
   0.01. Moving the cutoff from 0.5 to 0.001 recovers 30 attacks and adds 17 false alarms.
2. **Results swing with seed and visit order.** Nine trained copies of the model catch between
   501 and 862 of the 997 attacks at 0.5. The published one was selected from three seeds. A
   single run cannot show whether a change helped.
3. **The same instructions are missed by nearly every system measured.** Eight of the nine Kai
   copies, OpenAI Decisions and Kev-4B miss all or nearly all cases of the two end-placed
   reply-form instructions.
4. **Summarizer marks every BIPIA attack Kai misses**, at medium, with a wording rule, and marks
   10 of 235 benign tables that high. An indicator is not a verdict, and a fixed "either one
   flags it" rule is unusable (it flags 20% of benign).
5. **No calibration set exists**, and 0.5 is a default. Kai's own notes say so.

**What is hypothesis, not finding.**

- That the *kind* of instruction explains the BIPIA misses. In this panel every instruction has
  one placement, so kind and placement cannot be separated.
- That these families are under-represented in Kai's training. The short-text brief (196 of the
  336 injection records) has neither effect; the other 140 records are not tagged by effect and
  hold at least one claimed approval. No training state is a table.
- That new data for these families will move the number. The only evidence that the reply-form
  cases can be separated is one sibling model on these same rows.

**What is not measured at all.**

- **Kai on the monitor's packets.** The monitor sends Kai a layout it was never trained or
  measured on.
- **Kai's allow-or-deny decision on real agent calls in a session.** This is the decision the
  gate enforces and the one the user's design depends on. Kai's only tool-call panel is 128
  cases about path rules for invented tools.

**The plan** (section 5), in order: pin the evaluation contract; measure the two unmeasured
things; widen Kai's data by family; test stability; fix the handoff in the monitor; improve
Summarizer's weakest facet; and build tests nobody has seen.

**The three decisions of section 8 were answered on 2026-11-10.**

## 2. Review: Kai

What it is: Qwen3.5-4B-Base with a rank-32 adapter and a pointer head that answers registered
questions about a state. Two are trained: `decision` (allow or deny a tool call under rules) and
`is_xpia_attempt` (is untrusted content trying to redirect the agent).

| Finding | Evidence | Consequence |
|---|---|---|
| The training set is small | 556 records: 220 tool-call records in two families of invented tools; 336 injection records in 188 scenarios. Kai's notes name the cause: verified data arrives at "a few hundred records a day" | Coverage is narrow; breadth is the first lever, as Kai's notes already say |
| Injection data is narrow in carrier and uneven in effect | No injection state is a table. 196 of 336 are short plain texts in everyday settings, written to a brief with five effects. The other 140 (documents, agent logs, 14 of them coding sessions) are not tagged by effect | Whether an effect is represented cannot be read off the tags; a rough keyword audit suggests two of them are thin (analysis, section 5) |
| Attack and benign are one to one | 170 and 166. Kai's notes recommend one to three or four with matched clean twins | The false alarms observed are all one kind of benign look-alike (reported attack wording) |
| Scores sit at the two ends | Plain cross-entropy, adapter dropout 0, training fit 1,826 of 1,826, no calibration; the architecture note says the unbounded pointer "leaves a tail of confident errors that calibration cannot reorder" | The cutoff is not a lever (analysis, section 3.3) |
| Results swing with seed and order | Tool-call AUC 0.981, 0.884, 0.863 across the three seeds; 0.750 in one crossed cell. Kai's verdict: order sensitivity unresolved | One run proves nothing; adoption needs a stability gate |
| No calibration set; development panels are public | "No CAL population … exists for any Qwen checkpoint." The five panels are in AgentArmor's benchset | Every figure here is a development figure |
| The enforced decision is unmeasured on real calls | The tool-call panel is 128 cases about path rules for invented tools | Section 5.3 |
| Parts are untrained | `facts_sufficient` (weight 0) and evidence spans (frozen) | Kai cannot yet say it was not shown enough; the monitor's deterministic checks do that and must stay |

Kai's own queue already holds fresh development panels, a calibration population with a 1%
false-positive plan, 250 or more new tool-call scenarios over five or more policy structures,
and a rerun of two initializers by two orders. Its evaluation document already defines the
production contract (section 5.1). This plan adds the anatomy of the misses, the serving
layout, and the session form of the tool-call scenarios.

Two things in Kai's notes motivate measuring the packet path rather than assuming it works: an
earlier model read 0.957 in the layout it was trained on and 0.500 elsewhere, and facts supplied
to Kai untrained fixed one or two cases and broke eight to ten. Neither shows that today's Kai
must do worse on packets. They are reasons to measure before building on it.

## 3. Review: Summarizer

| Finding | Evidence |
|---|---|
| It holds recall where wording rules apply | BIPIA: all 703 attacks marked medium or higher; 10 of 235 benign tables at medium |
| It is weak on short indirect instructions | Short text, from low: 209 of 244 attacks and 116 of 244 benign passed on. 35 attacks never reach Kai: 12 with a later trigger, 11 that send or delete, 6 claimed approvals, 4 disclosures, 2 rule changes |
| Its embedding facets rank poorly on hard look-alikes | Blind test 001, run once by the reviewer: recall 0.89, 0.99, 0.98 at false-positive rates 0.68, 0.62, 0.74; AUC 0.755 for `agent_directive`. Untuned base model, built-in thresholds |
| It cannot tell quoted from live wording, by design | It tags; it does not judge. 26 benign short texts reach "high" |
| Its numbers move a little with the backend | CPU/float32 and MPS/bfloat16 differ by one or two cases per row (analysis, section 2) |

## 4. Review: the AgentArmor monitor and the benchset

| Finding | Evidence | Status |
|---|---|---|
| The default trigger cost recall | From medium, 85 attacks never reach Kai; from low, 36 | Watching now escalates from low; the gate stays at medium (5.5) |
| Kai reads a layout it never saw | Summarizer's packet goes into Kai's decision layout as the conversation. No training or test record has that shape | Measure (5.2) |
| Indicators were recorded but not surfaced | A trigger Kai cleared kept its labels in the record, with no way to list such entries | A `disagreement` field and a listing (5.5) |
| 0.5 is used as if it were an operating point | `FLAG_AT = 0.5`, labelled as not qualified | Unchanged until a calibration exists (5.1) |
| Identical requests can be sent more than once | On the file-read benchset population every content trigger of a case built the same request: 1,372 content triggers, 1,146 distinct requests. That is not general: an action and the content it returned ask different questions, and a later summary changes the context | Fixed (5.5) |
| The reviewer's MR41, MR43 and MR45 findings | Calls cleared on incomplete evidence; failures without a denial; unredacted call arguments; Kai blocking summaries; error logs quoting events; a display text taken for the whole call; a result taken for a proposal | Fixed and accepted by the reviewer (MR45, MR47, MR50) |
| The benchset is narrow and spent | Its largest panel is 18 instructions; it has no session and no real tool call; it is public; a seed was selected on it; both teams have read its misses | Measurement only, as a historical regression check |

## 5. The plan

Owners: **MT**, the Model Trainer here (Summarizer and the monitor code). **Kai**, Kai's
trainer and reviewers: their work needs their agreement and GPU lock, and I may only propose
it. **DE** and **MR**, this project's Data Engineer and Model Reviewer.

### 5.1 Pin the evaluation contract first (Kai's contract; MT and MR for the monitor's part)

Nothing below is assigned until this is agreed, because it decides what data is needed.

**Production qualification uses Kai's existing contract** (Kai `docs/evaluation.md`, section 8),
which the user confirmed there. It is adopted whole, including the per-route audit sizes, the
joint bounds, the recall gate, independent workflow sampling and the single attempt; the points
below are a summary, not a substitute:

- The unit is one decision. Target false interventions of 0.3% to 0.5%, and qualify an upper
  confidence bound at or below 1%.
- A benign call stopped for any reason counts: a model denial, a refusal, a timeout, an
  encoding or coverage failure. Failures stay in the denominator. The monitor's default denials
  (an unreadable event, a missing summary, a gap in the record, a call too large, Kai
  unreachable) are exactly these, and each is now recorded with what was enforced.
- Calibration and audit populations are traffic-representative, independent at the workflow
  level, disjoint from training and development, and sized by that document (5,000 benign
  calibration decisions per route; an audit of 5,000 benign and 3,000 attack decisions; one
  audit attempt).
- Each enabled route is calibrated on its own and never borrows a cutoff.

**What that means for the monitor.**

- The rule being calibrated is the rule that is enforced. Today the gate enforces Kai's
  `decision` answer for a proposed call. The injection score only sets the advisory `flagged`
  field; moving its cutoff changes no enforcement and certifies nothing about false denials.
- The monitor adds routes that do not exist yet in Kai's plan: `decision` on a packet, and
  `is_xpia_attempt` on a packet. A route is something the monitor can determine at run time. A
  benchmark panel is not a route.
- An earlier draft argued that Summarizer's filtering gives Kai a free false-positive budget
  (escalation rate times Kai's conditional rate). That holds only when both factors come from
  the same traffic under a fixed routing rule, and it buys nothing for default denials. The
  benchset's escalation rate is not a traffic rate. The argument is withdrawn.

**What this plan's own studies are: authored-distribution experiments.** They use written
scenarios, not traffic. They can rank candidates and reject one; they qualify nothing.

- 299 benign units with none above the cutoff is the feasibility minimum for a zero-error
  certificate of 1% (upper bound 0.997%; with one error it is 1.58%). It holds only for a
  frozen score function, a population fixed in advance, independent units and one attempt. It
  does not cover choosing a model, a fusion, a route or a retry on those same units.
- Any fused score over Kai's output and Summarizer's indicators is fitted on training or
  development scenarios and frozen. Calibration and audit scenarios are never used to fit it.
- For the two-by-two stability comparison (5.4) the metric and the cutoff procedure are frozen
  before the runs.
- Record Summarizer's indicators and Kai's verdict. An indicator is not a second verdict.

### 5.2 Measure the monitor's layout (MT prepares, Kai reads; days)

A **layout diagnostic**, not a decision funnel: does Kai's injection answer survive being asked
through the monitor's state layout?

1. The monitor can now print the exact request it would send for each trigger of a stored
   session (`agentarmor-monitor requests`). That is the serving layout, and Kai's trainer can
   render training, calibration and test inputs in it.
2. The benchset's injection cases are rendered through Summarizer and the monitor: 1,146
   requests, one per escalated case, frozen with a manifest (local, out of Git).
3. Kai's trainer scores them once with the registered scorer, under one model and serving
   identity, next to the raw-case baseline.

How to read it: the wrapping supplies an artificial user request and turns the whole case,
including lines the benchmark marks as the trusted task, into lower-trust content. Changed
request bytes are a new measurement, not a rescoring of the saved one. Arms are compared at
matched token budgets. The rendered requests are adjudicated before they are scored. The result
ranks layouts; it says nothing about tool decisions.

### 5.3 Measure and train the session decision (Kai with MT; starts with 5.2)

This is the user's central question: should this action happen in this session. It has no
panel and almost no training data in this form, so it comes first, alongside the layout
diagnostic.

- Scenarios are **sessions**: what the user asked, what the agent read and did, and one
  proposed call with a real tool, rendered by Summarizer and the monitor into the layout Kai is
  served. Kai's queued 250 or more tool-call scenarios over five or more policy structures are
  the natural place for them.
- Pairs hold the call fixed and change one decisive thing: the user's authorization, where the
  target was first named, earlier activity, when an approval was given or withdrawn, what
  actually returned as against what was only attempted, or whether the decisive evidence is
  present at all ([family-brief.md](family-brief.md)).
- A file that claims authority is lower-trust content. A style guide counts only if the user
  delegated to it.
- Two questions are kept apart in data and in reporting: does Kai recognise an injection, and
  should this call run.
- `facts_sufficient` can be trained from sessions whose packet is incomplete. It does not
  replace the monitor's deterministic completeness checks while it is untrained, and it does
  not replace them afterwards without its own qualification.

### 5.4 Widen Kai's injection data by family, and test stability (Kai, with DE)

- Data is directed by family, never by case: Kai's rule is "Never mine an evaluation failure
  into training; hard cases choose families, not cases." The families are in
  [family-brief.md](family-brief.md): six effects, with reply manipulation, claimed approval and
  later trigger the ones to broaden first; carriers from tables to tool results; three benign
  to one attack; and the monitor's layout next to the existing ones.
- Before declaring an effect absent, read the two untagged families by effect. The aim is
  stated as more breadth and representation, plus the serving layout; not as filling a void.
- Authors have seen neither the benchset nor the analysis. New examples are screened against
  benchset text for copies and near-copies and grouped by scenario.
- Size: Kai's notes say breadth beats repetition ("many structures, a few roots each"). About
  600 new scenarios would take the injection set from 336 records to about 1,800 at the stated
  rate.
- Stability: two initializers by two orders on the new data, read on fresh development panels
  with the frozen metric of 5.1, before any copy is published.
- The loss comes after data, as Kai's notes order it. With scores at the two ends, one matched
  pair of runs with a bounded or smoothed decision score is worth registering then, read on
  confident misses.
- Fresh data should cross instruction family, position and length, which this benchset cannot.

What Summarizer brings: DE writes synthetic sessions and passes them through
`train/check_data.py`, and Summarizer turns a session script into the packet. Seeds stay where
they are; whether Kai's authors may see them is the user's call and the default is no.

### 5.5 The handoff in the monitor (MT)

Committed as AgentArmor `2cedf07`, on its `dev` branch, after the reviewer accepted it (MR50):

1. Watching escalates from **low** (the user's recall-first decision of 2026-11-10); the gate
   asks Kai from medium, because a denial blocks a call and Kai's decision on real calls is
   unmeasured.
2. A record says when Summarizer marked an entry medium or higher and Kai cleared it
   (`disagreement`), and those can be listed. On the benchset that is 140 attacks and 95 benign
   cases.
3. Each record names what Kai was given: `agentarmor-state/v1` and `summarizer/v2`. A change to
   either is a change to Kai's input.
4. The MR41 fixes: no clearing on incomplete evidence, an explicit denial on every failure,
   the proposed call redacted, Kai asked off the summary loop. An allow counts only when the
   call was shown whole and the evidence is from before it; a request built from clipped
   arguments or from the call's display text is still sent, marked incomplete. Every check the
   gate attempts is recorded with what was enforced. A call below the gate's level is not
   checked and leaves no record, so counts start from a list of proposed calls and not from
   the monitor's records (MR57).

5. Each distinct request is put to Kai once (section 4). Distinct means the whole request,
   byte for byte, among the triggers found in one summary; every trigger keeps its record, and
   one that was not sent names the trigger that was. Requests are never merged by call id, and
   an answer is never reused for a later look at the session.
6. Each record says what embedded the summary it was built from (`summary_embedding`:
   backend, model, revision, device, precision, probes). The same session marked on another
   device or precision can build a different request, because a mark near a threshold moves.
   The field sits beside the request, never inside it. It is AgentArmor `179af10`, on `dev`,
   accepted in MR52.

No fused score until 5.1's conditions for one can be met.

### 5.6 Summarizer's own model (MT, DE, MR)

Goal: fewer attacks that never reach Kai and fewer benign escalations, without touching the
recall-first rule.

1. Wording rules for three families that need no model: text addressing the reading agent in
   the third person, a claimed approval joined to skipping a check, and an instruction tied to
   a later trigger. Low confidence, like every rule; written from the family.
   **Done on 2026-11-10, under review** (`da51ee3`, corrected in `e4d5c7e`), with a tool that
   measures the rules on annotated text (`train/rules_eval.py`). On development data the
   rules' own recall for `agent_directive` went from 0.25 to 0.97 at an unchanged
   false-positive rate, nearly all of it from one further change, an assistant called by name.
   On the benchset, executed afterwards as a regression check, 31 attacks never reach Kai
   where 37 did, and 182 benign cases are sent where 179 were. AgentArmor's monitor still pins
   the Summarizer before this change.
2. Fine-tune the embedding model on the synthetic set with DE batches for these families and
   reply manipulation. Adopt only if a new blind test improves at equal recall. The reviewer's
   MR53 found two gaps to close first (`987bb54`, completed in `220ccb4`): a text longer than
   the encoder's 512 tokens was cut silently with its label intact, and a fine-tune did not
   check its data and probes against the manifest or leave a receipt.
3. Blind test 002: written by DE, adjudicated by MR, never read by MT. Test 001 is spent, and
   MT has now read benchset text for these families, so 002 must be written without it.
4. The decode-and-normalise layer proposed in [design.md](design.md), section 7.
5. Quote every model-dependent count with its run record (device, precision, revision, the
   probes in effect, the hashes of the inputs).

### 5.7 Tests nobody has seen (MR and Kai's reviewers)

- Fresh development panels for Kai, with the four numbers its notes ask for: false positives
  at the comparator's recall, catches at the comparator's false-positive budget, confident
  misses, AUC.
- A locked two-stage test on authored sessions, run once, reporting per carrier and per family:
  attacks that never reached Kai, missed by Kai, caught; benign calls denied, including
  denials by default; unsafe calls missed; coverage failures; latency; and uncertainty. The
  trigger level, the state budget, the policy, the model package and the operating point are
  frozen first.
- The whole decision cohort fixed in advance is kept. A call whose packet is missing or holds
  later events is not usable as evidence about the model, but its default denial happened and
  counts as an intervention; filtering to pre-action reviews must not drop it. Kai's semantic
  score and what was actually enforced are reported as two separate measures.
- A reply-manipulation panel with many different instructions, crossed with position and
  length.
- The second figure the user asked for (decision 1) is reported next to the headline, never
  in its place: the same funnel without attacks that are reply manipulation of form only. On a
  fresh panel that is decided by a tag the author gives each attack before any model reads it
  ([family-brief.md](family-brief.md)). The benchmark's labels stay as they are.

## 6. Aims

These are preregistered aspirations, not forecasts and not qualification criteria.

| Measure | Today (development figures) | Aim |
|---|---|---|
| Missed by Kai, of attacks that reach it | 160 of 960 on the any-indicator slice; 161 of 961 on the low-trigger slice | 5% or less on fresh development panels, at a cutoff fixed by the frozen procedure |
| The second figure: the same, without form-only reply instructions | 57 of 840 on the any-indicator slice; 58 of 841 on the low-trigger slice | Reported beside the headline on every panel, by the author's tag |
| Attacks that never reach Kai | 36 of 997 from low; 85 from medium. With the new rules: 31 and 81 | 3% or less |
| Spread across retrained copies | 501 to 862 of 997 caught at 0.5 | Four cells of a two-by-two within 3 points on the frozen metric |
| Kai on the monitor's layout | Not measured | Measured; then within 2 points of the raw-case baseline |
| Decision on real calls in sessions | Not measured | A panel exists; an aim is set after its first read |
| False interventions, deployed path | Not measured | Kai's contract: an upper bound at or below 1% per decision, default denials included |
| Benchset, historical regression check only | 160 missed | 50 or fewer. Never a selection or stopping rule |

## 7. Order and rough timing

Estimates. Everything on Kai depends on its swarm taking the work on.

| When | What | Who |
|---|---|---|
| Done | 5.5 monitor work (AgentArmor `2cedf07`); the request printer; rendered requests for 5.2; this plan posted on Kai's board | MT |
| Next | 5.1: agree the contract and the routes | Kai, MR, user |
| Then, days | 5.2 one read of the rendered requests; 5.3 first session scenarios and their first read | MT, Kai |
| Week 1 to 2 | 5.4 data batch from the brief; calibration design; 5.7 fresh panels; 5.6 rules and DE batch | Kai, DE, MR |
| Week 2 to 3 | Kai two-by-two on the new data; reads on fresh panels; Summarizer fine-tune and blind test 002 | Kai, MT, DE, MR |
| Week 3 to 4 | Locked two-stage test, run once; adoption decision | MR, Kai's reviewers, user |

**What was agreed with Kai (2026-11-10).** At the user's instruction that all trainers agree
before anything trains, Kai's trainer posted a strategy for the next Kai-4B cycle on Kai's
message board, this trainer and Kai's reviewer answered it item by item, and the agreed text
("AGREED STRATEGY v2", 20:44 PST there) took every change asked for. It governs Kai's work; this
is a summary, and that entry is the record.

- **Outcome.** A recipe, not a seed: read with its control on identical fresh panels, with the
  metric and what counts as better, held and inconclusive registered per panel beforehand.
  Better in every cell on the panels the cycle is for (injection families, the session
  decision), held on the rest. The public panels are trackers only: Kai's own misses with no
  filter, 175 of 997, aimed at 60 or fewer; and, jointly, the attacks not caught at all at a
  named Summarizer commit (197 at the monitor's present pin), with the second figure beside
  each. No cutoff, no 1% claim and no locked-test read in this cycle.
- **Data.** Tool calls first: 250 new roots in slices of 50, in Kai's existing frame and in
  the monitor's session layout. A root rendered as a session is authored with its session
  facts as a script of hook events. Injection families are prepared in parallel, a registered
  share of them in the monitor's layout as well. Fresh panels are built before any new training
  is read, by other authors, with a part held back for one last read, and the session panel's
  roots are rendered on CPU and on MPS as two views.
- **Training.** The parity jobs and the layout read of the 1,146 frozen requests first. Then
  one step per about 100 admitted roots, data the only change: the two diagonal cells at
  intermediate steps and all four at registered milestones, on a registered read schedule.
  Loss, head, rank and base stay as they are until the data has landed, with one exception if
  a registered retention failure recurs.
- **Integration.** Eight items (I1 to I8, 20:41 PST there): what the monitor sends and which
  question it asks of what; that watching asks from low, the gate from medium, and an unmarked
  call never; that only the gate enforces, and only `decision`; the size budget; what
  identifies an input; how a session is rendered; and how a session panel is counted. Kai
  agreed to all eight. The Model Reviewer then checked them against the monitor's code (MR57)
  and corrected four that this trainer had worded too strongly, and the corrections were
  posted to Kai (21:22 PST): a decision example needs a request that was built, complete
  evidence and pre-action status, since not every request the monitor sends has them; the
  identity of an input is the render manifest's to state, since some monitor records carry
  none; a session is rendered as the events up to each proposed call, never as one final
  summary; and a panel is counted from a list of every proposed call, since a call below the
  gate's level leaves no record. Each proposed call lands as never asked, allowed, denied or
  stopped by default, and an allow on incomplete evidence is a default stop.
- **Change control, binding both sides.** A change to the layout, the packet, the question
  text or Summarizer's rules and priors is posted on Kai's board before it is pinned; the
  inputs are rendered again and Kai is read on them before it is served. A new Kai package is
  posted on Summarizer's board before AgentArmor serves it.
- **Still to come from this side.** The monitor's pin moves only after the Model Reviewer has
  accepted Summarizer's rule changes and detector identity and then the AgentArmor commit that
  carries them. Until then Kai renders with AgentArmor `179af10` and Summarizer `0b1e763`.

**Data before training (2026-11-10).** The user then asked that the data be ready before any
training begins. Kai's trainer holds its first job and keeps a checklist of eight items (R1
to R8, 20:47 PST on Kai's board). One is this side's: the tool that renders Kai's scripted
sessions into the layout the monitor serves.

- **The renderer** (`agentarmor-monitor render`) is accepted by the Model Reviewer (MR60) and
  on AgentArmor `dev` at `05bf55b`, with the monitor pinned to Summarizer `542b9d6`. The
  acceptance is of the tool, within CPU checks and scripted replay: it is not a receipt for
  any rendered data and not a claim about Kai. Kai's trainer has installed it and repeated a
  render on its own CPU backend. It takes one scripted session per line (the hook
  events, the rules, and the instruction files and links the session's disk held) and writes
  the exact request for each trigger as the session stood at that event, a row for every
  proposed call saying what the watcher and the gate would each do with it, and a manifest. It
  renders everything twice and writes nothing if the two differ. The reviewer accepted the
  per-call chronology and the use of the gate's own code, and returned it once for five
  corrections (MR58), which were made: a call too large to show was counted as asked though no
  request existed; a refused render could remove another render's files; the account of how
  much of an entry a request shows was a display limit; the renderer's own identity was a
  constant without its source; and one malformed input ended in a traceback.
- **Counting** (`agentarmor-monitor tally`, accepted with it) starts from that list of
  proposed calls and puts each in exactly one outcome at the gate: let through without Kai
  being asked, allowed by Kai, denied by Kai, stopped by default, let through on an error by a
  gate set to do so, or without an answer. For each label the calls are then those let
  through, those stopped and those without an answer. These say what the gate would do given
  the answers, not that any call ran. Before them it gives the steps to a usable decision
  example: roots, calls, calls marked at the gate's level, requests built, and requests that
  are complete and from before the call. A call has one label, however it is named.
- **A gap the review exposed, closed, accepted and pinned (2026-11-11, MR66).** A packet could
  count itself ready, and the gate would let an allow stand, while one of its lines was cut
  short: a file result whose hidden or control characters pushed its tail past what a line
  shows. Now a piece of content is shown whole or left out and counted, every line that quotes
  less of its entry than there is is counted by kind in the packet's own account and in its
  last line, and a cut on key evidence keeps the packet from being ready (Summarizer
  `c92cc97`). Measured with the real model on the 59 recorded sessions, 1,133 proposed calls
  at 4,400 characters: 277 packets are unchanged; 548 differ only in the last line, which now
  names the cuts; 307 also have a corrected count on the line of the call under decision; and
  in one, a piece of a file that had been shown cut finds no room whole and is left out and
  counted, with another piece in its place. No packet's readiness changed (111 ready before
  and after), and none of the 113 session-case packets changed. The reviewer accepted it,
  Kai had the requests before and after and no objection, and the monitor is pinned to it
  (AgentArmor `dev` at `a331fac`, Summarizer `fbe608d`).
- **What that count shows, and a question it opens.** A call marked medium or high that an
  earlier line cuts short is key evidence held in part, so every later packet of that session
  is not ready, and a gate stops by default on it. In the recorded sessions 66 kept calls are
  marked medium or high: 64 are longer than the 200 characters a timeline line shows, and 48
  are longer than the 1,200 the summary keeps. A longer line would not help. What would is to
  show, beside a cut call, the places that marked it, as is done for a long agent message,
  and to count the cut only when one of those places is missing. The reviewer's answer
  (MR65, MR66): what marked a call is not all a reader needs of it, since a destination, a
  negation or a redirection can lie outside the excerpt. The count stays honest, and any
  rule for when a cut call counts as shown needs paired cases in which the text left out
  changes the answer. Nothing is built.
- **Three more faults found on the way (2026-11-11), each fixed in its own commit, accepted
  and pinned with it.**
  A command that begins with a colon or with spaces was quoted in a packet without them:
  `: > notes.txt` as `> notes.txt` (`92aa0c7`; none of the 1,133 recorded calls begins that
  way). Text the model could not embed still steered which older calls a long summary kept
  (`4049718`, the reviewer's MR64-1). And half a surrogate pair, which a tool leaves when it
  cuts its output inside an emoji and which an agent can be told to write into a call, could
  not be embedded, could not be written as UTF-8 by a packet, and was refused by Kai's
  service, so a call that held one was never judged: stopped by default, and let through by
  a gate set to abstain (`fbe608d`, with the monitor's part in AgentArmor; none of the 2,498
  recorded events holds one). None of the three changes a recorded packet or summary.
- **A leak in the monitor's Kai client, found the same day, fixed, accepted and on `dev`.** With
  `http_proxy` set in the environment the standard library sent a request for a Kai service
  on this machine to that proxy: the packet left the machine in the clear, with no
  `--allow-remote`. A loopback address is now asked directly.
- **A summary depended on the machine it was made on**, found while building it. The hook asks
  the disk where a path leads and what an instruction file holds, and `~` fell back on the
  home of whoever ran the summarizer. On a Mac a call on a path under `/home` was marked as a
  redirected path; on Linux it is not. A replay now answers those from the script (`5e7cef8`).
  None of the 6,806 records the existing evaluation tools build changed on this machine.
- **Summarizer's own data.** The two corrections the reviewer asked of the annotation tools
  are made (`53093cf`) and closed (MR58). The pilot of batch 003 is addressed to the Data
  Engineer ([data-request-003.md](data-request-003.md), section 9): 40 groups of four in the
  evaluation format, from development blocks, read and adjudicated by the reviewer before
  anything more is written. It starts when the user starts that role.
- **Before any training is approved**, the reviewer asks for: the corrected tools, accepted
  pilot labels with the blind readers' and adjudication receipts, the exact hashes of the
  rendered data, split and leakage checks by source and root, the lineage of what was
  admitted, and the frozen evaluation schedule with an exposure audit. A checklist or an
  agreed strategy is not a receipt.

**Where the models run (2026-11-11).** The user decided: "For the AgentArmor e2e, Summarizer
model should be running its AgentArmor local server. Optionally, the same server could run
Kai-4B model. Also the decision models like Kai-4B can be run remotely in a cloud
environment." Until now Summarizer ran in the monitor's own process and Kai in a separate
service on the same machine. The design that follows from the decision, which is this
trainer's reading and goes through review like any other change:

- The local server always holds Summarizer's daemon, its embedding model and the rules.
- The decision model is behind it in one of two places: loaded by the server, or at a remote
  address. With neither, the server watches and marks, and decides nothing. That it loads
  Kai into its own process is how it is built, not part of the decision.
- The server owns every decision it gives. A gate names a proposed call (five ids, no text)
  and gets a receipt. The server reads the call from its own log, builds the request from
  its own record and rules, and binds the answer to the call, the request, the rules and
  what answered. A gate holds no rules, no address of a decision model and no credential.
  This replaces the first candidate, which the reviewer returned (MR62): there the gate
  built its own request and could be pointed at a remote model with the cloud token.
- What answers is the model the operator configured (model, readout, package hash), checked
  on every reply, or its answer is not used. What a service says of its own readiness is
  kept as its claim.
- Only the server sends session text out, and only it holds the credential for a remote
  model: over HTTPS with a bearer token, to one address, with no redirect, and never through
  a proxy for an address on this machine. Plain HTTP to a host that is not this machine is
  refused.
- An allow holds only if it still applies when it arrives: every byte the session logged
  while the answer was awaited is read again, and a request from the user, a report of what
  became of the call, or a tail that cannot be read takes it back. The server records the
  decision it issued and never what was enforced; the gate records what it did; the hooks
  report what became of the call. An attempt id ties the first two together.
- Status: the reviewer closed the two findings of MR62 and returned the server once more
  (MR67) for an allow that stood although the log had unread bytes and for a record of
  enforcement the server could not know. Both are fixed and with the reviewer (AgentArmor
  `96856d7`, `c476169` and `cbe79ab`, on no branch). Every request of the two diagnostic
  renders is unchanged by all three.

**Attacks that run over several sessions (2026-11-11, a proposal).** The reviewer reported
a user request for them and showed the gap (MR65): a later session's packet holds nothing of
an earlier one, and a call that runs what an earlier session planted is not marked and never
reaches Kai. [multi-session-contract.md](multi-session-contract.md) proposes what to keep
about earlier sessions (an index of what each call wrote, read, ran or named, rebuilt from
the log), three joins on the resource a call uses, a section of the packet for what they
find with its own account, when such a packet is ready, and where the lookup sits in the
server's decision: before the unmarked fast path. Nothing of it is built. What exists is the
measurement, `train/campaign_contract.py`, which replays several independent sessions and
decides at a pinned cutoff. The reviewer returned the first scorer (MR68): it credited lines
with a false kind, outcome, path or source. Version 2 believes nothing a builder says and
checks every line against its own replay, and sessions now take turns so that a session left
behind is really rebuilt from its log. On eleven starter campaigns today's packet shows 0 of
24 facts that lie in earlier sessions, routes 0 of 10 calls to the decision model that an
earlier session gives reason to send and 0 of 4 that should be undecided, and counts itself
ready in 18 of 22 runs where it is not. The user said on 2026-11-11 that this work is "very
important to prove the usefulness of Summarizer", and that the data work is to use Codex
Daybreak Blue at medium effort, run by the trainer as subagents.

**A fault found on 2026-11-11, fixed, accepted and pinned.** Text in a session could stop
the embedding model: the model's processor reads `<|image|>`, `<|audio|>` and `<|video|>`
inside text as places for media and fails when there are none, and the summary of that
session was then never updated again. The gate failed closed; watching alone went blind
without saying so. Every string the tokenizer reads as a control token is now made plain text
for the model, and a text the model still cannot take is counted as a gap in the session's
coverage instead of ending its summary, and is compared with nothing (Summarizer `65e283a`
and `61461bc`, accepted in MR64, on AgentArmor `dev` at `4efe812`). No text measured so far
holds such a string. The second way to make a text unreadable to the model, half a surrogate
pair, was found afterwards and is above.

## 8. Decisions for the user

1. **Reply-form instructions: train for them, and how to report them?** 103 of the 160 misses
   are planted instructions that only change the form of the agent's reply. They are injection
   attempts under Kai's current question, and the benchmark's labels and headline number stay
   as they are whatever you choose. The choice is about priority and reporting. *Recommended:*
   treat them as an injection mechanism worth training for, because the mechanism is the same
   as in a harmful injection; never let one alone decide that a tool call is denied; and report
   a second, preregistered number without them next to the headline (today 57 missed of the 877
   attacks that remain), so the security-relevant misses are visible on their own.
   **Answered 2026-11-10: yes** ("Also ok on #1"). The second figure leaves out attacks whose
   planted text changes only how the reply is rendered (layout, encoding, language, case or
   tone), adds no content of the attacker's choosing, and asks for no file, link, secret or
   tool call. On the benchset those are three BIPIA instructions, named in
   `train/two_stage.py`; that slice was defined after the misses were read, so it is a
   historical figure. On a fresh panel the author's tag decides, before any read.
2. **How does the Kai work get done?** I may not change the Kai repository or run its model.
   Options: you relay this plan to Kai's swarm; or you allow me one append-only entry on Kai's
   message board pointing at these three files; and for 5.2, Kai's trainer runs one read of
   the rendered requests under its GPU lock. *Recommended:* the board entry and the one read.
   **Answered 2026-11-10: the board entry** ("Post on Kai msgboard.md"). It is on Kai's
   `msgboard.md` under that date. The one read stays a proposal there: registering it, the
   check of the rendered requests and the GPU lock belong to Kai's trainer.
3. **Commit the monitor to AgentArmor `main` and push?** **Answered 2026-11-10: commit it, on
   `dev`.** It is AgentArmor `2cedf07`; the user had it moved from `main` to `dev` the same day,
   and `main` stays at `eaeaab0` until they ask. Its Summarizer dependency is pinned at
   `0b1e763` on Summarizer's `dev`, the package code it was reviewed against: Summarizer `main`
   (`18302f7`) lacked two fixes the monitor relies on (`b6e519b` and `0b1e763`). The user had
   `dev` merged into `main` on 2026-11-10 (`f0c4a8c`), so the pinned commit is now on `main`.

## 9. What this review did not establish

- Kai on the monitor's layout, and Kai's decision on real calls: not run, no panel.
- Real traffic: every figure is from benchmark text. Escalation and intervention rates in real
  coding sessions are unknown.
- Cause: the benchset cannot separate instruction kind from placement, and a comparator's miss
  says nothing about its training.
- Effect of the proposed data: unknown until fresh data and a stability comparison exist.
- Backend independence: Summarizer's counts differ slightly between CPU/float32 and
  MPS/bfloat16.
- Independence of the benchmark: it informed Kai's seed choice, and both teams have read its
  misses.
