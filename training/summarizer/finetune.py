"""Fine-tune EmbeddingGemma 2's text encoder so the summarizer's probes recognize their patterns.

    python training/summarizer/finetune.py --data training/summarizer/data/built --out training/summarizer/runs/NAME

Two objectives are trained together when their data is present:

- facets.train.jsonl: (probe query, text, label). A ranking loss (CoSENT) pulls text a probe
  describes above text it does not, including the benign look-alikes built as hard negatives.
- intent.train.jsonl: (agent action, user request). In-batch contrast pulls an action toward the
  request it served, which is what the summarizer's goal-drift comparison relies on. No tool
  writes this file: the pairs must be synthetic and accepted like any other batch, never mined
  from session logs.

The encoder is small and the aim is recognition of broad patterns, not recall of strings, so the
token embedding table stays frozen unless --train-embeddings is given, the learning rate is low,
and build_dataset.py has already randomized every entity. Both losses are wrapped so shorter
(Matryoshka) prefixes of the embedding keep working.

Afterwards the probes are calibrated on the dev split and the model is measured on train, dev and
heldout next to the base model. Runs on CUDA (NVIDIA, AMD ROCm), Apple silicon or CPU.

Before any weights are loaded the run is held to what it was given. The data directory must be
the one build_dataset.py wrote: every split file has the hash its manifest declares, no group
is in two splits, and the pairs were built with the probes in use. Every query and text is
counted with the model's own tokenizer under the prompt it is trained with, and a text the
encoder would see only the start of stops the run: past the cut its label would still stand
and its evidence would be gone. An output directory that already holds a run is never written
over. A finished run leaves receipt.json: the code, the base model, the inputs by hash, the
prompts and token limit, the seed, the objectives, how much was trained, and the outputs by
hash. A run without a receipt did not finish.
"""

import argparse
import hashlib
import json
from pathlib import Path

from bundle_manifest import manifest
from calibrate import calibrate_probes, identity
from common import (
    DOCUMENT_PROMPT,
    MAX_TOKENS,
    QUERY_PROMPT,
    load_model,
    load_probes,
    pick_device,
    plain,
    prompted_tokens,
)
from common import served as as_served
from evaluate import probe_report
from score_eval import bundle, code_version, model_identity, read_once, rows_of
from summarizer.embedder import MODEL
from summarizer.probes import dump

DIMS = [768, 512, 256, 128]
SPLITS = ("train", "dev", "heldout")


def freeze_token_embeddings(model):
    """Stop the vocabulary table from learning, so no single token can become the signal."""
    frozen = 0
    for name, parameter in model.named_parameters():
        if "embed_tokens" in name:
            parameter.requires_grad = False
            frozen += parameter.numel()
    if not frozen:
        raise RuntimeError("found no token embedding table to freeze")
    return frozen


def measure(model, probes, pairs):
    """Rows for every split that has pairs, from the pairs the run was bound to."""
    rows = []
    for split in sorted(pairs):
        if pairs[split]:  # no family was held out: there is nothing to measure there
            rows += probe_report(model, probes, pairs[split], split)
    return rows


def fresh(out):
    """Refuse an output directory that already holds a run: a run is never written over."""
    held = [name for name in ("receipt.json", "model", "metrics.jsonl") if (out / name).exists()]
    if held:
        raise SystemExit(f"{out} already holds a run ({', '.join(held)}): choose another --out")


def bound(data, probes, intent=None):
    """(pairs by split, intent pairs, record): the data, read once and held to its manifest.

    The bytes that are hashed are the bytes that are trained on. A missing manifest, a file
    that is not the one it declares, a split file it does not list, a group in two splits, or
    pairs built with other probes each stop the run, before any model is loaded.
    """
    manifest = data / "manifest.jsonl"
    if not manifest.is_file():
        raise SystemExit(
            f"{data} has no manifest.jsonl: build it with training/summarizer/build_dataset.py"
        )
    raw, manifest_sha = read_once(manifest)
    listed = rows_of(raw)
    declared = {row["file"]: row for row in listed if "file" in row}
    spec = hashlib.sha256(json.dumps(probes, sort_keys=True).encode()).hexdigest()
    built_with = next((row["probes_sha256"] for row in listed if "probes_sha256" in row), None)
    if built_with is None:
        raise SystemExit(
            f"{data}: its manifest does not name the probes it was built with; rebuild it"
        )
    if built_with != spec:
        raise SystemExit(f"{data} was built with other probes than this run was given")
    wanted = {f"facets.{split}.jsonl" for split in SPLITS}
    present = {path.name for path in data.glob("facets.*.jsonl")}
    if set(declared) != wanted or present != wanted:
        odd = ", ".join(sorted((set(declared) | present) ^ wanted)) or "nothing listed"
        raise SystemExit(f"{data}: the manifest and the split files do not agree on {odd}")
    pairs, files, wrong = {}, {}, []
    for split in SPLITS:
        name = f"facets.{split}.jsonl"
        raw, files[name] = read_once(data / name)
        pairs[split] = rows_of(raw)
        if files[name] != declared[name]["sha256"] or len(pairs[split]) != declared[name]["rows"]:
            wrong.append(name)
    if wrong:
        raise SystemExit(f"{data}: not the bytes the manifest declares: {', '.join(wrong)}")
    groups = {split: {pair["group"] for pair in pairs[split]} for split in SPLITS}
    for first, second in (("train", "dev"), ("train", "heldout"), ("dev", "heldout")):
        if shared := groups[first] & groups[second]:
            raise SystemExit(f"{data}: {len(shared)} groups are in both {first} and {second}")
    queries = {probe["id"]: set(probe["queries"]) for probe in probes}
    if any(
        pair["query"] not in queries.get(pair["probe"], ()) for p in pairs.values() for pair in p
    ):
        raise SystemExit(f"{data} was built with other probes than this run was given")
    record = {"manifest_sha256": manifest_sha, "files": files, "probes_sha256": spec}
    record["pairs"] = {split: len(pairs[split]) for split in SPLITS}
    record["groups"] = {split: len(groups[split]) for split in SPLITS}
    chosen = []
    if intent is not None:
        raw, digest = read_once(intent)
        chosen = rows_of(raw)
        record["intent"] = {"file": str(intent), "sha256": digest, "pairs": len(chosen)}
    return pairs, chosen, record


def token_budget(pairs, intent_pairs, probes, count, limit=MAX_TOKENS):
    """The longest input of each kind under its prompt, or SystemExit when one would be cut.

    count(prompt name, texts) is common.prompted_tokens' counter. Everything the run will encode
    is counted. That is every split, not only the one trained on, since a text cut during
    calibration or measurement loses its evidence in the same way. It is also every query and
    negative of every probe: measurement encodes them all, and a build puts only some into pairs.
    """
    every = [pair for split in pairs.values() for pair in split]
    queries = {pair["query"] for pair in every} | {pair["anchor"] for pair in intent_pairs}
    for probe in probes:
        queries |= set(probe["queries"]) | set(probe.get("negatives") or ())
    queries = sorted(queries)
    texts = sorted({pair["text"] for pair in every} | {pair["positive"] for pair in intent_pairs})
    found = {"limit": limit}
    for kind, prompt, items in (("query", QUERY_PROMPT, queries), ("text", DOCUMENT_PROMPT, texts)):
        lengths = count(prompt, items)
        found[kind] = {"count": len(items), "longest": max(lengths, default=0)}
        found[kind]["over"] = sum(length > limit for length in lengths)
    over = found["query"]["over"] + found["text"]["over"]
    if over:
        longest = max(found["query"]["longest"], found["text"]["longest"])
        raise SystemExit(
            f"{over} inputs are longer than the {limit} tokens the encoder is given (the longest "
            f"is {longest}). Each would be cut, and what comes after the cut would be lost while "
            "its label stayed. Shorten them, or label and train them piece by piece."
        )
    return found | {"cut": "nothing: a longer input stops the run"}


def check_inputs(data, intent=None):
    """Refuse training data nobody chose, before any model is loaded.

    An older build_dataset.py could leave intent.*.jsonl in the data directory, mined from real
    session logs. Such a file is never picked up by its name: the pairs to use are named with
    --intent, and a leftover one that was not named stops the run so it cannot be used by
    accident. It is not deleted; that is its owner's call.
    """
    chosen = Path(intent).resolve() if intent else None
    if chosen is not None and not chosen.is_file():
        raise SystemExit(f"--intent {intent}: no such file")
    stray = [path for path in sorted(Path(data).glob("intent.*.jsonl")) if path.resolve() != chosen]
    if stray:
        names = ", ".join(path.name for path in stray)
        raise SystemExit(
            f"{data} holds {names}, which this run was not told to use. Pairs mined from "
            "session logs must not be trained on: move the files away, or pass an accepted "
            "synthetic file with --intent."
        )
    return chosen


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", required=True, help="directory written by build_dataset.py")
    parser.add_argument("--out", required=True, help="run directory; the model goes in out/model")
    parser.add_argument("--base", default=MODEL, help="Hugging Face id or local model directory")
    parser.add_argument(
        "--probes", help="probes.jsonl the data was built with; built-in if omitted"
    )
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--max-steps", type=int, default=-1, help="stop early; for smoke runs")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--recall", type=float, default=0.9, help="calibration target")
    parser.add_argument("--device", default="auto", help="auto, cuda, mps or cpu")
    parser.add_argument("--train-embeddings", action="store_true")
    parser.add_argument("--no-matryoshka", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--intent",
        help="accepted synthetic (anchor, positive) pairs for the second objective; never "
        "session logs. Nothing in --data is used for it unless named here",
    )
    args = parser.parse_args(argv)

    import torch
    from datasets import Dataset
    from sentence_transformers import (
        SentenceTransformerTrainer,
        SentenceTransformerTrainingArguments,
    )
    from sentence_transformers.sentence_transformer.losses import (
        CoSENTLoss,
        MatryoshkaLoss,
        MultipleNegativesRankingLoss,
    )

    data, out = Path(args.data), Path(args.out)
    fresh(out)
    intent = check_inputs(data, args.intent)
    _, probes = load_probes(args.probes)
    pairs, intent_pairs, inputs = bound(data, probes, intent)
    count, prompt_texts = prompted_tokens(args.base)
    tokens = token_budget(pairs, intent_pairs, probes, count)
    device = pick_device(args.device)
    # The base model is measured the way it is served too, so base and tuned rows compare.
    base = load_model(args.base, device)
    base_rows = [{"model": "base", **row} for row in measure(base, probes, pairs)]
    receipt = {"run": "finetune", "code": code_version(), "seed": args.seed}
    receipt["base"] = model_identity(args.base, base, device)
    model = load_model(args.base, device, training=True)
    total = sum(parameter.numel() for parameter in model.parameters())
    frozen = 0 if args.train_embeddings else freeze_token_embeddings(model)
    print(f"device {device}; training {total - frozen:,} of {total:,} parameters")

    def wrap(loss):
        return loss if args.no_matryoshka else MatryoshkaLoss(model, loss, matryoshka_dims=DIMS)

    def served(rows, columns):
        """Rows for the trainer with their texts as the runtime serves them (common.plain)."""
        made = [{c: row[c] for c in columns} for row in rows]
        for column in (c for c in columns if c != "label"):
            for row, text in zip(made, plain(model, [row[column] for row in made]), strict=True):
                row[column] = text
        return made

    facets = pairs["train"]
    datasets = {"facets": Dataset.from_list(served(facets, ("query", "text", "label")))}
    losses, objectives = {"facets": wrap(CoSENTLoss(model))}, {"facets": "CoSENTLoss"}
    query, document = model.prompts[QUERY_PROMPT], model.prompts[DOCUMENT_PROMPT]
    prompts = {"facets": {"query": query, "text": document}}
    if intent_pairs:
        datasets["intent"] = Dataset.from_list(served(intent_pairs, ("anchor", "positive")))
        losses["intent"] = wrap(MultipleNegativesRankingLoss(model))
        objectives["intent"] = "MultipleNegativesRankingLoss"
        prompts["intent"] = {"anchor": query, "positive": document}

    autocast = device == "cuda" and torch.cuda.is_bf16_supported()
    trainer = SentenceTransformerTrainer(
        model=model,
        args=SentenceTransformerTrainingArguments(
            output_dir=str(out / "checkpoints"),
            num_train_epochs=args.epochs,
            max_steps=args.max_steps,
            per_device_train_batch_size=args.batch_size,
            learning_rate=args.lr,
            warmup_steps=0.1,  # a share of the run
            weight_decay=0.01,
            prompts=prompts,
            bf16=autocast,
            use_cpu=device == "cpu",
            logging_steps=10,
            save_strategy="no",
            report_to="none",
            seed=args.seed,
        ),
        train_dataset=datasets,
        loss=losses,
    )
    trainer.train()

    model.save_pretrained(str(out / "model"))
    # Calibrate and measure the saved model the way it will be served: loaded from disk, in the
    # inference precision of this device, not the float32 copy that was just trained.
    served = load_model(str(out / "model"), device)
    dev = pairs["dev"]
    measured = identity(served, device, dev)
    calibrated = calibrate_probes(served, probes, dev, args.recall, measured)
    dump(calibrated, out / "model" / "probes.jsonl")
    rows = base_rows + [{"model": "tuned", **row} for row in measure(served, calibrated, pairs)]
    with open(out / "metrics.jsonl", "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
            print(json.dumps(row))
    # The inputs are read again: a run whose data changed under it leaves no receipt.
    if bound(data, probes, intent)[2] != inputs:
        raise SystemExit(f"{data} changed while the run was using it: no receipt is written")
    # The weights are float32 while they are trained. On CUDA the forward pass may run under
    # bfloat16 autocast all the same, which is a different computation and is said here.
    receipt["training_precision"] = {
        "parameters": "float32",
        "autocast": "bfloat16" if autocast else None,
    }
    receipt |= {"device": device, "served": measured}
    receipt |= {"inputs": inputs, "prompts": prompt_texts, "tokens": tokens}
    receipt["objectives"] = objectives | {"matryoshka": None if args.no_matryoshka else DIMS}
    receipt["trained"] = {
        "epochs": args.epochs,
        "max_steps": args.max_steps,
        "steps": trainer.state.global_step,
        "batch_size": args.batch_size,
        "learning_rate": args.lr,
        "pairs": {name: len(rows) for name, rows in datasets.items()},
        "parameters": total - frozen,
        "token_embeddings": "trained" if args.train_embeddings else "frozen",
    }
    receipt["calibration"] = {"split": "dev", "recall": args.recall, "groups": measured["groups"]}
    receipt["outputs"] = {"model": bundle(out / "model")}
    receipt["outputs"]["metrics_sha256"] = read_once(out / "metrics.jsonl")[1]
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    # The directory's name for the runtime: a manifest of what the run left (bundle_manifest).
    name, _ = manifest(out, lambda directory: as_served(served))
    print(f"model and calibrated probes in {out / 'model'}; metrics and receipt in {out}")
    print(f"bundle {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
