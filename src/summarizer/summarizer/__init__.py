"""On-the-fly session summaries for agent guard models.

What a reader of summaries needs is importable from here:

    load_summary(agent, session_id, wait=0.0, call_id=None, call_sha256=None)
    digest(summary, max_chars=3000, focus=None, ...)            text plus what did not fit
    render(summary, max_chars=3000, focus=None, measure=len)    the text alone
    evidence(agent, session_id, entry)                          the text an entry was cut from
    call_sha256(tool_name, tool_input)                          identity of a tool call
"""

__version__ = "0.1.0"

SCHEMA = "summarizer/v2"
MODEL = "google/embeddinggemma-2"
# Pinned so embeddings, probe calibration and fine-tuned models keep meaning the same thing.
REVISION = "f9972ac22e696a105c482202646979e33e4b5a1e"

# Loaded on first use, so the hook's import of this package costs nothing but these lines. None of
# these names may also be a module in the package: importing a submodule rebinds its name here.
_LAZY = {
    "load_summary": "store",
    "digest": "text",
    "render": "text",
    "evidence": "events",
    "call_sha256": "hook",
}


def __getattr__(name):
    if name not in _LAZY:
        raise AttributeError(name)
    from importlib import import_module

    value = globals()[name] = getattr(import_module(f".{_LAZY[name]}", __name__), name)
    return value
