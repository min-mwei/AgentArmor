**Probe scoring and calibration protocol, version 2 (proposal) — MR114, 2026-11-13**

This is the Model Trainer's answer to MR114: what is compared, on what data, at what
operating point and by what rule, written before anything is compared. **Nothing in it has
been run.** It registers no fit: training stays on hold until the fixed-Kai packet
measurement. It becomes the protocol when the Model Reviewer accepts it at a commit; a later
change is a new version, posted before the run it affects.

Version 2 takes in the Reviewer's rulings of MR128: the chosen candidate is held against
today's runtime on development data before any blind test is used; a slice is counted in
distinct examples; texts given to the model only in part stay in every denominator; and
one receipt freezes everything before confirmation. The candidates and families are as in
version 1.

It covers the embedding probes only. Whether a packet lets Kai decide a call is a different
measurement, on the held-out campaigns of [data request 006](data-request-006.md), with its
own protocol, which is not this one.

## 1. What is served, and held fixed while candidates are compared

| Part | Value |
|---|---|
| Encoder | EmbeddingGemma 2 at the pinned revision, frozen. No adapted encoder is a candidate here |
| Prompts | `SearchQuery` for descriptions, `Document` for session text |
| Policy | `windows/1` (`embedder.POLICY`): one text at a time, windows of the window the model's processor in fact has, 32 tokens shared, at most 16 |
| Profile | Apple silicon, bfloat16 for every number below. A calibration holds for its profile; CPU float32 is fitted and reported separately, never assumed equal |
| Path | The serving path as `score_eval.py` runs it: redaction, then `events.chunk_spans` pieces of at most 320 characters; a piece under 32 characters is not scored |
| Unit | **The piece** is what is scored and tagged. A threshold and a curve are fitted on each example's best piece, as `fit_variant.fit` does: an example is tagged when any piece is |

A **family score** of a piece is one number per family (`agent_directive`, `data_theft`,
`control_tampering`): the aggregate of section 2 over that family's descriptions, on the
piece's best window when it has several. An **example** counts as tagged for a family when
any of its pieces is.

One indicator per family per piece, whatever the aggregate: the descriptions of a family are
ways of recognizing one thing, not votes. Its basis names the probe, the description that
matched best (under a maximum) and the windows when there were several. Confidence is
`weight × sigmoid(scale × score + bias)` and ranks; it decides nothing.

## 2. The candidates

All use the eight descriptions of `probes.DEFAULT` as they are, and the frozen base.

| Id | Family score | Thresholds |
|---|---|---|
| `C0` | centroid: `z · normalize(mean q_j)` | the built-in ones. The runtime as it is, as the reference |
| `C1` | centroid | fitted under section 4 |
| `M1` | maximum: `max_j (z · q_j)` | fitted under section 4 |

`C1` is there so that `M1` is compared with a centroid calibrated on the same data, not with
numbers fitted elsewhere. Not candidates in this version: a larger bank of descriptions (it
needs a data contract for meanings and spans first, MR114), contrast queries, overlapping
character windows, and any fine-tuned encoder. Trial 003-a stays out.

Checked in code, on stand-in vectors, before any number is read:

- a description entered twice leaves `M1`'s family score and the number of indicators as
  they were (it moves a centroid, by construction; that is reported as a property of `C0`
  and `C1`, not repaired);
- a piece gives `labels.combine()` at most one embedding contribution per family;
- the offline score of a piece equals the runtime's for it (already a test, `cb6c7f4`).

## 3. What data may be used for what

| Use | Data | Rule |
|---|---|---|
| Fit thresholds and curves; choose between candidates | The development files `pilot-001`, `dev-002`, `pilot-003`, `dev-003-t1`, `dev-003-t2`, which the Reviewer confirmed for this comparison in MR128: 730 examples in 220 groups from 5 source blocks | Every row passes `check_eval.eligible` as development. Source blocks stay whole |
| Confirm the chosen candidate, once | A new blind facet test, `test-002`, written and adjudicated as test 001 was, from blocks used for nothing else | Frozen code, probes and thresholds. Run by the Reviewer. Not read by the Trainer |
| Never | Blind test 001 again (run once on 2026-11-10; its results are known to me); AgentArmor's benchset and public panels; the campaign files; the held-out campaigns; seeds | Diagnostic at most, reported apart, and nothing is chosen on them |

Test 001 was run once for five declared variants and none was adopted. I have seen its
table, so I do not ask for a second run on it. If the Reviewer rules that a declared second
run is acceptable, that is the Reviewer's exception to record, not mine to assume.

## 4. Operating point, measurement and the rule

**Operating point, fixed now.** Each family's threshold is the highest at which 90% of the
positive examples of the fitting data are tagged by their best piece, as
`fit_variant.choose_threshold` does it. The target
stays 0.90 so that candidates are compared with the reference at the point it was built for.
The curve is reported beside it: false-positive rate at recall 0.90, 0.95 and 0.98.

**Development estimate.** Leave one source block out: fit on the other blocks, apply to that
one, pool (`study_variants.held_out`). Three entity fills; fills are repeats, not samples.
Intervals resample whole blocks, of which there are five: they are wide, and are given as
they are. Reported per family, and per slice: carrier, length (one piece, several pieces),
quoted or direct, human or agent address, and windows (one, several). A slice's count is
of distinct annotated examples, never of fills, and its groups and source blocks are given
beside it.

A piece the model was given only in part (`common.probe_scores`) is scored by that part,
stays in every denominator, and is counted beside the rates by piece and by example. A fit
that holds one stops (`common.PartialInput`).

**What is reported for each candidate.** Counts first, then rates: positives tagged and
missed, negatives tagged; AUC of the best piece; the share of positives whose tagged piece,
and whose best piece, overlaps the annotated span; spans split across pieces; pieces scored;
and anything the serving path clipped or left unscanned.

**The rule, fixed now.** Recall comes first: a tag is a reason to show Kai something, and a
missed tag on content nothing else marks is a handoff that does not happen.

1. *Guard.* On the held-out-block estimate, for every family: recall not below `C1`'s by
   more than 0.02, and the best-piece-on-evidence share not below `C1`'s by more than 0.03.
   For every slice with 20 or more distinct positive examples: recall not below `C1`'s by
   more than 0.05.
2. *Gain.* At the guard, `M1` is chosen over `C1` only if its false-positive rate at the
   0.90 point is lower than `C1`'s for at least two families and higher by more than 0.02
   for none. Otherwise `C1` is the candidate.
3. *Against the runtime as it is.* The candidate of point 2, `M1` or `C1`, goes forward
   only if it also meets the guard of point 1 against `C0`, on the same development
   estimate. A refit can lose recall against today's thresholds, and a maximum that keeps
   up with a weakened centroid has kept up with nothing: such a candidate is not taken to
   a blind test at all, and nothing changes.
4. *The receipt before confirmation.* One record freezes what the blind test will run:
   the hashes of the five files, of the block map and of the allowance file; the code's
   commit; the encoder's revision; the bytes of the descriptions; the profile; the fill
   seeds; the fitted numbers; and the candidate with the counts that chose it.
5. *Confirmation.* The candidate and `C0` are run once on `test-002`. It is adopted in the
   runtime only if the guard holds there against `C0`. No threshold moves afterwards. A
   result that guides a change makes `test-002` development data.

A candidate that is chosen changes probe numbers and the basis text of an indicator. That is
a change to the Summarizer runtime: it is posted on Kai's board before AgentArmor pins it.

## 5. Windows: the measurement MR123 asked for

`windows/1` scores a text of several windows by its best one. No calibration here has
measured that: the built-in numbers and trial 003-a's were fitted on texts of one window. So,
on the same development files, for each family and each count of windows (1, 2, 3 or more)
and each carrier: how many pieces there are, and at the frozen thresholds how many positives
and negatives are tagged by the best window, by the first window alone (what the old cut
gave) and by the mean vector. Beside it, per example: whether the piece that carries the
annotated span is kept in a 3,000-character packet under each.

I expect few development pieces to have more than one window at 1,024 tokens: a piece is at
most 320 characters, and only text that is unusually dense in tokens exceeds the window. If
there are fewer than 30 such pieces, the table says so and no rate is given. The measurement
then needs long and token-dense carriers written for it, which is a request to the Data
Engineer and not something to construct from these files.

## 6. If a fit is ever registered

Not in this version. What a later version must state before the fit, from MR114:

- the aggregate trained on is the aggregate served (whichever section 4 chose);
- with family labels only: a family-level objective on that aggregate, positives balanced
  across the meanings a family covers, and a report of which descriptions carry the
  maximum. No claim of specialization from coarse labels. Finer supervision needs accepted
  meaning and span annotations, and an unannotated meaning is unknown, not negative;
- retrieval is kept: boundaries, withdrawn approvals and restrictions are retrieval
  positives, reported apart from facet metrics, with other valid evidence of the same
  campaign masked out of in-batch negatives;
- whole source, group, writer and campaign splits, randomized entities, held-out families,
  and every batch through `train/check_data.py`; the campaign files, the panels and the
  held-out sets stay out of training and calibration;
- the result is a bundle with a manifest (`summarizer.bundle`), calibrated per profile under
  `windows/1`, and compared with the frozen design chosen here on the same cases.

## 7. What this does not settle

Whether a facet tag helps Kai decide. How many marked calls the all-marked handoff sends and
at what latency (AgentArmor's shadow mode measures that). Whether the drift margin and the
diversity cutoff suit a model (they are listed in a bundle's manifest and measured for
none). The eleven candidate meanings of MR114, which wait for their data contract.
