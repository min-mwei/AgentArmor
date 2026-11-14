"""The whole stack in shadow, on campaigns: the hooks AgentArmor generates, the local server with
earlier sessions on, and a recording proxy in front of Kai (run sheet, section 9, item 3).

    python training/summarizer/shadow_campaigns.py run CAMPAIGNS --ids ID [ID ...] \\
        --layouts claude_code codex_code --kai-standin --out DIR [--budget 11000] [--embedder hash]
    python training/summarizer/shadow_campaigns.py run CAMPAIGNS --pairs PAIR [PAIR ...] \\
        --kai-url http://127.0.0.1:8009 --kai-package-sha256 HASH --out DIR \\
        --embedder embeddinggemma

One run is one campaign in one layout (campaign_contract.layout), on a stack of its own:

- `agentarmor-monitor serve --shadow --earlier-sessions on --max-chars BUDGET`, as a process with
  a spool, a home and a Codex home of its own under --out (the spool in a short temporary folder
  while it runs, for the daemon's socket, then moved under --out). Nothing of this account's home
  is written, and nothing of it is read but, with --embedder embeddinggemma, the model's files in
  its Hugging Face cache. No client's settings are touched. A run's sessions are the only ones
  its server sees: two campaigns never share a spool, so one is never an earlier session of the
  other.
- In front of Kai, a recording proxy started here. The monitor's --kai-url is the proxy, and the
  proxy relays each request and answer unchanged to --kai-url, or to a stand-in Kai served
  behind the Kai service's own HTTP handler (--kai-standin). Each exchange is kept whole in the
  run's kai-exchanges.jsonl, under --out only.
- Every event of every session, in the schedule's order (campaign_contract.replay), goes through
  the gate command `agentarmor-monitor hooks-config --gate EXE --shadow` generates for the
  session's client, with the event on stdin, under that hook's own deadline. The one option
  added is --server, for the run's port: a generated hook names none and asks 127.0.0.1:8010,
  where a real local server may answer. Each event gets a millisecond of its own
  (campaign_contract._past_this_millisecond). Session ids and working directories are as laid out.
- Between two turns of the schedule the runner waits until the server has caught up (every
  session's summary covers its log), as tests/test_shadow_path.py waits between its sessions:
  sessions that take turns are minutes or days apart, and a lag of the summarizer at hook speed
  is not a gap in what an earlier session holds. Within a turn, events follow at hook speed.
- After the call under decision is answered, its outcome is reported through the same gate,
  so that the gate's observed record ties it to the answer: the step's own outcome as the
  session-case contract lays it out, or, when the campaign leaves the call without one (DEV
  003 does), a returned call with an empty result ("assumed"): a shadow gate stops nothing, so
  the call runs. --no-outcome leaves it out. Then the runner waits for the server to catch up,
  and up to --settle seconds for the watcher's own requests to Kai.

The server's home is a scratch folder. So that the summarizer's home (taken from a session's
transcript path) and the history lookup's home (the server's Path.home()) are one home, as on a
user's machine, the campaign's home is moved there: its {home} paths and its transcript paths.
Such paths read differently from the DEV renders, and each run counts the events that hold one
outside the transcript path (home_events).

What is written to --out:
- runs.jsonl, one record per run: every gate's exit status and stdout bytes, the server's issued
  record(s), the gate's answered record and any observed record for the call under decision, the
  proxy's account of the request the issued record names (its SHA-256 as the monitor computes
  it, length, EARLIER SESSIONS section, [s:ALIAS] lines, HTTP status, p(deny) or error), the
  monitor's own count of halvings, timings, and the server's /health at the end. No session
  text is copied into it by this runner.
- summary.json: counts only, no session text: runs, gate exit or silence violations, calls under
  decision with and without an answered record, Kai requests by HTTP status, timeouts,
  kai_unavailable, oversize halvings, latency p50/p95/p99, and each run's route, verdict and why.
- runs/RUN/: the run's kai-exchanges.jsonl, spool and server log. These hold session text.

What a run cannot observe:
- No client runs. The events are the campaign's, laid out as each client's hook would deliver
  them; whether a client delivers exactly these (which events it hooks, their order, their
  payload) is not exercised. An event the generated hooks do not register for its client is
  still fed, and counted (unhooked_events).
- Client-side enforcement is not exercised. A shadow gate stops nothing, so what a client does
  with a gate's denial, with a gate that fails to start, or with one that overruns its deadline
  (Claude's onFailure, which is never set in shadow; Codex, which has no failure handler and
  puts no write_stdin to a hook) is not observed. An overrun of the hook's deadline is recorded
  here as a violation, not acted on.
- No tool runs. A reported result is the campaign's, or assumed; "returned" is not an effect.
- With --kai-standin no model, tokenizer or admission runs: the HTTP handler is the service's
  own, the answers are fixed, and --standin-limit refuses by body bytes with 413, where the
  real service refuses an over-long token count with 400 (the monitor halves on either).
- The proxy sees what crosses HTTP. It cannot tell which requests a decision made: only the
  issued record's request hash says that. The watcher's own requests (every trigger at --level
  low) share the Kai service with the decisions, and are counted with them.
- Timing between turns is the runner's, not a user's; a server that lags behind a fast
  interleaving of sessions is not measured.

CPU only with --embedder hash and --kai-standin. --embedder embeddinggemma loads the encoder in
the server: a model job, refused while KAI_MODEL_JOBS is set. Nothing here is a qualified
policy (policy_ready false), and a shadow "would deny" is never a prevented effect.
"""

import argparse
import contextlib
import hashlib
import itertools
import json
import math
import os
import re
import select
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

import campaign_contract as cc
import case_contract as contract
from common import read_jsonl

REPO = Path(__file__).resolve().parents[2]
# The monitor and the Kai service are run from this checkout's src/, as pytest.ini puts them: an
# installed copy of either can be another version of it.
SOURCES = [str(REPO / "src" / name) for name in ("vllm", "monitor", "summarizer")]
for _source in reversed(SOURCES[:2]):
    if _source not in sys.path:
        sys.path.insert(0, _source)

from agentarmor_monitor.earlier import alias as monitor_alias  # noqa: E402
from agentarmor_monitor.escalate import read as read_detections  # noqa: E402
from agentarmor_monitor.kai import loopback  # noqa: E402
from agentarmor_vllm.requests import validate_request  # noqa: E402
from agentarmor_vllm.server import DENIAL  # noqa: E402
from agentarmor_vllm.server import handler as service_handler  # noqa: E402

from summarizer.store import load_summary, safe_id  # noqa: E402

SCHEMA = "agentarmor-shadow-campaigns/v1"
STAND_IN_PACKAGE = "0" * 64  # what the stand-in says its package is
HISTORY_LINE = re.compile(r"\[s:([0-9a-z]+)\] ")
SOCKET_ROOM = 100  # bytes a spool's path leaves for run/daemon.sock within a Unix socket's limit
DEADLINE = 60  # seconds, for a generated hook that names none
MODEL_JOBS = "KAI_MODEL_JOBS"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


# Loopback only, and no proxy from the environment: a request for this machine stays on it.
_OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect).open


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def get_json(url, timeout=5.0):
    with _OPEN(urllib.request.Request(url), timeout=timeout) as reply:  # noqa: S310
        return json.loads(reply.read().decode("utf-8"))


# --- Kai: a stand-in, and the proxy in front of whatever Kai is


class StandIn:
    """A Kai that keeps every request it is given whole and answers it, behind the Kai service's
    own HTTP handler (agentarmor_vllm.server.handler), reporting itself as STAND_IN_PACKAGE.

    limit: a body of more bytes is refused with 413, as the service refuses a body over its own
    limit. delay: seconds each answer takes. p_deny: the decision's probability of deny (the
    choice is deny from 0.5). Its answers are not under test. One request at a time, as the
    service serves them."""

    def __init__(self, p_deny=0.1, limit=None, delay=0.0):
        self.p_deny, self.limit, self.delay = float(p_deny), limit, float(delay)
        self.received = []  # {"status", "body"}: every request that reached it, whole
        self.lock = threading.Lock()
        self.server = self.url = None

    def metadata(self):
        about = {"backend": "stub", "model": "kai-4b", "readout": "pointer", "policy_ready": False}
        return about | {"evidence_available": False, "package_sha256": STAND_IN_PACKAGE}

    def evaluate(self, value):
        request = validate_request(value)
        with self.lock:
            self.received.append({"status": 200, "body": value})
        if self.delay:
            time.sleep(self.delay)
        answers = {}
        if "decision" in request.questions:
            odds = {"allow": 1.0 - self.p_deny, "deny": self.p_deny}
            choice = "deny" if self.p_deny >= 0.5 else "allow"
            answers["decision"] = {"type": "choice", "choice": choice, "probabilities": odds}
        if "is_xpia_attempt" in request.questions:
            answers["is_xpia_attempt"] = {"type": "noul", "noul": 0.04}
        return {**self.metadata(), "answers": answers, "coverage": "complete"}

    def start(self):
        stand_in, base = self, service_handler(self)

        class Handler(base):
            def do_POST(self):
                sizes = self.headers.get_all("Content-Length", [])
                size = int(sizes[0]) if len(sizes) == 1 and sizes[0].isdigit() else None
                if stand_in.limit is not None and size is not None and size > stand_in.limit:
                    body = self.rfile.read(size)  # read whole, so the sender is not reset
                    try:
                        value = json.loads(body)
                    except ValueError:
                        value = None
                    with stand_in.lock:
                        stand_in.received.append({"status": 413, "body": value})
                    self.reply(413, DENIAL)
                    return
                super().do_POST()

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def close(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()


def request_account(body):
    """What a request is, without its text: the monitor's identifier for it (escalate.request_sha256:
    SHA-256 of its canonical JSON), its bytes, its questions, its state's length, and whether that
    state has an EARLIER SESSIONS section and how many [s:ALIAS] lines, by alias."""
    told = {"bytes": len(body), "sha256": None, "questions": None, "state_chars": None}
    told |= {"earlier_section": None, "s_lines": None, "s_aliases": None}
    try:
        value = json.loads(body.decode("utf-8"))
        canonical = json.dumps(value, sort_keys=True).encode("ascii")
        told["sha256"] = hashlib.sha256(canonical).hexdigest()
        told["questions"] = sorted(value.get("questions") or {})
        state = value.get("state")
    except (ValueError, UnicodeError, AttributeError, TypeError):
        return told
    if isinstance(state, str):
        lines = state.split("\n")
        found = Counter(m.group(1) for line in lines if (m := HISTORY_LINE.match(line)))
        told |= {"state_chars": len(state), "earlier_section": cc.TITLE in lines}
        told |= {"s_lines": sum(found.values()), "s_aliases": dict(sorted(found.items()))}
    return told


def answer_account(status, data):
    """What came back, without text: the status, the decision and its p(deny), p(xpia), and the
    service's error, which is its own fixed wording."""
    told = {"status": status, "choice": None, "p_deny": None, "p_xpia": None, "error": None}
    try:
        reply = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError):
        return told
    if not isinstance(reply, dict):
        return told
    answers = reply.get("answers") if isinstance(reply.get("answers"), dict) else {}
    decision, xpia = answers.get("decision"), answers.get("is_xpia_attempt")
    if isinstance(decision, dict):
        told["choice"] = decision.get("choice")
        odds = decision.get("probabilities")
        if isinstance(odds, dict) and isinstance(odds.get("deny"), int | float):
            told["p_deny"] = float(odds["deny"])
    if isinstance(xpia, dict) and isinstance(xpia.get("noul"), int | float):
        told["p_xpia"] = float(xpia["noul"])
    if status is not None and not 200 <= status < 300 and isinstance(reply.get("error"), str):
        told["error"] = reply["error"]
    return told


def gone(connection):
    """Whether the other end has closed: what the monitor does when it stops waiting."""
    try:
        if not select.select([connection], [], [], 0)[0]:
            return False
        return connection.recv(1, socket.MSG_PEEK) == b""
    except OSError:
        return True


class Proxy:
    """A recording proxy in front of one Kai service, for one run.

    Each request and its answer are relayed unchanged and kept whole in `log` (session text, in
    the run's folder): the request when it arrives, the answer when it comes, each a line with the
    exchange's number. In memory it keeps an account of each without text (request_account,
    answer_account), with when it came, how long the service took, and what became of the answer:
    "delivered"; "monitor_gave_up" (the monitor had closed its end before the answer, as it does
    when its --kai-timeout ends); "upstream_timeout" or "upstream_error" (nothing came back);
    "write_failed"; or, for one still awaited when the run ended with the monitor waiting for it,
    "unfinished". When nothing comes back the connection is closed with no answer, so that the
    monitor meets what it would meet with no proxy: nothing came back."""

    def __init__(self, upstream, log, timeout):
        self.upstream, self.log, self.timeout = upstream.rstrip("/"), Path(log), timeout
        os.close(os.open(self.log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))  # session text
        self.exchanges, self.lock, self.busy = [], threading.Lock(), 0
        self.count = itertools.count(1)
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(30)  # for reading a request, not for its answer

            def log_message(self, *_):
                pass  # never a path, a header or a body

            def do_GET(self):
                proxy.relay(self, None)

            def do_POST(self):
                sizes = self.headers.get_all("Content-Length", [])
                framed = not self.headers.get("Transfer-Encoding") and len(sizes) == 1
                if not framed or not sizes[0].isdigit():  # the monitor never sends this
                    self.close_connection = True
                    return
                proxy.relay(self, self.rfile.read(int(sizes[0])))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def relay(self, handler, body):
        with self.lock:
            self.busy += 1
        try:
            self._relay(handler, body)
        finally:
            with self.lock:
                self.busy -= 1

    def _keep(self, line):
        """One line of the whole record. Called with the lock held."""
        with open(self.log, "a", encoding="utf-8") as out:
            out.write(json.dumps(line) + "\n")

    def _relay(self, handler, body):
        seq, began, at = next(self.count), time.monotonic(), time.time()
        account = {"seq": seq, "path": handler.path, "t0": began, "ms": None}
        account |= {"outcome": "in_flight", "proxy_error": None}
        account |= (request_account(body) if body is not None else {}) | answer_account(None, b"")
        text = None if body is None else body.decode("utf-8", "replace")
        with self.lock:
            account["_connection"] = handler.connection
            self.exchanges.append(account)
            self._keep({"seq": seq, "ts": round(at, 3), "path": handler.path, "request": text})
        headers = {} if body is None else {"Content-Type": "application/json"}
        if handler.headers.get("Authorization"):  # passed on, never kept
            headers["Authorization"] = handler.headers["Authorization"]
        asked = urllib.request.Request(self.upstream + handler.path, data=body, headers=headers)
        status, data, kind, error = None, b"", None, None
        try:
            with _OPEN(asked, timeout=self.timeout) as reply:
                status, kind, data = reply.status, reply.headers.get("Content-Type"), reply.read()
        except urllib.error.HTTPError as refusal:
            status, kind, data = refusal.code, refusal.headers.get("Content-Type"), refusal.read()
        except Exception as failure:  # noqa: BLE001 - whatever it was, nothing came back
            timed_out = isinstance(failure, TimeoutError)
            timed_out |= isinstance(getattr(failure, "reason", None), TimeoutError)
            error = "upstream_timeout" if timed_out else f"upstream_error:{type(failure).__name__}"
        took = round((time.monotonic() - began) * 1000)
        outcome = error.split(":")[0] if error else "delivered"
        if status is not None and gone(handler.connection):
            outcome = "monitor_gave_up"
        if outcome == "delivered":
            try:
                handler.send_response(status)
                handler.send_header("Content-Type", kind or "application/json")
                handler.send_header("Content-Length", str(len(data)))
                handler.send_header("Connection", "close")
                handler.end_headers()
                handler.wfile.write(data)
            except OSError:
                outcome = "write_failed"
        handler.close_connection = True
        reply = {"seq": seq, "ts": round(time.time(), 3), "ms": took, "status": status}
        reply |= {"response": data.decode("utf-8", "replace"), "outcome": outcome}
        with self.lock:
            account.pop("_connection", None)
            account |= {"ms": took, "outcome": outcome, "proxy_error": error}
            account |= answer_account(status, data)
            self._keep(reply | {"proxy_error": error})

    def snapshot(self, wait):
        """The accounts, once those in flight have had up to `wait` seconds to end. Taken while
        the server still runs: an answer still awaited is "monitor_gave_up" if the monitor has
        closed its end, else "unfinished". Nothing after this changes what it returned."""
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            with self.lock:
                if not self.busy:
                    break
            time.sleep(0.02)
        with self.lock:
            for account in self.exchanges:
                connection = account.pop("_connection", None)
                if account["outcome"] == "in_flight":
                    late = connection is not None and gone(connection)
                    account["outcome"] = "monitor_gave_up" if late else "unfinished"
            return json.loads(json.dumps(self.exchanges))

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# --- the stack: a launcher, the generated hooks, the server


LAUNCHER = """#!{python}
import sys
sys.path[:0] = {paths!r}
from agentarmor_monitor.cli import main
raise SystemExit(main())
"""


def launcher(folder):
    """`agentarmor-monitor`, as a console script of this checkout's sources would be."""
    path = Path(folder) / "bin" / "agentarmor-monitor"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(LAUNCHER.format(python=sys.executable, paths=SOURCES), encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def environment(spool, home, args):
    """The server's and the gates' environment: scratch spool, home and Codex home, this
    checkout's sources, and nothing of the account's Kai token, client settings or switches."""
    env = dict(os.environ)
    for name in ("AGENTARMOR_KAI_TOKEN", "CLAUDE_CONFIG_DIR", "SUMMARIZER_DISABLE", "PYTHONHOME"):
        env.pop(name, None)
    env |= {"SUMMARIZER_HOME": str(spool), "HOME": str(home), "CODEX_HOME": str(home / ".codex")}
    env |= {"PYTHONPATH": os.pathsep.join(SOURCES), "PYTHONDONTWRITEBYTECODE": "1"}
    if args.embedder == "embeddinggemma" and not (env.get("HF_HUB_CACHE") or env.get("HF_HOME")):
        # The model is read from this account's cache, as it stands: the scratch home has none.
        env["HF_HUB_CACHE"] = os.path.expanduser("~/.cache/huggingface/hub")
    return env


def generated_hooks(exe, env):
    """{agent: the hooks block `agentarmor-monitor hooks-config --gate EXE --shadow` prints}."""
    told = {}
    for agent in cc.AGENTS:
        command = [exe, "hooks-config", "--agent", agent, "--gate", exe, "--shadow"]
        made = subprocess.run(command, capture_output=True, env=env, timeout=120, check=True)
        told[agent] = json.loads(made.stdout)
    return told


def gate_command(hooks, agent, event, url):
    """(argv, deadline, whether the client hooks this event) for one event: the generated
    handler for it, with --server for the run's port. Claude Code runs a handler in exec form,
    Codex through a shell."""
    groups = hooks[agent]["hooks"]
    hooked = event in groups
    handler = groups[event if hooked else "PreToolUse"][0]["hooks"][0]
    deadline = handler.get("timeout") or DEADLINE
    if "args" in handler:
        return [handler["command"], *handler["args"], "--server", url], deadline, hooked
    line = f"{handler['command']} --server {shlex.quote(url)}"
    return ["/bin/sh", "-c", line], deadline, hooked


class Server:
    """`agentarmor-monitor serve` in shadow with earlier sessions on, as a process."""

    def __init__(self, exe, env, args, kai_url, package, log):
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        command = [exe, "serve", "--shadow", "--earlier-sessions", "on"]
        command += ["--max-chars", str(args.budget), "--embedder", args.embedder]
        command += ["--kai-url", kai_url, "--kai-package-sha256", package]
        command += ["--kai-timeout", str(args.kai_timeout), "--wait", str(args.wait)]
        command += ["--port", str(self.port)]
        if args.policy:
            command += ["--policy", args.policy]
        if args.embedder == "embeddinggemma":
            command += ["--local-files-only"]
            for name in ("model", "probes", "device"):
                if getattr(args, name):
                    command += [f"--{name}", getattr(args, name)]
        self.log = open(log, "wb")  # noqa: SIM115 - held while the process runs
        self.process = subprocess.Popen(command, stdout=self.log, stderr=subprocess.STDOUT, env=env)
        deadline = time.monotonic() + args.start_timeout
        while True:
            try:
                self.health = get_json(self.url + "/health", timeout=2)
                break
            except (OSError, ValueError):
                if self.process.poll() is not None:
                    self.close()
                    tail = Path(log).read_bytes()[-800:].decode("utf-8", "replace")
                    raise RuntimeError(f"the local server did not start: {tail}") from None
                if time.monotonic() > deadline:
                    self.close()
                    raise RuntimeError("the local server did not come up in time") from None
                time.sleep(0.1)
        decisions = self.health.get("decisions") or {}
        wanted = {"mode": "shadow", "earlier_sessions": "on", "max_chars": args.budget}
        got = {name: decisions.get(name) for name in wanted}
        if got != wanted:
            self.close()
            raise RuntimeError(f"the local server is not the one asked for: {got}")

    def alive(self):
        return self.process.poll() is None

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(15)
        self.log.close()
        return self.process.returncode


@contextlib.contextmanager
def spool_env(spool):
    """SUMMARIZER_HOME for this process, while the runner reads a run's summaries."""
    kept = os.environ.get("SUMMARIZER_HOME")
    os.environ["SUMMARIZER_HOME"] = str(spool)
    try:
        yield
    finally:
        if kept is None:
            os.environ.pop("SUMMARIZER_HOME", None)
        else:
            os.environ["SUMMARIZER_HOME"] = kept


@contextlib.contextmanager
def live_spool(final):
    """A spool for a run's server: at `final` when its socket path fits, else in a short
    temporary folder for as long as the server runs, then moved to `final`."""
    final = Path(final)
    if len(str(final / "run" / "daemon.sock")) <= SOCKET_ROOM:
        final.mkdir(parents=True, mode=0o700)
        yield final
        return
    base = "/tmp" if os.path.isdir("/tmp") else None  # noqa: S108 - a short path, made private
    spool = Path(tempfile.mkdtemp(prefix="smz-shadow-", dir=base))
    try:
        yield spool
    finally:
        try:
            os.rename(spool, final)
        except OSError:  # another file system: copy what is a file, and drop the rest

            def sockets(folder, names):
                return [
                    n for n in names if stat.S_ISSOCK(os.lstat(os.path.join(folder, n)).st_mode)
                ]

            shutil.copytree(spool, final, ignore=sockets)
            shutil.rmtree(spool, ignore_errors=True)


def caught_up(server, spool, sessions, timeout, settle=None):
    """{"fresh", "idle", "ms"}: wait until every session given has a summary covering its log, by
    the server's /health (no session behind) and by the summaries themselves; then, with settle,
    up to that many seconds more until the watcher has nothing waiting for Kai."""
    began, fresh, idle = time.monotonic(), False, None
    while time.monotonic() - began <= timeout:
        try:
            behind = (get_json(server.url + "/health").get("sessions") or {}).get("behind")
        except (OSError, ValueError):
            behind = None
        with spool_env(spool):
            whole = all((load_summary(a, s) or {}).get("fresh") for a, s in sessions)
        if behind == 0 and whole:
            fresh = True
            break
        time.sleep(0.05)
    if settle is not None:
        idle, until = False, time.monotonic() + settle
        while time.monotonic() <= until:
            try:
                waiting = (get_json(server.url + "/health").get("sessions") or {}).get("waiting")
            except (OSError, ValueError):
                waiting = None
            if waiting == 0:
                idle = True
                break
            time.sleep(0.05)
    return {"fresh": fresh, "idle": idle, "ms": round((time.monotonic() - began) * 1000)}


# --- one run


def laid_out(campaign, agent, home):
    """campaign_contract.layout, with the campaign's home at `home`, the home the server runs in:
    its {home} paths, and the transcript paths a summary takes its home from."""
    moved = json.loads(json.dumps(campaign).replace("{home}", json.dumps(home)[1:-1]))
    laid = cc.layout(moved, agent)
    for part in laid["parts"]:
        for event in (*part["laid"]["events"], *part["laid"]["later"]):
            path = event.get("transcript_path")
            if isinstance(path, str) and path.startswith(cc.HOME + "/"):
                event["transcript_path"] = home + path[len(cc.HOME) :]
    return laid


def in_order(laid):
    """[(part, event number, event)] in the schedule's order (campaign_contract.replay)."""
    parts = {part["id"]: part for part in laid["parts"]}
    return [
        (parts[name], number, parts[name]["laid"]["events"][number - 1])
        for name, start, stop in laid["schedule"]
        for number in range(start + 1, stop + 1)
    ]


def outcome_of(part, event):
    """(the report of the call under decision once it ran, where it comes from), or (None, None)
    when there is none to report: the step's own outcome as the session-case contract lays it out
    ("step"), or, for a call the campaign leaves with none, a returned call with an empty result
    ("assumed")."""
    case = part["laid"]["case"]
    step = next(s for s in case["steps"] if s["id"] == case["decide"])
    source = "step"
    if not any(key in step for key in ("output", "denied", "error", "pending")):
        step, source = {**step, "output": ""}, "assumed"
    for _, hook, fields in contract._step_events(step, part["agent"])[1:]:
        if hook is not None:
            call = {name: event[name] for name in ("tool_name", "tool_input", "tool_use_id")}
            return {**event, **fields, "hook_event_name": hook, **call}, source
    return None, None


def folder_of(spool, agent, session_id):
    """A session's folder in a spool, named as the store names it (summarizer.store.session_dir)."""
    return Path(spool) / "sessions" / agent / safe_id(session_id)


def alias(agent, session_id):
    """The alias the server's [s:ALIAS] lines give a session (earlier.alias)."""
    return monitor_alias(agent, safe_id(session_id))


def gate_once(argv, event, env, deadline):
    """One event through one gate process, as a client runs a hook: the event on stdin, under the
    hook's deadline. Returns (what is recorded of it, when it began, when it ended); then waits
    out the hook clock's millisecond, so that no two events share a time."""
    began = time.monotonic()
    try:
        done = subprocess.run(
            argv, input=json.dumps(event).encode("ascii"), capture_output=True, env=env,
            timeout=deadline,
        )  # fmt: skip
        status, out, overrun = done.returncode, done.stdout, False
    except subprocess.TimeoutExpired as late:
        status, out, overrun = None, late.stdout or b"", True
    ended = time.monotonic()
    cc._past_this_millisecond()
    told = {"event": event["hook_event_name"], "exit": status, "stdout_bytes": len(out)}
    told |= {"ms": round((ended - began) * 1000), "deadline_overrun": overrun}
    if out:  # a gate's own words, a denial at most: kept so that a violation can be read
        told["stdout"] = out.decode("utf-8", "replace")[:2000]
    return told, began, ended


def error_lines(spool):
    """{log: {step: count}} of the hook's and the gate's error logs: a step and a class, no text."""
    told = {}
    for name in ("hook-errors.log", "gate-errors.log"):
        try:
            lines = (Path(spool) / name).read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        told[name] = dict(
            Counter(line.split("\t")[2] if line.count("\t") >= 2 else "?" for line in lines)
        )
    return told


def halvings(record):
    """The monitor's own count of the budget reductions it made after refusals (escalate.detect,
    refusal_retries). A reduction that rebuilds a request refused before sends nothing, so the
    requests built no longer count them (MR251); a record from before that count says built - 1."""
    told = (record.get("routing") or {}).get("refusal_retries") or record.get("refusal_retries")
    if isinstance(told, dict) and isinstance(told.get("budget_reductions"), int):
        return told["budget_reductions"]
    counts = (record.get("routing") or {}).get("requests") or record.get("requests") or {}
    built = counts.get("built")
    return max(0, built - 1) if isinstance(built, int) else 0


def route_of(record):
    if not record:
        return None, None
    routing = record.get("routing") or {}
    history = (record.get("packet") or {}).get("history") or {}
    return history.get("route") or routing.get("earlier_route"), routing.get("reason")


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "--", text)


def drive(laid, args, stack, folder):
    """Run one laid-out campaign through the stack: a server and a proxy of its own, every event
    through its gate in the schedule's order, the call's outcome, and the waits. Returns what was
    seen: each gate, each wait, the call's gate window, the outcome fed, the server's last
    /health and exit status, the proxy's accounts, and why the run failed, if it did."""
    seen = {"gates": [], "turns": [], "window": None, "caught_up": None, "health": None}
    seen |= {"outcome": {"fed": False, "source": None, "event": None}, "failed": None}
    events, deciding, fed = in_order(laid), laid["deciding"], []
    with live_spool(folder / "spool") as spool:
        env = environment(spool, stack["home"], args)
        proxy = Proxy(stack["upstream"], folder / "kai-exchanges.jsonl", args.proxy_timeout)
        server = None
        try:
            server = Server(
                stack["exe"], env, args, proxy.url, stack["package"], folder / "server.log"
            )

            def through(part, event):
                name = event["hook_event_name"]
                argv, deadline, hooked = gate_command(
                    stack["hooks"], part["agent"], name, server.url
                )
                told, began, ended = gate_once(argv, event, env, deadline)
                seen["gates"].append({"part": part["id"], "hooked": hooked} | told)
                return began, ended

            previous = None
            for index, (part, number, event) in enumerate(events):
                if part["id"] != previous and fed:  # a new turn: the server catches up first
                    waited = caught_up(server, spool, fed, args.catch_up)
                    seen["turns"].append(waited | {"before": part["id"]})
                previous = part["id"]
                if (part["agent"], part["session_id"]) not in fed:
                    fed.append((part["agent"], part["session_id"]))
                window = through(part, event)
                if index == len(events) - 1:
                    seen["window"] = window
                if number == part["damaged_at"]:  # a line of the log that cannot be read
                    log = folder_of(spool, part["agent"], part["session_id"]) / "events.jsonl"
                    with open(log, "ab") as out:
                        out.write(b"{damaged\n")
                if not server.alive():
                    raise RuntimeError("the local server stopped during the run")
            if args.outcome:
                report, source = outcome_of(deciding, events[-1][2])
                if report is not None:
                    through(deciding, report)
                    seen["outcome"] = {"fed": True, "source": source}
                    seen["outcome"]["event"] = report["hook_event_name"]
            seen["caught_up"] = caught_up(server, spool, fed, args.catch_up, args.settle)
            seen["health"] = get_json(server.url + "/health")
        except Exception as error:  # noqa: BLE001 - a run that failed is recorded as one
            seen["failed"] = f"{type(error).__name__}: {error}"[:2000]
        finally:
            seen["exchanges"] = proxy.snapshot(args.settle)  # while the server still runs
            seen["server_exit"] = server.close() if server is not None else None
            proxy.close()
    try:
        seen["nudge_socket"] = b"no nudge socket" not in (folder / "server.log").read_bytes()
    except OSError:
        seen["nudge_socket"] = None
    return seen


def run_one(campaign, agent, args, stack):
    """The record of one campaign in one layout, through the whole stack."""
    name = f"{campaign['id']}/{agent}"
    folder = stack["out"] / "runs" / safe_name(name)
    folder.mkdir(parents=True, mode=0o700)  # its spool and its exchanges hold session text
    home = str(stack["home"])
    laid = laid_out(campaign, agent, home)
    deciding = laid["deciding"]
    call = deciding["laid"]["call"]
    events = [event for *_, event in in_order(laid)]
    record = {"run": name, "campaign": campaign["id"], "pair_id": campaign.get("pair_id")}
    record |= {"family": campaign.get("family"), "layout": agent}
    record["layout_sha256"] = hashlib.sha256(
        json.dumps(events, sort_keys=True).encode()
    ).hexdigest()
    record["home_events"] = sum(
        home in json.dumps({k: v for k, v in e.items() if k != "transcript_path"}) for e in events
    )
    record["deciding"] = {"part": deciding["id"], "agent": deciding["agent"]}
    record["deciding"] |= {"session_id": deciding["session_id"], "call_id": call}
    record["deciding"]["call_sha256"] = deciding["laid"]["identity"]
    happened = [p for p in laid["parts"] if not p["later"]]
    record["sessions"] = [
        {"part": p["id"], "agent": p["agent"], "alias": alias(p["agent"], p["session_id"])}
        | {"events": p["cut"], "cwd_exists_here": os.path.exists(p["cwd"])}
        for p in happened
    ]
    seen = drive(laid, args, stack, folder)
    record |= {"failed": seen["failed"], "server_exit": seen["server_exit"]}
    record |= {"nudge_socket": seen["nudge_socket"], "caught_up": seen["caught_up"]}
    record["events"] = {"fed": len(events), "outcome": seen["outcome"]}
    record["events"]["unhooked"] = sum(1 for g in seen["gates"] if not g["hooked"])
    record["events"]["damaged_logs"] = sum(1 for p in laid["parts"] if p["damaged"])
    record |= {"gates": seen["gates"], "turns": seen["turns"]}

    # What the server and the gates put on record (escalate.read), from the spool as it was left.
    spool = folder / "spool"
    mine = read_detections(folder_of(spool, deciding["agent"], deciding["session_id"]))
    record["issued"] = [r for r in mine if r.get("stage") == "issued" and r.get("call_id") == call]
    record["answered"] = [
        r for r in mine if r.get("stage") == "answered" and r.get("call_id") == call
    ]
    record["observed"] = [
        r for r in mine if r.get("stage") == "observed" and r.get("call_id") == call
    ]
    every = [
        r for p in happened for r in read_detections(folder_of(spool, p["agent"], p["session_id"]))
    ]
    gated = [r for r in every if r.get("stage") == "issued"]
    # The watcher's own records (daemon.Monitor): a trigger's key, and no stage.
    kinds = ("action", "content", "message")
    watched = [r for r in every if "stage" not in r and str(r.get("key")).split(":")[0] in kinds]

    # The request the issued record names, as the proxy saw it, and the requests made while the
    # call's gate waited (the watcher's among them).
    last = record["issued"][-1] if record["issued"] else None
    sent = bool(last and (last.get("last_request") or {}).get("transport_attempts"))
    key = (last.get("packet") or {}).get("request_sha256") if sent else None
    posts = [e for e in seen["exchanges"] if e["path"] == "/v1/systemone"]
    window = seen["window"]
    inside = [e for e in posts if window and window[0] <= e["t0"] <= window[1]]
    named = [e for e in posts if key and e["sha256"] == key]
    named = [e for e in named if e in inside] or named

    def bare(account):
        told = {k: v for k, v in account.items() if k != "t0"}
        if told.get("s_aliases") is not None:
            told["s_parts"] = {
                s["part"]: told["s_aliases"].get(s["alias"], 0) for s in record["sessions"]
            }
        return told

    record["kai"] = {
        "requests": len(posts),
        "by_status": dict(Counter(str(e["status"]) for e in posts)),
        "outcomes": dict(Counter(e["outcome"] for e in posts)),
        "ms": [e["ms"] for e in posts if e["ms"] is not None],
        "health_checks": len(seen["exchanges"]) - len(posts),
        "issued_request_sha256": key,
        "request": bare(named[-1]) if named else None,
        "window": [bare(e) for e in inside],
        "halvings": halvings(last) if last else 0,
    }
    answer = record["answered"][-1] if record["answered"] else {}
    route, reason = route_of(last)
    record["outcome"] = {"route": route, "reason": reason}
    record["outcome"] |= {
        "verdict": (last or answer).get("verdict"),
        "why": (last or answer).get("why"),
    }
    record["outcome"] |= {"status": (last or {}).get("status"), "would": answer.get("would")}
    record["outcome"]["p_deny"] = ((last or {}).get("kai") or {}).get("p_deny")
    record["timing"] = {
        "gate_ms": round((window[1] - window[0]) * 1000) if window else None,
        "gate_elapsed_ms": answer.get("elapsed_ms"),
        "kai_ms": named[-1]["ms"] if named else None,
    }
    record["all_gated"] = {
        "issued": len(gated),
        "answered": sum(1 for r in every if r.get("stage") == "answered"),
        "verdicts": dict(Counter(str(r.get("verdict")) for r in gated)),
        "put_to_kai": sum(
            1 for r in gated if ((r.get("routing") or {}).get("requests_sent") or 0) > 0
        ),
        "kai_unavailable": sum(1 for r in gated if r.get("status") == "kai_unavailable"),
        "halvings": sum(halvings(r) for r in gated),
    }
    record["watcher"] = {
        "records": len(watched),
        "statuses": dict(Counter(str(r.get("status")) for r in watched)),
        "halvings": sum(halvings(r) for r in watched),
    }
    record["errors"] = error_lines(spool)
    record["server"] = {"health": seen["health"]}
    return record


# --- the summary: counts only


def percentiles(values):
    ordered = sorted(v for v in values if isinstance(v, int | float))
    if not ordered:
        return {"n": 0, "p50": None, "p95": None, "p99": None, "max": None}

    def at(share):  # nearest rank
        return ordered[min(len(ordered), max(1, math.ceil(share * len(ordered)))) - 1]

    return {"n": len(ordered), "p50": at(0.5), "p95": at(0.95), "p99": at(0.99), "max": ordered[-1]}


def identity_of(health):
    """What the stack was, from a server's /health: the encoder, the decision model, the rules."""
    health = health or {}
    embedding = (health.get("summarizer") or {}).get("embedding") or {}
    model = health.get("decision_model") or {}
    reported = model.get("reported") or {}
    decisions = health.get("decisions") or {}
    kept = ("backend", "model", "revision", "device", "dtype", "probes", "calibrated")
    told = {"embedding": {name: embedding.get(name) for name in kept}}
    told["decision_model"] = {
        name: reported.get(name) for name in ("backend", "model", "readout", "package_sha256")
    }
    told["decision_model"] |= {
        "identity": model.get("identity"),
        "reachable": model.get("reachable"),
    }
    told["decisions"] = {
        name: decisions.get(name)
        for name in ("mode", "level", "max_chars", "layout", "earlier_sessions")
    } | {"policy_sha256": (decisions.get("policy") or {}).get("sha256")}
    return told


def summarize(records, args):
    gates = [g for r in records for g in r["gates"]]
    posts = Counter()
    for record in records:
        posts.update(record["kai"]["by_status"])
    outcomes = Counter()
    for record in records:
        outcomes.update(record["kai"]["outcomes"])

    def status(test):
        return sum(n for code, n in posts.items() if code.isdigit() and test(int(code)))

    told = {"schema": SCHEMA, "advisory": True, "policy_ready": False}
    told["settings"] = {
        "budget": args.budget, "embedder": args.embedder, "layouts": args.layouts,
        "kai": "stand-in" if args.kai_standin else "url", "kai_timeout": args.kai_timeout,
        "wait": args.wait, "outcome": args.outcome,
    }  # fmt: skip
    identities = [identity_of(r["server"]["health"]) for r in records if r["server"]["health"]]
    told["stack"] = identities[0] if identities else None
    told["stack_same_in_every_run"] = all(i == identities[0] for i in identities)
    told["runs"] = len(records)
    told["runs_failed"] = sum(1 for r in records if r["failed"])
    told["layouts_with_identical_events"] = sum(
        1
        for campaign in {r["campaign"] for r in records}
        if len({r["layout_sha256"] for r in records if r["campaign"] == campaign}) == 1
        and sum(1 for r in records if r["campaign"] == campaign) > 1
    )
    told["home_events"] = sum(r["home_events"] for r in records)
    told["gates"] = {
        "invocations": len(gates),
        "violations": sum(
            1 for g in gates if g["exit"] != 0 or g["stdout_bytes"] or g["deadline_overrun"]
        ),
        "nonzero_exit": sum(1 for g in gates if g["exit"] not in (0, None)),
        "stdout_not_empty": sum(1 for g in gates if g["stdout_bytes"]),
        "deadline_overruns": sum(1 for g in gates if g["deadline_overrun"]),
        "unhooked_events": sum(r["events"]["unhooked"] for r in records),
    }
    answered = [len(r["answered"]) for r in records]
    told["deciding_calls"] = {
        "total": len(records),
        "with_answered": sum(1 for n in answered if n),
        "exactly_one_answered": sum(1 for n in answered if n == 1),
        "more_than_one_answered": sum(1 for n in answered if n > 1),
        "without_answered": sum(1 for n in answered if not n),
        "with_issued": sum(1 for r in records if r["issued"]),
        "put_to_kai": sum(1 for r in records if r["kai"]["issued_request_sha256"]),
        "request_seen_by_proxy": sum(1 for r in records if r["kai"]["request"]),
        "with_observed": sum(1 for r in records if r["observed"]),
        "outcome_fed": sum(1 for r in records if r["events"]["outcome"]["fed"]),
    }
    told["kai_requests"] = {
        "total": sum(posts.values()),
        "http_2xx": status(lambda c: 200 <= c < 300),
        "http_400_413": status(lambda c: c in (400, 413)),
        "http_5xx": status(lambda c: 500 <= c < 600),
        "http_other": status(lambda c: not (200 <= c < 300 or c in (400, 413) or 500 <= c < 600)),
        "no_response": posts.get("None", 0),
        "outcomes": dict(sorted(outcomes.items())),
    }
    told["timeouts"] = outcomes.get("monitor_gave_up", 0) + outcomes.get("upstream_timeout", 0)
    told["kai_unavailable"] = {
        "deciding": sum(1 for r in records if r["outcome"]["status"] == "kai_unavailable"),
        "all_gated": sum(r["all_gated"]["kai_unavailable"] for r in records),
        "watcher": sum(r["watcher"]["statuses"].get("kai_unavailable", 0) for r in records),
    }
    told["oversize_halvings"] = {
        "deciding": sum(r["kai"]["halvings"] for r in records),
        "all_gated": sum(r["all_gated"]["halvings"] for r in records),
        "watcher": sum(r["watcher"]["halvings"] for r in records),
    }
    told["latency_ms"] = {
        "gate_deciding": percentiles(r["timing"]["gate_ms"] for r in records),
        "gate_deciding_elapsed": percentiles(r["timing"]["gate_elapsed_ms"] for r in records),
        "gate_every_event": percentiles(g["ms"] for g in gates),
        "kai_deciding": percentiles(r["timing"]["kai_ms"] for r in records),
        "kai_every_request": percentiles(ms for r in records for ms in r["kai"]["ms"]),
    }
    told["caught_up"] = {
        "turn_waits": sum(len(r["turns"]) for r in records),
        "turn_waits_not_fresh": sum(1 for r in records for t in r["turns"] if not t["fresh"]),
        "runs_not_fresh_at_end": sum(
            1 for r in records if not (r.get("caught_up") or {}).get("fresh")
        ),
        "runs_not_idle_at_end": sum(
            1 for r in records if not (r.get("caught_up") or {}).get("idle")
        ),
    }
    told["per_run"] = [
        {"run": r["run"], "failed": bool(r["failed"])} | r["outcome"]
        | {"halvings": r["kai"]["halvings"], "s_lines": (r["kai"]["request"] or {}).get("s_lines")}
        for r in records
    ]  # fmt: skip
    return told


# --- the command


def selected(rows, ids, pairs):
    """The campaigns asked for, in the order asked: ids, then each pair's campaigns as listed."""
    by_id = {row["id"]: row for row in rows}
    missing = [i for i in ids if i not in by_id]
    missing += [p for p in pairs if not any(row.get("pair_id") == p for row in rows)]
    if missing:
        raise SystemExit(f"shadow: not in the campaigns file: {', '.join(missing)}")
    chosen = [by_id[i] for i in ids]
    chosen += [row for p in pairs for row in rows if row.get("pair_id") == p]
    seen, unique = set(), []
    for row in chosen:
        if row["id"] not in seen:
            seen.add(row["id"])
            unique.append(row)
    return unique


def run(args, stand_in=None):
    """Run every campaign asked for in every layout asked for; returns the summary. stand_in is
    a StandIn to use for --kai-standin (one is made from the options when none is given)."""
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit("shadow: --out is a new or an empty folder")
    out.mkdir(parents=True, exist_ok=True)
    campaigns = selected(read_jsonl(args.campaigns), args.ids or [], args.pairs or [])
    home = out / "home"
    (home / ".codex").mkdir(parents=True)
    owned = None
    if args.kai_standin:
        if stand_in is None:
            given = {"p_deny": args.standin_p_deny, "limit": args.standin_limit}
            given["delay"] = args.standin_delay
            stand_in = owned = StandIn(**{k: v for k, v in given.items() if v is not None})
        if stand_in.url is None:
            stand_in.start()
        upstream, package = stand_in.url, STAND_IN_PACKAGE
    else:
        upstream, package = args.kai_url, args.kai_package_sha256
    exe = launcher(out)
    stack = {"out": out, "home": home, "exe": exe, "upstream": upstream, "package": package}
    # Printing a configuration reads no spool: the one named here is never made.
    stack["hooks"] = generated_hooks(exe, environment(out / "no-spool", home, args))
    (out / "hooks.json").write_text(json.dumps(stack["hooks"], indent=2) + "\n", encoding="utf-8")
    records = []
    try:
        total = len(campaigns) * len(args.layouts)
        for campaign in campaigns:
            for agent in args.layouts:
                began = time.monotonic()
                record = run_one(campaign, agent, args, stack)
                records.append(record)
                with open(out / "runs.jsonl", "a", encoding="utf-8") as lines:
                    lines.write(json.dumps(record) + "\n")
                told = record["outcome"]
                print(
                    f"shadow: {len(records)}/{total} {record['run']}: {told['verdict']} "
                    f"({told['why']}), route {told['route']}, "
                    f"{'failed, ' if record['failed'] else ''}{time.monotonic() - began:.1f} s",
                    file=sys.stderr,
                    flush=True,
                )
    finally:
        if owned is not None:
            owned.close()
    summary = summarize(records, args)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    go = commands.add_parser("run", help="run campaigns through the whole stack, in shadow")
    go.add_argument("campaigns", help="a campaigns JSONL file (campaign_contract)")
    go.add_argument("--ids", nargs="+", help="campaign ids to run")
    go.add_argument("--pairs", nargs="+", help="pair ids: every campaign of each, as listed")
    go.add_argument("--layouts", nargs="+", choices=cc.AGENTS, default=list(cc.AGENTS))
    kai = go.add_mutually_exclusive_group(required=True)
    kai.add_argument("--kai-url", help="a Kai service on this machine (a tunnel is one)")
    kai.add_argument("--kai-standin", action="store_true", help="a stand-in Kai, CPU only")
    go.add_argument("--kai-package-sha256", help="the package the Kai at --kai-url must be")
    go.add_argument("--standin-limit", type=int, help="the stand-in refuses a larger body (413)")
    go.add_argument("--standin-delay", type=float, help="seconds each stand-in answer takes")
    go.add_argument("--standin-p-deny", type=float, help="the stand-in's p(deny), 0.1 if omitted")
    go.add_argument("--out", required=True, help="a new or an empty folder")
    go.add_argument("--budget", type=int, default=11000, help="the server's --max-chars")
    go.add_argument("--embedder", choices=("hash", "embeddinggemma"), default="hash")
    go.add_argument("--model", help="a local EmbeddingGemma 2 directory (embeddinggemma)")
    go.add_argument("--probes", help="probes calibrated for --model (embeddinggemma)")
    go.add_argument("--device", help="cuda, mps or cpu (embeddinggemma)")
    go.add_argument("--policy", help="rules for the server; its default rules if omitted")
    go.add_argument("--kai-timeout", type=float, default=30.0, help="the server's --kai-timeout")
    go.add_argument("--wait", type=float, default=3.0, help="the server's --wait")
    go.add_argument("--proxy-timeout", type=float, help="how long the proxy waits for Kai")
    go.add_argument("--start-timeout", type=float, default=600.0, help="for the server to start")
    go.add_argument("--catch-up", type=float, default=120.0, help="seconds to wait for summaries")
    go.add_argument("--settle", type=float, default=30.0, help="seconds for the watcher at the end")
    go.add_argument("--no-outcome", dest="outcome", action="store_false")
    args = parser.parse_args(argv)
    if not (args.ids or args.pairs):
        parser.error("name campaigns with --ids or --pairs")
    standing = (args.standin_limit, args.standin_delay, args.standin_p_deny)
    if args.kai_url:
        if not loopback(args.kai_url):
            parser.error(
                "--kai-url is an address on this machine: the proxy sends what it is given"
            )
        if not re.fullmatch("[0-9a-f]{64}", args.kai_package_sha256 or ""):
            parser.error("--kai-url needs --kai-package-sha256: 64 lowercase hexadecimal digits")
        if any(value is not None for value in standing):
            parser.error("the --standin options are for --kai-standin")
    elif args.kai_package_sha256:
        parser.error("--kai-package-sha256 is for --kai-url: the stand-in names its own")
    if args.embedder == "embeddinggemma" and os.environ.get(MODEL_JOBS) is not None:
        parser.error(
            f"--embedder embeddinggemma is a model job, and {MODEL_JOBS} refuses them here"
        )
    if args.proxy_timeout is None:
        args.proxy_timeout = args.kai_timeout + 30.0
    return args


def main(argv=None):
    summary = run(parse(argv))
    told = {key: summary[key] for key in ("runs", "runs_failed", "gates", "deciding_calls")}
    told |= {"kai_requests": summary["kai_requests"], "timeouts": summary["timeouts"]}
    print(json.dumps(told, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
