"""Shared pieces of the training tools: files, devices, the model, probe similarity."""

import json
from pathlib import Path

import numpy as np

from summarizer.embedder import (
    DOCUMENT_PROMPT,
    MODEL,
    QUERY_PROMPT,
    REVISION,
    WINDOW_TOKENS,
    Embedder,
    best_window,
    controls_of,
    inert,
    unit,
)
from summarizer.probes import load as load_probes

__all__ = ["DOCUMENT_PROMPT", "QUERY_PROMPT", "load_probes"]
# The longest input a training run takes, in tokens: a longer one stops the run
# (finetune.token_budget), since a pair cut while it is trained on keeps its label and loses
# its evidence. It is not what a measurement encodes at: that is the runtime's window.
MAX_TOKENS = 512


def read_jsonl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def pick_device(name="auto"):
    """cuda covers NVIDIA and AMD (ROCm) builds of PyTorch; mps is Apple silicon."""
    import torch

    if name != "auto":
        return name
    if torch.cuda.is_available():
        return "cuda"
    return "mps" if torch.backends.mps.is_available() else "cpu"


def load_model(model=MODEL, device="auto", revision=None, training=False):
    """EmbeddingGemma 2 with only its text encoder. Training runs in float32 everywhere."""
    import os

    import torch
    from sentence_transformers import SentenceTransformer

    device = pick_device(device)
    half = not training and (
        device == "mps" or (device == "cuda" and torch.cuda.is_bf16_supported())
    )
    loaded = SentenceTransformer(
        model,
        revision=None if os.path.isdir(model) else (revision or REVISION),
        device=device,
        config_kwargs={"vision_config": None, "audio_config": None},
        model_kwargs={"torch_dtype": torch.bfloat16 if half else torch.float32},
    )
    loaded.max_seq_length = MAX_TOKENS if training else WINDOW_TOKENS
    return loaded


def served(model):
    """The runtime's embedder around a loaded model. Everything measured goes through it."""
    made = getattr(model, "summarizer_embedder", None)
    if made is None:
        made = Embedder.around(model)
        model.summarizer_embedder = made
    return made


def plain(model, texts):
    """Texts as the runtime hands them to the model: no string in them is a control token.

    The runtime's embedder does this to every text (embedder.inert). Anything that embeds or
    trains through the model directly has to do the same, or it measures and fits inputs the
    runtime never serves, and stops on a text that holds "<|image|>".
    """
    pattern = getattr(model, "summarizer_controls", ...)
    if pattern is ...:
        pattern = controls_of(model.tokenizer)
        model.summarizer_controls = pattern
    return [inert(text, pattern) for text in texts]


def prompted_tokens(model=MODEL, revision=None):
    """(count, prompts) from a model's tokenizer and prompt strings alone; no weights are loaded.

    count(prompt name, texts) gives, for each text, how many tokens the encoder would be handed
    for it under that prompt, uncut. prompts are the two prompt strings in use. A text counted
    above MAX_TOKENS is one the encoder would see only the start of.
    """
    import os

    from transformers import AutoTokenizer

    local = os.path.isdir(model)
    revision = None if local else (revision or REVISION)
    if local:
        config = Path(model) / "config_sentence_transformers.json"
    else:
        from huggingface_hub import hf_hub_download

        config = hf_hub_download(model, "config_sentence_transformers.json", revision=revision)
    prompts = json.loads(Path(config).read_text(encoding="utf-8"))["prompts"]
    tokenizer = AutoTokenizer.from_pretrained(model, revision=revision)
    controls = controls_of(tokenizer)

    def count(prompt_name, texts):
        # Counted as served: a control-token string in a text is text, and takes more tokens.
        prompted = [prompts[prompt_name] + inert(text, controls) for text in texts]
        if not prompted:
            return []
        return [len(ids) for ids in tokenizer(prompted, truncation=False)["input_ids"]]

    return count, {name: prompts[name] for name in (QUERY_PROMPT, DOCUMENT_PROMPT)}


def encode(model, texts, prompt_name):
    """Vectors as the runtime makes them, by the runtime's own embedder (served()).

    Every score a probe is evaluated, calibrated or fitted on comes through here, so it is
    the vector the summarizer would serve: one text at a time, and a text longer than the
    model's window in windows. A batch here once gave other vectors than the runtime's, and
    examples within a thousandth of a threshold were tagged differently in a measurement
    than in service (MR89-1); a text cut here at 512 tokens was served whole (MR113-2).
    The minibatch a model is trained with is another matter and is not set here.
    """
    return served(model)._encode(texts, prompt_name)


def inference(model):
    """How texts are encoded and scored for a measurement, for its receipt: the policy,
    the batch, and the window this model's processor in fact has."""
    info = served(model).info
    told = {"policy": info["policy"], "batch": info["batch"]}
    return told | {"max_tokens": info["window_tokens"], "max_windows": info["max_windows"]}


class PartialInput(ValueError):
    """A text to be scored was longer than the model can be given, even in windows."""


def spans(model, texts):
    """({windows: how many of the texts are embedded as that many}, the rows of the texts
    of which some part is in no window), for a receipt. A calibration fitted on texts of
    one window has measured nothing about the best of several, and a text embedded only in
    part was not measured at all beyond where its windows end. Nothing is encoded here."""
    embedder = served(model)
    counts, cut = {}, []
    for row, text in enumerate(texts):
        pieces, _, left_out = embedder._pieces(inert(text, embedder.controls), DOCUMENT_PROMPT)
        counts[len(pieces)] = counts.get(len(pieces), 0) + 1
        if left_out:
            cut.append(row)
    return {str(windows): counts[windows] for windows in sorted(counts)}, cut


def document_scores(model, texts, score):
    """(score(vectors) for each text, the rows of the texts embedded only in part).

    A text is scored as the runtime scores lower-trust content: one embedded as several
    windows takes the best of them (embedder.best_window). One too long for the windows
    there are is embedded as far as they reach, and its score is of that part alone: the
    runtime reports such a text as a gap in coverage, and so must whoever measures with
    this. The rows are the encoder's own account of its call, not a second reading.
    """
    embedder = served(model)
    vectors = embedder.documents(texts)
    parts, cut = dict(embedder.parts), sorted(embedder.cut)  # of this call
    scores = [best_window(score, vector, parts.get(row)) for row, vector in enumerate(vectors)]
    return (np.stack(scores) if scores else np.zeros((0, 0), dtype=np.float32)), cut


def probe_similarities(model, probes, texts):
    """Similarity of each text to each probe, computed exactly as the runtime computes it,
    for texts the model was given whole. One it was given only part of stops the call
    (PartialInput): a threshold fitted on its score, or a rate that counts it, would
    describe a text nobody scored. A measurement that is to count such texts uses
    probe_scores() and reports them.
    """
    scores, cut = probe_scores(model, probes, texts)
    if cut:
        info = served(model).info
        raise PartialInput(
            f"{len(cut)} of {len(texts)} texts are longer than the model can be given, even "
            f"as {info['max_windows']} windows of {info['window_tokens']} tokens (the first "
            f"is number {cut[0] + 1}). Their scores would be of their beginnings only. "
            "Cut them into pieces as the runtime does, or count them with probe_scores()."
        )
    return scores


def probe_scores(model, probes, texts):
    """(similarity of each text to each probe, the rows of the texts embedded only in part).

    A probe that carries "negatives" (an offline experiment, not the runtime) is scored by
    contrast: similarity to what it describes less "contrast" (1.0 unless given) times
    similarity to its negatives, the same topic as a warning, a policy or a description.
    A window is scored that way like any text, and a long text takes its best window.
    """
    rows = [encode(model, probe["queries"], QUERY_PROMPT).mean(axis=0) for probe in probes]
    rows = unit(np.stack(rows))
    against = {
        column: unit(encode(model, probe["negatives"], QUERY_PROMPT).mean(axis=0)[None, :])
        for column, probe in enumerate(probes)
        if probe.get("negatives")
    }

    def score(vectors):
        scores = vectors @ rows.T
        for column, other in against.items():
            scores[:, column] -= probes[column].get("contrast", 1.0) * (vectors @ other.T)[:, 0]
        return scores

    scores, cut = document_scores(model, texts, score)
    return (scores if len(texts) else np.zeros((0, len(probes)), dtype=np.float32)), cut


def auc(scores, labels):
    """Chance that a positive outranks a negative; ties count half. None without both classes."""
    scores, labels = np.asarray(scores, dtype=np.float64), np.asarray(labels, dtype=bool)
    positive, negative = scores[labels], scores[~labels]
    if not len(positive) or not len(negative):
        return None
    wins = (positive[:, None] > negative[None, :]).sum()
    ties = (positive[:, None] == negative[None, :]).sum()
    return float((wins + 0.5 * ties) / (len(positive) * len(negative)))
