# Receipt: session-roots-train-002

Date: 2026-11-13 America/Los_Angeles

Writer exactly as launched: “You are the Data Engineer of the Summarizer project, working in this repository.”

## Files opened

- `AGENTS.md`
- `agent_swarm.md`
- `docs/data-request-008.md`, sections 1–10 in full
- opening text of `train/campaign_contract.py`
- `docs/data-request-004.md`, sections 3 and 5 only
- `docs/data-request-005.md`, sections 2 and 4 only
- `docs/family-brief.md`
- `train/data/synthetic/session-roots-train-002.jsonl`, reopened only to audit and repair this sitting's new roots

No `session-roots-train-001.*` file was opened. Existing artifacts are supplied only to the checkers.

## Batch

Twenty-five pairs, fifty roots. Pair-side session count: 68 (18 three-session pairs and 7 two-session pairs). Family counts: claimed_approval 3, credential_moved 3, limit_crossed 3, poisoned_control 3, staged_exfiltration 3, and 2 each in agent_handoff, changed_source, planted_file, poisoned_notes, slow_interleaved.

Every pair changes exactly one earlier session text field, the user boundary prompt; a mechanical audit found that sole difference in all 25 pairs. Each root declares the boundary, exact object binding, reported prerequisite, and lower-trust provenance where present. Pairs 16 and 17 write coherent helper scripts before the other agent runs them; no step uses a task-written file before its producing step. The remaining pairs do not create a file that they later consume.

Facts on every root are `boundary`, `object`, and `prerequisite`; the 32 roots whose trace contains lower-trust text also declare `lower_claim`. Thus the declared facts cover the operative permission or limit, the call-to-object binding, every conditional prerequisite, and lower-trust provenance where applicable.

## Pair table

| Pair | Family | Technique | One differing earlier fact |
|---|---|---|---|
| t002-p01 | claimed_approval | approval claim in a vendor checklist | earlier user boundary/permission |
| t002-p02 | claimed_approval | ticket claims authority for out-of-scope deletion | earlier user boundary/permission |
| t002-p03 | claimed_approval | review note claims permission to replace an access control | earlier user boundary/permission |
| t002-p04 | credential_moved | credential bundle redirected by a build note | earlier user boundary/permission |
| t002-p05 | credential_moved | runbook redirects a private token inventory | earlier user boundary/permission |
| t002-p06 | credential_moved | agent crosses a credential destination boundary | earlier user boundary/permission |
| t002-p07 | limit_crossed | agent exceeds a literal transfer-size ceiling | earlier user boundary/permission |
| t002-p08 | limit_crossed | agent exceeds an authorized deletion count | earlier user boundary/permission |
| t002-p09 | limit_crossed | agent exceeds a private-record recipient limit | earlier user boundary/permission |
| t002-p10 | poisoned_control | loaded release control contains an external directive | earlier user boundary/permission |
| t002-p11 | poisoned_control | loaded cleanup control targets a shared directory | earlier user boundary/permission |
| t002-p12 | poisoned_control | loaded diagnostic control exports a credential | earlier user boundary/permission |
| t002-p13 | staged_exfiltration | staged private roster sent on an imported directive | earlier user boundary/permission |
| t002-p14 | staged_exfiltration | agent sends a staged private archive beyond the named recipient | earlier user boundary/permission |
| t002-p15 | staged_exfiltration | staging note redirects a confidential design packet | earlier user boundary/permission |
| t002-p16 | agent_handoff | handoff repeats a note-directed destructive action | earlier user boundary/permission |
| t002-p17 | agent_handoff | second agent crosses a private-data recipient boundary | earlier user boundary/permission |
| t002-p18 | changed_source | changed package source carries an approval directive | earlier user boundary/permission |
| t002-p19 | changed_source | agent uses changed source outside an authorized destructive scope | earlier user boundary/permission |
| t002-p20 | planted_file | planted helper file is executed as policy | earlier user boundary/permission |
| t002-p21 | planted_file | agent executes a planted cleanup beyond the workspace | earlier user boundary/permission |
| t002-p22 | poisoned_notes | notes claim approval to send private minutes | earlier user boundary/permission |
| t002-p23 | poisoned_notes | maintenance notes direct deletion of retained evidence | earlier user boundary/permission |
| t002-p24 | slow_interleaved | interleaved note redirects a private case bundle | earlier user boundary/permission |
| t002-p25 | slow_interleaved | agent crosses an interleaved destructive boundary | earlier user boundary/permission |

## Checks

All three checks used one fresh scratch `SUMMARIZER_HOME` created under `$TMPDIR`; all exited 0.

Local checker last line:

```json
{"campaigns": 50, "pairs": 25, "groups": 25, "family": {"claimed_approval": 6, "credential_moved": 6, "limit_crossed": 6, "poisoned_control": 6, "staged_exfiltration": 6, "agent_handoff": 4, "changed_source": 4, "planted_file": 4, "poisoned_notes": 4, "slow_interleaved": 4}, "route": {"asked": 50}, "expect": {"sufficient": 50}, "kind": {"attack": 25, "benign": 25}, "decision": {"deny": 25, "allow": 25}, "declared": {"sessions_3_or_more": 36, "schedule": 50, "both_agents_named": 50, "damage": 0, "steps_20_or_more": 10}, "at_cutoff": {"returns_to_a_session": 50, "gap_reached": 0, "gap_reached_in_another_namespace": 0, "bridge_across_agents": 4, "sessions_reached_3_or_more": 36, "agents_reached_2": 50, "gap_reached_in_the_calls_namespace": 0, "steps_20_or_more": 10, "steps_40_or_more": 0, "steps_median": 11, "call_session_steps_20_or_more": 0}, "bridged_pairs_by_write_outcome": {"returned": 2}, "review": {"prose_in_more_than_4_pairs_once_names_are_masked": {"pieces": 0, "largest_in_pairs": 0}, "work_stamped_from_a_few_shapes": {"campaigns_with_a_session_of_12_steps": 10, "mostly_three_shapes": 0, "largest_share": 0.32}}, "problems": 0, "scorer": "campaign-contract/9", "sha256": "0501b718294c273a71ab4875087ccb8c1ea849ca2af3c3a46b1eb65b2fc097b6", "checker_sha256": "5e7b6bf8a90264ea9111169e9ea7c13f0a7c7c2f121167820b07f9848fcf481d", "code": "a2111b71476b0ac6c0fd2757d2cf8f4549d6c0fd", "existing": [{"file": "train/data/starter/campaigns-v1.jsonl", "sha256": "cc9fe0bba69fb59bfcc75420106ccfeef9f063b341cbe589681e53d924d7d99b"}, {"file": "train/data/synthetic/campaigns-dev-001-r1.jsonl", "sha256": "a9fdcfc2920544999e889874c79b5901e251955892080c988766171c77582c9a"}, {"file": "train/data/synthetic/campaigns-dev-002-full.jsonl", "sha256": "2109cdd9dc0467f2fea8e62f344944c6828f67f47ae1bc81d10d93850f5c3a95"}, {"file": "train/data/synthetic/campaigns-dev-003.jsonl", "sha256": "58809644e80334d2448408c1d953a13102c908a11c4feb3300ee933e6e3c3799"}, {"file": "train/data/synthetic/session-roots-train-001.jsonl", "sha256": "d9f25545d36a8ff303544e7da73076149a1e4482c2e10411190f24d3d1ba69cc"}], "options": {"attack_pairs": true, "require": {}}, "replay": {"ran": true, "agents": ["claude_code", "codex_code"], "runtime": "hash", "budget": 20000}, "complete": true, "manifest": "train/data/synthetic/session-roots-train-002.manifest.jsonl"}
```

AgentArmor campaign checker last line:

```json
{"campaigns": 50, "pairs": 25, "groups": 25, "family": {"claimed_approval": 6, "credential_moved": 6, "limit_crossed": 6, "poisoned_control": 6, "staged_exfiltration": 6, "agent_handoff": 4, "changed_source": 4, "planted_file": 4, "poisoned_notes": 4, "slow_interleaved": 4}, "route": {"asked": 50}, "expect": {"sufficient": 50}, "kind": {"attack": 25, "benign": 25}, "decision": {"deny": 25, "allow": 25}, "declared": {"sessions_3_or_more": 36, "schedule": 50, "both_agents_named": 50, "damage": 0, "steps_20_or_more": 10}, "at_cutoff": {"returns_to_a_session": 50, "gap_reached": 0, "gap_reached_in_another_namespace": 0, "bridge_across_agents": 4, "sessions_reached_3_or_more": 36, "agents_reached_2": 50, "gap_reached_in_the_calls_namespace": 0, "steps_20_or_more": 10, "steps_40_or_more": 0, "steps_median": 11, "call_session_steps_20_or_more": 0}, "bridged_pairs_by_write_outcome": {"returned": 2}, "review": {"prose_in_more_than_4_pairs_once_names_are_masked": {"pieces": 0, "largest_in_pairs": 0}, "work_stamped_from_a_few_shapes": {"campaigns_with_a_session_of_12_steps": 10, "mostly_three_shapes": 0, "largest_share": 0.32}, "reads_of_an_earlier_version_shown_only_by_the_read": 0}, "problems": 0, "scorer": "campaign-contract/9", "sha256": "0501b718294c273a71ab4875087ccb8c1ea849ca2af3c3a46b1eb65b2fc097b6", "checker_sha256": "f538b02166a1771294ebea650c4232c874eeb32c04380e81cc8cf4ac0969bed3", "code": "7806e88c31f5c2da97bffd1a58bb41d9a85f1d9e-dirty", "existing": [{"file": "training/summarizer/data/starter/campaigns-v1.jsonl", "sha256": "cc9fe0bba69fb59bfcc75420106ccfeef9f063b341cbe589681e53d924d7d99b"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl", "sha256": "a9fdcfc2920544999e889874c79b5901e251955892080c988766171c77582c9a"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl", "sha256": "2109cdd9dc0467f2fea8e62f344944c6828f67f47ae1bc81d10d93850f5c3a95"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-003.jsonl", "sha256": "58809644e80334d2448408c1d953a13102c908a11c4feb3300ee933e6e3c3799"}, {"file": "training/summarizer/data/synthetic/session-roots-train-001.jsonl", "sha256": "d9f25545d36a8ff303544e7da73076149a1e4482c2e10411190f24d3d1ba69cc"}], "options": {"attack_pairs": true, "require": {}}, "replay": {"ran": true, "agents": ["claude_code", "codex_code"], "runtime": "hash", "budget": 20000}, "complete": true}
```

AgentArmor evidence checker last line:

```json
{"roots": 50, "refused": 0, "labelled": 50, "named_facts": 182, "omissions_refused": 364, "omissions": 364, "file_sha256": "0501b718294c273a71ab4875087ccb8c1ea849ca2af3c3a46b1eb65b2fc097b6", "checker_sha256": "2eb5f447ecc90d198200324a371df75837b5c16dea1f38f4327c695c2d718ea3", "scorer": "campaign-contract/12", "budget": 20000}
```

## Rule counts and correlation

True counts across 50 roots: `external-instructions` 16, `secrets` 12, `destructive-scope` 8, `unestablished-authorization` 25, and `delegated-task` 25.

Exact true-label combinations: 8 roots have external-instructions + secrets + unestablished-authorization; 4 have external-instructions + destructive-scope + unestablished-authorization; 4 have external-instructions + unestablished-authorization; 4 have secrets + unestablished-authorization; 4 have destructive-scope + unestablished-authorization; 1 has only unestablished-authorization; and all 25 benign roots have only delegated-task.

## Shortfalls

None. The checker also reports zero prose pieces repeated across more than four pairs after name masking, zero chronology faults, and all 364 named-fact omission trials refused.

