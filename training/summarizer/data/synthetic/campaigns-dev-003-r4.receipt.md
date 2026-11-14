# Campaigns dev 003 revision 4: returned-result repair receipt

- Writer: `gpt-daybreak-blue-latest` at medium effort
- Request: data request 009, section 7 only
- Result: 80 campaigns in 40 pairs; 488 campaign-step instances gained `"output": ""`
- Quiet inventory repair: 26 campaign-step instances (13 aligned steps, identically in both
  twins) changed a result-less `find` command to redirect its stdout to `/dev/null`; none was a
  declared fact or bridge, and no story observation depended on its listing
- New non-return markers: pending 0, refused 0, failed 0; every checker-named call returned
- Existing non-return markers retained: pending 0, refused 2 campaign instances (one aligned
  denied replacement), failed 4 campaign instances (two aligned failed replacements)
- Labels, boundaries, calls, cutoffs, differing facts, routes and decisions: unchanged
- Proof: copied byte for byte from revision 3 because no proof row's words changed
- Manifest: `training/summarizer/data/eval/campaigns-dev-003-r4.manifest.jsonl`
- Campaign SHA-256: `e7d3e0ee6c1f6c8226a19e3389a1452d9e4c79661b715f8fba398da247d8ac27`

## Files opened

Directly read or structurally inspected:

- `AGENTS.md`
- `agent_swarm.md`
- `training/summarizer/docs/data-request-009.md`, sections 1–7
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
- `training/summarizer/data/synthetic/campaigns-dev-003-r3.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-003-r3.proof.md`
- `training/summarizer/data/synthetic/campaigns-dev-003-r3.receipt.md`
- `training/summarizer/data/synthetic/campaigns-dev-003-r4.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-003-r4.proof.md`

Opened by the required or diagnostic campaign checks (executed, not inspected):

- `training/summarizer/check_campaigns.py`
- `training/summarizer/data/starter/campaigns-v1.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl`
- `training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl`
- `training/summarizer/data/eval/campaigns-dev-003-r4.manifest.jsonl`

No checker source was read, and no file under `private/`, no `campaigns-test-*`, no
`session-roots-train-*`, and no message board was opened.

## Check and review counts

The diagnostic revision-3 check exited nonzero with 80 campaign records refused. Its last-line
review counts before the repair were:

```json
{"prose_in_more_than_4_pairs_once_names_are_masked":{"pieces":3,"largest_in_pairs":14},"work_stamped_from_a_few_shapes":{"campaigns_with_a_session_of_12_steps":18,"mostly_three_shapes":0,"largest_share":0.3},"reads_of_an_earlier_version_shown_only_by_the_read":2,"reads_after_an_effect_not_shown":76,"of_them_showing_other_text_than_the_last_write":0,"reads_in_a_form_not_compared":18}
```

The required revision-4 check exited 0. Its complete last line, including the after-review counts,
was:

```json
{"campaigns":80,"pairs":40,"groups":40,"family":{"later_look_disagrees":6,"credential_moved":6,"poisoned_notes":6,"slow_interleaved":6,"poisoned_control":6,"staged_exfiltration":6,"limit_crossed":6,"planted_file_later":4,"changed_source":4,"claimed_approval":4,"handoff_between_agents":4,"approval_withdrawn":6,"scope_stretched":6,"lookalike_name":6,"destruction_prepared_earlier":4},"route":{"asked":68,"unasked":6,"undecided":6},"expect":{"sufficient":74,"insufficient":6},"kind":{"attack":40,"benign":40},"decision":{"deny":40,"allow":40},"declared":{"sessions_3_or_more":58,"schedule":80,"both_agents_named":80,"damage":8,"steps_20_or_more":54},"at_cutoff":{"returns_to_a_session":80,"gap_reached":8,"gap_reached_in_another_namespace":2,"bridge_across_agents":20,"sessions_reached_3_or_more":58,"agents_reached_2":80,"gap_reached_in_the_calls_namespace":6,"steps_20_or_more":54,"steps_40_or_more":18,"steps_median":23,"call_session_steps_20_or_more":18},"attack_pairs_by_area":{"None":18,"destructive_action":4,"data_exposure":10,"control_change":4,"credential_access":4},"bridged_pairs_by_write_outcome":{"failed":2,"refused":1,"returned":23},"review":{"prose_in_more_than_4_pairs_once_names_are_masked":{"pieces":3,"largest_in_pairs":14},"work_stamped_from_a_few_shapes":{"campaigns_with_a_session_of_12_steps":18,"mostly_three_shapes":0,"largest_share":0.29},"reads_of_an_earlier_version_shown_only_by_the_read":2,"reads_after_an_effect_not_shown":122,"of_them_showing_other_text_than_the_last_write":0,"reads_in_a_form_not_compared":24},"problems":0,"scorer":"campaign-contract/9","sha256":"e7d3e0ee6c1f6c8226a19e3389a1452d9e4c79661b715f8fba398da247d8ac27","checker_sha256":"bdbab0dac573bb9336bdad8f85aa2b322ed04c515a1bdc277be9d584271250e2","code":"c053f28348160a38d9e0fc4cfb5a407b96cdbf7d","existing":[{"file":"training/summarizer/data/starter/campaigns-v1.jsonl","sha256":"cc9fe0bba69fb59bfcc75420106ccfeef9f063b341cbe589681e53d924d7d99b"},{"file":"training/summarizer/data/synthetic/campaigns-dev-001-r1.jsonl","sha256":"a9fdcfc2920544999e889874c79b5901e251955892080c988766171c77582c9a"},{"file":"training/summarizer/data/synthetic/campaigns-dev-002-full.jsonl","sha256":"2109cdd9dc0467f2fea8e62f344944c6828f67f47ae1bc81d10d93850f5c3a95"}],"options":{"attack_pairs":true,"require":{"sessions_reached_3_or_more":"44","agents_reached_2":"36","returns_to_a_session":"27","steps_20_or_more":"45","steps_40_or_more":"18","call_session_steps_20_or_more":"14","gap_reached_in_the_calls_namespace":"6","gap_reached_in_another_namespace":"2"}},"replay":{"ran":true,"agents":["claude_code","codex_code"],"runtime":"hash","budget":20000},"complete":true,"manifest":"training/summarizer/data/eval/campaigns-dev-003-r4.manifest.jsonl"}
```

## Shortfalls

None. The required check exits 0 with `problems: 0`; every returned pre-call tool call now has a
result, genuinely denied or failed calls retain their explicit markers, both twins remain aligned,
and no label changed.
