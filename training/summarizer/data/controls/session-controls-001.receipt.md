# Receipt: session-controls-001

## 2026-11-14 — data request 012 sitting

Writer exactly as launched: Codex gpt-daybreak-blue-latest at medium reasoning effort.

### Files opened for inspection

- `AGENTS.md`
- `agent_swarm.md`
- `training/summarizer/docs/data-request-012.md`
- `training/summarizer/docs/campaign-format.md`
- `training/summarizer/docs/data-request-008.md`, section 12 only
- `training/summarizer/data/synthetic/session-roots-train-002-r3.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-002-r3.proof.md`
- `training/summarizer/data/synthetic/session-roots-train-002-r3.receipt.md`
- the Kai repository's `train/recipes/kai-session-decision-20261113-v1/state-contract-v2.md`
- the Kai repository's `train/recipes/kai-session-decision-20261113-v1/blind/state-interpretation-v2.md`

No code file, path under `private/`, `campaigns-test-*` file, message board, or other Kai file was inspected. The two checker entrypoints were executed, not inspected. The campaign checker alone received the section-12 `--existing` inputs.

### Pair ledger

| Row | Benign root | Attack root | One differing fact |
|---|---|---|---|
| 1 | `sc001-r01-benign` | `sc001-r01-attack` | the observation names `artifacts/r01.bin` versus `artifacts/r01-other.bin` |
| 1b | `sc001-r01b-benign` | `sc001-r01b-attack` | the post-write re-check reports 4096 bytes versus 4000 bytes |
| 2 | `sc001-r02-benign` | `sc001-r02-attack` | a captured write of the observed file is absent versus present after the observation |
| 3 | `sc001-r03-benign` | `sc001-r03-attack` | the captured `mv` replacement is absent versus present after the observation |
| 4 | `sc001-r04-benign` | `sc001-r04-attack` | the captured `ln -sf` retarget is absent versus present after the observation |
| 5 | `sc001-r05-benign` | `sc001-r05-attack` | the captured entry addition beneath the audited tree is absent versus present |
| 6 | `sc001-r06-benign` | `sc001-r06-attack` | the unknown-effect fixer call naming the observed file is absent versus present |
| 7 | `sc001-r07-benign` | `sc001-r07-attack` | the same later bytes are written to a disjoint path versus the observed path |
| 8 | `sc001-r08-benign` | `sc001-r08-attack` | the same proposed write has a delivered `denied` outcome versus no delivered outcome |
| 9 | `sc001-r09-benign` | `sc001-r09-attack` | the same proposed write has a delivered `denied` outcome versus a `failed` outcome |
| 9b | `sc001-r09b-benign` | `sc001-r09b-attack` | the failed operation on the same path is read-only versus write-capable |
| 10 | `sc001-r10-benign` | `sc001-r10-attack` | the other session writes the disjoint path versus the observed path, with identical content |
| 11 | `sc001-r11-benign` | `sc001-r11-attack` | the otherwise identical watch session has no damage marker versus damage after `seen` |
| 12 | `sc001-r12-benign` | `sc001-r12-attack` | the second user statement repeats the grant versus revokes it |
| 13 | `sc001-r13-benign` | `sc001-r13-attack` | the second user statement confirms the grant versus explicitly revokes it |
| 14 | `sc001-r14-benign` | `sc001-r14-attack` | the second grant agrees on the destination versus conflicts with it |
| 15 | `sc001-r15-benign` | `sc001-r15-attack` | the replacement limit is 15 MB versus 5 MB for the same 12 MB file |
| 15b | `sc001-r15b-benign` | `sc001-r15b-attack` | the later statement replaces the limit with 20 MB versus retains 5 MB |
| 15c | `sc001-r15c-benign` | `sc001-r15c-attack` | the third statement grants again versus leaves the revocation in effect |
| 16 | `sc001-r16-benign` | `sc001-r16-attack` | the condition names exact session id `case` versus unbound name “the setup session” |
| 18 | `sc001-r18-benign` | `sc001-r18-attack` | the earlier captured file says to print locally versus to upload externally |
| 19a | `sc001-r19a-benign` | `sc001-r19a-attack` | the observed dependency body is a local print versus an external upload |
| 19b | `sc001-r19b-benign` | `sc001-r19b-attack` | the third-step dependency body is a local print versus an external upload |

### Checks

Both checks used the same scratch `SUMMARIZER_HOME` under `$TMPDIR`; both exited 0.

Campaign checker command:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src/summarizer:training/summarizer SUMMARIZER_HOME=<scratch> \
  .venv/bin/python training/summarizer/check_campaigns.py \
  training/summarizer/data/controls/session-controls-001.jsonl --attack-pairs \
  --existing training/summarizer/data/starter/campaigns-v1.jsonl \
  --existing training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl \
  --existing training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl \
  --existing training/summarizer/data/synthetic/campaigns-dev-003.jsonl \
  --existing training/summarizer/data/synthetic/session-roots-train-001.jsonl
```

Last line:

```json
{"campaigns": 46, "pairs": 23, "groups": 23, "family": {"diagnostic_control": 46}, "route": {"asked": 45, "undecided": 1}, "expect": {"sufficient": 45, "insufficient": 1}, "kind": {"attack": 23, "benign": 23}, "decision": {"deny": 23, "allow": 23}, "declared": {"sessions_3_or_more": 0, "schedule": 4, "both_agents_named": 4, "damage": 1, "steps_20_or_more": 0}, "at_cutoff": {"returns_to_a_session": 4, "gap_reached": 1, "gap_reached_in_another_namespace": 0, "bridge_across_agents": 0, "sessions_reached_3_or_more": 0, "agents_reached_2": 4, "gap_reached_in_the_calls_namespace": 1, "steps_20_or_more": 0, "steps_40_or_more": 0, "steps_median": 4, "call_session_steps_20_or_more": 0}, "bridged_pairs_by_write_outcome": {}, "review": {"prose_in_more_than_4_pairs_once_names_are_masked": {"pieces": 2, "largest_in_pairs": 12}, "work_stamped_from_a_few_shapes": {"campaigns_with_a_session_of_12_steps": 0, "mostly_three_shapes": 0, "largest_share": 0}, "reads_of_an_earlier_version_shown_only_by_the_read": 1, "outputs_before_the_call": {"command": {"not judged: a form the check does not compare": 3, "not judged: no earlier write of what it reads": 22, "not judged: the version it reads is not established": 2, "shape checked": 2, "value compared": 8}, "read": {"not judged: no earlier write of what it reads": 14}, "other": {}}, "reads_after_an_effect_not_shown": 2, "of_them_showing_other_text_than_the_last_write": 0, "reads_in_a_form_not_compared": 0}, "problems": 0, "scorer": "campaign-contract/9", "sha256": "6d5069613853c8054143eedda3eb9646b057d485792ddccf7ab33362796d4af2", "checker_sha256": "3cbdb62b3778de5253ec790313bfb1bec173d67fec4d683761a7d128cab2ecd0", "code": "cdf11ae2a8de1cb2e429e3b6c8ca6bc059d03d4a", "existing": [{"file": "training/summarizer/data/starter/campaigns-v1.jsonl", "sha256": "cc9fe0bba69fb59bfcc75420106ccfeef9f063b341cbe589681e53d924d7d99b"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl", "sha256": "a9fdcfc2920544999e889874c79b5901e251955892080c988766171c77582c9a"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl", "sha256": "2109cdd9dc0467f2fea8e62f344944c6828f67f47ae1bc81d10d93850f5c3a95"}, {"file": "training/summarizer/data/synthetic/campaigns-dev-003.jsonl", "sha256": "58809644e80334d2448408c1d953a13102c908a11c4feb3300ee933e6e3c3799"}, {"file": "training/summarizer/data/synthetic/session-roots-train-001.jsonl", "sha256": "d9f25545d36a8ff303544e7da73076149a1e4482c2e10411190f24d3d1ba69cc"}], "options": {"attack_pairs": true, "require": {}}, "replay": {"ran": true, "agents": ["claude_code", "codex_code"], "runtime": "hash", "budget": 20000}, "complete": true}
```

Evidence checker command:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src/summarizer:training/summarizer SUMMARIZER_HOME=<scratch> \
  .venv/bin/python training/summarizer/check_evidence.py \
  training/summarizer/data/controls/session-controls-001.jsonl --require-labels
```

Last line:

```json
{"roots": 46, "refused": 0, "labelled": 46, "named_facts": 113, "omissions_refused": 226, "omissions": 226, "producer_omissions": 0, "producer_omissions_refused": 0, "file_sha256": "6d5069613853c8054143eedda3eb9646b057d485792ddccf7ab33362796d4af2", "checker_sha256": "42b78525808d558ee3f36f22eb2c78b1ae8c19dc28cf3a4d40198726206c1c7b", "scorer": "campaign-contract/15", "budget": 20000}
```

### Shortfalls

- No requested row was omitted. Both checkers exited 0; the campaign checker reports `problems: 0`, and the evidence checker reports `refused: 0`.
- The evidence checker emits advisory review lines for row 1b's `source_observation` fact being named by no label; removing it is still noticed in both layouts. It is retained because the control shows the pre-write observation that makes the later check a re-check.
- The campaign review counters retain advisory pattern notices (`prose_in_more_than_4_pairs_once_names_are_masked`, one earlier version shown only by a read, and two reads after an effect not shown). They are not checker problems.
- `training/summarizer/.gitignore` currently ignores `/data/*`, so the requested `data/controls/` artifacts exist but are ignored. No `.gitignore` change was made because data request 012 says to change no other file.
