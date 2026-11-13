"""Stand-ins for the model, for tests that run without it."""

import re

import numpy as np

from summarizer.embedder import ENCODE_BATCH, MAX_WINDOWS, POLICY, Embedder, control_pattern


class WordModel:
    """Stands for the model and its processor: a token is a word, a prompt costs two, and
    like the real tokenizer it takes only what is UTF-8. A text longer than the window is
    cut off without a word, as the real processor cuts it."""

    prompts = {"SearchQuery": "q: ", "Document": "d: "}

    def __init__(self, window=10_000):
        self.window, self.seen = window, []

    def preprocess(self, inputs, prompt=None, processing_kwargs=None):
        (text,) = inputs
        count = 2 + len(text.split())
        uncut = ((processing_kwargs or {}).get("text") or {}).get("truncation") is False
        return {"input_ids": np.zeros((1, count if uncut else min(count, self.window)))}

    def tokenizer(self, text, **_):
        return {"offset_mapping": [found.span() for found in re.finditer(r"\S+", text)]}

    def encode(self, texts, **_):
        self.texts = [text.encode("utf-8").decode("utf-8") for text in texts]
        self.seen += self.texts
        rows = np.zeros((len(texts), 4), dtype=np.float32)
        for row, piece in enumerate(texts):  # which of four words a piece holds
            for at, word in enumerate(("alpha", "beta", "gamma", "delta")):
                rows[row, at] = float(word in piece.split())
        return rows + 0.01


def word_embedder(window=10_000):
    embedder = object.__new__(Embedder)  # the class without the weights
    embedder.model, embedder.controls = WordModel(window), control_pattern(["<|image|>"])
    embedder.window, embedder.overhead = window, {"SearchQuery": 2, "Document": 2}
    embedder.info = {"backend": "words", "model": "words", "revision": "1", "dim": 4}
    embedder.info |= {"policy": POLICY, "batch": ENCODE_BATCH}
    embedder.info |= {"window_tokens": window, "max_windows": MAX_WINDOWS}
    embedder.model.summarizer_embedder = (
        embedder  # what the training tools' common.served() looks for
    )
    return embedder
