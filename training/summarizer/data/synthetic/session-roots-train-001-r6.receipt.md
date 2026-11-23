# Receipt: session roots train 001 revision 6

## 2026-11-14 — section 23 sitting

- Writer: `Codex gpt-daybreak-blue-latest`
- Reasoning effort: `medium`
- Scope: section 23 of data request 008 only; revision 5 and every earlier revision were left unchanged.

### Files opened

Opened as instructions or authoring references:

- `AGENTS.md`
- `agent_swarm.md`
- `training/summarizer/docs/data-request-008.md`, sections 12 and 23 only
- `training/summarizer/data/synthetic/session-roots-train-001-r5.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-001-r5.proof.md`
- `training/summarizer/data/synthetic/session-roots-train-001-r5.receipt.md`
- `training/summarizer/data/synthetic/session-roots-train-001-r5.manifest.jsonl`

Opened as revision outputs for verification:

- `training/summarizer/data/synthetic/session-roots-train-001-r6.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-001-r6.proof.md`
- `training/summarizer/data/synthetic/session-roots-train-001-r6.manifest.jsonl`

Opened only by the required checker commands, not inspected as authoring material:

- `training/summarizer/check_campaigns.py`
- `training/summarizer/check_evidence.py`
- `training/summarizer/data/starter/campaigns-v1.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-003.jsonl`

No code was inspected. No `private/` path, `campaigns-test-*` file, message board, benchset, seed, blind-read artifact or other data-request section was opened.

### Change

Only `t001-slow2-attack` changed. Before, its declared `facts` contained ten entries:

```text
boundary, revoke_effect, claim, replacement_verification, archive_assignment_checks,
archive_verification, assignment_check, fallback_check, test_issue_write,
replacement_signature
```

After, its declared `facts` contains eight entries:

```text
boundary, revoke_effect, claim, archive_assignment_checks, archive_verification,
assignment_check, fallback_check, test_issue_write
```

The removed declarations are `replacement_verification` and `replacement_signature`. Every step, every other fact, the story and every `story.evidence` list are byte-for-byte unchanged from revision 5. `t001-slow2-benign` is unchanged and retains both declarations. The proof's revision heading and the attack root's `host_facts` inventory were updated to match; no other proof grounding changed. The campaign checker generated the revision-6 manifest.

### Checks

Both required commands ran from the AgentArmor repository root with one fresh scratch `SUMMARIZER_HOME` under `$TMPDIR` and exited 0. The campaign command named `training/summarizer/data/synthetic/session-roots-train-001-r6.manifest.jsonl` with `--manifest`.

`check_campaigns.py` last line:

```json
{"campaigns": 50, "pairs": 25, "groups": 25, "family": {"staged_exfiltration": 6, "poisoned_control": 6, "claimed_approval": 6, "credential_moved": 6, "limit_crossed": 6, "agent_handoff": 4, "changed_source": 4, "planted_file": 4, "poisoned_notes": 4, "slow_interleaved": 4}, "route": {"asked": 50}, "expect": {"sufficient": 50}, "kind": {"attack": 25, "benign": 25}, "decision": {"deny": 25, "allow": 25}, "declared": {"sessions_3_or_more": 30, "schedule": 38, "both_agents_named": 50, "damage": 0, "steps_20_or_more": 26}, "at_cutoff": {"returns_to_a_session": 26, "gap_reached": 0, "gap_reached_in_another_namespace": 0, "bridge_across_agents": 8, "sessions_reached_3_or_more": 30, "agents_reached_2": 50, "gap_reached_in_the_calls_namespace": 0, "steps_20_or_more": 26, "steps_40_or_more": 0, "steps_median": 20, "call_session_steps_20_or_more": 0}, "bridged_pairs_by_write_outcome": {"returned": 4}, "review": {"prose_in_more_than_4_pairs_once_names_are_masked": {"pieces": 3, "largest_in_pairs": 8}, "work_stamped_from_a_few_shapes": {"campaigns_with_a_session_of_12_steps": 6, "mostly_three_shapes": 0, "largest_share": 0.25}, "reads_of_an_earlier_version_shown_only_by_the_read": 0, "outputs_before_the_call": {"command": {"not judged: a form the check does not compare": 302, "value compared": 8}, "read": {"not judged: no earlier write of what it reads": 168, "not judged: the version it reads is not established": 78, "value compared": 56}, "other": {"not judged: a tool whose output the check does not compare": 56}}, "reads_after_an_effect_not_shown": 78, "of_them_showing_other_text_than_the_last_write": 0, "reads_in_a_form_not_compared": 0}, "problems": 0, "scorer": "campaign-contract/9", "sha256": "9013f5cb4db41941ac8008f43a21a92d84ddc5f4d7538b01785b56a1dba72716", "checker_sha256": "3cbdb62b3778de5253ec790313bfb1bec173d67fec4d683761a7d128cab2ecd0", "code": "31fea1cd1cee0c723f8288ce120fe597f01b6e1a", "existing": [{"file": "training/summarizer/data/starter/campaigns-v1.jsonl", "sha256": "cc9fe0bba69fb59bfcc75420106ccfeef9f063b341cbe589681e53d924d7d99b"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl", "sha256": "a9fdcfc2920544999e889874c79b5901e251955892080c988766171c77582c9a"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl", "sha256": "2109cdd9dc0467f2fea8e62f344944c6828f67f47ae1bc81d10d93850f5c3a95"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-003.jsonl", "sha256": "58809644e80334d2448408c1d953a13102c908a11c4feb3300ee933e6e3c3799"}], "options": {"attack_pairs": true, "require": {}}, "replay": {"ran": true, "agents": ["claude_code", "codex_code"], "runtime": "hash", "budget": 20000}, "complete": true, "manifest": "training/summarizer/data/synthetic/session-roots-train-001-r6.manifest.jsonl"}
```

`check_evidence.py --require-labels` last line:

```json
{"roots": 50, "refused": 0, "labelled": 50, "named_facts": 160, "omissions_refused": 320, "omissions": 320, "producer_omissions": 92, "producer_omissions_refused": 92, "file_sha256": "9013f5cb4db41941ac8008f43a21a92d84ddc5f4d7538b01785b56a1dba72716", "checker_sha256": "7d23c942c32d0c793b7994c6866e4f656c80209a83664524cd9fc8b65bfff1d1", "scorer": "campaign-contract/16", "budget": 20000}
```

### Shortfalls

None.
