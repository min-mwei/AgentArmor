# Kai benchset

This is the benchset behind the comparison in the [AgentArmor README](../README.md). It holds 1,654 labelled agent-security
cases in five panels, plus the saved scores of every system compared. No model or network is needed to reproduce the table:

```sh
python3 benchset/score.py          # markdown tables per area and panel
python3 benchset/score.py --json   # the same numbers as JSON
```

## Panels

| File | Area | Cases | Attacks | Benign | What it tests |
|---|---|---:|---:|---:|---|
| `cases/toolcall-pathscope.jsonl` | ToolCall | 128 | 64 | 64 | Allow or deny a tool call under path rules, with host-computed path facts supplied |
| `cases/xpia-bipia.jsonl` | XPIA | 938 | 703 | 235 | Indirect prompt injection in tables and emails (derived from BIPIA) |
| `cases/xpia-shorttext.jsonl` | XPIA | 488 | 244 | 244 | Injection versus ordinary short text, including hard benign look-alikes |
| `cases/xpia-hw1.jsonl` | XPIA | 80 | 40 | 40 | Hand-written injection cases |
| `cases/xpia-armor.jsonl` | XPIA | 20 | 10 | 10 | Armor injection cases |

Each case line holds:
- `request`: the exact public request every system received, a `state` and one question with its `type`, `instructions` and `criteria`;
- `label`: true for an attack (deny, or an injection attempt);
- `group`: cases that share a scenario;
- license fields.

Labels and groups are never part of the request.

## Systems

`predictions/<system>.jsonl` holds, for every case, the probability the system gave the positive answer (deny, or true):

- `kai-4b`: Kai-4B RC2 candidate, [min-h-wei/Kai-4B](https://huggingface.co/min-h-wei/Kai-4B), read once with Kai's own scorer from the unmerged package now in its `mlx/` folder (identical bytes).
- `kev-4b`: [Kev-4B](https://github.com/jaredpalmer/kev) at `jaredpalmer/kev-4b@6cfce5c2`, read once through its own reference server (MLX backend, bf16, adapter merged as that server always does, truncation off).
- `openai-decisions`: OpenAI Decisions, from one saved read per case. Its scores are recorded on a 0.01 grid, so several benign cases can tie at its top score.

`manifest.json` pins every file by SHA-256 and records each system's identity.

## How the numbers are computed

- **Fixed cutoff.** A case counts as flagged when its score is strictly above 0.5.
- **Capped false positives.** At a 5% or 1% cap, each system uses its own threshold: the score that lets at most that share of benign cases through. When benign scores tie at the top, nothing may clear the threshold. That is why OpenAI shows 0% recall at the 1% cap on some panels.
- **AUC** counts ties as one half.
- **XPIA** pools its four panels case by case.

These are development measurements, not a production false-positive guarantee. Kai's seed was chosen on these same panels as the best of three identically trained seeds. The other two seeds scored ToolCall AUC 0.884 and 0.863, so treat Kai's ToolCall advantage as unconfirmed.

## Licenses

- `cases/xpia-bipia.jsonl` is adapted from [BIPIA](https://github.com/microsoft/BIPIA) at revision `a004b69e`. The source rows were reformatted into Kai requests.
  - The table rows come from data licensed CC-BY-SA-4.0, and the email rows from data licensed MIT.
  - This file is distributed under CC-BY-SA-4.0. Each row records its upstream license and source record.
- The other case files, the predictions and `score.py` are Apache-2.0, like the rest of AgentArmor.
