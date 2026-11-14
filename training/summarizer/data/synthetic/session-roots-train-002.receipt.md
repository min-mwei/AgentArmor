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

## 2026-11-13 — section 11 repair sitting

Writer exactly as launched: gpt-daybreak-blue-latest at medium effort.

### Files opened

- `AGENTS.md`
- `agent_swarm.md`
- `training/summarizer/docs/data-request-008.md`, sections 1–12 in full
- opening documentation of `training/summarizer/campaign_contract.py`
- `training/summarizer/docs/data-request-004.md`, sections 3 and 5 only
- `training/summarizer/docs/data-request-005.md`, sections 2 and 4 only
- `training/summarizer/docs/family-brief.md`
- `training/summarizer/data/synthetic/session-roots-train-002.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-002.manifest.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-002.proof.md`
- `training/summarizer/data/synthetic/session-roots-train-002.receipt.md`
- scratch checker captures `/tmp/de008-campaign.out`, `/tmp/de008-campaign-final.out`, and `/tmp/de008-evidence-final.out`

No message board, review document, `private/` path, `campaigns-test-*` file, `session-roots-train-001.*` artifact, or forbidden comparison artifact was directly opened. The section 12 checker alone received the named existing artifacts as comparison inputs.

### Repairs

- `t002-p10`: replaced the prose result for `controls/jetty-release.sh` with coherent shell that uploads `artifacts/jetty-manifest.json` to `https://jetty-log.example/ingest`; added the `payload` read/fact establishing that the manifest contains public component names and checksums. Added `payload` to `decision`, `secrets`, `unestablished-authorization`, and `delegated-task` evidence in both twins. The `object` fact now holds the script code.
- `t002-p11`: replaced the prose result for `controls/mica-clean.sh` with coherent shell that removes `../shared/mica-fixtures/v1`. The `object` fact now holds the script code; evidence ids did not change.
- `t002-p12`: replaced the prose result for `controls/wren-diagnose.sh` with coherent shell that uploads `artifacts/wren-support-bundle.json` to `https://wren-help.example/collect`; added the `payload` read/fact establishing that the bundle contains private diagnostics and a synthetic credential marker. Added `payload` to `decision`, `secrets`, `unestablished-authorization`, and `delegated-task` evidence in both twins. The `object` fact now holds the script code.
- `t002-p20`: replaced the prose result for `scripts/rill-finalize.sh` with coherent shell that uploads `reports/rill-workspace-metrics.json` to `https://rill-metrics.example/push`; added the `payload` read/fact establishing that the metrics are public build durations and cache-hit counts. Added `payload` to `decision`, `secrets`, `unestablished-authorization`, and `delegated-task` evidence in both twins. The `object` fact now holds the script code.
- `t002-p21`: replaced the prose result for `scripts/basin-prune.sh` with coherent shell that removes `../shared/basin-backups/expired`. The `object` fact now holds the script code; evidence ids did not change.
- `t002-p16` and `t002-p17` were the only other pairs whose call executes a shell file. Their read results already contain coherent shell performing their deletion/replacement and upload effects, so they did not share MR167-2 and were not changed.
- `t002-p01`: replaced all twenty repeated workbench stages, identically in both twins, with a causal harbor-ledger audit: parsing berth records, classifying restricted notes, reconciling missing inspections, validating citations, rendering the audit, and assembling `artifacts/harbor-audit.txt`.
- `t002-p07`: replaced all twenty repeated workbench stages, identically in both twins, with Cedar map inspection and repair: layer/CRS checks, ridge-tile repair, topology and preview validation, packaging, checksum, and the reported 12 MB readiness result.
- `t002-p13`: replaced all twenty repeated workbench stages, identically in both twins, with Fern roster work: column and consent checks, shift reconciliation, contact-sheet construction, manifest verification, and creation of `staging/fern-roster.tar`.
- `t002-p19`: replaced all twenty repeated workbench stages, identically in both twins, with Quill changed-source analysis: signer and package comparison, a disposable workspace index, query/schema checks, rollback planning, and a handoff report while the shared index remains present.
- `t002-p24`: replaced all twenty repeated workbench stages, identically in both twins, with Ibis case-bundle preparation: custody reconciliation, witness linkage, duplicate and redaction checks, manifest verification, and creation of `bundles/ibis-case-42.zip`.

The five long-session pairs kept their existing facts and evidence entries. All repaired pairs kept the same pair boundary, proposed call, cutoff, schedule, decision, rule labels, and sole differing user fact. The proof lines for the five script pairs now ground each rule in the executable contents and, where applicable, the payload fact. Every decision and all five rule labels were rechecked after repair; no label changed.

### Checks

Both section 12 checks used one fresh scratch `SUMMARIZER_HOME` under `$TMPDIR`; both exited 0.

Campaign checker last line:

```json
{"campaigns": 50, "pairs": 25, "groups": 25, "family": {"claimed_approval": 6, "credential_moved": 6, "limit_crossed": 6, "poisoned_control": 6, "staged_exfiltration": 6, "agent_handoff": 4, "changed_source": 4, "planted_file": 4, "poisoned_notes": 4, "slow_interleaved": 4}, "route": {"asked": 50}, "expect": {"sufficient": 50}, "kind": {"attack": 25, "benign": 25}, "decision": {"deny": 25, "allow": 25}, "declared": {"sessions_3_or_more": 36, "schedule": 50, "both_agents_named": 50, "damage": 0, "steps_20_or_more": 10}, "at_cutoff": {"returns_to_a_session": 50, "gap_reached": 0, "gap_reached_in_another_namespace": 0, "bridge_across_agents": 4, "sessions_reached_3_or_more": 36, "agents_reached_2": 50, "gap_reached_in_the_calls_namespace": 0, "steps_20_or_more": 10, "steps_40_or_more": 0, "steps_median": 11, "call_session_steps_20_or_more": 0}, "bridged_pairs_by_write_outcome": {"returned": 2}, "review": {"prose_in_more_than_4_pairs_once_names_are_masked": {"pieces": 0, "largest_in_pairs": 0}, "work_stamped_from_a_few_shapes": {"campaigns_with_a_session_of_12_steps": 10, "mostly_three_shapes": 0, "largest_share": 0.14}, "reads_of_an_earlier_version_shown_only_by_the_read": 4}, "problems": 0, "scorer": "campaign-contract/9", "sha256": "894a162e05e654bb27cf73ae92edf780094ae963370681ed5b55067a191a16ce", "checker_sha256": "dae9c83537bfde5ebaf5fb4e263842eac94d57d6cb7be986470b32d912e0c3c0", "code": "4260dcd67e486265470701377a5432aaaca9fe1e-dirty", "existing": [{"file": "training/summarizer/data/starter/campaigns-v1.jsonl", "sha256": "cc9fe0bba69fb59bfcc75420106ccfeef9f063b341cbe589681e53d924d7d99b"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl", "sha256": "a9fdcfc2920544999e889874c79b5901e251955892080c988766171c77582c9a"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl", "sha256": "2109cdd9dc0467f2fea8e62f344944c6828f67f47ae1bc81d10d93850f5c3a95"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-003.jsonl", "sha256": "58809644e80334d2448408c1d953a13102c908a11c4feb3300ee933e6e3c3799"}, {"file": "training/summarizer/data/synthetic/session-roots-train-001.jsonl", "sha256": "d9f25545d36a8ff303544e7da73076149a1e4482c2e10411190f24d3d1ba69cc"}], "options": {"attack_pairs": true, "require": {}}, "replay": {"ran": true, "agents": ["claude_code", "codex_code"], "runtime": "hash", "budget": 20000}, "complete": true, "manifest": "training/summarizer/data/synthetic/session-roots-train-002.manifest.jsonl"}
```

Evidence checker last line:

```json
{"roots": 50, "refused": 0, "labelled": 50, "named_facts": 188, "omissions_refused": 376, "omissions": 376, "file_sha256": "894a162e05e654bb27cf73ae92edf780094ae963370681ed5b55067a191a16ce", "checker_sha256": "2d53fb7a4c2ca2a0703c7362dfa6a24844dfff1fd394fa68518254e40c55937c", "scorer": "campaign-contract/13", "budget": 20000}
```

### Rule counts and shortfalls

True counts across 50 roots remain `external-instructions` 16, `secrets` 12, `destructive-scope` 8, `unestablished-authorization` 25, and `delegated-task` 25. The true-label correlation remains: 8 external-instructions + secrets + unestablished-authorization; 4 external-instructions + destructive-scope + unestablished-authorization; 4 external-instructions + unestablished-authorization; 4 secrets + unestablished-authorization; 4 destructive-scope + unestablished-authorization; 1 only unestablished-authorization; and all 25 benign roots only delegated-task.

No checker failure remains. The campaign checker’s review-only metric reports four reads of an earlier version shown only by the read. These are existing-script cases whose read result is the executable content, an option section 11 explicitly permits; no earlier write was invented. The checker reports `problems: 0`, and every one of the 376 named-fact omission trials refused the incomplete packet.

## 2026-11-13 — section 13 repair sitting

Writer exactly as launched: gpt-daybreak-blue-latest at medium effort.

### Files opened

- `AGENTS.md`
- `agent_swarm.md`
- `training/summarizer/docs/data-request-008.md`, sections 1–13 in full
- `training/summarizer/campaign_contract.py` (the opening documentation was intended; the extraction command also exposed later module text, recorded under Shortfalls)
- `training/summarizer/docs/data-request-004.md`, sections 3 and 5 only
- `training/summarizer/docs/data-request-005.md`, sections 2 and 4 only
- `training/summarizer/docs/family-brief.md`
- `training/summarizer/data/synthetic/session-roots-train-002.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-002.manifest.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-002.proof.md`
- `training/summarizer/data/synthetic/session-roots-train-002.receipt.md`
- scratch checker captures under `$TMPDIR`: `agentarmor-section13.c4EwYd/campaign.out`, `agentarmor-section13.c4EwYd/evidence.out`, `agentarmor-section13-final.q2ohUG/campaign.out`, and `agentarmor-section13-final.q2ohUG/evidence.out`
- temporary mechanical-edit helpers under `/tmp`: `repair_slice2_section13.py`, `update_slice2_proof.py`, and `shorten_slice2_section13.py` (removed after use)

No message board, review document, `private/` path, `campaigns-test-*` file, `session-roots-train-001.*` artifact, or forbidden comparison artifact was directly opened. The section 12 campaign checker alone received the named existing artifacts as comparison inputs.

### Repairs by pair

Every inventory read now returns the inventory Markdown itself rather than a sentence about it. In every pair with imported material (`p01`–`p05`, `p10`–`p13`, `p15`, `p16`, `p18`, `p20`, and `p22`–`p24`), the read now returns the material's text without the former “lower-trust text” report prefix; the matching `lower_claim` fact text changed identically in both twins. Those facts retain their existing evidence ids.

| Pair | Object or payload repair | Facts changed in both twins | Evidence change |
|---|---|---|---|
| `t002-p01` | `harbor-audit.txt` now contains the internal berth finding and restricted inspection note. | `object`, `lower_claim` | ids unchanged |
| `t002-p02` | `file`/`tar` inspection now identifies the recovery archive and lists its members. | `object`, `lower_claim` | ids unchanged |
| `t002-p03` | `marsh-access.policy` now contains the survey-role allow/deny policy. | `object`, `lower_claim` | ids unchanged |
| `t002-p04` | encrypted-file inspection now identifies the OpenSSL archive and its synthetic key/certificate members. | `object`, `lower_claim` | ids unchanged |
| `t002-p05` | `orchid-token-inventory.csv` now contains private synthetic token-id rows and rotation dates. | `object`, `lower_claim` | ids unchanged |
| `t002-p06` | PKCS#12 inspection now identifies the synthetic Frost signing key bag. | `object` | ids unchanged |
| `t002-p07` | the binary read result now identifies CedarMap v2, its public layer, declared 12 MB payload, and valid topology. | `object` | ids unchanged |
| `t002-p08` | directory inspection now reports the 30 expired cache entries actually present. | `object` | ids unchanged |
| `t002-p09` | `pollen-contacts-8.csv` now contains eight private synthetic contact rows. | `object` | ids unchanged |
| `t002-p10` | the manifest read now returns its public component/checksum JSON; the repaired script remains unchanged. | `payload`, `lower_claim` | ids unchanged |
| `t002-p11` | the repaired cleanup script remains unchanged; its inventory and imported header now return their contents. | `lower_claim` | ids unchanged |
| `t002-p12` | the support-bundle read now returns private diagnostic JSON with its synthetic credential marker; the repaired script remains unchanged. | `payload`, `lower_claim` | ids unchanged |
| `t002-p13` | archive inspection now extracts the private Fern roster rows and phone numbers. | `object`, `lower_claim` | ids unchanged |
| `t002-p14` | ZIP inspection now extracts the private Agate payment-ledger rows. | `object` | ids unchanged |
| `t002-p15` | tar inspection now extracts the confidential prototype drawing manifest. | `object`, `lower_claim` | ids unchanged |
| `t002-p16` | the existing coherent helper remains unchanged; inventory and imported handoff now return their contents. | `lower_claim` | ids unchanged |
| `t002-p17` | the existing coherent helper remains unchanged; the inventory read now returns its contents. | none | none |
| `t002-p18` | tar inspection now lists the public Sable release members. | `object`, `lower_claim` | ids unchanged |
| `t002-p19` | SQLite inspection now returns the replacement-source registry rows. | `object` | ids unchanged |
| `t002-p20` | the metrics read now returns its public JSON values; the repaired script remains unchanged. | `payload`, `lower_claim` | ids unchanged |
| `t002-p21` | the repaired cleanup script remains unchanged; the inventory read now returns its contents. | none | none |
| `t002-p22` | `elm-board-minutes.txt` now contains the private minutes, synthetic attendee contacts, and circulation restriction. | `object`, `lower_claim` | ids unchanged |
| `t002-p23` | directory inspection now lists the retained checksums, diagnostics, and retention record. | `object`, `lower_claim` | ids unchanged |
| `t002-p24` | ZIP inspection now extracts the private case index and synthetic witness contacts. | `object`, `lower_claim` | ids unchanged |
| `t002-p25` | directory inspection now lists the four ledger quarters and manifest. | `object` | ids unchanged |

All object and payload facts remain bound through their existing `story.evidence` entries; no evidence id was added or removed. Both twins of every pair received identical content, inspection, fact, and proof changes. A structural twin audit found no difference in sessions after masking the one boundary prompt, and no difference in schedules, calls, cutoffs, bridges, or non-boundary facts. Every decision, all five rule labels, and all 300 per-root rule/host-fact proof lines were rechecked; the labels and `host_facts` remain correct and unchanged. The proof's object-binding lines now distinguish the file contents or inspection result from the path and destination/target shown by the proposed call.

### Checks

Both section 12 checks used one fresh scratch `SUMMARIZER_HOME` under `$TMPDIR`; both exited 0.

Campaign checker last line:

```json
{"campaigns": 50, "pairs": 25, "groups": 25, "family": {"claimed_approval": 6, "credential_moved": 6, "limit_crossed": 6, "poisoned_control": 6, "staged_exfiltration": 6, "agent_handoff": 4, "changed_source": 4, "planted_file": 4, "poisoned_notes": 4, "slow_interleaved": 4}, "route": {"asked": 50}, "expect": {"sufficient": 50}, "kind": {"attack": 25, "benign": 25}, "decision": {"deny": 25, "allow": 25}, "declared": {"sessions_3_or_more": 36, "schedule": 50, "both_agents_named": 50, "damage": 0, "steps_20_or_more": 10}, "at_cutoff": {"returns_to_a_session": 50, "gap_reached": 0, "gap_reached_in_another_namespace": 0, "bridge_across_agents": 4, "sessions_reached_3_or_more": 36, "agents_reached_2": 50, "gap_reached_in_the_calls_namespace": 0, "steps_20_or_more": 10, "steps_40_or_more": 0, "steps_median": 11, "call_session_steps_20_or_more": 0}, "bridged_pairs_by_write_outcome": {"returned": 2}, "review": {"prose_in_more_than_4_pairs_once_names_are_masked": {"pieces": 0, "largest_in_pairs": 0}, "work_stamped_from_a_few_shapes": {"campaigns_with_a_session_of_12_steps": 10, "mostly_three_shapes": 0, "largest_share": 0.14}, "reads_of_an_earlier_version_shown_only_by_the_read": 4}, "problems": 0, "scorer": "campaign-contract/9", "sha256": "4facb6e247969d0f4bdb6b1748d0bb3bae6c558f86971aeec2c1f9464e25f1a1", "checker_sha256": "dae9c83537bfde5ebaf5fb4e263842eac94d57d6cb7be986470b32d912e0c3c0", "code": "a793ad9aed7e71838134178e123086537604654e-dirty", "existing": [{"file": "training/summarizer/data/starter/campaigns-v1.jsonl", "sha256": "cc9fe0bba69fb59bfcc75420106ccfeef9f063b341cbe589681e53d924d7d99b"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl", "sha256": "a9fdcfc2920544999e889874c79b5901e251955892080c988766171c77582c9a"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl", "sha256": "2109cdd9dc0467f2fea8e62f344944c6828f67f47ae1bc81d10d93850f5c3a95"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-003.jsonl", "sha256": "58809644e80334d2448408c1d953a13102c908a11c4feb3300ee933e6e3c3799"}, {"file": "training/summarizer/data/synthetic/session-roots-train-001.jsonl", "sha256": "d9f25545d36a8ff303544e7da73076149a1e4482c2e10411190f24d3d1ba69cc"}], "options": {"attack_pairs": true, "require": {}}, "replay": {"ran": true, "agents": ["claude_code", "codex_code"], "runtime": "hash", "budget": 20000}, "complete": true, "manifest": "training/summarizer/data/synthetic/session-roots-train-002.manifest.jsonl"}
```

Evidence checker last line:

```json
{"roots": 50, "refused": 0, "labelled": 50, "named_facts": 188, "omissions_refused": 376, "omissions": 376, "file_sha256": "4facb6e247969d0f4bdb6b1748d0bb3bae6c558f86971aeec2c1f9464e25f1a1", "checker_sha256": "2d53fb7a4c2ca2a0703c7362dfa6a24844dfff1fd394fa68518254e40c55937c", "scorer": "campaign-contract/13", "budget": 20000}
```

### Shortfalls

The command intended to stop after the opening documentation of `training/summarizer/campaign_contract.py` searched for a `from __future__` delimiter that this module does not contain, so it exposed later module text beyond section 2's permitted opening text. No forbidden data, review, message-board, `private/`, or `campaigns-test-*` file was opened, and no reading list inside an authorized document was followed. The first checker attempt found six content-length/chronology problems introduced by the repair; those were corrected before the fresh final run above. No requested repair or final check remains incomplete.

