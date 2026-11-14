"""Check a file of authored campaigns before it goes to review.

    python training/summarizer/check_campaigns.py training/summarizer/data/synthetic/campaigns-dev-002.jsonl \\
        --existing training/summarizer/data/starter/campaigns-v1.jsonl \\
        --existing training/summarizer/data/synthetic/campaigns-dev-001.jsonl

`campaign_contract.py` says whether one campaign can be scored. This says whether a file of
them is fit to hand in: every campaign valid and replayable, its own evidence showable, the
pairs really pairs, the story of each one stated, and nothing stamped out of another.

It checks form. It cannot tell whether a label is right, whether a twin isolates the one
difference it claims, or whether two campaigns are one idea in other words: those are read by
a person. A file that passes is ready to be read, not accepted.

What it refuses (each is a line `{"id" or "file", "problems": [...]}` and exit status 1):

- a campaign `campaign_contract.validate` refuses, or whose reference packet (its own facts,
  bridges and limits written out from a replay) does not pass for either agent;
- no `story`, or one without `kind` ("attack" or "benign"), `decision` ("allow" or "deny":
  what a careful person holding all the evidence would decide about the proposed call; it is
  not the route) and `why`; an attack without a `technique`;
- a pair that is not two campaigns of one `group`, or, with `--attack-pairs`, not one attack
  and one benign twin;
- a bridged write that failed or was refused, whose report is not a required fact;
- two campaigns of different pairs whose sessions are the same once every word is masked;
- a step string of twenty characters or more that also stands in an `--existing` file, or in
  more than four pairs of this file;
- in a family of handoffs between agents, a campaign whose writer and user of the file are
  the same agent;
- with `--require NAME=N`, fewer than N campaigns in which NAME holds at the cutoff;
- a `story.area` that is not one of the six areas a request may ask an attack to name;
- a step that uses a file the task itself writes before the trace has written it (MR165-1).
  Laid out in the order its steps happen, a read of the file, or a command naming it (other
  than printf or echo), comes before the first step that writes it, and no earlier version
  of it is established. An earlier version is established in two ways only: a session
  declares the file `preexisting` (files of its project that exist before the trace begins,
  paths as its steps write them, `{cwd}` allowed); or the read is in the very session that
  then writes the file, and shows text other than what that session writes (the ordinary
  read-then-edit). A read that shows the very text a later write produces is refused even so.
  Declaring a file preexisting is a statement about the story, not a way to excuse a read: a
  later write never justifies an earlier read. A paraphrase of a later version, read in the
  session that writes it, cannot be told from an earlier version by this check: such reads
  are counted under `review` for a person to look at.

With `--manifest PATH` a file that passes also gets a manifest: for each campaign its id,
pair, group, family, numbers of sessions and steps and the hash of its content, and a
record of counts. It holds no text and no label, and is what those who may not read a
held-out file are given.

Counts come in two kinds and are named apart. `declared` is what the campaigns say of
themselves (a schedule, a damage marker, two agent names). `at_cutoff` is what is in fact
exercised when each campaign is laid out up to its call, for every agent: a session the
schedule never visits has not happened, and a gap in it is no gap. A request's numbers are
about `at_cutoff`. `review` holds what a reader should look at and no rule decides: prose
that recurs across pairs once names are masked.

The last line is the receipt to post with the file. It names the bytes that were checked
(each file is read once and hashed from what was parsed), the files compared against, the
options, this checker's own hash, and whether the reference evidence was replayed, for
which agents and with what. With `--no-replay` it says `"complete": false`: that is a
partial diagnostic and not the check a request asks for. No campaign text is printed.
"""

import argparse
import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from campaign_contract import (
    AGENTS,
    HOME,
    SCORER,
    code_version,
    layout,
    reference,
    replay,
    score,
    shell_words,
    validate,
)

KINDS, DECISIONS = ("attack", "benign"), ("allow", "deny")
OUTCOMES = ("result", "failed", "denied")
WRITE_OUTCOMES = (("denied", "refused"), ("failed", "failed"), ("result", "returned"))
LONG_STRING, MANY_PAIRS = 20, 4
REFERENCE_BUDGET = 20000  # characters the reference packet may take when it is replayed


def story_problems(campaign):
    story = campaign.get("story")
    if not isinstance(story, dict):
        return ["no story: kind, decision and why"]
    found = []
    if story.get("kind") not in KINDS:
        found.append(f"story.kind is one of {KINDS}")
    if story.get("decision") not in DECISIONS:
        found.append(f"story.decision is one of {DECISIONS}")
    if not isinstance(story.get("why"), str) or len(story["why"].split()) < 6:
        found.append("story.why is a sentence saying what the decision turns on")
    if story.get("kind") == "attack" and not story.get("technique"):
        found.append("an attack names its technique")
    if "area" in story and story["area"] not in AREAS:
        found.append(f"story.area is one of {AREAS}")
    return found


def outcome_problems(campaign):
    """A bridged write that failed or was refused must carry that report as a required fact.

    A write that returned reports only its path, which the later call names too, so nothing
    more can be asked of it here. A failure or a refusal is what tells such a case from the
    others, and a packet that leaves it out must not pass.
    """
    steps = {
        (session["id"], step["id"]): step
        for session in campaign["sessions"]
        for step in session["steps"]
    }
    found = []
    for bridge in campaign.get("bridges", ()):
        at = (bridge.get("session"), bridge.get("step"))
        if not {"error", "denied"} & set(steps.get(at, {})):
            continue
        events = {f["event"] for f in campaign["facts"] if (f["session"], f["step"]) == at}
        if not events & set(OUTCOMES):
            found.append(f"the write at {at[0]}/{at[1]} has no outcome among the facts")
    return found


def timeline(campaign):
    """Every step that happens before the call, in the order campaign_contract.layout replays
    them, as (session, step with its paths filled in, cwd)."""
    laid = layout(campaign)
    parts = {part["id"]: part for part in laid["parts"]}
    order = []
    for name, start, upto in laid["schedule"]:
        part = parts[name]
        steps = {step["id"]: step for step in part["laid"]["case"]["steps"]}
        here = sorted(
            (min(marks.values()), sid)
            for sid, marks in part["laid"]["where"].items()
            if start < min(marks.values()) <= upto
        )
        order += [(name, steps[sid], part["cwd"]) for _, sid in here]
    return order


_MAKES = {">", ">>", "-o", "--out", "--output"}  # the word after one of these is written
_SAYS = {"printf", "echo"}  # commands whose words are printed, not files used


def _resolved(word, cwd):
    path = HOME + word[1:] if word == "~" or word.startswith("~/") else word
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path))


def file_uses(step, cwd):
    """(paths a step writes, paths it reads or names) by a plain reading of the step.

    A write step writes its file and a read step reads its own. A command writes what follows
    a redirection or an output option and the destination of cp, mv, install or tee, and names
    every other word (the value after an option's = included). Whether a named file is read
    is not settled here: naming a file the task writes later is enough to look at."""
    if "write" in step:
        return {os.path.normpath(step["write"])}, set()
    if "read" in step:
        return set(), {os.path.normpath(step["read"])}
    command = step.get("run")
    if not isinstance(command, str):
        return set(), set()
    found = shell_words(command)
    words = [word for word, _ in found] if found is not None else command.split()
    says = bool(words) and words[0] in _SAYS  # its words are printed; a redirection still writes
    made, named = set(), set()
    for at, word in enumerate(words):
        if word in _MAKES or at == 0:
            continue
        before = words[at - 1]
        if before in _MAKES or (words[0] == "tee" and not word.startswith("-")):
            made.add(_resolved(word, cwd))
        elif word.startswith(">"):
            made.add(_resolved(word.lstrip(">"), cwd))
        elif words[0] in ("cp", "mv", "install") and at == len(words) - 1:
            made.add(_resolved(word, cwd))
        elif not says:
            for part in (word, word.split("=", 1)[1] if "=" in word else ""):
                if part and not part.startswith("-"):
                    named.add(_resolved(part, cwd))
    if words and not says:
        named.add(_resolved(words[0], cwd))  # a script run by its own path
    return made, named - made


def chronology(campaign):
    """(problems, reads to look at): steps that use a file the task writes before the trace has
    written it (MR165-1), and reads of an earlier version established only by the read itself."""
    order = timeline(campaign)
    first, texts = {}, defaultdict(list)
    for at, (session, step, cwd) in enumerate(order, 1):
        made, _ = file_uses(step, cwd)
        for path in made:
            first.setdefault(path, (at, session, step["id"]))
            if "write" in step:
                texts[path].append(str(step.get("content", "")).strip())
    cwds = {part["id"]: part["cwd"] for part in layout(campaign)["parts"]}
    declared = {
        os.path.normpath(path.replace("{cwd}", cwds[session["id"]]).replace("{home}", HOME))
        for session in campaign["sessions"]
        for path in session.get("preexisting", ())
    }
    found, look = [], []
    for at, (session, step, cwd) in enumerate(order, 1):
        _, used = file_uses(step, cwd)
        for path in sorted(used):
            made = first.get(path)
            if made is None or made[0] <= at:
                continue
            where = f"{session}/{step['id']} (step {at})"
            writer = f"{made[1]}/{made[2]} (step {made[0]})"
            shown, said = os.path.relpath(path, cwd), str(step.get("output", "")).strip()
            if "read" in step and said in texts[path]:
                found.append(f"{where} reads in {shown} the very text {writer} writes later")
            elif path in declared:
                continue
            elif "read" in step and made[1] == session:
                look.append(
                    f"{where} reads {shown} before its own session rewrites it at step {made[0]}"
                )
            else:
                found.append(
                    f"{where} uses {shown} before {writer} writes it, and no earlier version of it "
                    "is established"
                )
    return found, look


def chronology_problems(campaign):
    """Steps that use a file the task writes before the trace has written it (MR165-1)."""
    return chronology(campaign)[0]


def step_strings(campaign):
    return {
        value
        for session in campaign.get("sessions", ())
        for step in session.get("steps", ())
        for key, value in step.items()
        if key != "id" and isinstance(value, str) and len(value) >= LONG_STRING
    }


def told(campaign):
    """A campaign's story when it is one, and an empty one when it is not."""
    story = campaign.get("story")
    return story if isinstance(story, dict) else {}


def masked(campaign):
    """What a copy keeps: the sessions with their names for sessions and steps taken out and
    every word replaced.

    Sessions and steps are numbered by position, so a copy that only renames its sessions,
    steps, campaign, pair or group has the same fingerprint as its original. Digits inside
    what a step says or does are kept: an amount or a port is evidence, not a name.
    """
    body = []
    for session in campaign.get("sessions", ()):
        steps = {step.get("id"): number for number, step in enumerate(session.get("steps", ()))}
        kept = {key: value for key, value in session.items() if key not in ("id", "steps")}
        if "damage" in kept:
            kept["damage"] = steps.get(kept["damage"])
        kept["steps"] = [
            {key: value for key, value in step.items() if key != "id"}
            for step in session.get("steps", ())
        ]
        body.append(kept)
    text = json.dumps(body, sort_keys=True)
    return hashlib.sha256(re.sub(r"[A-Za-z]+", "w", text).encode()).hexdigest()


def shown_truthfully(campaign, runtime):
    """Whether the campaign's own evidence passes when written out, for every agent."""
    for agent in AGENTS:
        laid = layout(campaign, agent)
        summaries, _ = replay(laid, runtime)
        result = score(laid, summaries, reference(laid, summaries, REFERENCE_BUDGET))
        if not result["passes"]:
            return f"its own evidence does not pass when written out ({agent})"
    return None


AT_CUTOFF = ("returns_to_a_session", "gap_reached", "gap_reached_in_another_namespace")
AT_CUTOFF += ("bridge_across_agents", "sessions_reached_3_or_more", "agents_reached_2")
AT_CUTOFF += ("gap_reached_in_the_calls_namespace",)
AREAS = ("data_exposure", "credential_access", "file_activity", "persistence")
AREAS += ("control_change", "destructive_action")  # what an attack's story.area may name
# What stands for a name in prose: anything quoted, any word with a path or identifier
# character or a digit in it, and any word with a capital letter (a project, a person, a
# marker; also the first word of a sentence, which costs nothing).
_NAMES = re.compile(r"`[^`]*`|\"[^\"]*\"|'[^']*'|\S*[/.{}_\-\d:=@#]\S*|\b\w*[A-Z]\w*\b")


def at_cutoff(campaign):
    """For each agent's layout: what of the campaign has happened when its call is proposed.

    campaign_contract.layout is the authority: a session the schedule never visits has not
    happened, damage counts once its step is reached, and a session with no agent of its
    own takes the layout's.
    """
    found = []
    for agent in AGENTS:
        laid = layout(campaign, agent)
        parts = {part["id"]: part for part in laid["parts"]}
        mine = laid["deciding"]
        earlier = [part for part in laid["parts"] if not part["mine"] and not part["later"]]
        # A return is leaving a session, another one advancing, and coming back. The laid-out
        # schedule holds only the turns that advance a session; two turns of one session in
        # a row are one visit.
        visits = [name for name, _, _ in laid["schedule"]]
        visits = [name for at, name in enumerate(visits) if at == 0 or visits[at - 1] != name]
        one = {"returns_to_a_session": len(visits) > len(set(visits))}
        one["gap_reached"] = any(part["damaged"] for part in earlier)
        one["gap_reached_in_another_namespace"] = any(
            part["damaged"] and part["namespace"] != mine["namespace"] for part in earlier
        )
        one["gap_reached_in_the_calls_namespace"] = any(
            part["damaged"] and part["namespace"] == mine["namespace"] for part in earlier
        )
        one["bridge_across_agents"] = any(
            parts[bridge["session"]]["agent"] != mine["agent"]
            for bridge in campaign.get("bridges", ())
        )
        reached = [part for part in laid["parts"] if not part["later"]]
        one["sessions_reached_3_or_more"] = len(reached) >= 3
        one["agents_reached_2"] = len({part["agent"] for part in reached}) >= 2
        one["steps_before_the_call"] = sum(
            sum(1 for marks in part["laid"]["where"].values() if min(marks.values()) <= part["cut"])
            for part in laid["parts"]
        )
        one["steps_in_the_session_of_the_call"] = sum(
            1 for marks in mine["laid"]["where"].values() if min(marks.values()) <= mine["cut"]
        )
        found.append(one)
    return found


def prose_clusters(campaigns):
    """How many pieces of prose recur in more than four pairs once names are masked.

    Prompts, outputs and written content only, never commands: tool syntax repeats for
    good reasons. Paths, quoted names, anything with a dot, slash, brace, dash or underscore
    or digit in it, and words with a capital letter are replaced. This is for a reader to
    look at: it flags and decides nothing, and two stories can be one idea without sharing
    a sentence.
    """
    pairs = defaultdict(set)
    for campaign in campaigns:
        for session in campaign["sessions"]:
            for step in session["steps"]:
                for key in ("prompt", "output", "content"):
                    value = step.get(key)
                    if isinstance(value, str) and len(value) >= LONG_STRING:
                        pairs[_NAMES.sub("N", value)].add(campaign["pair_id"])
    sizes = sorted(
        (len(found) for found in pairs.values() if len(found) > MANY_PAIRS), reverse=True
    )
    return {"pieces": len(sizes), "largest_in_pairs": sizes[0] if sizes else 0}


def step_shape(step):
    """A step with its names and numbers masked and everything else kept: which fields it
    has, and the words of each value. Each value is masked by itself, before anything is
    put together, so that a field's name and a command's words are never taken for a
    quoted name and lost."""
    return tuple(
        (key, _NAMES.sub("N", value) if isinstance(value, str) else json.dumps(value))
        for key, value in sorted(step.items())
        if key != "id"
    )


def stamped_work(campaigns):
    """Campaigns whose longest session is mostly a few step shapes said over and over.

    A step's shape (step_shape) is its fields and their words with every name and number
    masked, commands included: a run of `printf` lines differing in a counter is one
    shape, and twelve different commands are twelve. For each campaign's longest session
    of twelve steps or more, the share of its steps taken by its three commonest shapes
    is found; a campaign counts when that
    share is over half. It is for a reader: long work that is real has intermediate
    state, and no count of shapes can see that.
    """
    found, shares = 0, []
    for campaign in campaigns:
        longest = max(campaign["sessions"], key=lambda session: len(session["steps"]))
        steps = longest["steps"]
        if len(steps) < 12:
            continue
        shapes = Counter(step_shape(step) for step in steps)
        share = sum(count for _, count in shapes.most_common(3)) / len(steps)
        shares.append(share)
        found += share > 0.5
    return {
        "campaigns_with_a_session_of_12_steps": len(shares),
        "mostly_three_shapes": found,
        "largest_share": round(max(shares), 2) if shares else 0,
    }


def check(campaigns, existing=(), runtime=None, attack_pairs=False):
    """({id: problems}, problems of the file, counts)."""
    by_id, of_file = defaultdict(list), []
    ids = Counter(c.get("id") for c in campaigns)
    of_file += [f"id {name} is used {count} times" for name, count in ids.items() if count > 1]
    known = set().union(*(step_strings(c) for c in existing)) if existing else set()
    pairs, shapes, used = defaultdict(list), defaultdict(set), defaultdict(set)
    for campaign in campaigns:
        name = campaign.get("id", "?")
        refused = validate(campaign)
        by_id[name] += refused
        by_id[name] += story_problems(campaign)
        if refused:
            continue
        by_id[name] += outcome_problems(campaign)
        by_id[name] += chronology_problems(campaign)
        if runtime is not None:
            wrong = shown_truthfully(campaign, runtime)
            by_id[name] += [wrong] if wrong else []
        pairs[campaign["pair_id"]].append(campaign)
        shapes[masked(campaign)].add(campaign["pair_id"])
        mine = step_strings(campaign)
        if mine & known:
            by_id[name].append(f"{len(mine & known)} step strings also stand in an existing file")
        for value in mine:
            used[value].add(campaign["pair_id"])
    for pair, members in pairs.items():
        kinds = sorted(str(told(m).get("kind", "?")) for m in members)
        if len(members) != 2 or len({m["group"] for m in members}) != 1:
            of_file.append(f"pair {pair} is not two campaigns of one group")
        elif attack_pairs and kinds != ["attack", "benign"]:
            of_file.append(f"pair {pair} is not one attack and one benign twin")
    copies = [sorted(found) for found in shapes.values() if len(found) > 1]
    of_file += [f"pairs {found} have the same sessions once words are masked" for found in copies]
    stamped = sum(1 for found in used.values() if len(found) > MANY_PAIRS)
    if stamped:
        of_file.append(f"{stamped} step strings are used in more than {MANY_PAIRS} pairs")
    valid = [c for c in campaigns if not validate(c)]
    counts = {"campaigns": len(campaigns), "pairs": len(pairs)}
    counts["groups"] = len({c["group"] for c in valid})
    for key in ("family", "route", "expect"):
        counts[key] = dict(Counter(c[key] for c in valid))
    stories = [told(c) for c in valid]
    counts["kind"] = dict(Counter(str(s.get("kind")) for s in stories))
    counts["decision"] = dict(Counter(str(s.get("decision")) for s in stories))
    # What a campaign declares, under names that say it is only declared.
    counts["declared"] = {
        "sessions_3_or_more": sum(len(c["sessions"]) >= 3 for c in valid),
        "schedule": sum("schedule" in c for c in valid),
        "both_agents_named": sum(
            len({s.get("agent") for s in c["sessions"] if s.get("agent")}) >= 2 for c in valid
        ),
        "damage": sum(any(s.get("damage") for s in c["sessions"]) for c in valid),
        "steps_20_or_more": sum(sum(len(s["steps"]) for s in c["sessions"]) >= 20 for c in valid),
    }
    # What is in fact exercised when the campaign is laid out up to its call, which is what
    # a request's numbers are about. A count is of campaigns where it holds for every agent.
    reached = [at_cutoff(c) for c in valid]
    counts["at_cutoff"] = {
        name: sum(all(one[name] for one in found) for found in reached) for name in AT_CUTOFF
    }
    sizes = [min(one["steps_before_the_call"] for one in found) for found in reached]
    counts["at_cutoff"]["steps_20_or_more"] = sum(size >= 20 for size in sizes)
    counts["at_cutoff"]["steps_40_or_more"] = sum(size >= 40 for size in sizes)
    counts["at_cutoff"]["steps_median"] = sorted(sizes)[len(sizes) // 2] if sizes else 0
    own = [min(one["steps_in_the_session_of_the_call"] for one in found) for found in reached]
    counts["at_cutoff"]["call_session_steps_20_or_more"] = sum(size >= 20 for size in own)
    # What an attack is about, where a request asks its stories to say: counted in pairs.
    if any("area" in story for story in stories):
        attacks = [c for c in valid if told(c).get("kind") == "attack"]
        counts["attack_pairs_by_area"] = dict(Counter(str(told(c).get("area")) for c in attacks))
    for campaign, found in zip(valid, reached, strict=True):
        handoff = "agents" in campaign["family"] or "handoff" in campaign["family"]
        if handoff and not all(one["bridge_across_agents"] for one in found):
            by_id[campaign["id"]].append(
                "a handoff between agents whose writer and user of the file are one agent"
            )
    # Bridged pairs by what the hooks report of the bridged write before the call, read off
    # the laid-out events and never off what an author wrote or left out: "returned" needs a
    # result event, "failed" and "refused" their own events, and a write with none of them
    # by the cutoff is "proposed". A pair counts under an outcome when a twin has it in
    # every agent's layout; one whose outcome depends on the layout is counted apart.
    outcomes = defaultdict(set)
    for campaign in valid:
        seen = []
        for agent in AGENTS:
            laid = layout(campaign, agent)
            parts = {part["id"]: part for part in laid["parts"]}
            for bridge in campaign.get("bridges", ()):
                part = parts[bridge["session"]]
                marks = part["laid"]["where"][bridge["step"]]
                had = {name for name, at in marks.items() if at <= part["cut"]}
                kind = next((label for event, label in WRITE_OUTCOMES if event in had), "proposed")
                seen.append((bridge["session"], bridge["step"], kind))
        by_write = defaultdict(set)
        for session, step, kind in seen:
            by_write[(session, step)].add(kind)
        for kinds in by_write.values():
            outcomes[kinds.pop() if len(kinds) == 1 else "differs_by_agent"].add(
                campaign["pair_id"]
            )
    counts["bridged_pairs_by_write_outcome"] = {k: len(v) for k, v in sorted(outcomes.items())}
    counts["review"] = {"prose_in_more_than_4_pairs_once_names_are_masked": prose_clusters(valid)}
    counts["review"]["work_stamped_from_a_few_shapes"] = stamped_work(valid)
    counts["review"]["reads_of_an_earlier_version_shown_only_by_the_read"] = sum(
        len(chronology(c)[1]) for c in valid
    )
    return {name: found for name, found in by_id.items() if found}, of_file, counts


def manifest(campaigns, digest):
    """What can be said of a file to those who may not read it: one record for each campaign
    with its identity, family, size and the hash of its own content, and one of counts.
    No text of a campaign, and none of its labels (story, route, expect, facts)."""
    rows = []
    for campaign in campaigns:
        body = json.dumps(campaign, sort_keys=True, ensure_ascii=False).encode("utf-8")
        sessions = campaign.get("sessions", ())
        row = {key: campaign.get(key) for key in ("id", "pair_id", "group", "family")}
        row |= {"sessions": len(sessions), "steps": sum(len(s.get("steps", ())) for s in sessions)}
        rows.append(row | {"sha256": hashlib.sha256(body).hexdigest()})
    pairs = len({campaign.get("pair_id") for campaign in campaigns})
    return [*rows, {"file_sha256": digest, "campaigns": len(campaigns), "pairs": pairs}]


def read_once(path):
    """(the campaigns of a file, the SHA-256 of the very bytes they were parsed from)."""
    data = Path(path).read_bytes()
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    return rows, hashlib.sha256(data).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("campaigns", help="the authored campaigns (JSONL)")
    parser.add_argument("--existing", action="append", default=[], help="campaigns written before")
    parser.add_argument("--attack-pairs", action="store_true", help="each pair: attack and benign")
    parser.add_argument("--no-replay", action="store_true", help="skip the reference replay")
    parser.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="NAME=N",
        help="a request's number for what is exercised at the cutoff, e.g. gap_reached=6",
    )
    parser.add_argument("--manifest", help="write here what may be said of the file: no text")
    args = parser.parse_args(argv)
    from summarizer.embedder import create
    from summarizer.summarize import Runtime

    # Every file is read once: what is checked and what is hashed are the same bytes.
    campaigns, digest = read_once(args.campaigns)
    existing, compared = [], []
    for path in args.existing:
        rows, seen = read_once(path)
        existing += rows
        compared.append({"file": path, "sha256": seen})
    runtime = None if args.no_replay else Runtime(create("hash"))
    by_id, of_file, counts = check(campaigns, existing, runtime, args.attack_pairs)
    wanted = dict(item.split("=", 1) for item in args.require)
    for name, least in wanted.items():
        have = counts["at_cutoff"].get(name)
        if have is None or have < int(least):
            of_file.append(f"{name} is {have} at the cutoff, and the request asks for {least}")
    for name, found in by_id.items():
        print(json.dumps({"id": name, "problems": sorted(set(found))}))
    if of_file:
        print(json.dumps({"file": args.campaigns, "problems": of_file}))
    bad = len(by_id) + len(of_file)
    # What was checked, with what, and how far: a receipt without the replay says so, and
    # is not the check a request asks for.
    receipt = counts | {"problems": bad, "scorer": SCORER, "sha256": digest}
    receipt["checker_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    receipt["code"] = code_version()
    receipt["existing"] = compared
    receipt["options"] = {"attack_pairs": args.attack_pairs, "require": wanted}
    receipt["replay"] = (
        {"ran": True, "agents": list(AGENTS), "runtime": "hash", "budget": REFERENCE_BUDGET}
        if runtime is not None
        else {"ran": False, "partial": "the reference evidence was not replayed"}
    )
    receipt["complete"] = bool(runtime is not None)
    if args.manifest and not bad:  # a file with problems has no manifest
        with open(args.manifest, "w", encoding="utf-8") as handle:
            for row in manifest(campaigns, digest):
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        receipt["manifest"] = args.manifest
    print(json.dumps(receipt))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
