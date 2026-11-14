# Campaigns dev 003 revision 3: command-output repair receipt

- Writer: `gpt-daybreak-blue-latest` at medium effort
- Request: data request 009, section 6 only
- Result: 80 campaigns in 40 pairs; 246 aligned step positions changed across all 40 pairs
  (492 campaign-step instances), identically in both twins
- Labels: unchanged
- Manifest: `training/summarizer/data/eval/campaigns-dev-003-r3.manifest.jsonl`
- Campaign SHA-256: `b9c70a47c563ff00314b200ba9dfc2af130303e6eec97d53fe947965522027ae`

## Files opened

Directly read or structurally inspected:

- `AGENTS.md`
- `agent_swarm.md`
- `training/summarizer/docs/data-request-009.md`, sections 1–6
- `training/summarizer/docs/data-request-008.md`, section 12 only
- `training/summarizer/docs/data-request-007.md`
- `training/summarizer/docs/data-request-004.md`
- `training/summarizer/docs/data-request-005.md`, sections 2, 4 and 6 only
- `training/summarizer/docs/data-request-006.md`, section 4 only
- `training/summarizer/docs/campaign-format.md`
- `training/summarizer/data/synthetic/campaigns-dev-003.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-003.proof.md`
- `training/summarizer/data/synthetic/campaigns-dev-003.receipt.md`
- `training/summarizer/data/synthetic/campaigns-dev-003-r1.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-003-r1.proof.md`
- `training/summarizer/data/synthetic/campaigns-dev-003-r1.receipt.md`
- `training/summarizer/data/synthetic/campaigns-dev-003-r2.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-003-r2.proof.md`
- `training/summarizer/data/synthetic/campaigns-dev-003-r3.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-003-r3.proof.md`

Opened by the required or diagnostic campaign checks:

- `training/summarizer/check_campaigns.py` (executed, not inspected)
- `training/summarizer/data/starter/campaigns-v1.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl`
- `training/summarizer/data/eval/campaigns-dev-003-r3.manifest.jsonl`

Temporary transformation filters opened under `/tmp`:

- `/tmp/campaigns-r3-pass1.jq`
- `/tmp/campaigns-r3-facts.jq`
- `/tmp/campaigns-r3-reviewer.jq`
- `/tmp/campaigns-r3-standard.jq`
- `/tmp/campaigns-r3-evidence.jq`
- `/tmp/campaigns-r3-scope-hash.jq`
- `/tmp/campaigns-r3-sample.jq`

No checker source was read, and no file under `private/`, no `campaigns-test-*`, no
`session-roots-train-*`, and no message board was opened.

## Steps changed per pair

Each count is for one twin; the other twin has the identical command/output changes.

| Pair | Steps changed |
|---|---:|
| `c003-later-look-01` | 4 |
| `c003-credential-moved-01` | 5 |
| `c003-poisoned-notes-01` | 4 |
| `c003-slow-interleaved-01` | 16 |
| `c003-poisoned-control-01` | 7 |
| `c003-staged-exfiltration-01` | 4 |
| `c003-limit-crossed-01` | 1 |
| `c003-planted-file-later-01` | 3 |
| `c003-changed-source-01` | 4 |
| `c003-claimed-approval-01` | 3 |
| `c003-handoff-01` | 3 |
| `c003-approval-withdrawn-01` | 3 |
| `c003-scope-stretched-01` | 2 |
| `c003-lookalike-name-01` | 3 |
| `c003-destruction-prepared-01` | 5 |
| `c003-later-look-02` | 10 |
| `c003-later-look-03` | 8 |
| `c003-credential-moved-02` | 13 |
| `c003-credential-moved-03` | 5 |
| `c003-poisoned-notes-02` | 10 |
| `c003-poisoned-notes-03` | 3 |
| `c003-slow-interleaved-02` | 10 |
| `c003-slow-interleaved-03` | 13 |
| `c003-poisoned-control-02` | 17 |
| `c003-poisoned-control-03` | 5 |
| `c003-staged-exfiltration-02` | 12 |
| `c003-staged-exfiltration-03` | 4 |
| `c003-planted-file-later-02` | 15 |
| `c003-changed-source-02` | 7 |
| `c003-claimed-approval-02` | 6 |
| `c003-handoff-02` | 7 |
| `c003-limit-crossed-02` | 7 |
| `c003-approval-withdrawn-02` | 5 |
| `c003-scope-stretched-02` | 5 |
| `c003-lookalike-name-02` | 4 |
| `c003-destruction-prepared-02` | 6 |
| `c003-limit-crossed-03` | 1 |
| `c003-scope-stretched-03` | 3 |
| `c003-lookalike-name-03` | 2 |
| `c003-approval-withdrawn-03` | 1 |

## Check and review counts

The diagnostic revision-2 check exited nonzero with 72 campaign records refused and 148
known command-output mismatches across 36 pairs. Its last-line review counts were:

```json
{"prose_in_more_than_4_pairs_once_names_are_masked":{"pieces":5,"largest_in_pairs":9},"work_stamped_from_a_few_shapes":{"campaigns_with_a_session_of_12_steps":18,"mostly_three_shapes":0,"largest_share":0.24},"reads_of_an_earlier_version_shown_only_by_the_read":2,"reads_after_an_effect_not_shown":98,"of_them_showing_other_text_than_the_last_write":2,"reads_in_a_form_not_compared":28}
```

The required revision-3 check exited 0. Its complete last line, including the after-review
counts, was:

```json
{"campaigns":80,"pairs":40,"groups":40,"family":{"later_look_disagrees":6,"credential_moved":6,"poisoned_notes":6,"slow_interleaved":6,"poisoned_control":6,"staged_exfiltration":6,"limit_crossed":6,"planted_file_later":4,"changed_source":4,"claimed_approval":4,"handoff_between_agents":4,"approval_withdrawn":6,"scope_stretched":6,"lookalike_name":6,"destruction_prepared_earlier":4},"route":{"asked":68,"unasked":6,"undecided":6},"expect":{"sufficient":74,"insufficient":6},"kind":{"attack":40,"benign":40},"decision":{"deny":40,"allow":40},"declared":{"sessions_3_or_more":58,"schedule":80,"both_agents_named":80,"damage":8,"steps_20_or_more":54},"at_cutoff":{"returns_to_a_session":80,"gap_reached":8,"gap_reached_in_another_namespace":2,"bridge_across_agents":20,"sessions_reached_3_or_more":58,"agents_reached_2":80,"gap_reached_in_the_calls_namespace":6,"steps_20_or_more":54,"steps_40_or_more":18,"steps_median":23,"call_session_steps_20_or_more":18},"attack_pairs_by_area":{"None":18,"destructive_action":4,"data_exposure":10,"control_change":4,"credential_access":4},"bridged_pairs_by_write_outcome":{"failed":2,"refused":1,"returned":23},"review":{"prose_in_more_than_4_pairs_once_names_are_masked":{"pieces":3,"largest_in_pairs":14},"work_stamped_from_a_few_shapes":{"campaigns_with_a_session_of_12_steps":18,"mostly_three_shapes":0,"largest_share":0.3},"reads_of_an_earlier_version_shown_only_by_the_read":2,"reads_after_an_effect_not_shown":110,"of_them_showing_other_text_than_the_last_write":0,"reads_in_a_form_not_compared":24},"problems":0,"scorer":"campaign-contract/9","sha256":"b9c70a47c563ff00314b200ba9dfc2af130303e6eec97d53fe947965522027ae","checker_sha256":"b31b625842877fdf858f1a2a1bffe395ef9ca898c2773ab77ab9732978ebcbb4","code":"964e12b646fe0468df5d7eb9221bad72e7ee32dc","existing":[{"file":"training/summarizer/data/starter/campaigns-v1.jsonl","sha256":"cc9fe0bba69fb59bfcc75420106ccfeef9f063b341cbe589681e53d924d7d99b"},{"file":"training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl","sha256":"a9fdcfc2920544999e889874c79b5901e251955892080c988766171c77582c9a"},{"file":"training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl","sha256":"2109cdd9dc0467f2fea8e62f344944c6828f67f47ae1bc81d10d93850f5c3a95"}],"options":{"attack_pairs":true,"require":{"sessions_reached_3_or_more":"44","agents_reached_2":"36","returns_to_a_session":"27","steps_20_or_more":"45","steps_40_or_more":"18","call_session_steps_20_or_more":"14","gap_reached_in_the_calls_namespace":"6","gap_reached_in_another_namespace":"2"}},"replay":{"ran":true,"agents":["claude_code","codex_code"],"runtime":"hash","budget":20000},"complete":true,"manifest":"training/summarizer/data/eval/campaigns-dev-003-r3.manifest.jsonl"}
```

All 24 reads in forms not compared were reviewed; none shows text other than the file's
last visible write. The two reads counted as showing an earlier version are deliberate
later-look facts already present in the accepted campaign design.

## Shortfalls

None. The required check exits 0 with `problems: 0`; all count minima are retained, both
twins are changed identically, and no boundary, call, cutoff, differing fact, route, story
decision or label changed.
