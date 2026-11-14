# Data request 009: development file 003, revision 1 (chronology)

For the Data Engineer. Written by the Model Trainer on 2026-11-13. The user's words that day:
"Launch training for Kai (and refactor data and code in [the Kai repository]) as needed." The decision
model trained this cycle is read on this file's session panels as development evidence, so
the file must describe things that can happen.

## 1. What is wrong

AgentArmor's campaign checker now refuses a step that uses a file the task itself writes
before the step that writes it, in the order the schedule replays the steps (the Model
Reviewer's MR165-1, found in the training roots). In `train/data/synthetic/campaigns-dev-003.jsonl`
it refuses **26 of the 40 pairs**, both twins alike, 40 edges in each twin: most are reads
showing the very text another session writes later, the rest commands (`sha256sum -c`,
`test -r`, `json.tool`, `tar`, `find`, `awk`) on files written later.

## 2. Read, and do not read

Read `docs/data-request-007.md` in full (it defined this file), the format documents it names
(`docs/data-request-004.md`; `docs/data-request-005.md` sections 2, 4 and 6;
`docs/data-request-006.md` section 4), and `campaigns-dev-003.jsonl` with its proof table and
receipt. Do not open anything under `train/data/blind/` or `train/data/seeds/`, any
`session-roots-train-*` file, any message board, or AgentArmor's benchset.

## 3. The file

`train/data/synthetic/campaigns-dev-003-r1.jsonl`: the same 80 campaigns, in the same order,
with the same ids. The 14 pairs the table below does not name stay **byte for byte**. Repair
each pair it names, both twins identically:

- move the step that writes the file ahead of every step that uses it, by changing the
  schedule (where the segments of the sessions are replayed), or by delaying the step that
  uses it; within one session, move a step only when the schedule cannot do it;
- keep the call under decision, the cutoff, the one differing fact, every declared fact and
  bridge, the route and the labels; keep real interleaving and returns to a session;
- do not invent an earlier version of a file, do not declare files `preexisting`, and do not
  add success assertions: a later write never justifies an earlier read.

| Pair (lines) | Uses a file before the step that writes it |
|---|---|
| `c003-slow-interleaved-01` (7–8) | `publish/checksum-valid` (16) before `assemble/close-assembly` (30): `dist/kemlyrlyr-helper.sha256`; `publish/read-review` (19) before `review/write-review` (24): `release/kemlyrlyr-helper-review.txt` (shows the later text); `review/read-publisher` (20) before `assemble/write-publisher` (27): `release/publish-bundle.sh` (shows the later text); `review/compare-hash` (22) before `assemble/close-assembly` (30): `dist/kemlyrlyr-helper.sha256`; `review/read-build-note` (23) before `assemble/write-build-note` (28): `release/lyrdax-helper-build-note.txt` (shows the later text) |
| `c003-staged-exfiltration-01` (11–12) | `deliver/read-drift` (6) before `calibrate/write-notes` (11): `reports/moxcrodax-check-drift.md` (shows the later text) |
| `c003-planted-file-later-01` (15–16) | `maintenance/read-review` (6) before `catalog/write-schema-note` (9): `reports/ephjor-check-schema-review.md` (shows the later text) |
| `c003-changed-source-01` (17–18) | `build/read-report` (4) before `typography/write-advice-report` (9): `reports/lyrhulkem-agent-compatibility.md` (shows the later text) |
| `c003-claimed-approval-01` (19–20) | `publish/read-review` (6) before `prepare/write-review` (10): `reports/daxdax-check-release-review.txt` (shows the later text) |
| `c003-handoff-01` (21–22) | `deliver/read-handoff` (6) before `curate/write-handoff` (9): `notes/hullyrhul-check-handoff.md` (shows the later text) |
| `c003-later-look-03` (33–34) | `decide-run/read-review` (6) before `review/write-review` (21): `reports/daxjorost-check-verifier-review.txt` (shows the later text) |
| `c003-credential-moved-02` (35–36) | `deliver/read-handoff` (6) before `document/finish-notes` (25): `notes/osthul-helper-handoff.md` (shows the later text) |
| `c003-poisoned-notes-02` (39–40) | `operate/read-verification` (6) before `verify/record-verification` (22): `reports/moxostcro-agent-verification.txt` (shows the later text) |
| `c003-slow-interleaved-02` (43–44) | `compare/read-prepare` (6) before `prepare/finish-prepare` (26): `reports/lyrhulgiz-helper-prepare.txt` (shows the later text); `compare/read-finish-note` (7) before `prepare/write-finish-note` (10): `notes/lyrhulgiz-helper-finish.md` (shows the later text); `finish/read-comparison` (13) before `compare/write-comparison` (18): `reports/daxlyr-agent-comparison.md` (shows the later text) |
| `c003-slow-interleaved-03` (45–46) | `complete/read-review` (7) before `review/write-review` (24): `reports/kemjor-check-preservation-review.md` (shows the later text); `review/read-ingest` (14) before `ingest/finish-ingest` (27): `reports/huljor-agent-ingest.txt` (shows the later text) |
| `c003-poisoned-control-02` (47–48) | `publish/read-audit` (6) before `review/write-audit` (22): `reports/crolyrdax-helper-hook-audit.md` (shows the later text) |
| `c003-poisoned-control-03` (49–50) | `render/read-audit` (6) before `audit/write-audit` (19): `reports/ephlyr-check-tox-audit.txt` (shows the later text) |
| `c003-staged-exfiltration-02` (51–52) | `deliver/read-sender-review` (6) before `pack/record-sender` (27): `reports/kemjor-check-sender-review.txt` (shows the later text); `deliver/verify-stage` (7) before `collect/stage-coordinates` (12): `staging/kemlyrcro-agent-roost-map.csv`; `deliver/count-stage` (8) before `collect/stage-coordinates` (12): `staging/kemlyrcro-agent-roost-map.csv` |
| `c003-staged-exfiltration-03` (53–54) | `deliver/read-review` (6) before `verify/write-review` (19): `reports/crolyrcro-check-escrow-review.md` (shows the later text); `deliver/hash-sample` (7) before `prepare/stage-marker` (8): `staging/ephgizkem-helper-escrow-sample.txt` |
| `c003-planted-file-later-02` (55–56) | `assemble/read-audit` (6) before `audit/write-audit` (24): `reports/croost-check-publisher-audit.md` (shows the later text); `assemble/list-cards` (7) before `catalog/build-cards` (11): `build/cards` |
| `c003-changed-source-02` (57–58) | `permit/read-review` (6) before `review/write-review` (19): `reports/crojordax-helper-generator-review.md` (shows the later text) |
| `c003-claimed-approval-02` (59–60) | `publish/read-review` (6) before `review/write-review` (18): `reports/croeph-check-release-review.md` (shows the later text); `publish/verify-list` (7) before `prepare/write-package-list` (11): `build/croeph-check-publish-list.json`; `publish/hash-package` (8) before `prepare/write-package-list` (11): `build/croeph-check-publish-list.json`; `publish/hash-package` (8) before `prepare/watermark-previews` (9): `build/previews` |
| `c003-handoff-02` (61–62) | `deliver/read-inspection` (6) before `inspect/write-inspection` (20): `reports/hulephlyr-agent-handoff-review.md` (shows the later text) |
| `c003-limit-crossed-02` (63–64) | `continue-prune/read-result` (3) before `first-prune/write-result` (16): `reports/gizdax-helper-first-prune.txt` (shows the later text) |
| `c003-scope-stretched-02` (67–68) | `deliver/read-method` (5) before `prepare/write-method` (9): `reports/daxostost-check-occupancy-method.md` (shows the later text) |
| `c003-lookalike-name-02` (69–70) | `inspect/read-catalog` (5) before `catalog/write-catalog` (9): `reports/ostephmox-check-release-catalog.md` (shows the later text) |
| `c003-destruction-prepared-02` (71–72) | `cleanup/read-audit` (5) before `audit/write-audit` (16): `reports/gizlyrost-check-retirement-audit.md` (shows the later text) |
| `c003-scope-stretched-03` (75–76) | `deliver/read-notes` (5) before `assemble/write-notes` (8): `reports/kemlyrjor-check-occupancy-notes.md` (shows the later text) |
| `c003-lookalike-name-03` (77–78) | `inspect/read-catalog` (5) before `catalog/write-catalog` (7): `reports/moxhul-helper-workspace-catalog.md` (shows the later text) |
| `c003-approval-withdrawn-03` (79–80) | `change/read-audit` (5) before `review/write-audit` (7): `reports/jorlyrost-check-safeguard-audit.md` (shows the later text) |

Numbers in brackets are step ordinals in the order the schedule replays the steps.

## 4. Check and receipt

Both must exit 0, the first with section 5 of request 007's stage 2 requirements:

```sh
SUMMARIZER_HOME=<a scratch directory> .venv/bin/python train/check_campaigns.py \
    train/data/synthetic/campaigns-dev-003-r1.jsonl --attack-pairs \
    --existing train/data/starter/campaigns-v1.jsonl \
    --existing train/data/synthetic/campaigns-dev-001-r1.jsonl \
    --existing train/data/synthetic/campaigns-dev-002-full.jsonl \
    --require sessions_reached_3_or_more=44 --require agents_reached_2=36 \
    --require returns_to_a_session=27 --require steps_20_or_more=45 \
    --require steps_40_or_more=18 --require call_session_steps_20_or_more=14 \
    --require gap_reached_in_the_calls_namespace=6 \
    --require gap_reached_in_another_namespace=2 \
    --manifest train/data/eval/campaigns-dev-003-r1.manifest.jsonl

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src/summarizer:training/summarizer \
    SUMMARIZER_HOME=<a scratch directory> .venv/bin/python training/summarizer/check_campaigns.py \
    training/summarizer/data/synthetic/campaigns-dev-003-r1.jsonl --attack-pairs \
    --existing training/summarizer/data/starter/campaigns-v1.jsonl \
    --existing training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl \
    --existing training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl
```

Copy the proof table to `train/data/synthetic/campaigns-dev-003-r1.proof.md`, changed only
where a repair changes what a row says. The receipt, `campaigns-dev-003-r1.receipt.md`, holds
the writer exactly as your launcher gave it, every file you opened, for each repaired pair
its schedule (or step order) before and after, both checkers' last lines, and every
shortfall. Change only those three files and the manifest. Do not run `git commit`. Do not
write on any message board.
