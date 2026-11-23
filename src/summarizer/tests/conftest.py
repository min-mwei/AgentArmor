import shutil
import tempfile

import pytest

from summarizer.embedder import HashEmbedder
from summarizer.hook import build_record
from summarizer.store import view
from summarizer.summarize import Runtime, Session


@pytest.fixture
def home(monkeypatch):
    """A private spool directory, short enough for a Unix socket path."""
    path = tempfile.mkdtemp(prefix="smz-", dir="/tmp")
    monkeypatch.setenv("SUMMARIZER_HOME", path)
    monkeypatch.delenv("SUMMARIZER_DISABLE", raising=False)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def runtime():
    # strict: a test must fail on an event the summarizer cannot read, not count it and move on.
    return Runtime(HashEmbedder(), strict=True)


def summarize(events, runtime, agent="claude_code", one_at_a_time=False):
    """Run hook payloads through the same path the daemon uses and return the grouped summary."""
    session = Session(agent, "test", runtime)
    for event in events:
        session.add(build_record(agent, event))
        if one_at_a_time:
            session.flush()
    session.flush()
    return view(session.summary())


def entries(summary, kind=None, label=None):
    found = summary["entries"]
    if kind:
        found = [entry for entry in found if entry["type"] == kind]
    if label:
        found = [entry for entry in found if label in entry["labels"]]
    return found
