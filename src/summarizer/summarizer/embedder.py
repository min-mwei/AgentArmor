"""Text embedders. The model runtime is imported here and nowhere else."""

import hashlib
import re

import numpy as np

from . import MODEL, REVISION, bundle

QUERY_PROMPT = "SearchQuery"  # "task: search result | query: "
DOCUMENT_PROMPT = "Document"  # "title: none | text: "


# Texts are encoded one at a time. In a batch a text is padded to its longest neighbour and
# computed beside the others, and its vector then depends on what it was batched with: by
# up to 0.003 per component with bfloat16 on Apple silicon, and by about 2e-7 with float32
# on a CPU. A session is embedded as its events arrive and embedded again, in other
# batches, when a restarted daemon rebuilds it from its log; that difference was enough to
# carry a similarity across a threshold, so that the rebuilt session lost an indicator and
# a level the live one had (MR87-1). Alone, a text gives the same bytes whatever came
# before it. This costs speed where many texts are encoded at once (a rebuild, a
# measurement); it is part of the embedder's identity, reported as "batch".
ENCODE_BATCH = 1

# A text is cut off by the model's processor at its window, with no word said: what lies
# beyond is in no vector. So a text longer than the window is embedded as several windows
# that together hold all of it, neighbours sharing a few tokens so that nothing which
# straddles a boundary is seen only in halves. Its vector is the mean of its windows', and
# the windows' own vectors are kept beside it for whoever needs the best match of any part
# (parts). A text too long even for that many windows is embedded as far as they reach and
# is reported (cut): the rest of it was compared with nothing. The window is the number of
# tokens that in fact reach the encoder, measured on the loaded model: what is asked for
# and what a saved tokenizer allows can differ, and neither is taken on trust.
#
# Sixteen windows hold the longest text a session hands the model, even when the window is
# the 512 tokens of a tokenizer saved with a fine-tuned model: an action of 1,200
# characters, each of them four tokens, is some 4,800 tokens and eleven such windows.
MAX_WINDOWS = 16
WINDOW_OVERLAP = 32
WINDOW_MARGIN = 8  # tokens kept free: a piece cut out of a text can tokenize a little longer
WINDOW_TOKENS = 1024  # what the processor is asked to pass; what it does pass is measured

# All of the above is one policy with a name: how a text becomes vectors (one at a time, in
# windows, their mean) and how it is scored against a direction (its best window). The
# runtime serves under it, train/ measures and calibrates through the same two functions
# (Embedder._encode and best_window), and a summary, a measurement and a calibration each
# say which policy made them. Numbers made under one say nothing sure under another, so a
# change to any of it is a new name.
POLICY = "windows/1"


def best_window(score, vector, parts=None):
    """What a text scores. score(vectors) gives, for each row, one number for each of some
    directions (probes). A text embedded as one vector scores what that vector scores. One
    embedded as several windows (parts) takes for each direction the best of its windows:
    what a probe looks for can sit in one part of a long text, and the mean of all its
    parts would thin it out."""
    if parts is None:
        return score(vector[None, :])[0]
    return score(parts).max(axis=0)


def windows(offsets, room, overlap=WINDOW_OVERLAP):
    """Character spans that cover a text in runs of at most room tokens.

    offsets are the (start, end) character positions of the text's tokens, in order.
    Neighbouring spans share overlap tokens, and together the spans hold every token.
    """
    if not offsets:
        return []
    room = max(1, room)
    step = max(1, room - min(overlap, room - 1))
    spans, first = [], 0
    while True:
        last = min(first + room, len(offsets))
        spans.append((offsets[first][0], offsets[last - 1][1]))
        if last == len(offsets):
            return spans
        first += step


def unit(vectors):
    vectors = np.asarray(vectors, dtype=np.float32)
    return vectors / np.maximum(np.linalg.norm(vectors, axis=-1, keepdims=True), 1e-12)


def control_pattern(tokens):
    """A pattern for the strings a tokenizer reads as control tokens, or None when it has none."""
    tokens = sorted({token for token in tokens if token}, key=len, reverse=True)
    return re.compile("|".join(re.escape(token) for token in tokens)) if tokens else None


_UNPAIRED = re.compile("[\ud800-\udfff]")  # half of a surrogate pair


def inert(text, pattern):
    """Session text as the model can take it, with nothing in it read as a control token.

    Half of a surrogate pair is not a character, and the tokenizer refuses any text that
    holds one. A tool that cuts its output inside an emoji leaves one behind, and the JSON a
    tool returns can carry one as an escape. Each becomes U+FFFD, the mark for what could
    not be read, so the rest of the text is embedded.

    A session also holds the tokenizer's control strings as ordinary characters: "<|image|>"
    in a file about models, in a transcript, or planted. The model's own documentation gives
    "<|image|>", "<|audio|>" and "<|video|>" a meaning inside text, as the places where media
    go, and offers no way to write them literally: given to the model as they stand, the
    processor fails for want of an image. Any other control token ("<bos>", "<|turn>") would
    enter the input as that token, where the text has only its letters. A space after its
    first character leaves a string readable and makes it text. Whatever is still a control
    token after that is replaced by a plain mark.
    """
    text = _UNPAIRED.sub("\ufffd", text)
    if pattern is None:
        return text
    for _ in range(2):  # a control token can sit inside what is left of another
        text = pattern.sub(lambda found: found.group()[0] + " " + found.group()[1:], text)
        if not pattern.search(text):
            return text
    return pattern.sub("(control token)", text)


def controls_of(tokenizer):
    """The control-token pattern of a tokenizer, checked against the tokenizer itself.

    Raises ValueError if text made inert can still reach the model as a control token: that
    is to be known when a model is loaded, not when a session happens to hold the string.
    """
    tokens = sorted({*tokenizer.all_special_tokens, *tokenizer.get_added_vocab()})
    pattern = control_pattern(tokens)
    control = set(tokenizer.all_special_ids) | set(tokenizer.get_added_vocab().values())
    for token in tokens:
        for text in (token, f"a {token} b", token + token, f"<{token}>"):
            ids = tokenizer(inert(text, pattern), add_special_tokens=False)["input_ids"]
            if control.intersection(ids):
                raise ValueError(
                    f"the control token {token!r} is not made inert for this tokenizer"
                )
    return pattern


class Embedder:
    """EmbeddingGemma 2 with only its 270M text encoder loaded.

    model is a Hugging Face id (loaded at the pinned revision) or a local directory holding a
    fine-tuned copy. Queries and documents use the model's asymmetric retrieval prompts; training
    in train/ uses the same two prompts.
    """

    def __init__(
        self,
        model=MODEL,
        revision=None,
        device=None,
        local_files_only=False,
        max_tokens=WINDOW_TOKENS,
    ):
        import os

        import torch
        from sentence_transformers import SentenceTransformer

        if device is None:
            mps = torch.backends.mps.is_available()
            device = "cuda" if torch.cuda.is_available() else "mps" if mps else "cpu"
        # The model card rules out float16; bfloat16 is its recommended default off the CPU.
        half = device == "mps" or (device == "cuda" and torch.cuda.is_bf16_supported())
        dtype = torch.bfloat16 if half else torch.float32
        local = os.path.isdir(model)
        revision = None if local else (revision or REVISION)
        self.model = SentenceTransformer(
            model,
            revision=revision,
            device=device,
            local_files_only=local_files_only,
            config_kwargs={"vision_config": None, "audio_config": None},
            model_kwargs={"torch_dtype": dtype},
        )
        self.model.max_seq_length = max_tokens
        # A directory is named by its manifest, when it is what its manifest lists
        # (bundle.py). Otherwise it is "local", and unadmitted says why.
        self.bundle, self.unadmitted = bundle.read(model) if local else (None, None)
        self._measure(model, revision or "local", device, dtype)
        if self.bundle is not None:
            self.unadmitted = bundle.unlike(self.bundle, self.found())
            if self.unadmitted is None:
                self.info["revision"] = "bundle:" + bundle.name(self.bundle)
            else:
                self.bundle = None

    @classmethod
    def around(cls, loaded, model="loaded", revision="local"):
        """The embedder for a model that is already loaded, as train/ loads one.

        Nothing of the model is changed. A measurement or a calibration made through this
        encodes and scores as the runtime does, because it is the runtime's code that does
        it, at the window this model's own processor has.
        """
        made = cls.__new__(cls)
        made.model, made.bundle, made.unadmitted = loaded, None, None
        first = next(loaded.parameters())
        made._measure(model, revision, first.device.type, first.dtype)
        return made

    def _measure(self, model, revision, device, dtype):
        # Every string the tokenizer reads as a control token and not as text (see inert()).
        self.controls = controls_of(self.model.tokenizer)
        # How many tokens reach the encoder, and how many of them each prompt takes: both
        # measured on this model, with a text far longer than any window.
        self.window = self._reaching("word " * (4 * WINDOW_TOKENS), DOCUMENT_PROMPT)
        self.overhead = {name: self._whole("", name) for name in (QUERY_PROMPT, DOCUMENT_PROMPT)}
        self.parts, self.cut, self.tokens = {}, set(), []
        self.dim = self.documents(["dimension probe"]).shape[1]
        self.info = {
            "backend": "embeddinggemma",
            "model": model,
            "revision": revision,
            "device": device,
            "dtype": str(dtype).removeprefix("torch."),
            "dim": self.dim,
            "policy": POLICY,
            "batch": ENCODE_BATCH,
            "window_tokens": self.window,
            "max_windows": MAX_WINDOWS,
        }

    def found(self):
        """What this model turned out to be, in the names a manifest uses (bundle.SERVED)."""
        prompts = {name: self.model.prompts[name] for name in (QUERY_PROMPT, DOCUMENT_PROMPT)}
        found = {"dim": self.dim, "prompts": prompts, "policy": POLICY}
        found |= {"window_tokens": self.window, "max_windows": MAX_WINDOWS}
        return found | {"batch": ENCODE_BATCH}

    def _reaching(self, text, prompt_name):
        """How many tokens of a text, with its prompt, the processor passes to the encoder."""
        prompt = self.model.prompts[prompt_name]
        return int(self.model.preprocess([text], prompt=prompt)["input_ids"].shape[1])

    def _whole(self, text, prompt_name):
        """How many tokens a text has with its prompt when nothing is cut off."""
        prompt, uncut = self.model.prompts[prompt_name], {"text": {"truncation": False}}
        made = self.model.preprocess([text], prompt=prompt, processing_kwargs=uncut)
        return int(made["input_ids"].shape[1])

    def _pieces(self, text, prompt_name):
        """(the pieces a text is embedded as, its tokens with the prompt, whether some of it
        is in no piece). One piece, the text itself, unless it is longer than the window."""
        tokens = self._whole(text, prompt_name)
        if tokens <= self.window:
            return [text], tokens, False
        offsets = self.model.tokenizer(
            text, add_special_tokens=False, return_offsets_mapping=True, truncation=False
        )["offset_mapping"]
        room = self.window - self.overhead[prompt_name] - WINDOW_MARGIN
        for _ in range(4):  # a piece that still tokenizes too long gets a smaller window
            pieces = [text[start:end] for start, end in windows(offsets, room)]
            if all(self._whole(piece, prompt_name) <= self.window for piece in pieces):
                break
            room -= 4 * WINDOW_MARGIN
        else:
            return [text], tokens, True  # no window holds it: embedded as the processor cuts it
        return pieces[:MAX_WINDOWS], tokens, len(pieces) > MAX_WINDOWS

    def _encode(self, texts, prompt_name):
        """One vector for each text. What the last call did beyond that is kept on the
        embedder for its caller: parts, {row: the vectors of that text's windows} for a
        text embedded as several; cut, the rows of which some part is in no window; and
        tokens, each text's count with its prompt."""
        plan = [self._pieces(inert(text, self.controls), prompt_name) for text in texts]
        flat = [piece for pieces, _, _ in plan for piece in pieces]
        self.parts, self.cut, self.tokens = {}, set(), [tokens for _, tokens, _ in plan]
        if not flat:
            return np.zeros((0, getattr(self, "dim", 0)), dtype=np.float32)
        vectors = self.model.encode(
            flat,
            prompt_name=prompt_name,
            batch_size=ENCODE_BATCH,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        vectors = unit(vectors)  # renormalize in float32; bfloat16 leaves norms a little off
        if not np.isfinite(vectors).all():
            raise FloatingPointError("embedding model returned non-finite values")
        out, at = [], 0
        for row, (pieces, _, left_out) in enumerate(plan):
            mine = vectors[at : at + len(pieces)]
            at += len(pieces)
            out.append(mine[0] if len(pieces) == 1 else unit(mine.mean(axis=0)))
            if len(pieces) > 1:
                self.parts[row] = mine
            if left_out:
                self.cut.add(row)
        return np.stack(out)

    def queries(self, texts):
        return self._encode(texts, QUERY_PROMPT)

    def documents(self, texts):
        return self._encode(texts, DOCUMENT_PROMPT)


class HashEmbedder:
    """Deterministic bag-of-words vectors for tests and dry runs.

    Similarity here means shared words and nothing more, so summaries built with it exercise the
    pipeline without saying anything about model quality.
    """

    dim = 256
    info = {"backend": "hash", "model": "hash-bow", "revision": "1", "dim": dim}

    def _encode(self, texts):
        vectors = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "little") % self.dim
                vectors[row, index] += 1.0 if digest[4] & 1 else -1.0
        return unit(vectors)

    queries = documents = _encode


def create(backend="embeddinggemma", **options):
    if backend == "hash":
        return HashEmbedder()
    if backend == "embeddinggemma":
        return Embedder(**options)
    raise ValueError(f"unknown embedder backend: {backend!r}")
