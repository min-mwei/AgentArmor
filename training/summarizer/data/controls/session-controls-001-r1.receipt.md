# Receipt: session-controls-001-r1

## 2026-11-14 — data request 012, section 5

Writer exactly as launched: Codex gpt-daybreak-blue-latest at medium reasoning effort.

### Files opened for inspection

- `AGENTS.md`
- `agent_swarm.md`
- `training/summarizer/docs/data-request-012.md`
- `training/summarizer/docs/campaign-format.md`
- `training/summarizer/docs/data-request-008.md`, section 12 only
- `training/summarizer/data/controls/session-controls-001.jsonl`
- `training/summarizer/data/controls/session-controls-001.proof.md`
- `training/summarizer/data/controls/session-controls-001.receipt.md`
- `training/summarizer/data/controls/session-controls-001.review.md`
- the Kai repository's `train/recipes/kai-session-decision-20261113-v1/state-contract-v2.md`
- the Kai repository's `train/recipes/kai-session-decision-20261113-v1/blind/state-interpretation-v2.md`
- scratch report `$TMPDIR/controls-r1-check.json`

No code file, path under `private/`, `campaigns-test-*` file, message board, or other Kai file was inspected. The three checker entrypoints were executed, not inspected. The campaign checker alone received the section-12 `--existing` inputs. The original JSONL remains at SHA-256 `6d5069613853c8054143eedda3eb9646b057d485792ddccf7ab33362796d4af2`.

### Pair ledger

| Row | Benign root | Attack root | `differs` |
|---|---|---|---|
| 1 | `sc001-r01-benign` | `sc001-r01-attack` | `case/observe` |
| 1b | `sc001-r01b-benign` | `sc001-r01b-attack` | `case/recheck` |
| 2 | `sc001-r02-benign` | `sc001-r02-attack` | `case/change` |
| 3 | `sc001-r03-benign` | `sc001-r03-attack` | `case/replace` |
| 4 | `sc001-r04-benign` | `sc001-r04-attack` | `case/retarget` |
| 5 | `sc001-r05-benign` | `sc001-r05-attack` | `case/add` |
| 6 | `sc001-r06-benign` | `sc001-r06-attack` | `case/unknown` |
| 7 | `sc001-r07-benign` | `sc001-r07-attack` | `case/change`, `case/other` |
| 8 | `sc001-r08-benign` | `sc001-r08-attack` | `inspect/write_attempt` |
| 9 | `sc001-r09-benign` | `sc001-r09-attack` | `case/failed_write` |
| 9b | `sc001-r09b-benign` | `sc001-r09b-attack` | `case/failed_look`, `case/failed_write` |
| 10 | `sc001-r10-benign` | `sc001-r10-attack` | `other/touch` |
| 11 | `sc001-r11-benign` | `sc001-r11-attack` | `watch` |
| 12 | `sc001-r12-benign` | `sc001-r12-attack` | `case/grant2` |
| 13 | `sc001-r13-benign` | `sc001-r13-attack` | `case/grant2` |
| 14 | `sc001-r14-benign` | `sc001-r14-attack` | `case/grant2` |
| 15 | `sc001-r15-benign` | `sc001-r15-attack` | `case/grant2` |
| 15b | `sc001-r15b-benign` | `sc001-r15b-attack` | `case/grant2` |
| 15c | `sc001-r15c-benign` | `sc001-r15c-attack` | `case/grant3` |
| 16 | `sc001-r16-benign` | `sc001-r16-attack` | `case/grant` |
| 18 | `sc001-r18-benign` | `sc001-r18-attack` | `case/earlier` |
| 19a | `sc001-r19a-benign` | `sc001-r19a-attack` | `inspect/leaf` |
| 19b | `sc001-r19b-benign` | `sc001-r19b-attack` | `inspect/leaf` |

### Checks

All three commands used the same scratch `SUMMARIZER_HOME` under `$TMPDIR` in the final combined run.

Control checker coverage line:

```json
{"roots": 46, "views": 92, "both_clients": 46, "one_client": 0}
```

Control checker last line (exit 0):

```text
0 problems in 46 roots
```

Campaign checker last line (exit 1):

```json
{"campaigns": 46, "pairs": 23, "groups": 23, "family": {"diagnostic_control": 46}, "route": {"asked": 45, "undecided": 1}, "expect": {"sufficient": 45, "insufficient": 1}, "kind": {"attack": 23, "benign": 23}, "decision": {"deny": 23, "allow": 23}, "declared": {"sessions_3_or_more": 0, "schedule": 4, "both_agents_named": 0, "damage": 1, "steps_20_or_more": 0}, "at_cutoff": {"returns_to_a_session": 4, "gap_reached": 1, "gap_reached_in_another_namespace": 0, "bridge_across_agents": 0, "sessions_reached_3_or_more": 0, "agents_reached_2": 0, "gap_reached_in_the_calls_namespace": 1, "steps_20_or_more": 0, "steps_40_or_more": 0, "steps_median": 4, "call_session_steps_20_or_more": 0}, "bridged_pairs_by_write_outcome": {}, "review": {"prose_in_more_than_4_pairs_once_names_are_masked": {"pieces": 2, "largest_in_pairs": 12}, "work_stamped_from_a_few_shapes": {"campaigns_with_a_session_of_12_steps": 0, "mostly_three_shapes": 0, "largest_share": 0}, "reads_of_an_earlier_version_shown_only_by_the_read": 1, "outputs_before_the_call": {"command": {"not judged: a form the check does not compare": 3, "not judged: no earlier write of what it reads": 22, "not judged: the version it reads is not established": 2, "shape checked": 2, "value compared": 8}, "read": {"not judged: no earlier write of what it reads": 14}, "other": {}}, "reads_after_an_effect_not_shown": 2, "of_them_showing_other_text_than_the_last_write": 0, "reads_in_a_form_not_compared": 0}, "problems": 1, "scorer": "campaign-contract/9", "sha256": "5be124cf3e36b2120a90762b861fee7c3ea5223ed2a33adcf6673b74eca3b5c6", "checker_sha256": "3cbdb62b3778de5253ec790313bfb1bec173d67fec4d683761a7d128cab2ecd0", "code": "64eeaa9852148b37de4d9e5abf43f59395476cbb", "existing": [{"file": "training/summarizer/data/starter/campaigns-v1.jsonl", "sha256": "cc9fe0bba69fb59bfcc75420106ccfeef9f063b341cbe589681e53d924d7d99b"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl", "sha256": "a9fdcfc2920544999e889874c79b5901e251955892080c988766171c77582c9a"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl", "sha256": "2109cdd9dc0467f2fea8e62f344944c6828f67f47ae1bc81d10d93850f5c3a95"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-003.jsonl", "sha256": "58809644e80334d2448408c1d953a13102c908a11c4feb3300ee933e6e3c3799"}, {"file": "training/summarizer/data/synthetic/session-roots-train-001.jsonl", "sha256": "d9f25545d36a8ff303544e7da73076149a1e4482c2e10411190f24d3d1ba69cc"}], "options": {"attack_pairs": true, "require": {}}, "replay": {"ran": true, "agents": ["claude_code", "codex_code"], "runtime": "hash", "budget": 20000}, "complete": true}
```

Evidence checker last line (exit 1):

```json
{"roots": 46, "refused": 1, "labelled": 46, "named_facts": 113, "omissions_refused": 224, "omissions": 224, "producer_omissions": 20, "producer_omissions_refused": 20, "file_sha256": "5be124cf3e36b2120a90762b861fee7c3ea5223ed2a33adcf6673b74eca3b5c6", "checker_sha256": "7d23c942c32d0c793b7994c6866e4f656c80209a83664524cd9fc8b65bfff1d1", "scorer": "campaign-contract/16", "budget": 20000}
```

### Shortfalls

- No requested row was omitted. The file has 23 rows and 46 roots, and every root has both client views.
- The control checker exits 0 with no problems. The campaign and evidence checkers exit 1 only for the required Codex view of `sc001-r08-benign`: respectively, `its own evidence does not pass when written out (codex_code)` and `its reference packet does not pass (codex_code)`. Section 5.3 requires that exact view to remain deny/incomplete with `unresolved_condition`, while section 5.2 says such a non-capture incomplete asked view is an expected negative. Removing the Codex override, pinning the root to Claude Code, or changing the denied write would contradict section 5; changing checker behavior is outside the three-file scope. Therefore the requirement that all three checks exit 0 could not be met without violating another section-5 requirement.
- The evidence checker also emits advisory review lines for row 1b's `source_observation` fact being named by no label; its removal is noticed in both layouts, as in version 1.
- No commit was made and no message board was opened or written.

## 2026-11-14 — subsection 5.7 amendment to rows 10 and 19b

Rows 10 and 19b were amended under data request 012, subsection 5.7. In row 10 benign, the
other session's write of the disjoint file remains in the trace, but `disjoint_write` is no longer
declared in `facts` or named by `story.evidence`; continuity is represented by the checked file's
object account. In both row-19b roots, `scripts/r19b-task.sh` now links to the newly observed
`scripts/r19b-deep.sh`, whose body holds the former third-file body. The new `deep_link` fact names
the third file's result, `dependency_body` moved to the fourth file's result, and
`control.may_lose` contains only `dependency_body`. The row-19b twin delta is now `inspect/deep`.

Writer exactly as launched: Codex gpt-daybreak-blue-latest at medium reasoning effort.

### Files opened in the subsection 5.7 sitting

- `AGENTS.md`
- `agent_swarm.md`
- `training/summarizer/docs/data-request-012.md`, sections 4 and 5
- `training/summarizer/docs/data-request-008.md`, section 12 only
- `training/summarizer/data/controls/session-controls-001-r1.jsonl`
- `training/summarizer/data/controls/session-controls-001-r1.proof.md`
- `training/summarizer/data/controls/session-controls-001-r1.receipt.md`
- scratch report `$TMPDIR/controls-r1-check.json`

No code file, path under `private/`, `campaigns-test-*` file, message board, or Kai repository file
was opened. The three checker entrypoints were executed, not inspected.

### Amended pair ledger entries

| Row | Benign root | Attack root | `differs` |
|---|---|---|---|
| 10 | `sc001-r10-benign` | `sc001-r10-attack` | `other/touch` |
| 19b | `sc001-r19b-benign` | `sc001-r19b-attack` | `inspect/deep` |

### Subsection 5.7 checks

All three commands used the same fresh scratch `SUMMARIZER_HOME` under `$TMPDIR` and exited 0.

Control checker coverage line:

```json
{"roots": 46, "views": 92, "both_clients": 46, "one_client": 0}
```

Control checker last line:

```text
0 problems in 46 roots
```

Campaign checker last line:

```json
{"campaigns": 46, "pairs": 23, "groups": 23, "family": {"diagnostic_control": 46}, "route": {"asked": 45, "undecided": 1}, "expect": {"sufficient": 45, "insufficient": 1}, "kind": {"attack": 23, "benign": 23}, "decision": {"deny": 23, "allow": 23}, "declared": {"sessions_3_or_more": 0, "schedule": 4, "both_agents_named": 0, "damage": 1, "steps_20_or_more": 0}, "at_cutoff": {"returns_to_a_session": 4, "gap_reached": 1, "gap_reached_in_another_namespace": 0, "bridge_across_agents": 0, "sessions_reached_3_or_more": 0, "agents_reached_2": 0, "gap_reached_in_the_calls_namespace": 1, "steps_20_or_more": 0, "steps_40_or_more": 0, "steps_median": 4, "call_session_steps_20_or_more": 0}, "bridged_pairs_by_write_outcome": {}, "review": {"prose_in_more_than_4_pairs_once_names_are_masked": {"pieces": 2, "largest_in_pairs": 12}, "work_stamped_from_a_few_shapes": {"campaigns_with_a_session_of_12_steps": 0, "mostly_three_shapes": 0, "largest_share": 0}, "reads_of_an_earlier_version_shown_only_by_the_read": 1, "outputs_before_the_call": {"command": {"not judged: a form the check does not compare": 3, "not judged: no earlier write of what it reads": 22, "not judged: the version it reads is not established": 2, "shape checked": 2, "value compared": 8}, "read": {"not judged: no earlier write of what it reads": 16}, "other": {}}, "reads_after_an_effect_not_shown": 2, "of_them_showing_other_text_than_the_last_write": 0, "reads_in_a_form_not_compared": 0}, "problems": 0, "scorer": "campaign-contract/9", "sha256": "c70aa4b2cb3ec99869bce73bd4ecd4756ad868d8b6b90a88590a0861264be2f4", "checker_sha256": "3cbdb62b3778de5253ec790313bfb1bec173d67fec4d683761a7d128cab2ecd0", "code": "419600cbf0cf423c9dc17bde8e1330cc3f2b16f2", "existing": [{"file": "training/summarizer/data/starter/campaigns-v1.jsonl", "sha256": "cc9fe0bba69fb59bfcc75420106ccfeef9f063b341cbe589681e53d924d7d99b"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl", "sha256": "a9fdcfc2920544999e889874c79b5901e251955892080c988766171c77582c9a"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl", "sha256": "2109cdd9dc0467f2fea8e62f344944c6828f67f47ae1bc81d10d93850f5c3a95"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-003.jsonl", "sha256": "58809644e80334d2448408c1d953a13102c908a11c4feb3300ee933e6e3c3799"}, {"file": "training/summarizer/data/synthetic/session-roots-train-001.jsonl", "sha256": "d9f25545d36a8ff303544e7da73076149a1e4482c2e10411190f24d3d1ba69cc"}], "options": {"attack_pairs": true, "require": {}}, "replay": {"ran": true, "agents": ["claude_code", "codex_code"], "runtime": "hash", "budget": 20000}, "complete": true}
```

Evidence checker last line:

```json
{"roots": 46, "refused": 0, "labelled": 46, "named_facts": 116, "omissions_refused": 232, "omissions": 232, "producer_omissions": 28, "producer_omissions_refused": 28, "file_sha256": "c70aa4b2cb3ec99869bce73bd4ecd4756ad868d8b6b90a88590a0861264be2f4", "checker_sha256": "7d23c942c32d0c793b7994c6866e4f656c80209a83664524cd9fc8b65bfff1d1", "scorer": "campaign-contract/16", "budget": 20000}
```

### Subsection 5.7 shortfalls

- None. Both requested row amendments were made, and all three checks exited 0.
- No commit was made and no message board was opened or written.

## 2026-11-14 — subsection 5.6 amendment to row 8

Row 8 was amended under data request 012, subsection 5.6. In both roots, the earlier `inspect`
session now contains the conditional grant, the check, and the write, while the deciding `case`
session contains the unconditional request `Upload artifacts/r08.bin now.` and the upload. The
`grant` fact moved to `inspect/grant`; the new `upload_request` fact records `case/request`, and
both roots' decision and rule evidence follow those facts. The attack write still returns. The
benign write is still denied, with lifecycle `inspect/write_attempt` refused for Claude Code and
proposed for Codex; its Codex override remains deny/complete. The only twin delta remains
`inspect/write_attempt`.

Writer exactly as launched: Codex gpt-daybreak-blue-latest at medium reasoning effort.

### Files opened in the subsection 5.6 sitting

- `AGENTS.md`
- `agent_swarm.md`
- `training/summarizer/docs/data-request-012.md`, sections 4 and 5
- `training/summarizer/docs/data-request-008.md`, section 12 only
- `training/summarizer/data/controls/session-controls-001-r1.jsonl`
- `training/summarizer/data/controls/session-controls-001-r1.proof.md`
- `training/summarizer/data/controls/session-controls-001-r1.receipt.md`

No code file, path under `private/`, `campaigns-test-*` file, message board, or Kai repository file
was opened. The three checker entrypoints were executed, not inspected.

### Amended pair ledger entry

| Row | Benign root | Attack root | `differs` |
|---|---|---|---|
| 8 | `sc001-r08-benign` | `sc001-r08-attack` | `inspect/write_attempt` |

### Subsection 5.6 checks

Every check used a fresh scratch `SUMMARIZER_HOME` under `$TMPDIR` and exited 0.

Control checker coverage line:

```json
{"roots": 46, "views": 92, "both_clients": 46, "one_client": 0}
```

Control checker last line:

```text
0 problems in 46 roots
```

Campaign checker last line:

```json
{"campaigns": 46, "pairs": 23, "groups": 23, "family": {"diagnostic_control": 46}, "route": {"asked": 45, "undecided": 1}, "expect": {"sufficient": 45, "insufficient": 1}, "kind": {"attack": 23, "benign": 23}, "decision": {"deny": 23, "allow": 23}, "declared": {"sessions_3_or_more": 0, "schedule": 4, "both_agents_named": 0, "damage": 1, "steps_20_or_more": 0}, "at_cutoff": {"returns_to_a_session": 4, "gap_reached": 1, "gap_reached_in_another_namespace": 0, "bridge_across_agents": 0, "sessions_reached_3_or_more": 0, "agents_reached_2": 0, "gap_reached_in_the_calls_namespace": 1, "steps_20_or_more": 0, "steps_40_or_more": 0, "steps_median": 4, "call_session_steps_20_or_more": 0}, "bridged_pairs_by_write_outcome": {}, "review": {"prose_in_more_than_4_pairs_once_names_are_masked": {"pieces": 2, "largest_in_pairs": 12}, "work_stamped_from_a_few_shapes": {"campaigns_with_a_session_of_12_steps": 0, "mostly_three_shapes": 0, "largest_share": 0}, "reads_of_an_earlier_version_shown_only_by_the_read": 1, "outputs_before_the_call": {"command": {"not judged: a form the check does not compare": 3, "not judged: no earlier write of what it reads": 22, "not judged: the version it reads is not established": 2, "shape checked": 2, "value compared": 8}, "read": {"not judged: no earlier write of what it reads": 14}, "other": {}}, "reads_after_an_effect_not_shown": 2, "of_them_showing_other_text_than_the_last_write": 0, "reads_in_a_form_not_compared": 0}, "problems": 0, "scorer": "campaign-contract/9", "sha256": "a0e3a3edfc892f521c1e79ea9034ca50b522f0e24f1d1236d06785bb8a3fd968", "checker_sha256": "3cbdb62b3778de5253ec790313bfb1bec173d67fec4d683761a7d128cab2ecd0", "code": "936314e87a40984511d00faba3064ea23ef88286-dirty", "existing": [{"file": "training/summarizer/data/starter/campaigns-v1.jsonl", "sha256": "cc9fe0bba69fb59bfcc75420106ccfeef9f063b341cbe589681e53d924d7d99b"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl", "sha256": "a9fdcfc2920544999e889874c79b5901e251955892080c988766171c77582c9a"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl", "sha256": "2109cdd9dc0467f2fea8e62f344944c6828f67f47ae1bc81d10d93850f5c3a95"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-003.jsonl", "sha256": "58809644e80334d2448408c1d953a13102c908a11c4feb3300ee933e6e3c3799"}, {"file": "training/summarizer/data/synthetic/session-roots-train-001.jsonl", "sha256": "d9f25545d36a8ff303544e7da73076149a1e4482c2e10411190f24d3d1ba69cc"}], "options": {"attack_pairs": true, "require": {}}, "replay": {"ran": true, "agents": ["claude_code", "codex_code"], "runtime": "hash", "budget": 20000}, "complete": true}
```

Evidence checker last line:

```json
{"roots": 46, "refused": 0, "labelled": 46, "named_facts": 115, "omissions_refused": 230, "omissions": 230, "producer_omissions": 24, "producer_omissions_refused": 24, "file_sha256": "a0e3a3edfc892f521c1e79ea9034ca50b522f0e24f1d1236d06785bb8a3fd968", "checker_sha256": "7d23c942c32d0c793b7994c6866e4f656c80209a83664524cd9fc8b65bfff1d1", "scorer": "campaign-contract/16", "budget": 20000}
```

### Subsection 5.6 shortfalls

- None. All requested row-8 changes were made, and all three checks exited 0.
- No commit was made and no message board was opened or written.

## 2026-11-14 — subsection 5.5 amendment to row 8

Row 8 was amended under data request 012, subsection 5.5. In both roots, an earlier `inspect`
session now checks `artifacts/r08.bin` and then makes the write, while the deciding `case` session
only asks and uploads. The attack write returns. The benign write is denied, with lifecycle
`inspect/write_attempt` refused for Claude Code and proposed for Codex. The benign Codex override
is deny/complete with no `incomplete_because`; the only twin delta is
`inspect/write_attempt`.

Writer exactly as launched: Codex gpt-daybreak-blue-latest at medium reasoning effort.

### Files opened in the amendment sitting

- `AGENTS.md`
- `agent_swarm.md`
- `training/summarizer/docs/data-request-012.md`, sections 4 and 5
- `training/summarizer/docs/data-request-008.md`, section 12 only
- `training/summarizer/docs/campaign-format.md`
- `training/summarizer/docs/multi-session-contract.md`
- `training/summarizer/README.md`
- `training/summarizer/data/controls/session-controls-001-r1.jsonl`
- `training/summarizer/data/controls/session-controls-001-r1.proof.md`
- `training/summarizer/data/controls/session-controls-001-r1.receipt.md`
- scratch report `$TMPDIR/controls-r1-check.json`

No code file, path under `private/`, `campaigns-test-*` file, message board, or Kai repository file
was opened. The three checker entrypoints were executed, not inspected.

### Amended pair ledger entry

| Row | Benign root | Attack root | `differs` |
|---|---|---|---|
| 8 | `sc001-r08-benign` | `sc001-r08-attack` | `inspect/write_attempt` |

### Amendment checks

All three commands used the same scratch `SUMMARIZER_HOME` under `$TMPDIR` in the final combined
run.

Control checker coverage line:

```json
{"roots": 46, "views": 92, "both_clients": 46, "one_client": 0}
```

Control checker last line (exit 0):

```text
0 problems in 46 roots
```

Campaign checker last line (exit 1):

```json
{"campaigns": 46, "pairs": 23, "groups": 23, "family": {"diagnostic_control": 46}, "route": {"asked": 45, "undecided": 1}, "expect": {"sufficient": 45, "insufficient": 1}, "kind": {"attack": 23, "benign": 23}, "decision": {"deny": 23, "allow": 23}, "declared": {"sessions_3_or_more": 0, "schedule": 4, "both_agents_named": 0, "damage": 1, "steps_20_or_more": 0}, "at_cutoff": {"returns_to_a_session": 4, "gap_reached": 1, "gap_reached_in_another_namespace": 0, "bridge_across_agents": 0, "sessions_reached_3_or_more": 0, "agents_reached_2": 0, "gap_reached_in_the_calls_namespace": 1, "steps_20_or_more": 0, "steps_40_or_more": 0, "steps_median": 4, "call_session_steps_20_or_more": 0}, "bridged_pairs_by_write_outcome": {}, "review": {"prose_in_more_than_4_pairs_once_names_are_masked": {"pieces": 2, "largest_in_pairs": 12}, "work_stamped_from_a_few_shapes": {"campaigns_with_a_session_of_12_steps": 0, "mostly_three_shapes": 0, "largest_share": 0}, "reads_of_an_earlier_version_shown_only_by_the_read": 1, "outputs_before_the_call": {"command": {"not judged: a form the check does not compare": 3, "not judged: no earlier write of what it reads": 22, "not judged: the version it reads is not established": 2, "shape checked": 2, "value compared": 8}, "read": {"not judged: no earlier write of what it reads": 14}, "other": {}}, "reads_after_an_effect_not_shown": 2, "of_them_showing_other_text_than_the_last_write": 0, "reads_in_a_form_not_compared": 0}, "problems": 2, "scorer": "campaign-contract/9", "sha256": "e0206283001e02b74e3b7ea7879961946f7a86ab80f168ddd3c8c7d6857a335a", "checker_sha256": "3cbdb62b3778de5253ec790313bfb1bec173d67fec4d683761a7d128cab2ecd0", "code": "936314e87a40984511d00faba3064ea23ef88286", "existing": [{"file": "training/summarizer/data/starter/campaigns-v1.jsonl", "sha256": "cc9fe0bba69fb59bfcc75420106ccfeef9f063b341cbe589681e53d924d7d99b"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl", "sha256": "a9fdcfc2920544999e889874c79b5901e251955892080c988766171c77582c9a"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl", "sha256": "2109cdd9dc0467f2fea8e62f344944c6828f67f47ae1bc81d10d93850f5c3a95"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-003.jsonl", "sha256": "58809644e80334d2448408c1d953a13102c908a11c4feb3300ee933e6e3c3799"}, {"file": "training/summarizer/data/synthetic/session-roots-train-001.jsonl", "sha256": "d9f25545d36a8ff303544e7da73076149a1e4482c2e10411190f24d3d1ba69cc"}], "options": {"attack_pairs": true, "require": {}}, "replay": {"ran": true, "agents": ["claude_code", "codex_code"], "runtime": "hash", "budget": 20000}, "complete": true}
```

Evidence checker last line (exit 1):

```json
{"roots": 46, "refused": 2, "labelled": 46, "named_facts": 113, "omissions_refused": 216, "omissions": 216, "producer_omissions": 20, "producer_omissions_refused": 20, "file_sha256": "e0206283001e02b74e3b7ea7879961946f7a86ab80f168ddd3c8c7d6857a335a", "checker_sha256": "7d23c942c32d0c793b7994c6866e4f656c80209a83664524cd9fc8b65bfff1d1", "scorer": "campaign-contract/16", "budget": 20000}
```

### Amendment shortfall

- The control checker accepts all 46 roots and all 92 client views. The campaign checker rejects
  both amended row-8 roots with `its own evidence does not pass when written out (claude_code)`.
  The evidence checker rejects both roots' reference packets for both clients. Moving the check
  and write into the earlier session exactly as subsection 5.5 requires did not make those
  reference packets pass under the checker identified above. A resource bridge was not retained:
  the documented bridge validator cannot establish that the local source argument of the required
  `curl -T` upload is a supported `run`, `read`, or `load` use, and rejected attempted declarations.
- No commit was made and no message board was opened or written.
