"""Turn logged sessions into seeds for synthetic training data.

    python training/summarizer/export_seeds.py --sessions ~/.summarizer/sessions \\
        --out training/summarizer/data/seeds/sessions.jsonl

A seed is a short piece of text a real session's agent was shown, with what the summarizer tagged
it as. Seeds show the Data Engineer what real carriers, phrasing and benign look-alikes are like;
new templates are written in their image. Seeds are never trained on and stay in
training/summarizer/data/seeds/, which Git ignores.

Entities are masked on the way out: hosts, URLs, addresses and mailboxes become slots and home
directories become ~. Secrets were already redacted when the session was logged. What is left is
still text from your sessions (file contents, pages, tool output), so read a seed file before you
hand it to anyone.

Each line is {"id", "block", "carrier", "labels", "level", "text"}: block is an opaque name for
the session the excerpt came from, and carrier is where the text came from (file, web, mcp,
shell, instructions, subagent, compaction, tool).
"""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from check_data import RUN, benchset_texts, runs
from common import read_jsonl, write_jsonl
from summarizer.facts import observables
from summarizer.store import EVENTS, read_events

_SLOTS = {"domain": "host", "ip": "host", "url": "url", "email": "email"}
_HOME = re.compile(r"/(?:Users|home)/[^/\s\"']+")


def mask(text):
    """Replace the entities in session text with slots; the same value gets the same slot."""
    slots, counts = {}, Counter()
    for _, kind, value in sorted(observables(text)):  # numbered in the order they appear
        if value.lower() not in slots:
            name = _SLOTS[kind]
            counts[name] += 1
            slots[value.lower()] = f"{{{name}{counts[name] if counts[name] > 1 else ''}}}"
    # Longest first, so a URL is replaced whole before the host inside it is looked for.
    for value in sorted(slots, key=len, reverse=True):
        text = re.sub(re.escape(value), slots[value], text, flags=re.I)
    return _HOME.sub("~", text)


def seeds(sessions, runtime, min_chars=40, exclude=()):
    """Masked excerpts of every logged session under sessions, each exported once.

    exclude is a set of word runs (check_data.runs); an excerpt that shares one is left out, so
    text an agent read from AgentArmor's benchset or from the starter examples never seeds data.
    """
    from summarizer.summarize import Session

    found = {}
    for log in sorted(Path(sessions).expanduser().glob(f"*/*/{EVENTS}")):
        session = Session(log.parent.parent.name, log.parent.name, runtime)
        for record in read_events(log)[0]:
            session.add(record)
        session.flush()
        # An opaque name for the session a seed came from. Seeds of one block are excerpts of the
        # same source, so a block is the unit that must not straddle training and evaluation.
        name = f"{log.parent.parent.name}/{log.parent.name}".encode()
        block = "block-" + hashlib.sha256(name).hexdigest()[:10]
        for entry in session.summary():
            if entry.get("type") != "content" or len(entry["text"]) < min_chars:
                continue
            if entry.get("aside"):  # member names and tool flags are schema, not seed material
                continue
            text = mask(entry["text"])
            if exclude and runs(text, RUN) & exclude:
                continue
            key = "seed-" + hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:12]
            seed = {"id": key, "block": block, "carrier": entry["source"]}
            seed["labels"] = entry["labels"]
            found.setdefault(key, seed | {"level": entry["level"], "text": text})
    return list(found.values())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sessions", required=True, help="a spool's sessions directory")
    parser.add_argument("--out", required=True, help="seed file to write (keep it out of Git)")
    parser.add_argument("--benign", type=int, default=24, help="unmarked excerpts kept per session")
    parser.add_argument(
        "--benchset", help="AgentArmor benchset: leave out anything that repeats it"
    )
    parser.add_argument("--exclude", action="append", default=[], help="examples to leave out too")
    parser.add_argument(
        "--drop", help="leave out excerpts matching this pattern, such as your name"
    )
    args = parser.parse_args(argv)

    from summarizer.embedder import HashEmbedder
    from summarizer.summarize import Config, Runtime

    # Wording rules and provenance do the tagging here; no model is loaded to export seeds.
    config = Config(max_marked=100_000, max_content=args.benign)
    texts = benchset_texts(args.benchset) if args.benchset else []
    texts += [row["text"] for path in args.exclude for row in read_jsonl(path)]
    exclude = set().union(*(runs(text, RUN) for text in texts)) if texts else set()
    rows = seeds(args.sessions, Runtime(HashEmbedder(), config=config), exclude=exclude)
    if args.drop:
        rows = [row for row in rows if not re.search(args.drop, row["text"], re.I)]
    write_jsonl(args.out, rows)
    levels = Counter(row["level"] for row in rows)
    carriers = Counter(row["carrier"] for row in rows)
    print(json.dumps({"seeds": len(rows), "levels": dict(levels), "carriers": dict(carriers)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
