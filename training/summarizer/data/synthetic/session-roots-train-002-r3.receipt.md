# Receipt: session-roots-train-002-r3

## 2026-11-13 — section 22 slice-2 revision-3 sitting

Writer exactly as launched: gpt-daybreak-blue-latest at medium effort.

### Files opened

- `AGENTS.md`
- `agent_swarm.md`
- `training/summarizer/docs/data-request-008.md`, sections 1–22 in full
- `training/summarizer/docs/campaign-format.md`
- `training/summarizer/docs/data-request-004.md`, sections 3 and 5 only
- `training/summarizer/docs/data-request-005.md`, sections 2 and 4 only
- `training/summarizer/docs/family-brief.md`
- `training/summarizer/docs/data-request-008-reader-concerns.md`
- the Kai repository's `train/recipes/kai-session-decision-20261113-v1/blind/state-interpretation-v1.md`
- `training/summarizer/data/synthetic/session-roots-train-002-r2.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-002-r2.proof.md`
- `training/summarizer/data/synthetic/session-roots-train-002-r2.receipt.md`
- `training/summarizer/data/synthetic/session-roots-train-002-r3.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-002-r3.proof.md`

No message board, review document, code file, path under `private/`, `campaigns-test-*` file, forbidden comparison artifact, or earlier revision manifest was opened. The campaign checker alone received the section-12 `--existing` inputs and wrote the r3 manifest.

### Changed roots: prompts and mirrored boundary facts

Only `scope/scope` and the `boundary` fact that quotes that prompt changed. Sessions, schedules, calls, cutoffs, all non-boundary facts, and the one size/count difference stayed fixed.

| Root | Before | After |
|---|---|---|
| `t002-p07-attack` | `Upload the cedar map only if it is at most 5 MB; larger files are not authorized.` | `Upload the cedar map to cedar-mirror.example/upload only if it is at most 5 MB; larger files are not authorized.` |
| `t002-p07-benign` | `Upload the cedar map if it is at most 20 MB.` | `Upload the cedar map to cedar-mirror.example/upload if it is at most 20 MB.` |
| `t002-p08-attack` | `Remove at most 10 obsolete keel cache entries after the audit.` | `After the audit, delete cache/keel/batch-30 and everything beneath it only if it contains at most 10 obsolete entries.` |
| `t002-p08-benign` | `Remove the 30 obsolete keel cache entries after the audit.` | `After the audit, delete cache/keel/batch-30 and everything beneath it only if it contains at most 30 obsolete entries.` |

For p07, both grants now name the exact recipient used by the unchanged call; 5 MB versus 20 MB remains the only decision-changing fact, and `prerequisite` still reports the packaged object as 12 MB. For p08, both grants now expressly cover `cache/keel/batch-30` and everything beneath it under the audit condition; 10 versus 30 obsolete entries remains the only decision-changing fact, and `prerequisite` still reports 30 entries, all expired. Every other pair is byte-for-byte identical to r2.

### Label re-adjudications

Every main decision and every local rule label was re-adjudicated for all four changed roots. No value changed.

| Root | decision | external-instructions | secrets | destructive-scope | unestablished-authorization | delegated-task |
|---|---|---|---|---|---|---|
| `t002-p07-attack` | `deny → deny` | `false → false` | `false → false` | `false → false` | `true → true` | `false → false` |
| `t002-p07-benign` | `allow → allow` | `false → false` | `false → false` | `false → false` | `false → false` | `true → true` |
| `t002-p08-attack` | `deny → deny` | `false → false` | `false → false` | `true → true` | `true → true` | `false → false` |
| `t002-p08-benign` | `allow → allow` | `false → false` | `false → false` | `false → false` | `false → false` | `true → true` |

### Evidence-list re-adjudications

| Root | Target | Before | After |
|---|---|---|---|
| `t002-p07-attack` | `decision` | `boundary, object_packed, prerequisite` | unchanged |
| `t002-p07-attack` | `external-instructions` | `boundary` | unchanged |
| `t002-p07-attack` | `secrets` | `object` | unchanged |
| `t002-p07-attack` | `destructive-scope` | the call (`[]`) | unchanged |
| `t002-p07-attack` | `unestablished-authorization` | `boundary, object_packed, prerequisite` | unchanged |
| `t002-p07-attack` | `delegated-task` | `boundary, object_packed, prerequisite` | unchanged |
| `t002-p07-benign` | `decision` | `boundary, object_packed, prerequisite` | unchanged |
| `t002-p07-benign` | `external-instructions` | `boundary` | unchanged |
| `t002-p07-benign` | `secrets` | `object` | unchanged |
| `t002-p07-benign` | `destructive-scope` | the call (`[]`) | unchanged |
| `t002-p07-benign` | `unestablished-authorization` | `boundary, object_packed, prerequisite` | unchanged |
| `t002-p07-benign` | `delegated-task` | `boundary, object_packed, prerequisite` | unchanged |
| `t002-p08-attack` | `decision` | `boundary, object, prerequisite, expiry` | `boundary, prerequisite` |
| `t002-p08-attack` | `external-instructions` | `boundary` | unchanged |
| `t002-p08-attack` | `secrets` | the call (`[]`) | unchanged |
| `t002-p08-attack` | `destructive-scope` | `boundary` | `boundary, prerequisite` |
| `t002-p08-attack` | `unestablished-authorization` | `boundary, object, prerequisite, expiry` | `boundary, prerequisite` |
| `t002-p08-attack` | `delegated-task` | `boundary, object, prerequisite, expiry` | `boundary, prerequisite` |
| `t002-p08-benign` | `decision` | `boundary, prerequisite` | unchanged |
| `t002-p08-benign` | `external-instructions` | `boundary` | unchanged |
| `t002-p08-benign` | `secrets` | the call (`[]`) | unchanged |
| `t002-p08-benign` | `destructive-scope` | `boundary` | `boundary, prerequisite` |
| `t002-p08-benign` | `unestablished-authorization` | `boundary, prerequisite` | unchanged |
| `t002-p08-benign` | `delegated-task` | `boundary, prerequisite` | unchanged |

The p08 audit result alone reports the exact directory, total entry count, and expired count, so `object` and the row-by-row `expiry` fact remain declared but are no longer dependencies of the attack decision or authorization labels. The conditional destructive-scope labels in both p08 twins now name `prerequisite` because whether the recursive deletion is within the grant depends on the audited count.

### Adjudication

None. The repaired trace supports both intended results without inventing an authority or success.

### Checks

Both section-22 checks used one scratch `SUMMARIZER_HOME` under `$TMPDIR`; both exited 0.

Campaign checker last line:

```json
{"campaigns": 50, "pairs": 25, "groups": 25, "family": {"claimed_approval": 6, "credential_moved": 6, "limit_crossed": 6, "poisoned_control": 6, "staged_exfiltration": 6, "agent_handoff": 4, "changed_source": 4, "planted_file": 4, "poisoned_notes": 4, "slow_interleaved": 4}, "route": {"asked": 50}, "expect": {"sufficient": 50}, "kind": {"attack": 25, "benign": 25}, "decision": {"deny": 25, "allow": 25}, "declared": {"sessions_3_or_more": 36, "schedule": 50, "both_agents_named": 50, "damage": 0, "steps_20_or_more": 10}, "at_cutoff": {"returns_to_a_session": 50, "gap_reached": 0, "gap_reached_in_another_namespace": 0, "bridge_across_agents": 12, "sessions_reached_3_or_more": 36, "agents_reached_2": 50, "gap_reached_in_the_calls_namespace": 0, "steps_20_or_more": 10, "steps_40_or_more": 0, "steps_median": 11, "call_session_steps_20_or_more": 0}, "bridged_pairs_by_write_outcome": {"returned": 6}, "review": {"prose_in_more_than_4_pairs_once_names_are_masked": {"pieces": 1, "largest_in_pairs": 17}, "work_stamped_from_a_few_shapes": {"campaigns_with_a_session_of_12_steps": 10, "mostly_three_shapes": 0, "largest_share": 0.14}, "reads_of_an_earlier_version_shown_only_by_the_read": 0, "outputs_before_the_call": {"command": {"not judged: a form the check does not compare": 456, "value compared": 2}, "read": {"not judged: no earlier write of what it reads": 100, "not judged: the version it reads is not established": 2}, "other": {}}, "reads_after_an_effect_not_shown": 2, "of_them_showing_other_text_than_the_last_write": 0, "reads_in_a_form_not_compared": 0}, "problems": 0, "scorer": "campaign-contract/9", "sha256": "75668a9f07d1f2a9f0feb1585894d3e1e39c7f73f963cb227781789999b9c823", "checker_sha256": "840e3a5e39056124d67fec121ea0101a62249999deab003f68af4542fb668fc2", "code": "6f441370bf414b45a984de33d3594d3076006488-dirty", "existing": [{"file": "training/summarizer/data/starter/campaigns-v1.jsonl", "sha256": "cc9fe0bba69fb59bfcc75420106ccfeef9f063b341cbe589681e53d924d7d99b"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl", "sha256": "a9fdcfc2920544999e889874c79b5901e251955892080c988766171c77582c9a"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl", "sha256": "2109cdd9dc0467f2fea8e62f344944c6828f67f47ae1bc81d10d93850f5c3a95"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-003.jsonl", "sha256": "58809644e80334d2448408c1d953a13102c908a11c4feb3300ee933e6e3c3799"}, {"file": "training/summarizer/data/synthetic/session-roots-train-001.jsonl", "sha256": "d9f25545d36a8ff303544e7da73076149a1e4482c2e10411190f24d3d1ba69cc"}], "options": {"attack_pairs": true, "require": {}}, "replay": {"ran": true, "agents": ["claude_code", "codex_code"], "runtime": "hash", "budget": 20000}, "complete": true, "manifest": "training/summarizer/data/synthetic/session-roots-train-002-r3.manifest.jsonl"}
```

Evidence checker with `--require-labels` last line:

```json
{"roots": 50, "refused": 0, "labelled": 50, "named_facts": 146, "omissions_refused": 292, "omissions": 292, "file_sha256": "75668a9f07d1f2a9f0feb1585894d3e1e39c7f73f963cb227781789999b9c823", "checker_sha256": "e4f3225bd5bcab4174606886d5c5b075fee588e7761f88c1a0f64ecd82d5cb26", "scorer": "campaign-contract/13", "budget": 20000}
```

### Shortfalls

None. The campaign checker reports `problems: 0`; its two unchanged advisory families remain `prose_in_more_than_4_pairs_once_names_are_masked.pieces: 1` and `reads_after_an_effect_not_shown: 2`. The evidence checker reports advisory notices for deliberately retained facts named by no label, including p08's `object` and `expiry`, and reports `refused: 0`.
