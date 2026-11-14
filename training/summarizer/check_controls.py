"""Checks a diagnostic control set at the package boundary (data request 012, MR199-3).

A control root is a campaign in the session-roots format with a "control" block: its row, its
counterpart, the expected decision, packet and host, and, where an agent layout changes what the
state can show, an expectation per deciding agent. The packet checks (check_campaigns.py,
check_evidence.py) validate the evidence a packet carries; they say nothing of these expectations.
This does, before any replay is scored:

- Every root is a valid campaign (campaign_contract.validate), so no step carries a field the
  contract does not read ("outcome": "denied" makes no refusal and is refused), and what became of
  a call is said once, of a call, and never beside an output it would not deliver.
- A view is a root as the replay delivers it to one deciding agent. A session that pins its agent
  replays that agent under either nominal layout, so a root whose expectations are per layout
  carries no pin; a case about one client says so (control.client) and has one view, in which
  sessions may pin their agents.
- Each view has one authoritative expectation: the root's, or its deciding agent's override in
  control.layouts ({"expected", "incomplete_because", "reason"}). An override may change the
  decision and the packet, never the host; it says why, and the replay bears the reason out: a
  step named in control.lifecycle is delivered differently to the two agents. The root's own
  expectation is the expectation of at least one view.
- Each step whose outcome a row turns on (a refusal, a failure, a call left pending) is declared in
  control.lifecycle, and is laid out for each view and checked: the events the hooks deliver for
  it are those of the declared outcome for the agent its session replays.
- The root's story decision is its expected decision. An expected incomplete packet names why
  (incomplete_because): why its history view is unfinished, in the words history.select() gives
  it, or the packet's unresolved condition (a call of the deciding session left with no result).
  That is the expected negative a review scores apart from a regression; control.may_lose names
  the facts that reason may take from the packet (evidence past the lookup's bound), and no
  others may go. The campaign contract
  routes a campaign undecided only for a capture gap (expect: insufficient), so a capture gap and
  nothing else requires that route.
- Twins name each other, share their row, and differ in exactly the steps control.differs names,
  read off their own steps ("session/step", or "session" when a session's own fields differ).

    python check_controls.py data/controls/session-controls-001-r1.jsonl [--report out.json]

Exit status 1 when any root has a problem. The report lists each root's views, with the effective
expectation and the deciding agent of each, the lifecycle as laid out, and the twin delta, for the
human semantic review this cannot replace.
"""

import argparse
import json
import sys

import campaign_contract as campaigns
import case_contract as contract
from campaign_contract import AGENTS, SCORERS
from common import read_jsonl

DECISIONS = ("allow", "deny")
PACKETS = ("complete", "incomplete")
HOSTS = ("none",)  # host-fact controls (row 20) are built by the Trainer from rendered states
REQUIRED = ("row", "expected", "derivation", "counterpart", "differs")
FIELDS = frozenset((*REQUIRED, "layouts", "lifecycle", "client", "incomplete_because", "may_lose"))
# Why a packet is not complete: why its history view is unfinished, in the words history.select()
# gives it (each routes the call undecided), and a call of the deciding session left with no result
# (the packet's unresolved condition: Codex delivers no refusal, so a refused call stays proposed).
HISTORY_REASONS = ("capture_gap", "lookup_unfinished", "session_reference_unbound", "not_indexed")
# What the lookup could not tell (history.select()'s "unsupported"), each of which leaves it
# unfinished: a control may name the precise one its row turns on.
UNSUPPORTED = (
    "control_load_unresolved",
    "keyed_producer_not_kept",  # MR233: a key-value observation whose producing call is not kept
    "keyed_observation_cut",  # MR233: a key-value observation not shown whole
    "program_text_cut",
    "named_body_cut",
    "dependency_search_bounded",
    "candidate_matches_a_write",
    "module_resolution_unknown",
    "object_calls_not_kept",
    "object_call_effects_not_known",
)
REASONS = (*HISTORY_REASONS, *UNSUPPORTED, "unresolved_condition")
# What the hooks deliver for a call, by its outcome: the mark of the event it ends with.
OUTCOMES = {"returned": "result", "refused": "denied", "failed": "failed", "proposed": None}
_ENDS = ("result", "denied", "failed")


def _expected_problems(expected, where):
    if not isinstance(expected, dict) or set(expected) != {"decision", "packet", "host"}:
        return [f"{where}: expected holds exactly decision, packet and host"]
    problems = []
    for key, allowed in (("decision", DECISIONS), ("packet", PACKETS), ("host", HOSTS)):
        if expected[key] not in allowed:
            problems.append(f"{where}: expected {key} is one of {', '.join(allowed)}")
    return problems


def _because_problems(expected, because, where):
    """An incomplete expectation names why, from REASONS; a complete one names nothing."""
    if not isinstance(expected, dict):
        return []
    if expected.get("packet") == "incomplete":
        if not because or not isinstance(because, list) or set(because) - set(REASONS):
            return [
                f"{where}: an expected incomplete packet names why, in incomplete_because, "
                f"from {', '.join(REASONS)}"
            ]
    elif because is not None:
        return [f"{where}: an expected complete packet names no reason it is incomplete"]
    return []


def effective(root, agent):
    """(expected, incomplete_because) of a control root's view whose deciding agent is agent: its
    override in control.layouts when it has one, else the root's own. The one place a view's
    authoritative expectation is read, for this check, the review and the arms' labels alike."""
    control = root["control"]
    override = (control.get("layouts") or {}).get(agent)
    if override is None:
        return control["expected"], control.get("incomplete_because")
    return override.get("expected"), override.get("incomplete_because")


def views(root):
    """{deciding agent: (nominal layout, {session id: the agent it replays})}: the views the replay
    delivers. With no client, one per agent; a client-specific case has one."""
    client = root["control"].get("client")
    found = {}
    for layout in [client] if client else AGENTS:
        agents = {session["id"]: session.get("agent", layout) for session in root["sessions"]}
        found.setdefault(agents[root["decide"]["session"]], (layout, agents))
    return found


def _marks(root, layout):
    """{(session, step): the marks of the events laid out for it} under one nominal layout."""
    laid = campaigns.layout(root, layout)
    return {
        (part["id"], step): marks
        for part in laid["parts"]
        for step, marks in part["laid"]["where"].items()
    }


def _apart(seen, entry):
    """Whether a lifecycle entry has its step delivered differently in the views: the outcomes it
    gives for the agents that step's session replays differ."""
    session = str(entry.get("step") or "").partition("/")[0]
    return len({entry.get(agents.get(session)) for _, agents in seen.values()}) > 1


def _outcome(marks):
    """What the hooks delivered for a call, read off its marks."""
    ends = [mark for mark in _ENDS if mark in marks]
    if not ends:
        return "proposed"
    return {"result": "returned", "denied": "refused", "failed": "failed"}[ends[0]]


def delta(root, twin):
    """The steps two twins differ in, read off their own steps: "session/step" for a step one of
    them lacks or holds differently, "session" for a session one lacks or whose own fields differ."""
    mine = {session["id"]: session for session in root["sessions"]}
    theirs = {session["id"]: session for session in twin["sessions"]}
    found = set()
    for name in mine.keys() | theirs.keys():
        if name not in mine or name not in theirs:
            found.add(name)
            continue
        own = {key: value for key, value in mine[name].items() if key != "steps"}
        other = {key: value for key, value in theirs[name].items() if key != "steps"}
        if own != other:
            found.add(name)
        steps = {step["id"]: step for step in mine[name]["steps"]}
        others = {step["id"]: step for step in theirs[name]["steps"]}
        found |= {
            f"{name}/{sid}"
            for sid in steps.keys() | others.keys()
            if steps.get(sid) != others.get(sid)
        }
    return sorted(found)


def check_root(root, roots):
    """(problems, report) for one control root, with the other roots of its file by id."""
    name = root.get("id", "?")
    problems = [f"{name}: {problem}" for problem in campaigns.validate(root, max(SCORERS))]
    control = root.get("control")
    if not isinstance(control, dict):
        return problems + [f"{name}: a control root carries a control block"], {}
    problems += [f"{name}: control lacks {key}" for key in REQUIRED if key not in control]
    problems += [
        f"{name}: control has unknown field {key!r}" for key in sorted(set(control) - FIELDS)
    ]
    if "expected" not in control or "counterpart" not in control:
        return problems, {}
    expected = control["expected"]
    problems += _expected_problems(expected, f"{name} control")
    client = control.get("client")
    if client is not None and client not in AGENTS:
        problems.append(f"{name}: control.client is one of {', '.join(AGENTS)}")
        return problems, {}
    pinned = [session["id"] for session in root["sessions"] if "agent" in session]
    if pinned and client is None:
        problems.append(
            f"{name}: sessions {', '.join(pinned)} pin their agent, so both nominal layouts would "
            "replay them as that agent: remove the pins, or make the case client-specific "
            "(control.client)"
        )
    if client is not None and control.get("layouts"):
        problems.append(f"{name}: a client-specific case has one view: its expectation is expected")
    story = root.get("story") or {}
    if story.get("decision") not in (None, expected.get("decision")):
        problems.append(
            f"{name}: its story decides {story['decision']}, its control expects otherwise"
        )
    because = control.get("incomplete_because")
    problems += _because_problems(expected, because, f"{name} control")
    # The facts the named reason may take from the packet, and no others (MR200-2): evidence past
    # the lookup's bound, say. Only an expected incomplete packet takes any.
    may_lose = control.get("may_lose")
    if may_lose is not None:
        known = {fact.get("id") for fact in root.get("facts", ())}
        if not isinstance(may_lose, list) or not may_lose or set(may_lose) - known:
            problems.append(f"{name}: control.may_lose is a list of the root's own fact ids")
        elif expected.get("packet") != "incomplete":
            problems.append(f"{name}: only an expected incomplete packet may lose a fact")
    if ("capture_gap" in (because or ())) != (root.get("route") == "undecided"):
        problems.append(
            f"{name}: a capture gap is routed undecided (expect: insufficient), and nothing else "
            "is: a packet incomplete for another reason keeps its campaign's route"
        )
    # What the hooks deliver for each declared step, in each view, laid out and read back.
    lifecycle = control.get("lifecycle") or []
    declared, laid_out = set(), {}
    try:
        seen = views(root)
        for agent, (layout, _) in seen.items():
            laid_out[agent] = _marks(root, layout)
    except (KeyError, TypeError, ValueError, StopIteration) as error:
        return problems + [f"{name}: it cannot be laid out ({error!r})"], {}
    for entry in lifecycle:
        step = entry.get("step") if isinstance(entry, dict) else None
        session, _, sid = str(step or "").partition("/")
        if (session, sid) not in laid_out[next(iter(laid_out))]:
            problems.append(f"{name}: control.lifecycle names {step!r}, no step of a session")
            continue
        declared.add((session, sid))
        for agent, (_, agents) in seen.items():
            wanted = entry.get(agents[session])
            if wanted not in OUTCOMES:
                problems.append(
                    f"{name}: control.lifecycle {step}: an outcome for {agents[session]} is one "
                    f"of {', '.join(OUTCOMES)}"
                )
                continue
            got = _outcome(laid_out[agent][(session, sid)])
            if got != wanted:
                problems.append(
                    f"{name}: {step} is declared {wanted} for {agents[session]}, and the hooks "
                    f"deliver it {got}"
                )
    for session in root["sessions"]:
        for step in session["steps"]:
            told = [key for key in contract.LIFECYCLE if key in step]
            if told and (session["id"], step["id"]) not in declared:
                problems.append(
                    f"{name}: {session['id']}/{step['id']} is {told[0]}: the row turns on what "
                    "became of it, so control.lifecycle declares it for each agent"
                )
    # One authoritative expectation per view; an override says why, and the replay bears it out.
    overrides = control.get("layouts") or {}
    effective = {}
    for agent in seen:
        override = overrides.get(agent)
        if override is None:
            effective[agent] = (expected, because)
            continue
        where = f"{name} control.layouts.{agent}"
        if not isinstance(override, dict) or set(override) - {
            "expected",
            "incomplete_because",
            "reason",
        }:
            problems.append(f"{where}: an override holds expected, incomplete_because and reason")
            continue
        problems += _expected_problems(override.get("expected"), where)
        told = override.get("incomplete_because")
        problems += _because_problems(override.get("expected"), told, where)
        effective[agent] = (override.get("expected"), told)
        if effective[agent] == (expected, because):
            continue
        if (override.get("expected") or {}).get("host") != expected.get("host"):
            problems.append(f"{where}: an override never changes the host expectation")
        if "capture_gap" in (told or ()):
            problems.append(f"{where}: a capture gap is the log's, the same for every agent")
        if not str(override.get("reason") or "").strip():
            problems.append(f"{where}: an override that differs from the root's says why (reason)")
        if not any(_apart(seen, entry) for entry in lifecycle if isinstance(entry, dict)):
            problems.append(
                f"{where}: the replay does not bear the override out: no step in control.lifecycle "
                "is delivered differently to the two agents"
            )
    for agent in sorted(set(overrides) - set(seen)):
        problems.append(f"{name}: control.layouts names {agent}, which decides in no view")
    if (expected, because) not in effective.values():
        problems.append(f"{name}: the root's expectation is the expectation of no view")
    # Twins.
    twin = roots.get(control["counterpart"])
    if twin is None:
        problems.append(f"{name}: its counterpart {control['counterpart']!r} is not in the file")
        found = []
    else:
        if (twin.get("control") or {}).get("counterpart") != name:
            problems.append(f"{name}: its counterpart does not name it back")
        if (twin.get("control") or {}).get("row") != control["row"]:
            problems.append(f"{name}: its counterpart is of another row")
        found = delta(root, twin)
        if not found:
            problems.append(f"{name}: it does not differ from its counterpart")
        if "differs" in control and sorted(control["differs"]) != found:
            problems.append(
                f"{name}: control.differs says {sorted(control['differs'])}, and the twins differ "
                f"in {found}"
            )
    report = {
        "id": name,
        "row": control["row"],
        "views": {
            agent: {"layout": layout, "agents": agents}
            | dict(
                zip(
                    ("expected", "incomplete_because"),
                    effective.get(agent, (None, None)),
                    strict=True,
                )
            )
            for agent, (layout, agents) in seen.items()
        },
        "lifecycle": {
            agent: {
                f"{session}/{sid}": _outcome(laid_out[agent][(session, sid)])
                for session, sid in sorted(declared)
            }
            for agent in seen
        },
        "differs": found,
    }
    return problems, report


def panel_problems(panels, roots):
    """Problems of rendered panels of a control set (MR201-2): a panel whose decision question's
    label is not its view's authoritative expectation (effective), by the panel's run
    (root/deciding agent). The render copies the arms' label; this holds what was written."""
    found = []
    for panel in panels:
        root_id, _, agent = str((panel.get("_meta") or {}).get("run") or "").partition("/")
        root = roots.get(root_id)
        if root is None or not root.get("control"):
            continue
        wanted = effective(root, agent)[0]["decision"]
        question = (panel.get("questions") or {}).get("decision") or {}
        if question.get("label") != wanted:
            found.append(
                f"{root_id}/{agent}: its decision question says {question.get('label')}, and its "
                f"view expects {wanted}"
            )
    return found


def check(rows):
    """(problems, reports, coverage) for a whole control file."""
    roots = {root.get("id"): root for root in rows}
    problems, reports = [], []
    if len(roots) != len(rows):
        problems.append("every root needs its own id")
    for root in rows:
        found, report = check_root(root, roots)
        problems += found
        reports.append(report)
    coverage = {
        "roots": len(rows),
        "views": sum(len(report.get("views", {})) for report in reports),
        "both_clients": sum(len(report.get("views", {})) == len(AGENTS) for report in reports),
        "one_client": sum(len(report.get("views", {})) == 1 for report in reports),
    }
    return problems, reports, coverage


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("controls")
    parser.add_argument("--report", help="write the per-root report here (JSON)")
    parser.add_argument(
        "--panels", action="append", default=[], help="rendered panels to hold to the labels"
    )
    args = parser.parse_args(argv)
    rows = list(read_jsonl(args.controls))
    problems, reports, coverage = check(rows)
    roots = {root.get("id"): root for root in rows}
    for path in args.panels:
        problems += panel_problems(list(read_jsonl(path)), roots)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as out:
            json.dump({"coverage": coverage, "roots": reports}, out, indent=2)
            out.write("\n")
    for problem in problems:
        print(problem)
    print(json.dumps(coverage))
    print(f"{len(problems)} problems in {len(rows)} roots")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
