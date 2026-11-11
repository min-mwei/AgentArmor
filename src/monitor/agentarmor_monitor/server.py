"""AgentArmor's local server: Summarizer and its model, the rules, and the way to a decision.

    agentarmor-monitor serve                                  # Summarizer only: watch and mark
    agentarmor-monitor serve --kai-package DIR                # Kai-4B loaded by this server
    agentarmor-monitor serve --kai-url https://kai.example \\
        --allow-remote --kai-token-file kai.token \\
        --kai-package-sha256 HASH                             # Kai somewhere else

The server holds Summarizer's daemon and its embedding model, always, and the rules. The
decision model is behind it in one of two places: loaded by this server, or at another
address, which can be a service on this machine, the near end of an SSH tunnel, or a host in
a cloud. With neither, the server watches and marks and decides nothing.

What it answers on its loopback port:

- GET /health: what is loaded, how far behind the sessions it is, whether the decision model
  answers and is the one configured, and under which rules it decides.
- POST /v1/decide: a gate names a proposed call (local.py) and gets a receipt (decide.py).
  The request holds no session text. The server builds what the decision model reads from
  its own record of the session and its own rules.
- POST /v1/systemone, only when the operator turned it on (--model-api): the decision model's
  own interface, for a caller that brings its own state.

Only this server sends session text off the machine, and only it holds the credential for a
remote model: over https only, to the one configured address, with no redirect followed, and
only when the operator said so (--allow-remote). The loopback port has no authentication: any
program on this machine can ask it. /v1/decide gives such a program nothing to send anywhere,
since the server writes every request itself. With --model-api a local program can have its
own text put to the decision model, with the server's credential when the model is remote:
that is the operator's choice to make, and it is off unless made.

The decision model is the one the operator configured, or its answer is not used: a reply is
admitted only if it names the configured model, readout and package (kai.profile). What a
service says about being ready for use as a policy is kept as its claim. Nothing here turns a
probability into a permission: every reply of this server says policy_ready false.
"""

import contextlib
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .kai import MAX_BYTES, KaiClient, KaiMismatch, KaiRejected, KaiUnavailable, admit
from .local import DECIDE

PLACES = ("local", "remote", "none")  # where the decision model is, as /health reports it
MODEL_API = "/v1/systemone"
QUESTION_BYTES = 4096  # a gate's question is a few ids
CONNECTIONS = 16  # handled at once; one more is closed unanswered, and its caller told nothing
REFUSED = {"error": "refused"}


class Absent:
    """No decision model: the server watches and marks, and decides nothing."""

    def metadata(self):
        return {"backend": "none", "model": None}

    def evaluate(self, value):
        raise RuntimeError("no decision model is behind this server")


class Relay:
    """A decision model at another address, asked on behalf of this server."""

    def __init__(self, client, fresh=5.0):
        self.client, self.fresh = client, fresh
        self.seen, self.at, self.lock = None, 0.0, threading.Lock()

    def metadata(self):
        """The other service's own account of itself, or that it did not answer.

        Asked again only after a few seconds: /health here is open to any local program, and
        each call must not become a request to the other address.
        """
        with self.lock:
            if self.seen is None or time.monotonic() - self.at > self.fresh:
                try:
                    self.seen = self.client.health() | {"reachable": True}
                except (KaiUnavailable, KaiRejected) as error:
                    unreached = {"backend": "relay", "model": None, "reachable": False}
                    self.seen = unreached | {"error": str(error)}
                self.at = time.monotonic()
            return dict(self.seen) if isinstance(self.seen, dict) else {"reachable": False}

    def evaluate(self, value):
        # Sorted as the handler below sorts them for a caller on the port: ValueError is
        # "refused" (the same bytes will not pass), anything else "unavailable" (try later).
        try:
            return self.client._call(MODEL_API, value)
        except KaiRejected as error:
            raise ValueError(str(error)) from None
        except KaiUnavailable as error:
            raise RuntimeError(str(error)) from None


class Front:
    """The decision model behind the server, as the server itself vouches for it.

    expected is the model the operator configured (kai.profile), or None when there is no
    decision model. Every answer is admitted against it before anyone gets it, and leaves
    here saying policy_ready false: what the model's own service claims about that is kept
    beside it, as a claim.

    One request at a time reaches a model loaded by this server: its engine is asked both by
    the watcher's worker and by the decisions. A model at another address takes requests as
    they come, so a slow answer to one does not hold up the next.
    """

    def __init__(self, judge, place, runtime=None, expected=None):
        if place not in PLACES:
            raise ValueError(f"the decision model is one of {PLACES}")
        if (place == "none") != (expected is None):
            raise ValueError("a decision model is configured with its profile, and only then")
        self.judge, self.place, self.expected = judge, place, expected
        self.lock = threading.Lock() if place == "local" else contextlib.nullcontext()
        self.summarizer = None
        if runtime is not None:  # what marks sessions on this server, by its own report
            self.summarizer = {"loaded": True, "embedding": dict(runtime.embedder.info)}
            self.summarizer["detector"] = runtime.detector

    def decision_model(self):
        """Where the decision model is, what was configured, and whether it is what answers."""
        about = {"at": self.place, "configured": self.expected}
        if self.place == "none":
            return about | {"reachable": None, "identity": None}
        try:
            said = dict(self.judge.metadata())
        except Exception as error:  # a model that cannot even say what it is
            return about | {"reachable": False, "identity": None, "error": type(error).__name__}
        reachable = said.pop("reachable", True)
        claim = said.pop("policy_ready", None)
        identity = None
        if reachable:
            try:
                admit(said, self.expected)
                identity = "ok"
            except KaiMismatch as error:
                identity = f"mismatch: {error}"
        about |= {"reachable": bool(reachable), "identity": identity, "reported": said}
        return about | {"claims_policy_ready": claim is True}

    def evaluate(self, value):
        """The decision model's answer to a request, admitted. Raises KaiMismatch when what
        answered is not the configured model: its answer goes no further."""
        with self.lock:
            reply = self.judge.evaluate(value)
        admit(reply, self.expected)
        claim = reply.get("policy_ready") is True
        return reply | {"policy_ready": False, "claims_policy_ready": claim}


class InProcess(KaiClient):
    """This server's own way to the decision model behind it: a call, not a request.

    The watcher and the decisions use it. It reads replies exactly as the client over HTTP
    does (KaiClient.ask), and sorts failures as the handler does for a caller on the port:
    a request the model refuses will be refused again, anything else may pass later.
    """

    def __init__(self, front):
        self.front, self.url, self.remote, self.token = front, "in-process", False, None
        self.expected = front.expected

    def identify(self):
        about = self.front.decision_model()
        if not about.get("reachable"):
            raise KaiUnavailable(str(about.get("error") or "the decision model does not answer"))
        if about.get("identity") != "ok":  # named by the part that differs, as admit() does
            raise KaiMismatch(str(about.get("identity")).removeprefix("mismatch: "))
        return about

    def _call(self, path, body=None):
        try:
            return self.front.decision_model() if body is None else self.front.evaluate(body)
        except KaiMismatch:
            raise
        except (ValueError, KeyError, TypeError, RecursionError) as error:
            raise KaiRejected(type(error).__name__) from None
        except Exception as error:
            raise KaiUnavailable(type(error).__name__) from None


def read_json(data):
    """A request body as JSON, strictly: one meaning or none. Raises ValueError."""

    def pairs(items):
        found = {}
        for key, value in items:
            if key in found:
                raise ValueError("duplicate JSON key")
            found[key] = value
        return found

    def no_constant(_):
        raise ValueError("nonfinite JSON value")

    return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=no_constant)


def health(front, decider, monitor=None, model_api=False):
    """What /health says. No session text, no secret, and never that anything is ready."""
    about = {"server": "agentarmor-local", "summarizer": front.summarizer}
    about["api"] = {"decide": DECIDE, "model": MODEL_API if model_api else None}
    if monitor is not None:
        # How far behind the server is: sessions whose log has moved past their summary, and
        # marked entries that still wait for the decision model.
        with monitor.state:
            waiting = len(monitor.queued) + len(monitor.waiting)
        try:
            behind = len(monitor.stale())
        except OSError:
            behind = None
        about["sessions"] = {"held": len(monitor.sessions), "behind": behind, "waiting": waiting}
    about["decision_model"] = front.decision_model()
    about["decisions"] = decider.about()
    return about | {"advisory": True, "policy_ready": False}


def handler(front, decider, monitor=None, model_api=False):
    """The request handler of the local server. It logs no request and no body."""

    class Handler(BaseHTTPRequestHandler):
        server_version, sys_version = "agentarmor-local", ""

        def setup(self):
            super().setup()
            self.connection.settimeout(10)  # for reading a request, not for deciding one

        def log_message(self, *_):
            pass  # never a path, a header or a body

        def reply(self, status, body):
            data = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def body(self, limit):
            """The request's JSON, or None after an error reply was sent."""
            lengths = self.headers.get_all("Content-Length", [])
            framed = not self.headers.get("Transfer-Encoding") and len(lengths) == 1
            if not framed or not lengths[0].isdigit():
                raise ValueError("invalid framing")
            size = int(lengths[0])
            if not 0 < size <= limit:
                self.reply(413, REFUSED)
                return None
            data = self.rfile.read(size)
            if len(data) != size:
                raise ValueError("incomplete body")
            return read_json(data)

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, health(front, decider, monitor, model_api))
            else:
                self.reply(404, REFUSED)

        def do_POST(self):
            try:
                if self.path == DECIDE:
                    asked = self.body(QUESTION_BYTES)
                    if asked is None:
                        return
                    result = decider.decide(asked)
                    if result["why"] == "request":  # not a question a gate asks
                        raise ValueError("not a question")
                elif self.path == MODEL_API and model_api:
                    value = self.body(MAX_BYTES)
                    if value is None:
                        return
                    result = front.evaluate(value)
                else:
                    self.reply(404, REFUSED)
                    return
                json.dumps(result, allow_nan=False)  # before any success header is sent
            except (ValueError, KeyError, TypeError, RecursionError, TimeoutError):
                self.reply(400, REFUSED)
                return
            except Exception:  # KaiMismatch among them: no answer that can be used
                self.reply(503, REFUSED)
                return
            self.reply(200, result)

    return Handler


class Bounded(ThreadingHTTPServer):
    """A threading server that handles so many connections at once and no more.

    A thread for every connection, without end, lets any local program hold the server's
    memory and its decision model. One connection too many is closed at once: a gate reads
    that as no answer, which it never takes for a pass.
    """

    daemon_threads = True

    def __init__(self, address, handle, limit=CONNECTIONS):
        super().__init__(address, handle)
        self.room = threading.BoundedSemaphore(limit)

    def process_request(self, request, client_address):
        if not self.room.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:  # no thread for it: the room is free again
            self.room.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.room.release()


def listen(front, decider, port, monitor=None, model_api=False, limit=CONNECTIONS):
    """Answer on a loopback port; returns (server, thread)."""
    handle = handler(front, decider, monitor, model_api)
    server = Bounded(("127.0.0.1", port), handle, limit)
    thread = threading.Thread(target=server.serve_forever, name="agentarmor-local", daemon=True)
    thread.start()
    return server, thread


def free(port):
    """Whether the loopback port can be taken now: asked before any model is loaded."""
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def load_kai(package=None, revision=None, cache_dir=None, local_files_only=False):
    """Kai-4B loaded by this server from a local export or a pinned revision.

    The options are checked and the model is loaded by the Kai service's own code (src/vllm):
    the same refusals, the same verification of the export, the same engine. It needs that
    package with its serving extra, on a host the engine supports. A wrong option ends in
    that service's own message.
    """
    try:
        from agentarmor_vllm.server import load_kai_service, parse_args
    except ImportError:
        raise SystemExit(
            "loading Kai in this server needs AgentArmor's src/vllm package with its serve extra"
        ) from None
    options = ["--package", package] if package else ["--kai-revision", revision]
    options += ["--cache-dir", cache_dir] if cache_dir else []
    options += ["--local-files-only"] if local_files_only else []
    return load_kai_service(parse_args(options))
