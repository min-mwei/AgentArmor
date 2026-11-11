"""The gate's side of AgentArmor's local server. Standard library only.

A gate asks the local server one thing: decide about this call of this session. It names the
call by what the hook event carries (agent, session id, call id) and by the hash of the call
it holds, and gives this asking a name of its own (an attempt). It sends nothing else: no
rules, no text about the session, and not the call's arguments, which the server reads from
its own record of the session and checks against that hash. So what a caller says can never
become policy, evidence or provenance.

What comes back is a receipt: what was decided, and what it was decided on.

Three things are kept apart, each recorded by whoever knows it. The server records the
decision it issued. The gate records what it then did, which the server cannot know: a gate
that gave up waiting has stopped the call whatever the server answers later. And what became
of the call is whatever the hooks report afterwards. The attempt ties the first two together.

The server is on this machine. A gate holds no credential for a decision model, reads none
from a file or from the environment, and is refused any address that is not a loopback one.
A loopback port is not authentication: any program of any user on this machine can ask it.
That is why the question is this narrow, and why the server offers nothing wider unless its
operator turns it on.
"""

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

from .kai import MAX_BYTES, loopback

DECIDE = "/v1/decide"
RECEIPT = "agentarmor-decision/v1"
PORT = 8010  # the local server's own; 8009 is where a Kai service answers
VERDICTS = ("abstain", "deny", "undecided")
ON_ERROR = ("deny", "abstain")
# What a gate sends: these six strings, and nothing else.
ASKED = ("agent", "session_id", "tool_use_id", "call_sha256", "attempt", "on_error")
BOUND = ASKED[:5]  # what a receipt must name as the gate named it


class ServerUnavailable(Exception):
    """The local server did not give a receipt for this call."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def question(agent, session, call_id, identity, on_error="deny", attempt=None):
    """What a gate asks about one proposed call.

    attempt names this asking, so that the decision the server records and what the gate then
    does can be told to belong together; a new one is made unless one is given. on_error is
    what the gate says it will do without a verdict: the server keeps it with its record and
    decides nothing by it.
    """
    asked = {"agent": agent, "session_id": session, "tool_use_id": call_id}
    asked |= {"call_sha256": identity, "attempt": attempt or secrets.token_hex(8)}
    return asked | {"on_error": on_error}


def bound_to(receipt, asked):
    """Whether a receipt is the answer to this asking about this call, and well formed."""
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT:
        return False
    if receipt.get("verdict") not in VERDICTS or receipt.get("policy_ready") is not False:
        return False
    bound = receipt.get("bound")
    return isinstance(bound, dict) and all(bound.get(name) == asked[name] for name in BOUND)


class Local:
    """A gate's way to the local server: this machine only, and no credential."""

    def __init__(self, url=f"http://127.0.0.1:{PORT}", timeout=30.0):
        parts = urllib.parse.urlsplit(url)
        plain = parts.scheme == "http" and parts.hostname and not parts.username
        if not plain or parts.path not in ("", "/") or parts.query or parts.fragment:
            raise ValueError("the local server's address is http://127.0.0.1:PORT")
        if not loopback(url):
            raise ValueError(
                "a gate asks AgentArmor's local server, on this machine: the address must be "
                "a loopback one. A decision model elsewhere is the server's to reach."
            )
        self.url, self.timeout = url.rstrip("/"), timeout
        # Asked directly: no proxy from the environment, and nowhere it points on to.
        direct = urllib.request.ProxyHandler({})
        self._open = urllib.request.build_opener(direct, _NoRedirect).open

    def _call(self, path, body=None):
        data = None if body is None else json.dumps(body, allow_nan=False).encode("utf-8")
        headers = {"Content-Type": "application/json"} if data is not None else {}
        call = urllib.request.Request(self.url + path, data=data, headers=headers)  # noqa: S310
        try:
            with self._open(call, timeout=self.timeout) as reply:
                return json.loads(reply.read(MAX_BYTES + 1).decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ServerUnavailable(f"HTTP {error.code}") from None
        except (OSError, ValueError) as error:  # refused, timed out, not JSON
            raise ServerUnavailable(type(error).__name__) from None

    def health(self):
        return self._call("/health")

    def decide(self, asked):
        """The server's receipt for one call. Raises ServerUnavailable when there is none,
        or when what came back is not a receipt for that call."""
        receipt = self._call(DECIDE, asked)
        if not bound_to(receipt, asked):
            raise ServerUnavailable("not a receipt for this call")
        return receipt
