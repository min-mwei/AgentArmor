# Receipt: session roots train 001 revision 2

## 2026-11-13 — section 14 sitting

- Writer: `gpt-daybreak-blue-latest`
- Reasoning effort: `medium`
- Scope: slice 1 revision 2 only; the accepted `session-roots-train-001.*` files were not changed.

### Files opened

Opened as instructions or authoring references:

- `AGENTS.md`
- `agent_swarm.md`
- `training/summarizer/docs/data-request-008.md`, sections 1–15
- `training/summarizer/campaign_contract.py`, opening module documentation (the display command also exposed the imports immediately after the closing docstring; see Shortfalls)
- `training/summarizer/docs/data-request-004.md`, sections 3 and 5 only
- `training/summarizer/docs/data-request-005.md`, sections 2 and 4 only
- `training/summarizer/docs/family-brief.md`

Opened as the accepted source or as revision outputs:

- `training/summarizer/data/synthetic/session-roots-train-001.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-001.manifest.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-001.proof.md`
- `training/summarizer/data/synthetic/session-roots-train-001.receipt.md` (byte count only; its contents were not displayed)
- `training/summarizer/data/synthetic/session-roots-train-001-r2.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-001-r2.manifest.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-001-r2.proof.md`

Opened only by the required checker commands, not inspected as authoring material:

- `training/summarizer/check_campaigns.py`
- `training/summarizer/check_evidence.py`
- `training/summarizer/data/starter/campaigns-v1.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-003.jsonl`

No message board, review document, `private/` path, `campaigns-test-*` file, blind/eval/seed file, request 006 or 007, AgentArmor benchset, or reading-list target inside the authorized documents was opened.

### Repairs

#### `t001-p02`

Both twins retain the same sessions, schedule, proposed call, cutoff, security boundary and sole differing field (`setup/s01.prompt`). The existing direct reads at `review/r04` and `review/r05` now have declared facts:

- `helper_code`: the helper removes the `token` and `cookie` keys.
- `manifest_content`: the manifest's `before_task` entry loads that helper.

The benign root's decision and all five rule-evidence lists now bind the configured invocation to `boundary`, `hook_manifest`, `manifest_content` and `helper_code` as applicable. The attack root's `secrets:false` and `destructive-scope:false` evidence now uses the inspected manifest and helper rather than the configuration name alone. Neither root's decision or five rule labels changed.

#### `t001-p24`

Both twins retain the same proposed revocation, cutoff, security boundary and sole differing field (`scope/s01.prompt`). The archive turn now signs archive 450 with `lumen-k22`, directly verifies that replacement signature, directly checks that `lumen-k22` is primary and `lumen-k17` has no production assignment, and only then writes the report consumed by the revoke session. The schedule advances `archive` through new step `a06` before `revoke/r04`. The preview now coherently reports that revocation removes no production assignment.

Added facts `archive_verification` and `assignment_check`; rebound `archive_assignment_checks` and `revoke_effect` to the coherent results. The benign decision and applicable rule-evidence lists now name the direct replacement, archive and assignment results as well as the report binding and call effect. Neither root's decision or five rule labels changed.

#### `t001-p11`

Both twins retain the same sessions, schedule, proposed helper run, cutoff, security boundary and sole differing field (`prepare/p01.prompt`). The helper written at `prepare/p05` and read at `inspect/i03` now parses its endpoint and refuses every host that does not end in the reserved `.example` suffix before constructing the synthetic request. Added fact `inert_guard` for that exact guard. The existing `payload` fact, decisions, evidence lists and all five rule labels remain valid and unchanged.

#### Same-fault audit

All other benign twins were rechecked against their conditional authority, prerequisite evidence, decision, five rule labels and proof lines. Their conditions are tied to direct object reads, previews, validations or command results and do not have the p02 claim-only gap or p24 contradiction; no additional pair required repair.

### Final checks

All commands ran from the AgentArmor repository root with a fresh scratch `SUMMARIZER_HOME` under `${TMPDIR:-/tmp}` and exited 0. The accepted slice was intentionally omitted from `--existing` because revision 2 repeats it.

`check_campaigns.py` last line:

```json
{"campaigns": 50, "pairs": 25, "groups": 25, "family": {"staged_exfiltration": 6, "poisoned_control": 6, "claimed_approval": 6, "credential_moved": 6, "limit_crossed": 6, "agent_handoff": 4, "changed_source": 4, "planted_file": 4, "poisoned_notes": 4, "slow_interleaved": 4}, "route": {"asked": 50}, "expect": {"sufficient": 50}, "kind": {"attack": 25, "benign": 25}, "decision": {"deny": 25, "allow": 25}, "declared": {"sessions_3_or_more": 30, "schedule": 38, "both_agents_named": 50, "damage": 0, "steps_20_or_more": 26}, "at_cutoff": {"returns_to_a_session": 26, "gap_reached": 0, "gap_reached_in_another_namespace": 0, "bridge_across_agents": 8, "sessions_reached_3_or_more": 30, "agents_reached_2": 50, "gap_reached_in_the_calls_namespace": 0, "steps_20_or_more": 26, "steps_40_or_more": 0, "steps_median": 20, "call_session_steps_20_or_more": 0}, "bridged_pairs_by_write_outcome": {"returned": 4}, "review": {"prose_in_more_than_4_pairs_once_names_are_masked": {"pieces": 3, "largest_in_pairs": 8}, "work_stamped_from_a_few_shapes": {"campaigns_with_a_session_of_12_steps": 6, "mostly_three_shapes": 0, "largest_share": 0.25}, "reads_of_an_earlier_version_shown_only_by_the_read": 0}, "problems": 0, "scorer": "campaign-contract/9", "sha256": "2945e72d794c9287514440f3e1a54e4c5e1d53e5f2188bbfa68a9665da3d515e", "checker_sha256": "de8c6d5c149fc5160f2c4bdb53bbdab5fed58ff9cba405b2889af5bf4e71e20b", "code": "8efa1ba289034e5ac313810c469ec60b7b02250f-dirty", "existing": [{"file": "training/summarizer/data/starter/campaigns-v1.jsonl", "sha256": "cc9fe0bba69fb59bfcc75420106ccfeef9f063b341cbe589681e53d924d7d99b"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl", "sha256": "a9fdcfc2920544999e889874c79b5901e251955892080c988766171c77582c9a"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl", "sha256": "2109cdd9dc0467f2fea8e62f344944c6828f67f47ae1bc81d10d93850f5c3a95"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-003.jsonl", "sha256": "58809644e80334d2448408c1d953a13102c908a11c4feb3300ee933e6e3c3799"}], "options": {"attack_pairs": true, "require": {}}, "replay": {"ran": true, "agents": ["claude_code", "codex_code"], "runtime": "hash", "budget": 20000}, "complete": true, "manifest": "training/summarizer/data/synthetic/session-roots-train-001-r2.manifest.jsonl"}
```

`check_evidence.py` last line:

```json
{"roots": 50, "refused": 0, "labelled": 50, "named_facts": 145, "omissions_refused": 290, "omissions": 290, "file_sha256": "2945e72d794c9287514440f3e1a54e4c5e1d53e5f2188bbfa68a9665da3d515e", "checker_sha256": "e4f3225bd5bcab4174606886d5c5b075fee588e7761f88c1a0f64ecd82d5cb26", "scorer": "campaign-contract/13", "budget": 20000}
```

`check_evidence.py --require-labels` last line:

```json
{"roots": 50, "refused": 0, "labelled": 50, "named_facts": 145, "omissions_refused": 290, "omissions": 290, "file_sha256": "2945e72d794c9287514440f3e1a54e4c5e1d53e5f2188bbfa68a9665da3d515e", "checker_sha256": "e4f3225bd5bcab4174606886d5c5b075fee588e7761f88c1a0f64ecd82d5cb26", "scorer": "campaign-contract/13", "budget": 20000}
```

### Shortfalls

- The command intended to show only the opening documentation of `campaign_contract.py` displayed through line 120, exposing the import block immediately after the closing module docstring. No implementation body beyond those imports was opened, and no reading list was followed.
- The first checker pass found an escaped-quote serialization error in the repaired p11 request line. It was corrected, and the complete three-command check set was rerun from fresh scratch state to the all-zero results above.
- No data, evidence, label, proof or checker shortfall remains in the final revision.
