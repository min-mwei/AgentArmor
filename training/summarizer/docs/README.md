# Summarizer documents

The data requests, protocols, contracts, plans and reviews of Summarizer's training and
measurement, moved here from the Summarizer repository on 2026-11-13, when that repository was
being retired at the user's request. Their text is as written there, except that machine
paths were rewritten for this public repository.

Paths they name map as follows:

| Written there | Here |
|---|---|
| `train/data/starter/`, `train/data/synthetic/`, `train/data/eval/` | `training/summarizer/data/starter/`, `…/synthetic/`, `…/eval/` |
| `train/data/seeds/`, `train/data/built*/`, `train/data/blind/` (never tracked) | `private/summarizer/…` (ignored; seeds are excerpts of real sessions, held-out sets are read only by their author and the Reviewer) |
| `train/*.py` (the checker, the contracts, the arms driver) | `training/summarizer/*.py` |
| `src/summarizer/` (the package) | `src/summarizer/summarizer/` |
| `docs/` | `training/summarizer/docs/` |
| `.venv/bin/python train/check_campaigns.py …` | from this repository's root: `PYTHONPATH=src/summarizer:training/summarizer python training/summarizer/check_campaigns.py …` |

Run campaign checks, the omission check and the monitor from this repository's root with
`PYTHONPATH=src/summarizer:training/summarizer` (the monitor also needs `src/monitor`).
