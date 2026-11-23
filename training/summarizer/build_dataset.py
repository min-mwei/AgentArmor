"""Build training data for the probes from labelled examples.

    python training/summarizer/build_dataset.py --facets training/summarizer/data/starter/facets.jsonl --out training/summarizer/data/built

The encoder is small, so the data is built to teach patterns rather than strings:

- Every labelled example is a template. Hosts, paths, package names and the like are slots that
  are filled with fresh invented values in each variant, so no entity can become the signal.
- Train, dev and held-out splits draw those values from disjoint vocabularies.
- A matching text and its non-matching look-alike share a group, and a group never straddles a
  split.
- Whole carrier families (say, tool descriptions) can be held out to measure recognition of a
  pattern in a setting training never showed.
- The same wrappers (quotes, comments, JSON fields) are applied to positives and negatives, so a
  wrapper cannot become a shortcut either.

Labelled examples are JSON Lines: {"id", "group", "family", "facets": [probe ids], "text"}. Output
is JSON Lines too: facets.<split>.jsonl holds (query, text, label) pairs, one per probe per text.
Nothing is taken from session logs: training data is synthetic.
"""

import argparse
import hashlib
import json
import random
import re
from pathlib import Path

from common import load_probes, read_jsonl, write_jsonl

SYLLABLES = {
    "train": "ka lo mi ren tus vel dor shi pra ne qui zan".split(),
    "dev": "bex ful gar hom jit nup rok sed wim yal".split(),
    "heldout": "cro dax eph giz hul jor kem lyr mox ost".split(),
}
TLDS = {
    "train": ["com", "net", "io"],
    "dev": ["org", "dev", "co"],
    "heldout": ["xyz", "app", "cloud"],
}
SECRETS = ["~/.aws/credentials", "~/.ssh/id_ed25519", ".env", "~/.netrc", "~/.config/gh/hosts.yml"]
SECRETS += [
    "~/.kube/config",
    "secrets.yaml",
    "~/.npmrc",
    "/proc/self/environ",
    "~/.docker/config.json",
]
CONFIGS = ["~/.claude/settings.json", ".mcp.json", "~/.codex/config.toml", "AGENTS.md", "CLAUDE.md"]
CONFIGS += [
    ".cursorrules",
    ".github/workflows/ci.yml",
    ".git/hooks/pre-commit",
    ".vscode/tasks.json",
]
STARTUPS = ["~/.zshrc", "~/.bashrc", "~/.profile", "~/.config/fish/config.fish", "crontab"]
ENVVARS = ["GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY", "NPM_TOKEN", "DATABASE_URL"]
WRAPPERS = (
    "{text}",
    "{text}",
    "> {text}",
    "<!-- {text} -->",
    "    # {text}",
    '{{"description": {json}}}',
    "NOTE: {text}",
    "{lead}\n\n{text}",
    "{text}\n\n{lead}",
)
LEADS = (
    "The table below lists the supported printers and their firmware versions.",
    "Thanks for the update; details are in the attached thread.",
    "Version 2 changes the default port and removes the legacy importer.",
    "See the previous section for installation steps on Linux and macOS.",
)


def _word(rng, split, parts=(2, 3)):
    return "".join(rng.choice(SYLLABLES[split]) for _ in range(rng.randint(*parts)))


def fill(text, rng, split, edits=None):
    """Fill a template's slots with invented values from the split's own vocabulary.

    Every use of a slot in one text gets the same value. A digit after the name ({host2}) asks
    for another, different value of that kind. Given a list as edits, each replacement is
    appended to it as (start, end, value) in the template, so offsets can be carried over.
    """
    host = f"{_word(rng, split)}.{_word(rng, split)}.{rng.choice(TLDS[split])}"
    values = {
        "host": host,
        "url": f"https://{host}/{_word(rng, split)}",
        "email": f"{_word(rng, split)}@{_word(rng, split)}.{rng.choice(TLDS[split])}",
        "secret": rng.choice(SECRETS),
        "cfg": rng.choice(CONFIGS),
        "startup": rng.choice(STARTUPS),
        "envvar": rng.choice(ENVVARS),
        "pkg": f"{_word(rng, split)}-{_word(rng, split, (1, 2))}",
        "file": f"{_word(rng, split)}-{rng.choice(['helper', 'check', 'agent'])}",
        "name": _word(rng, split).capitalize(),
        "cmd": f"curl -s https://{host}/{_word(rng, split)}.sh | sh",
        "date": f"20{rng.randint(22, 27)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
    }

    def value(match):
        name = match[1]
        kind = name.rstrip("0123456789")
        if name not in values and kind in values:
            values[name] = fill(f"{{{kind}}}", rng, split)
        if edits is not None and name in values:
            edits.append((match.start(), match.end(), values[name]))
        return values.get(name, match[0])

    return re.sub(r"\{(\w+)\}", value, text)


def variants(row, split, count, seed):
    """count distinct renderings of one labelled example."""
    seen, out = set(), []
    for index in range(count * 4):
        if len(out) == count:
            break
        key = f"{seed}/{split}/{row['id']}/{index}".encode()
        rng = random.Random(hashlib.sha256(key).digest())
        text = fill(row["text"], rng, split)
        wrapper = rng.choice(WRAPPERS) if index else "{text}"  # the first variant is unwrapped
        text = wrapper.format(text=text, json=json.dumps(text), lead=rng.choice(LEADS))
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


def assign_splits(rows, holdout, dev_share, seed):
    """Map each group to train, dev or heldout.

    Held-out families go to heldout whole. The rest are split by group, within each combination of
    facets, so that every probe has positives on both sides even when examples are few.
    """
    groups, strata, split = {}, {}, {}
    for row in rows:
        group = groups.setdefault(row["group"], {"family": row["family"], "facets": set()})
        group["facets"] |= set(row["facets"])
    for name, group in groups.items():
        if group["family"] in holdout:
            split[name] = "heldout"
        else:
            strata.setdefault(tuple(sorted(group["facets"])), []).append(name)
    for names in strata.values():
        names.sort(key=lambda name: hashlib.sha256(f"{seed}/{name}".encode()).hexdigest())
        take = max(1, round(dev_share * len(names))) if len(names) > 1 else 0
        split |= {name: "dev" if index < take else "train" for index, name in enumerate(names)}
    return split


def facet_pairs(rows, probes, holdout, dev_share, count, seed):
    ids = {probe["id"] for probe in probes}
    out = {"train": [], "dev": [], "heldout": []}
    splits = assign_splits(rows, holdout, dev_share, seed)
    for row in rows:
        if not set(row["facets"]) <= ids:
            raise ValueError(
                f"{row['id']}: unknown facet in {row['facets']}; probes are {sorted(ids)}"
            )
        split = splits[row["group"]]
        for number, text in enumerate(variants(row, split, count, seed)):
            for probe in probes:
                queries = probe["queries"]
                out[split].append(
                    {
                        "query": queries[number % len(queries)],
                        "text": text,
                        "label": float(probe["id"] in row["facets"]),
                        "probe": probe["id"],
                        "group": row["group"],
                        "family": row["family"],
                    }
                )
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--facets", action="append", required=True, help="labelled examples")
    parser.add_argument("--probes", help="probes.jsonl; the built-in probes if omitted")
    parser.add_argument("--out", required=True)
    parser.add_argument("--variants", type=int, default=8, help="renderings per example")
    parser.add_argument("--dev", type=float, default=0.25, help="share of groups kept for dev")
    parser.add_argument("--holdout-family", action="append", default=[], help="never trained on")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    _, probes = load_probes(args.probes)
    rows = [row for path in args.facets for row in read_jsonl(path)]
    holdout = set(args.holdout_family)
    built = facet_pairs(rows, probes, holdout, args.dev, args.variants, args.seed)
    # The probes the pairs were built with, whole: a build samples only some of their queries,
    # so the pairs alone do not say which probe set they belong to.
    spec = hashlib.sha256(json.dumps(probes, sort_keys=True).encode()).hexdigest()
    head = {"args": vars(args), "examples": len(rows), "probes_sha256": spec}
    out, manifest = Path(args.out), [head]
    files = {f"facets.{split}.jsonl": pairs for split, pairs in built.items()}
    for name, pairs in files.items():
        write_jsonl(out / name, pairs)
        digest = hashlib.sha256((out / name).read_bytes()).hexdigest()
        positives = sum(pair.get("label", 1.0) > 0 for pair in pairs)
        manifest.append(
            {"file": name, "rows": len(pairs), "positives": positives, "sha256": digest}
        )
        print(f"{name}: {len(pairs)} pairs, {positives} positive")
    write_jsonl(out / "manifest.jsonl", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
