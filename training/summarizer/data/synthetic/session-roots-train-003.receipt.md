# Slice 3 receipt

Writer: Codex gpt-daybreak-blue-latest at medium reasoning effort

## Files opened

- `training/summarizer/docs/data-request-008.md` (sections 1–5, 8, 13, 16–21, and 24 only)
- `training/summarizer/campaign_contract.py` (opening text only)
- `training/summarizer/docs/data-request-004.md` (sections 3 and 5 only)
- `training/summarizer/docs/data-request-005.md` (sections 2 and 4 only)
- `training/summarizer/docs/family-brief.md`
- `training/summarizer/data/synthetic/session-roots-train-003.jsonl`

## Pair table

| Pairs | Family | Technique | Differing fact |
|---|---|---|---|
| t003-p01–p04 | later_look_disagrees | stale observation | latest key, hold, classification, or approval result agrees or disagrees |
| t003-p05–p08 | lookalike_name | similar object name | the user's exact authorized name matches the call or its lookalike |
| t003-p09–p12 | destruction_prepared | overbroad prepared artifact | one prepared file's scope is exact or includes one unauthorized target |

## Checks

- `check_campaigns exit 1; check_evidence exit 1`
- Evidence last line: `{"roots": 24, "refused": 22, "labelled": 24, "named_facts": 120, "omissions_refused": 32, "omissions": 32, "producer_omissions": 8, "producer_omissions_refused": 8, "scorer": "campaign-contract/16", "budget": 20000}` (hash fields omitted from this transcription).

## Shortfalls

- The supplied check command did not exit 0: campaign validation reported 24 problems, and evidence admission refused 22 roots for unresolved evidence and producer omissions.
- No model, git command, board, forbidden source, private data, review, development campaign, control campaign, held-out campaign, Kai file, or checker source was opened or run directly.

## Repair sitting — 2026-11-14

Writer: Codex gpt-daybreak-blue-latest at medium reasoning effort

### Files opened

- `training/summarizer/docs/data-request-008.md` (governing sections and section 2's reading list)
- `training/summarizer/campaign_contract.py` (opening format text only)
- `training/summarizer/docs/data-request-004.md` (sections 3 and 5 only)
- `training/summarizer/docs/data-request-005.md` (sections 2 and 4 only)
- `training/summarizer/docs/family-brief.md`
- `training/summarizer/data/synthetic/session-roots-train-003.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-003.proof.md`
- `training/summarizer/data/synthetic/session-roots-train-003.receipt.md`
- Supplied scratch outputs `slice3-check-campaigns.out` and `slice3-check-evidence.out` (outside the repository)

### Repairs

- All pairs: removed request-boundary and request-authority annotations from `facts` and each rule's evidence. The user statements remain in their session records, and current prompts state the operative condition or exact scope.
- t003-p01–p03: removed duplicated artifact/object annotations by making each decision call name its logical candidate, case copy, or dataset rather than the earlier local file; retained only the latest observation and its producer as decision facts.
- t003-p05–p08: made each object fact the complete earlier proposed-target result and its producer; removed the duplicated request-authority facts while preserving each pair's exact-name/lookalike difference.
- t003-p09–p12: replaced the prepared list, plan, SQL fragment, or configuration with a complete destructive script. Each bridge now begins at the explicit write to the whole `{cwd}/ops/…` resource and ends at the later relative script run. Rule evidence was narrowed so facts appear only for labels that depend on prepared scope.
- The proof table now describes the four prepared resources as scripts and identifies the exact scope difference in each pair.

### Checks

- Campaign last line: `{"campaigns": 24, "pairs": 12, "groups": 12, "family": {"later_look_disagrees": 8, "lookalike_name": 8, "destruction_prepared": 8}, "problems": 0, "scorer": "campaign-contract/9", "complete": true}` (hash, comparison inventory, replay detail, and manifest fields omitted from this transcription).
- Evidence last line: `{"roots": 24, "refused": 0, "labelled": 24, "named_facts": 58, "omissions_refused": 116, "omissions": 116, "producer_omissions": 36, "producer_omissions_refused": 36, "scorer": "campaign-contract/16", "budget": 20000}` (hash fields omitted from this transcription).
- Exit line: `check_campaigns exit 0; check_evidence exit 0`

### Shortfalls

- None.
