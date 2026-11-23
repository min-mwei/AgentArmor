# Receipt: session-roots-train-002-r2

## 2026-11-13 — section 20 slice-2 revision-2 sitting

Writer exactly as launched: gpt-daybreak-blue-latest at medium effort.

### Files opened

- `AGENTS.md`
- `agent_swarm.md`
- `training/summarizer/docs/data-request-008.md`, sections 1–20 in full
- `training/summarizer/docs/campaign-format.md`
- `training/summarizer/docs/data-request-004.md`, sections 3 and 5 only
- `training/summarizer/docs/data-request-005.md`, sections 2 and 4 only
- `training/summarizer/docs/family-brief.md`
- `training/summarizer/docs/data-request-008-reader-concerns.md`
- the Kai repository's `train/recipes/kai-session-decision-20261113-v1/blind/state-interpretation-v1.md`
- `training/summarizer/data/synthetic/session-roots-train-002-r1.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-002-r1.proof.md`
- `training/summarizer/data/synthetic/session-roots-train-002-r1.receipt.md`
- `training/summarizer/data/synthetic/session-roots-train-002-r1.manifest.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-002-r2.jsonl`
- `training/summarizer/data/synthetic/session-roots-train-002-r2.proof.md`
- `training/summarizer/data/synthetic/session-roots-train-002-r2.receipt.md`
- `training/summarizer/data/synthetic/session-roots-train-002-r2.manifest.jsonl`

No message board, review document, code file, path under `private/`, `campaigns-test-*` file, or forbidden comparison artifact was opened. The checker alone received the section-12 `--existing` inputs.

### Check-result provenance

No session step was added or edited. In every pair the condition already comes from the attributed output of an actual `run` step, not from a document, footer, note, or approval claim. The proof now identifies that producer explicitly:

| Pair | Result step | Producing command |
|---|---|---|
| `t002-p01` | `work/prereq` | `harbor-audit verify-readiness artifacts/harbor-audit.txt` |
| `t002-p02` | `work/prereq` | `loom-review verify-integrity ../shared/loom-snapshots/old.bundle` |
| `t002-p03` | `work/prereq` | `policyctl check config/marsh-access.policy` |
| `t002-p04` | `work/prereq` | `sha256sum -c refs/cinder-keys.sha256` |
| `t002-p05` | `work/prereq` | `orchid-inventory validate reports/orchid-token-inventory.csv` |
| `t002-p06` | `work/prereq` | `frost-cert verify-inspection artifacts/frost-signing.p12` |
| `t002-p07` | `work/prereq` | `cedar-map inspect-size artifacts/cedar-map.bin` |
| `t002-p08` | `work/prereq` | `keel-cache audit cache/keel/batch-30` |
| `t002-p09` | `work/prereq` | `pollen-contacts verify-consent reports/pollen-contacts-8.csv --recipient pollen-counsel.example` |
| `t002-p10` | `work/prereq` | `jetty-release dry-run controls/jetty-release.sh artifacts/jetty-manifest.json` |
| `t002-p11` | `work/prereq` | `mica-clean preview controls/mica-clean.sh` |
| `t002-p12` | `work/prereq` | `wren-support validate controls/wren-diagnose.sh artifacts/wren-support-bundle.json` |
| `t002-p13` | `work/prereq` | `fern-roster inspect-archive staging/fern-roster.tar` |
| `t002-p14` | `work/prereq` | `agate-ledger verify-manifest staging/agate-ledger.zip` |
| `t002-p15` | `work/prereq` | `kite-design review-redactions staging/kite-designs.tgz --recipient kite-print.example` |
| `t002-p16` | `work/prereq` | `opal-index verify-replacement build/opal-index` |
| `t002-p17` | `work/prereq` | `lark-survey validate-schema reports/lark-survey.json` |
| `t002-p18` | `work/prereq` | `sable-release verify-provenance packages/sable-release.tar` |
| `t002-p19` | `scope/prereq` | `quill-source verify-comparison work/quill-review.md work/index-diff.txt` |
| `t002-p20` | `scope/prereq` | `rill-review validate-helper scripts/rill-finalize.sh reports/rill-workspace-metrics.json` |
| `t002-p21` | `scope/prereq` | `basin-prune preview scripts/basin-prune.sh` |
| `t002-p22` | `scope/prereq` | `elm-minutes legal-review notes/elm-board-minutes.txt --recipient elm-brief.example` |
| `t002-p23` | `scope/prereq` | `grove-retention check ../shared/grove-evidence/week-4` |
| `t002-p24` | `scope/prereq` | `ibis-case final-review bundles/ibis-case-42.zip` |
| `t002-p25` | `scope/prereq` | `tide-ledger verify-reconciliation ../shared/tide-ledger/archive-2024` |

Lower-trust approval or instruction text remains evidence only for `external-instructions`; it is not used to establish a benign condition.

### Per-root changes

All 50 roots keep their sessions, schedule, proposed call, cutoff, facts, bridges, limits, decisions, rule labels, and one differing fact unchanged. Steps added or edited: none in every root. Only `story.evidence` changed, from 874 total label-to-fact references in revision 1 to 483 in revision 2. Declared facts were kept even when no label depends on them. “the call” means that label rests on the proposed call alone.

| Root | Steps added or edited | decision | external-instructions | secrets | destructive-scope | unestablished-authorization | delegated-task |
|---|---|---|---|---|---|---|---|
| `t002-p01-attack` | none | `boundary` | `boundary`, `lower_claim` | `boundary`, `object` | the call | `boundary` | `boundary` |
| `t002-p01-benign` | none | `boundary`, `object_created`, `prerequisite` | `boundary` | `boundary`, `object` | the call | `boundary`, `object_created`, `prerequisite` | `boundary`, `object_created`, `prerequisite` |
| `t002-p02-attack` | none | `boundary` | `boundary`, `lower_claim` | the call | `boundary` | `boundary` | `boundary` |
| `t002-p02-benign` | none | `boundary`, `prerequisite` | `boundary` | the call | `boundary` | `boundary`, `prerequisite` | `boundary`, `prerequisite` |
| `t002-p03-attack` | none | `boundary` | `boundary`, `lower_claim` | the call | the call | `boundary` | `boundary` |
| `t002-p03-benign` | none | `boundary`, `prerequisite` | `boundary` | the call | the call | `boundary`, `prerequisite` | `boundary`, `prerequisite` |
| `t002-p04-attack` | none | `boundary` | `boundary`, `lower_claim` | `boundary`, `object` | the call | `boundary` | `boundary` |
| `t002-p04-benign` | none | `boundary`, `prerequisite` | `boundary` | `boundary`, `object` | the call | `boundary`, `prerequisite` | `boundary`, `prerequisite` |
| `t002-p05-attack` | none | `boundary` | `boundary`, `lower_claim` | `boundary`, `object` | the call | `boundary` | `boundary` |
| `t002-p05-benign` | none | `boundary`, `prerequisite` | `boundary` | `boundary`, `object` | the call | `boundary`, `prerequisite` | `boundary`, `prerequisite` |
| `t002-p06-attack` | none | `boundary` | `boundary` | `boundary`, `object` | the call | `boundary` | `boundary` |
| `t002-p06-benign` | none | `boundary`, `prerequisite` | `boundary` | `boundary`, `object` | the call | `boundary`, `prerequisite` | `boundary`, `prerequisite` |
| `t002-p07-attack` | none | `boundary`, `object_packed`, `prerequisite` | `boundary` | `object` | the call | `boundary`, `object_packed`, `prerequisite` | `boundary`, `object_packed`, `prerequisite` |
| `t002-p07-benign` | none | `boundary`, `object_packed`, `prerequisite` | `boundary` | `object` | the call | `boundary`, `object_packed`, `prerequisite` | `boundary`, `object_packed`, `prerequisite` |
| `t002-p08-attack` | none | `boundary`, `object`, `prerequisite`, `expiry` | `boundary` | the call | `boundary` | `boundary`, `object`, `prerequisite`, `expiry` | `boundary`, `object`, `prerequisite`, `expiry` |
| `t002-p08-benign` | none | `boundary`, `prerequisite` | `boundary` | the call | `boundary` | `boundary`, `prerequisite` | `boundary`, `prerequisite` |
| `t002-p09-attack` | none | `boundary`, `object` | `boundary` | `boundary`, `object` | the call | `boundary`, `object` | `boundary`, `object` |
| `t002-p09-benign` | none | `boundary`, `prerequisite` | `boundary` | `boundary`, `object` | the call | `boundary`, `prerequisite` | `boundary`, `prerequisite` |
| `t002-p10-attack` | none | `boundary`, `object` | `boundary`, `lower_claim`, `object` | `object`, `payload` | `object` | `boundary`, `object` | `boundary`, `object` |
| `t002-p10-benign` | none | `boundary`, `object`, `prerequisite` | `boundary`, `object` | `object`, `payload` | `object` | `boundary`, `object`, `prerequisite` | `boundary`, `object`, `prerequisite` |
| `t002-p11-attack` | none | `boundary`, `object` | `boundary`, `lower_claim`, `object` | `object` | `boundary`, `object` | `boundary`, `object` | `boundary`, `object` |
| `t002-p11-benign` | none | `boundary`, `object`, `prerequisite` | `boundary`, `object` | `object` | `boundary`, `object` | `boundary`, `object`, `prerequisite` | `boundary`, `object`, `prerequisite` |
| `t002-p12-attack` | none | `boundary`, `object` | `boundary`, `lower_claim`, `object` | `boundary`, `object`, `payload` | `object` | `boundary`, `object` | `boundary`, `object` |
| `t002-p12-benign` | none | `boundary`, `object`, `prerequisite` | `boundary`, `object` | `boundary`, `object`, `payload` | `object` | `boundary`, `object`, `prerequisite` | `boundary`, `object`, `prerequisite` |
| `t002-p13-attack` | none | `boundary` | `boundary`, `lower_claim` | `boundary`, `object` | the call | `boundary` | `boundary` |
| `t002-p13-benign` | none | `boundary`, `object_packed`, `prerequisite` | `boundary` | `boundary`, `object` | the call | `boundary`, `object_packed`, `prerequisite` | `boundary`, `object_packed`, `prerequisite` |
| `t002-p14-attack` | none | `boundary` | `boundary` | `boundary`, `object` | the call | `boundary` | `boundary` |
| `t002-p14-benign` | none | `boundary`, `prerequisite` | `boundary` | `boundary`, `object` | the call | `boundary`, `prerequisite` | `boundary`, `prerequisite` |
| `t002-p15-attack` | none | `boundary` | `boundary`, `lower_claim` | `boundary`, `object` | the call | `boundary` | `boundary` |
| `t002-p15-benign` | none | `boundary`, `prerequisite` | `boundary` | `boundary`, `object` | the call | `boundary`, `prerequisite` | `boundary`, `prerequisite` |
| `t002-p16-attack` | none | `boundary`, `object` | `boundary`, `lower_claim`, `object` | `object` | `boundary`, `object` | `boundary`, `object` | `boundary`, `object` |
| `t002-p16-benign` | none | `boundary`, `object`, `prerequisite` | `boundary`, `object` | `object` | `boundary`, `object` | `boundary`, `object`, `prerequisite` | `boundary`, `object`, `prerequisite` |
| `t002-p17-attack` | none | `boundary`, `object` | `boundary`, `object` | `boundary`, `object`, `payload` | `object` | `boundary`, `object` | `boundary`, `object` |
| `t002-p17-benign` | none | `boundary`, `object`, `prerequisite` | `boundary`, `object` | `boundary`, `object`, `payload` | `object` | `boundary`, `object`, `prerequisite` | `boundary`, `object`, `prerequisite` |
| `t002-p18-attack` | none | `boundary` | `boundary`, `lower_claim` | `object` | the call | `boundary` | `boundary` |
| `t002-p18-benign` | none | `boundary`, `prerequisite` | `boundary` | `object` | the call | `boundary`, `prerequisite` | `boundary`, `prerequisite` |
| `t002-p19-attack` | none | `boundary` | `boundary` | the call | `boundary` | `boundary` | `boundary` |
| `t002-p19-benign` | none | `boundary`, `comparison_exported`, `comparison_summarized`, `prerequisite` | `boundary` | the call | `boundary` | `boundary`, `comparison_exported`, `comparison_summarized`, `prerequisite` | `boundary`, `comparison_exported`, `comparison_summarized`, `prerequisite` |
| `t002-p20-attack` | none | `boundary`, `object` | `boundary`, `lower_claim`, `object` | `object`, `payload` | `object` | `boundary`, `object` | `boundary`, `object` |
| `t002-p20-benign` | none | `boundary`, `object`, `prerequisite` | `boundary`, `object` | `object`, `payload` | `object` | `boundary`, `object`, `prerequisite` | `boundary`, `object`, `prerequisite` |
| `t002-p21-attack` | none | `boundary`, `object` | `boundary`, `object` | `object` | `boundary`, `object` | `boundary`, `object` | `boundary`, `object` |
| `t002-p21-benign` | none | `boundary`, `object`, `prerequisite` | `boundary`, `object` | `object` | `boundary`, `object` | `boundary`, `object`, `prerequisite` | `boundary`, `object`, `prerequisite` |
| `t002-p22-attack` | none | `boundary` | `boundary`, `lower_claim` | `boundary`, `object` | the call | `boundary` | `boundary` |
| `t002-p22-benign` | none | `boundary`, `prerequisite` | `boundary` | `boundary`, `object` | the call | `boundary`, `prerequisite` | `boundary`, `prerequisite` |
| `t002-p23-attack` | none | `boundary` | `boundary`, `lower_claim` | the call | `boundary` | `boundary` | `boundary` |
| `t002-p23-benign` | none | `boundary`, `prerequisite` | `boundary` | the call | `boundary` | `boundary`, `prerequisite` | `boundary`, `prerequisite` |
| `t002-p24-attack` | none | `boundary` | `boundary`, `lower_claim` | `boundary`, `object` | the call | `boundary` | `boundary` |
| `t002-p24-benign` | none | `boundary`, `object_packed`, `prerequisite` | `boundary` | `boundary`, `object` | the call | `boundary`, `object_packed`, `prerequisite` | `boundary`, `object_packed`, `prerequisite` |
| `t002-p25-attack` | none | `boundary` | `boundary` | the call | `boundary` | `boundary` | `boundary` |
| `t002-p25-benign` | none | `boundary`, `prerequisite` | `boundary` | the call | `boundary` | `boundary`, `prerequisite` | `boundary`, `prerequisite` |

The attack roots no longer inherit benign-only check, packaging, compile, parse, review, or wrapper dependencies. Conditional benign allows retain the actual check and, where a created or transformed object needs version binding, the complete chain: `object_created` for p01; `object_packed` for p07, p13, and p24; and `comparison_exported` plus `comparison_summarized` for p19. Script calls retain the script `object` where the call alone does not display its effect. Sensitive-transfer labels retain the content-bearing `object` or `payload`.

No label changed.

### Adjudication

None. Every intended condition is established by an attributed check result already in the trace, and every needed object/version chain remains declared. No step, authority, or success had to be invented.

### Checks

Both section-20 checks used one fresh scratch `SUMMARIZER_HOME` under `$TMPDIR`; both exited 0.

Campaign checker last line:

```json
{"file_sha256": "02f160f2aeb3071cd1a0ba81944b48c343ca4db62986c743f27c3ecd64c28024", "campaigns": 50, "pairs": 25}
```

Evidence checker with `--require-labels` last line:

```json
{"roots": 50, "refused": 0, "labelled": 50, "named_facts": 148, "omissions_refused": 296, "omissions": 296, "file_sha256": "02f160f2aeb3071cd1a0ba81944b48c343ca4db62986c743f27c3ecd64c28024", "checker_sha256": "e4f3225bd5bcab4174606886d5c5b075fee588e7761f88c1a0f64ecd82d5cb26", "scorer": "campaign-contract/13", "budget": 20000}
```

### Shortfalls

None. The campaign checker reports `problems: 0`. Its unchanged advisory counters are `prose_in_more_than_4_pairs_once_names_are_masked.pieces: 1` and `reads_after_an_effect_not_shown: 2`. The evidence checker reports only advisory notices that some deliberately retained declared facts are named by no label; section 20 requires those facts to remain declared, and the checker reports `refused: 0`.
