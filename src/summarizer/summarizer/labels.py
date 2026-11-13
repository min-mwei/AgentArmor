"""Indicators: the labels and confidence the summarizer attaches to entries and observables.

Standard library only.

The summarizer tags; it does not judge. An indicator says that something worth a detector's
attention was observed: a label, how strongly that observation alone marks its subject as
suspicious (its confidence), and what raised it (its basis). An entry can be suspicious without
being part of an attack, and deciding which it is belongs to the detection model that reads the
summary. Tagging therefore favors recall: every observation becomes an indicator, weak ones
included, and nothing is suppressed for being common in benign sessions.

A subject's confidence combines its indicators as independent causes (noisy-OR), so corroboration
raises it and a single weak indicator stays low. The numbers below are priors read off
AgentArmor's threat model, not measured precision.
"""

import ipaddress
import re

LEVELS = (("high", 0.6), ("medium", 0.3))  # a subject with any indicator at all is at least "low"
LOWER_TRUST = ("untrusted", "derived")  # text the agent was shown, as opposed to told by the user

# What a deterministic flag on an agent action indicates, and how suspicious that is by itself.
ACTION = {
    "observer_tamper": ("observer_tampering", 0.70),  # this project's own evidence or daemon
    "reverse_shell": ("command_and_control", 0.60),  # a shell handed to a remote host
    "untrusted_target": ("untrusted_destination", 0.60),  # contacts a host only content named
    "control_bypass": ("control_bypass", 0.55),  # approvals, sandbox or hooks switched off
    "symlink": ("symlink_redirect", 0.50),  # the path resolves somewhere else
    "defense_off": ("defense_evasion", 0.50),  # security tools, logs or history switched off
    "dns_exfil": ("data_egress", 0.50),  # a DNS name built from command output or a variable
    "cloud_metadata": ("sensitive_access", 0.45),  # the instance credential service
    "capture_host": ("data_egress", 0.45),  # a host that exists to receive or relay data
    "data_in_url": ("data_egress", 0.45),  # command output or a variable placed in a request
    "exposure": ("exposure", 0.45),  # something made public or served to the network
    "identity_change": ("persistence", 0.45),  # new accounts, keys, roles or policy bindings
    "resource_abuse": ("resource_abuse", 0.45),  # fork bombs, miners
    "persistence_write": ("persistence", 0.45),  # agent, shell, Git or CI configuration written
    "tunnel": ("command_and_control", 0.40),  # a route in from outside
    "escalation": ("privilege_escalation", 0.40),  # setuid, world-writable, host mounts
    "scheduled_task": ("persistence", 0.40),  # cron, at, launchd, systemd timers
    "remote_exec": ("remote_code", 0.40),  # fetched text piped into an interpreter
    "obfuscated": ("obfuscation", 0.40),  # decoded or encoded payload in the command
    "sensitive": ("sensitive_access", 0.35),  # credentials, keys or environment touched
    "untrusted_reference": ("untrusted_reference", 0.35),  # writes a host only content named
    "untrusted_package": ("untrusted_package", 0.35),  # installs a package only content named
    "collection": ("collection", 0.35),  # clipboard, screen, traffic, secret hunting, staging
    "registry_change": ("supply_chain", 0.35),  # where packages or code come from was changed
    "file_encryption": ("file_encryption", 0.30),  # files encrypted in place or into an archive
    "network_scan": ("discovery", 0.30),  # scanners and brute-forcers
    "network_send": ("data_egress", 0.30),  # data sent to an external host
    "steering_text": ("steering_text", 0.30),  # the agent itself writes agent-steering wording
    "weak_steering_text": ("steering_text", 0.15),  # and only wording of the weak kinds below
    "privilege": ("privileged_command", 0.25),
    "destructive": ("destructive", 0.25),
    "lockfile_edit": ("supply_chain", 0.25),  # a dependency lockfile changed by hand
    "infra_destroy": ("destructive", 0.40),  # cloud or cluster resources deleted
    "secret_search": ("collection", 0.12),  # searching files for credentials: common, and a start
    "internal_target": ("internal_access", 0.20),  # loopback, private or link-local address
    "outside_write": ("outside_workspace_write", 0.20),
    "timestamp_change": ("defense_evasion", 0.15),  # a file's times set by hand
    "agent_spawn": ("agent_spawn", 0.15),
    "persistence_ref": ("config_reference", 0.10),  # such configuration named, write not seen
    "mcp_write": ("external_write", 0.10),
    "package_install": ("package_install", 0.08),
    "analysis_partial": ("analysis_partial", 0.10),  # the command was not read in every way
    "recon": (
        "discovery",
        0.08,
    ),  # who and where am I: common, and the first step of most intrusions
    "network": ("network_access", 0.05),
    "outside_read": ("outside_workspace_read", 0.05),
    "mcp": ("external_tool", 0.03),
}
# A flag here says nothing its stronger companion has not already said.
IMPLIED = {"network": "network_send", "mcp": "mcp_write", "package_install": "untrusted_package"}
DENIED = 0.55  # the agent's own permission classifier refused the action
FLOW_SEND, FLOW_ACCESS = 0.40, 0.20  # reaching outside after untrusted input and secrets
# The status of a call whose result the agent delivered. It is a reported result, not proof of
# every effect: Claude Code reports failures separately, but Codex delivers a result for a command
# that exited with an error too.
COMPLETED = "returned"
DRIFT_SLOPE, DRIFT_MAX = 2.0, 0.25  # per unit of similarity margin; alone it stays low
DRIFT_MARGIN = 0.05  # smaller margins are within the noise of an untuned encoder
RISKY = 0.2  # an action at least this suspicious on other grounds lends weight to what it follows

# What a flag on lower-trust content indicates. The *_text flags come from facts.text_flags.
CONTENT = {
    "hidden_text": ("hidden_text", 0.50),  # bidi overrides, tag characters, zero-width runs
    "override_text": ("agent_directive", 0.50),  # "ignore the previous instructions"
    "concealment_text": ("concealment_request", 0.45),  # "do not tell the user"
    "authority_text": ("authority_claim", 0.40),  # system or developer markers in data
    "bypass_text": ("bypass_request", 0.40),  # "skip approval", "disable the sandbox"
    "persistence_text": ("persistence_request", 0.40),  # "add this to AGENTS.md"
    "exfiltration_text": ("exfiltration_request", 0.40),  # "send ... to https://..."
    "addressed_text": ("agent_directive", 0.35),  # speaks to an AI agent or assistant
    "trigger_text": ("agent_directive", 0.35),  # ties an action to this text being read
    "responder_text": ("agent_directive", 0.30),  # speaks of the reader's reply or rules
    "directive_text": ("agent_directive", 0.20),  # "you must ...": weak, people get told too
    "deferred_text": ("agent_directive", 0.20),  # "the next time ...": weak, schedules say it too
    "approval_text": ("authority_claim", 0.20),  # "already approved": weak, so do review records
    "third_person_text": ("agent_directive", 0.15),  # "the assistant will ...": weak, so do docs
    "called_text": ("agent_directive", 0.15),  # "Claude, ...": weak, a sentence about it too
    "secret_text": ("secret_request", 0.30),  # asks for keys, tokens or credentials
    "sensitive_reference": ("sensitive_reference", 0.25),  # names a credential path
    "config_reference": ("config_reference", 0.15),  # names agent, shell, Git or CI config
    "secret_material": ("secret_material", 0.15),  # a credential format appeared in the text
}
# Wording rules that are weak on purpose. In lower-trust content each has its own low prior
# above. In the agent's own prose (what it writes into a file, its last message) wording of
# these kinds alone marks the agent at "low" and not at the 0.30 of steering_text: documentation
# an agent is asked to write says such things all the time. directive_text is older and keeps
# the behaviour it was accepted with.
WEAK_TEXT = frozenset({"third_person_text", "approval_text", "deferred_text", "called_text"})
NAMED = 0.50  # the text first named a destination the agent later contacted
FOLLOWED_ONCE, FOLLOWED_MORE = 0.30, 0.45  # risky actions read more like it than any request
FOLLOWED = 0.05  # only unremarkable actions did
PROMPT_HIDDEN = 0.40

# Observables: hosts, addresses, URLs, mailboxes, paths and packages seen in the session.
IOC = {
    "untrusted_destination": 0.60,  # the agent contacted it and only lower-trust content named it
    "persistence": 0.45,  # a configuration path the agent wrote
    "untrusted_package": 0.40,  # installed, and only lower-trust content named it
    "sensitive_access": 0.35,  # a credential path the agent touched
    "untrusted_reference": 0.35,  # the agent wrote it somewhere and only content named it
    "data_egress": 0.30,  # the agent sent data to it
    "embedded_credentials": 0.30,  # user:password@ inside a URL
    "encoded_query": 0.25,  # a long or encoded query string
    "sensitive_reference": 0.25,  # a credential path lower-trust content named
    "punycode": 0.20,
    "ip_literal": 0.15,  # a public address used in place of a name
    "config_reference": 0.15,  # a configuration path lower-trust content named
    "package_install": 0.08,
    "contacted": 0.05,
    "named_by_content": 0.03,  # nothing but lower-trust content has named it
}
_ENCODED = re.compile(r"[?&][^=&#]*=[A-Za-z0-9+/_%-]{40,}")


def priors():
    """Everything in this module that sets a confidence or a level, as plain data.

    A summary names the detectors that marked it by a hash of this and of the wording rules, so
    that two summaries marked under different numbers can be told apart from their records.
    """
    constants = {
        "DENIED": DENIED,
        "FLOW_SEND": FLOW_SEND,
        "FLOW_ACCESS": FLOW_ACCESS,
        "DRIFT_SLOPE": DRIFT_SLOPE,
        "DRIFT_MAX": DRIFT_MAX,
        "DRIFT_MARGIN": DRIFT_MARGIN,
        "RISKY": RISKY,
        "NAMED": NAMED,
        "FOLLOWED_ONCE": FOLLOWED_ONCE,
        "FOLLOWED_MORE": FOLLOWED_MORE,
        "FOLLOWED": FOLLOWED,
        "PROMPT_HIDDEN": PROMPT_HIDDEN,
    }
    return {
        "levels": [list(level) for level in LEVELS],
        "action": {flag: list(value) for flag, value in ACTION.items()},
        "implied": IMPLIED,
        "content": {flag: list(value) for flag, value in CONTENT.items()},
        "weak_text": sorted(WEAK_TEXT),
        "ioc": IOC,
        "constants": constants,
        "combined_by": "noisy-or",
    }


def combine(indicators):
    """Return (labels, confidence, level) for a list of {"label", "confidence", "basis"}."""
    remaining, strongest = 1.0, {}
    for item in indicators:
        remaining *= 1.0 - item["confidence"]
        strongest[item["label"]] = max(strongest.get(item["label"], 0.0), item["confidence"])
    confidence = round(1.0 - remaining, 2)
    labels = sorted(strongest, key=lambda label: (-strongest[label], label))
    if not indicators:
        return labels, confidence, "none"
    return labels, confidence, next((name for name, bar in LEVELS if confidence >= bar), "low")


def refs(seqs):
    return ",".join(f"#{seq}" for seq in sorted(set(seqs)))


def _indicator(label, confidence, basis):
    return {"label": label, "confidence": confidence, "basis": basis}


def action_indicators(flags, status, after=None, link=None):
    """Indicators on an agent action.

    flags maps each deterministic flag to what raised it. after is (untrusted seq, sensitive seq,
    observed) when the action reaches outside after the session has read untrusted input and
    touched sensitive data; observed says that access is known to have happened rather than only
    been attempted. link is (intent, similarity, seq): how much the action resembles the nearest
    user request and the nearest lower-trust content it could have been caused by.

    Indicators describe the attempt. Whether the call went through is the entry's status.
    """
    found = [
        _indicator(label, confidence, flags[flag])
        for flag, (label, confidence) in ACTION.items()
        if flag in flags and IMPLIED.get(flag) not in flags
    ]
    if status == "denied":
        basis = flags.get("denied") or "refused by the agent's permission classifier"
        found.append(_indicator("denied", DENIED, basis))
    if after:
        untrusted, sensitive, observed = after
        sends = "network_send" in flags or "mcp_write" in flags
        touched = "sensitive access" if observed else "attempted sensitive access"
        basis = f"reaches outside after untrusted input #{untrusted} and {touched} #{sensitive}"
        confidence = FLOW_SEND if sends and observed else FLOW_ACCESS
        found.append(_indicator("toxic_flow", confidence, basis))
    if link and link[1] is not None:
        intent, similarity, seq = link
        margin = similarity - (intent or 0.0)
        if margin >= DRIFT_MARGIN:
            asked = "no user request" if intent is None else f"any user request ({intent:.2f})"
            basis = f"resembles lower-trust #{seq} ({similarity:.2f}) more than {asked}"
            confidence = round(min(DRIFT_MAX, DRIFT_SLOPE * margin), 2)
            found.append(_indicator("goal_drift", confidence, basis))
    return found


def content_indicators(flags, named=(), followed=(), risky=(), matches=(), windows=0):
    """Indicators on lower-trust content the agent was shown.

    named are the seqs of actions this content named a destination for. followed are the seqs of
    actions that read more like it than like any user request, and risky those among them that
    are suspicious on other grounds. matches are (label, confidence, probe id, probability) for
    the embedding probes the content matched. windows is how many windows the content was
    embedded as when it was more than one: a match is then the best of that many, which a
    calibration fitted on texts of one window did not measure, and its basis says so.
    """
    found = [
        _indicator(label, confidence, flags[flag])
        for flag, (label, confidence) in CONTENT.items()
        if flag in flags
    ]
    if named:
        found.append(_indicator("destination_source", NAMED, f"names what {refs(named)} used"))
    if risky:
        confidence = FOLLOWED_ONCE if len(set(risky)) == 1 else FOLLOWED_MORE
        basis = f"risky {refs(risky)} resemble it more than any user request"
        found.append(_indicator("action_source", confidence, basis))
    elif followed:
        basis = f"{refs(followed)} resemble it more than any user request"
        found.append(_indicator("action_source", FOLLOWED, basis))
    best = f", best of {windows} windows" if windows > 1 else ""
    for label, confidence, probe, chance in matches:
        basis = f"embedding probe {probe} p={chance:.2f}{best}"
        found.append(_indicator(label, round(confidence, 2), basis))
    return found


def _public(address):
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def _uses(seen, role):
    """(returned seqs, attempted "#seq status" strings) of the actions that used an observable."""
    uses = [use for use in seen["uses"] if use["role"] == role]
    done = [use["seq"] for use in uses if use["status"] == COMPLETED]
    tried = [f"#{use['seq']} {use['status']}" for use in uses if use["status"] != COMPLETED]
    return done, tried


def _said(done, tried, verb, attempt):
    parts = [f"{verb} in {refs(done)}"] if done else []
    if tried:
        parts.append(f"{attempt} attempted in {', '.join(tried)}")
    return "; ".join(parts)


def ioc_indicators(kind, value, seen):
    """Indicators on one observable, from who named it and how the agent used it.

    seen records the first sighting ("first" seq and "by" trust), the first user mention ("user"),
    the first mention by each party ("mentions"), "referenced" for a path flag raised by
    lower-trust content, and "uses": one {"seq", "role", "status"} per agent action that named,
    contacted, sent to, read, wrote or installed it.
    Indicators stay on attempts; the basis says which uses returned a result. A use is untrusted
    when only lower-trust content had named the observable by then: a user who mentions it later
    does not rewrite what was known at the time.
    """
    found = []

    def add(label, basis):
        found.append(_indicator(label, IOC[label], basis))

    def foreign(seq=None):
        named = seen["user"] is not None and (seq is None or seen["user"] < seq)
        return seen["by"] in LOWER_TRUST and not named

    def any_foreign(role):
        return any(foreign(use["seq"]) for use in seen["uses"] if use["role"] == role)

    origin = f"first named by lower-trust #{seen['first']}"
    if kind in ("domain", "ip", "url", "email"):
        contacted = _said(*_uses(seen, "contacted"), "contacted", "contact")
        if contacted and any_foreign("contacted"):
            add("untrusted_destination", f"{contacted}; {origin}")
        elif any_foreign("agent"):
            named = _said(*_uses(seen, "agent"), "used by the agent", "use")
            add("untrusted_reference", f"{named}; {origin}")
        elif foreign() and not seen["uses"]:
            add("named_by_content", origin)
        sent = _said(*_uses(seen, "sent"), "data sent", "send")
        if sent:
            add("data_egress", sent)
        elif contacted:
            add("contacted", contacted)
        if kind == "ip" and _public(value):
            add("ip_literal", "public address used in place of a name")
        if "xn--" in value:
            add("punycode", "internationalized name in ASCII form")
        if kind == "url":
            if re.match(r"[a-z][a-z0-9+.-]*://[^/@]*:[^/@]*@", value, re.I):
                add("embedded_credentials", "user:password@ in the URL")
            if _ENCODED.search(value) or len(value.partition("?")[2]) > 120:
                add("encoded_query", "long or encoded query string")
    elif kind == "path":
        written = _said(*_uses(seen, "written"), "written", "write")
        if written:
            add("persistence", written)
        read = _said(*_uses(seen, "read"), "touched", "access")
        if read:
            add("sensitive_access", read)
        lower = [m["seq"] for m in seen.get("mentions", ()) if m["by"] in LOWER_TRUST]
        if seen.get("referenced") and lower:  # whoever else named it, lower-trust content did too
            add(seen["referenced"], f"named by lower-trust #{min(lower)}")
    elif kind == "package":
        installed = _said(*_uses(seen, "installed"), "installed", "install")
        if installed and any_foreign("installed"):
            add("untrusted_package", f"{installed}; {origin}")
        elif installed:
            add("package_install", installed)
    return found
