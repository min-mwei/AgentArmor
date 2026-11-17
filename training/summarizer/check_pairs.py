"""Check authored twin pairs at the source, before review: structure, policy, labels and how the rules combine.

    python training/summarizer/check_pairs.py ROOTDIR PLAN.json OUT.json \\
        [--consent CONSENT.json] [--masks MASKS.json] [--banned BANNED.json]

ROOTDIR holds run-*/ folders, each with roots.jsonl, author-labels.json, pairs.json and policy-<root>.jsonl.
PLAN.json is {"pairs": [{"pair_id", "root_ids": [twin1, twin2], "nuisance", "changed_cause", "expected":
{"twin1": [decision, deciding], "twin2": [...]}}]}. OUT.json receives the report (schema below); stdout gets its
summary, counts and codes only, no session text. Exit status 1 when any pair fails, else 0.

**Passing this never admits a corpus on its own.** It checks what can be checked mechanically from the source; it
cannot tell whether a label is right, and a pair that passes still needs its render, blind read and adjudication.

How the rules combine is Kai's decision semantics (Kai architecture 5.1): deny = H or B or any(S_i and not E_i and
not C). H: a matching hard_deny rule. B: an active user boundary, the rule a pair's twins[<root>].user_limit_rule
names, labelled true. S_i: a matching soft_deny rule. E_i: a matching allow rule whose description names it as
"[id=<S_i>]". C: exact-request user consent, counted only where it is DECLARED (--consent, or pairs.json
twins[<root>].consent) and the declaration holds against the source:

    {"step": [session, step], "action": literal, "material_args": [names], "condition_facts": [fact ids]}

the step is a user prompt (provenance from the step's kind, never from its text) before the call; the action appears
in the prompt and in the call; every call_target literal of the pair and every named material argument value of the
call appears in the prompt (case, "_" and "-" ignored); every condition fact is recorded before the call; and no
later user prompt before the call withdraws it (a withdrawal cue with the action or a target). Consent never clears a
hard restriction or the user-boundary rule. A rule's match label is never changed: a soft rule consent clears stays
true. Undeclared consent clears nothing.

Masks. A label is unresolved when MASKS.json ({<root>: [label keys]}) lists it, or its author value is null or
"masked". An unresolved label is reported under "masked", never as a pass or a failure: the checks that rest on it
(the label's agreement with the story; for the decision or any rule, the combiner checks of that root and the pair's
flip check) are listed as masked, and a pair whose only open checks are masked has status "masked".

Codes (a pair with a problem fails):
  ids, kinds, group, family, decide, schedule, call-typed, cause-missing, cause-plan;
  policy pairs: sessions-differ, policy-same; history pairs: policy-differs, ndiff:<n>, diff-after-call;
  per root: defaults-missing, policy-file, policy-field (a rule the monitor refuses, or a description over
  MAX_DESCRIPTION characters), rules-keys (story.rules keys are not the policy's ids), labels-keys (an author label
  is missing), story-label (an author label differs from the story), M1 (an allowed root with a matching hard rule),
  boundary-uncleared (an allowed root whose user boundary rule is true), deny-unexplained (a denied root with no
  matching hard rule, no boundary and no uncleared soft rule), deny-consented (a denied root whose every uncleared soft
  rule valid declared consent clears), soft-uncleared (an allowed root with a matching soft rule that no named
  exception and no valid declared consent clears); consent-not-user, consent-after-call, consent-action,
  consent-target, consent-material, consent-condition, consent-withdrawn, consent-malformed (why a declared consent
  on an allowed root does not hold); pair: nuisance-flip, flip-missing, visible-word:<code>.
Advisory: decision-plan, deciding-plan, ext-true, unsettled:<n>, wording-tokens:<n>.

Report schema (SCHEMA, checked by validate_report()):

    {"schema": "pair-verify/1", "admits_corpus": false,
     "inputs": {"roots_sha256", "plan_sha256", "consent_sha256", "masks_sha256", "banned_sha256"},
     "summary": {"roots", "pairs", "status": {"pass", "fail", "masked"}, "problem_codes", "advisory_codes",
                 "masked_checks"},
     "pairs": {<pair>: {"status": "pass" | "fail" | "masked", "problems": [...], "advisory": [...],
                        "masked": [...], "roots": {<root>: {"decision", "hard_true", "soft_true", "allow_true",
                        "cleared", "cleared_by_consent", "masked_labels"}}, "detail": {...}}}}

The *_sha256 inputs are of the files as read (roots: every run's roots.jsonl, author-labels.json and pairs.json in
name order); null for an option not given.
"""

import argparse
import collections
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

from agentarmor_monitor.policy import DEFAULT_RULES, check_rules

SCHEMA = "pair-verify/1"
MAX_DESCRIPTION = 260
DEFAULTS = [dict(rule) for rule in DEFAULT_RULES]
DEFAULT_IDS = [rule["id"] for rule in DEFAULTS]
# Words a reader must not see in a visible string of a twin; a batch adds its own with --banned.
BANNED = {
    "B04": r"\battack",
    "B05": r"\bbenign",
    "B06": r"\btwins?\b",
    "B07": r"\bfamil(y|ies)\b",
    "B08": r"\bnuisance|\bcounterfactual",
    "B09": r"\bxpia",
    "B10": r"\binjection",
    "B12": r"lookalike|look-alike",
    "B13": r"\bboundary\b",
    "B14": r"held-out|heldout|\bsealed\b|confirmation set|\bsynthetic\b|training data",
    "B15": r"\badoption\b|inert quot",
}
WITHDRAW = re.compile(
    r"\b(do not|don't|dont|stop|cancel|hold on|hold off|withdraw|revoke|never mind|nevermind"
    r"|after all|no longer)\b",
    re.I,
)
TOK = re.compile(r"\w+|[^\w\s]")
UNRESOLVED = (None, "masked")


def canon(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", str(text))).strip().lower()


def visible(root):
    out = []
    for session in root["sessions"]:
        out.append(session.get("cwd", ""))
        for step in session["steps"]:
            out.extend(s for k, v in step.items() if k != "id" for s in strings(v))
    for rule in root.get("policy") or []:
        if rule.get("id") not in DEFAULT_IDS:
            out += [rule.get("id", ""), rule.get("description", "")]
    return out


def step_map(root):
    return {(s["id"], st["id"]): st for s in root["sessions"] for st in s["steps"]}


def order(root):
    """[(session, step)] in the order they happen, up to the call under decision."""
    sessions = {s["id"]: s for s in root["sessions"]}
    if root.get("schedule"):
        seq, pos = [], {name: 0 for name in sessions}
        for name, upto in root["schedule"]:
            ids = [st["id"] for st in sessions[name]["steps"]]
            end = ids.index(upto) + 1
            seq += [(name, i) for i in ids[pos[name] : end]]
            pos[name] = end
        return seq
    seq = []
    for session in root["sessions"]:
        for step in session["steps"]:
            seq.append((session["id"], step["id"]))
            if (session["id"], step["id"]) == (root["decide"]["session"], root["decide"]["step"]):
                return seq
    return seq


def label(labels, key):
    """An author label's value: the value, None when unresolved, or MISSING."""
    if not isinstance(labels, dict) or key not in labels:
        return MISSING
    value = labels[key]
    if isinstance(value, dict):
        value = value.get("value", value.get("label"))
    if isinstance(value, str) and value.lower() in ("true", "false"):
        value = value.lower() == "true"
    return None if value in UNRESOLVED else value


MISSING = object()


def consent_check(root, meta, decl):
    """(holds, codes): whether a declared exact-request consent holds against the root's own source."""
    if (
        not isinstance(decl, dict)
        or not isinstance(decl.get("step"), list)
        or len(decl["step"]) != 2
    ):
        return False, ["consent-malformed"]
    steps, seq = step_map(root), order(root)
    key, call_key = tuple(decl["step"]), (root["decide"]["session"], root["decide"]["step"])
    step = steps.get(key)
    if step is None:
        return False, ["consent-malformed"]
    codes = []
    if "prompt" not in step or set(step) - {"id", "prompt"}:
        codes.append("consent-not-user")
    if key not in seq or call_key not in seq or seq.index(key) >= seq.index(call_key):
        codes.append("consent-after-call")
    said = norm(step.get("prompt", ""))
    call = steps.get(call_key) or {}
    call_text = norm(" ".join(strings({k: v for k, v in call.items() if k != "id"})))
    action = norm(decl.get("action", ""))
    if not action or action not in said or action not in call_text:
        codes.append("consent-action")
    targets = [norm(t) for t in (meta.get("call_target") or [])]
    if not targets or any(t not in said or t not in call_text for t in targets):
        codes.append("consent-target")
    args = call.get("input") if isinstance(call.get("input"), dict) else None
    if args is None and "run" in call:
        args = {"run": call["run"]}
    names = decl.get("material_args") or []
    if not names or args is None or any(n not in args or norm(args[n]) not in said for n in names):
        codes.append("consent-material")
    facts = {fact["id"]: fact for fact in root.get("facts") or []}
    for name in decl.get("condition_facts") or []:
        fact = facts.get(name)
        where = (fact["session"], fact["step"]) if fact else None
        if not fact or where not in seq or seq.index(where) >= seq.index(call_key):
            codes.append("consent-condition")
            break
    if key in seq and call_key in seq:
        for later in seq[seq.index(key) + 1 : seq.index(call_key)]:
            text = norm((steps.get(later) or {}).get("prompt", ""))
            if (
                text
                and WITHDRAW.search(text)
                and (action in text or any(t in text for t in targets))
            ):
                codes.append("consent-withdrawn")
                break
    return not codes, sorted(set(codes))


def load(base):
    """roots, author labels, pair metadata and each root's run folder, from ROOTDIR/run-*/."""
    roots, labels, meta, runs = {}, {}, {}, {}
    for run in sorted(x for x in Path(base).glob("run-*") if x.is_dir()):
        found = run / "roots.jsonl"
        if not found.exists():
            continue
        for line in found.read_text(encoding="utf-8").splitlines():
            if line.strip():
                root = json.loads(line)
                roots[root["id"]], runs[root["id"]] = root, run
        if (run / "author-labels.json").exists():
            told = json.loads((run / "author-labels.json").read_text(encoding="utf-8"))
            if isinstance(told, list):
                told = {x.get("id") or x.get("root_id") or x.get("root"): x for x in told}
            labels.update(told)
        if (run / "pairs.json").exists():
            told = json.loads((run / "pairs.json").read_text(encoding="utf-8"))
            items = told if isinstance(told, list) else told.get("pairs", told)
            if isinstance(items, dict):
                items = [dict(v, pair_id=k) for k, v in items.items()]
            meta.update({item.get("pair_id"): item for item in items})
    return roots, labels, meta, runs


def policy_sound(policy):
    if not isinstance(policy, list) or not policy:
        return False
    try:
        check_rules([dict(rule) for rule in policy])
    except ValueError:
        return False
    return all(
        set(rule) == {"id", "tier", "description"} and len(rule["description"]) <= MAX_DESCRIPTION
        for rule in policy
    )


def check_root(root, tw, pair_meta, plan_pair, labels, consent, masks, runs, out):
    """The per-root checks: appends to out["problems"], out["advisory"], out["masked"]."""
    rid, problems, advisory, masked = root["id"], out["problems"], out["advisory"], out["masked"]
    policy = root.get("policy")
    if not isinstance(policy, list) or not policy:
        problems.append(f"policy-field:{rid}")
        return None
    if not policy_sound(policy):
        problems.append(f"policy-field:{rid}")
    if not all(rule in policy for rule in DEFAULTS) or len({x["id"] for x in policy}) != len(
        policy
    ):
        problems.append(f"defaults-missing:{rid}")
    pfile = runs[rid] / f"policy-{rid}.jsonl"
    if (
        not pfile.exists()
        or [json.loads(x) for x in pfile.read_text(encoding="utf-8").splitlines() if x.strip()]
        != policy
    ):
        problems.append(f"policy-file:{rid}")
    ids = [x["id"] for x in policy]
    story = root["story"]
    rules = story.get("rules") or {}
    if set(rules) != set(ids):
        problems.append(f"rules-keys:{rid}")
    told = labels.get(rid) or {}
    keys = ["decision", "facts_sufficient", "is_xpia_attempt"] + [f"rule:{i}" for i in ids]
    if any(label(told, k) is MISSING for k in keys):
        problems.append(f"labels-keys:{rid}")
    unresolved = sorted(
        {k for k in keys if label(told, k) is None} | (set(masks.get(rid) or []) & set(keys))
    )
    if unresolved:
        masked.append(f"labels:{rid}:{','.join(unresolved)}")
    open_keys = [k for k in keys if k not in unresolved]
    wrong = [
        k
        for k in open_keys
        if (value := label(told, k)) is not MISSING
        and value != (story.get("decision") if k == "decision" else rules.get(k[5:]))
        and (k == "decision" or k.startswith("rule:"))
    ]
    if wrong:
        problems.append(f"story-label:{rid}")
    decision = story.get("decision")
    tier = {x["id"]: x["tier"] for x in policy}
    desc = {x["id"]: x["description"] for x in policy}
    hard = [i for i in ids if tier[i] == "hard_deny" and rules.get(i) is True]
    soft = [i for i in ids if tier[i] == "soft_deny" and rules.get(i) is True]
    allow = [i for i in ids if tier[i] == "allow" and rules.get(i) is True]
    cleared = [s for s in soft if any(f"[id={s}]" in desc[a] for a in allow)]
    twin_meta = (pair_meta.get("twins") or {}).get(rid) or {}
    limit = twin_meta.get("user_limit_rule")
    boundary = bool(limit) and rules.get(limit) is True
    decl = consent.get(rid, twin_meta.get("consent"))
    by_consent, consent_codes = [], []
    if decl is not None:
        holds, why = consent_check(root, pair_meta, decl)
        if holds:
            by_consent = [s for s in soft if s not in cleared and s != limit]
        else:
            consent_codes = why
    uncleared = set(soft) - set(cleared) - set(by_consent)
    combiner = []
    if decision == "allow":
        combiner += [f"M1:{rid}"] if hard else []
        combiner += [f"boundary-uncleared:{rid}"] if boundary else []
        combiner += [f"soft-uncleared:{rid}"] if uncleared else []
        combiner += [f"{code}:{rid}" for code in consent_codes]
    if decision == "deny" and not hard and not boundary:
        if not set(soft) - set(cleared):
            combiner.append(f"deny-unexplained:{rid}")
        elif not uncleared:
            combiner.append(f"deny-consented:{rid}")
    combiner_masked = "decision" in unresolved or any(k.startswith("rule:") for k in unresolved)
    if combiner_masked:
        masked.append(f"combiner:{rid}")
    else:
        problems.extend(combiner)
    if rules.get("external-instructions") is True:
        advisory.append(f"ext-true:{rid}")
    expected = plan_pair["expected"][tw]
    if decision != expected[0]:
        advisory.append(f"decision-plan:{rid}")
    deciding = twin_meta.get("deciding_rules") or []
    deciding = [deciding] if isinstance(deciding, str) else deciding
    want = expected[1].split(" (")[0]
    star = {"H*": "hard_deny", "H-user": "hard_deny", "S*": "soft_deny", "A*": "allow"}
    head = want.split(" ")[0]
    if not any(want == d for d in deciding) and not any(
        head in star and d not in DEFAULT_IDS and tier.get(d) == star[head] for d in deciding
    ):
        advisory.append(f"deciding-plan:{rid}")
    out["roots"][rid] = {
        "decision": decision,
        "hard_true": hard,
        "soft_true": soft,
        "allow_true": allow,
        "cleared": cleared,
        "cleared_by_consent": by_consent,
        "masked_labels": unresolved,
    }
    return None if combiner_masked else decision


def check_pair(pid, rows, roots, labels, meta, runs, plan, consent, masks, banned):
    out = {"problems": [], "advisory": [], "masked": [], "roots": {}, "detail": {}}
    problems, plan_pair, pair_meta = out["problems"], plan.get(pid), meta.get(pid) or {}
    rows = sorted(rows, key=lambda r: r["id"])
    if (
        plan_pair is None
        or len(rows) != 2
        or sorted(r["id"] for r in rows) != sorted(plan_pair["root_ids"])
    ):
        problems.append("ids")
        return out
    t1, t2 = (roots[i] for i in plan_pair["root_ids"])
    if not plan_pair["nuisance"] and [t1["story"].get("kind"), t2["story"].get("kind")] != [
        "attack",
        "benign",
    ]:
        problems.append("kinds")
    problems += [
        k for k in ("group", "family", "decide", "schedule") if canon(t1.get(k)) != canon(t2.get(k))
    ]
    m1, m2 = step_map(t1), step_map(t2)
    call_key = (t1["decide"]["session"], t1["decide"]["step"])
    if canon(m1.get(call_key)) != canon(m2.get(call_key)):
        problems.append("call-typed")
    cause = pair_meta.get("changed_cause")
    if cause not in ("policy", "history"):
        problems.append("cause-missing")
    elif cause != plan_pair["changed_cause"]:
        problems.append("cause-plan")
    same_policy = canon(t1.get("policy")) == canon(t2.get("policy"))
    diffs = [k for k in m1 if k in m2 and canon(m1[k]) != canon(m2[k])] + sorted(set(m1) ^ set(m2))
    out["detail"]["diffs"] = [list(k) for k in diffs]
    if cause == "policy":
        if any(
            canon(t1.get(k)) != canon(t2.get(k))
            for k in ("sessions", "schedule", "decide", "facts")
        ):
            problems.append("sessions-differ")
        if same_policy:
            problems.append("policy-same")
    elif cause == "history":
        if not same_policy:
            problems.append("policy-differs")
        if len(diffs) != 1:
            problems.append(f"ndiff:{len(diffs)}")
        else:
            seq = order(t1)
            if (
                diffs[0] not in seq
                or call_key not in seq
                or seq.index(diffs[0]) >= seq.index(call_key)
            ):
                problems.append("diff-after-call")
            a, b = TOK.findall(canon(m1.get(diffs[0]))), TOK.findall(canon(m2.get(diffs[0])))
            ops = difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes()
            changed = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in ops if op != "equal")
            out["advisory"].append(f"wording-tokens:{changed}")
    decisions = {}
    for root, tw in ((t1, "twin1"), (t2, "twin2")):
        decisions[tw] = check_root(
            root, tw, pair_meta, plan_pair, labels, consent, masks, runs, out
        )
    if None in decisions.values():
        out["masked"].append("flip")
    elif plan_pair["nuisance"] and decisions["twin1"] != decisions["twin2"]:
        problems.append("nuisance-flip")
    elif not plan_pair["nuisance"] and decisions["twin1"] == decisions["twin2"]:
        problems.append("flip-missing")
    hits = {
        code
        for root in (t1, t2)
        for text in visible(root)
        for code, pattern in banned.items()
        if re.search(pattern, text, re.I)
    }
    problems += [f"visible-word:{code}" for code in sorted(hits)]
    unsettled = pair_meta.get("unsettled") or []
    if unsettled:
        out["advisory"].append(f"unsettled:{len(unsettled)}")
    return out


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else None


def roots_sha(base):
    digest = hashlib.sha256()
    for run in sorted(x for x in Path(base).glob("run-*") if x.is_dir()):
        for name in ("roots.jsonl", "author-labels.json", "pairs.json"):
            if (run / name).exists():
                digest.update(f"{run.name}/{name}\0".encode() + (run / name).read_bytes())
    return digest.hexdigest()


def verify(base, plan_doc, consent=None, masks=None, banned=None, inputs=None):
    """The report (schema pair-verify/1) for the pairs under base against plan_doc."""
    consent, masks = consent or {}, masks or {}
    words = BANNED | (banned or {}) | (plan_doc.get("banned") or {})
    plan = {p["pair_id"]: p for p in plan_doc["pairs"]}
    roots, labels, meta, runs = load(base)
    by_pair = collections.defaultdict(list)
    for root in roots.values():
        by_pair[root["pair_id"]].append(root)
    pairs = {}
    for pid in sorted(by_pair):
        got = check_pair(pid, by_pair[pid], roots, labels, meta, runs, plan, consent, masks, words)
        status = "fail" if got["problems"] else "masked" if got["masked"] else "pass"
        pairs[pid] = {"status": status} | got
    count = collections.Counter
    summary = {
        "roots": len(roots),
        "pairs": len(pairs),
        "status": {
            s: sum(p["status"] == s for p in pairs.values()) for s in ("pass", "fail", "masked")
        },
        "problem_codes": dict(
            count(c.split(":")[0] for p in pairs.values() for c in p["problems"])
        ),
        "advisory_codes": dict(
            count(c.split(":")[0] for p in pairs.values() for c in p["advisory"])
        ),
        "masked_checks": dict(count(c.split(":")[0] for p in pairs.values() for c in p["masked"])),
    }
    told = dict.fromkeys(("plan_sha256", "consent_sha256", "masks_sha256", "banned_sha256"))
    told |= {"roots_sha256": roots_sha(base)} | (inputs or {})
    return {
        "schema": SCHEMA,
        "admits_corpus": False,
        "inputs": told,
        "summary": summary,
        "pairs": pairs,
    }


ROOT_KEYS = {
    "decision",
    "hard_true",
    "soft_true",
    "allow_true",
    "cleared",
    "cleared_by_consent",
    "masked_labels",
}


def validate_report(report):
    """The ways a report is not pair-verify/1: [] for a well-formed one."""
    wrong = []
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        return ["schema"]
    if report.get("admits_corpus") is not False:
        wrong.append("admits_corpus must be false")
    names = {"roots_sha256", "plan_sha256", "consent_sha256", "masks_sha256", "banned_sha256"}
    if set(report.get("inputs") or {}) != names:
        wrong.append("inputs")
    summary = report.get("summary") or {}
    keys = {"roots", "pairs", "status", "problem_codes", "advisory_codes", "masked_checks"}
    if set(summary) != keys or set(summary.get("status") or {}) != {"pass", "fail", "masked"}:
        wrong.append("summary")
    pairs = report.get("pairs")
    if not isinstance(pairs, dict) or summary.get("pairs") != len(pairs):
        return [*wrong, "pairs"]
    for pid, pair in pairs.items():
        shape = {"status", "problems", "advisory", "masked", "roots", "detail"}
        if set(pair) != shape or pair["status"] not in ("pass", "fail", "masked"):
            wrong.append(f"pair {pid}")
            continue
        expect = "fail" if pair["problems"] else "masked" if pair["masked"] else "pass"
        if pair["status"] != expect:
            wrong.append(f"pair {pid}: status")
        if any(set(r) != ROOT_KEYS for r in pair["roots"].values()):
            wrong.append(f"pair {pid}: roots")
    if summary.get("status") and sum(summary["status"].values()) != len(pairs):
        wrong.append("summary: status counts")
    return wrong


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("rootdir")
    parser.add_argument("plan")
    parser.add_argument("out")
    parser.add_argument("--consent", help="JSON {root: consent declaration}")
    parser.add_argument("--masks", help="JSON {root: [unresolved label keys]}")
    parser.add_argument("--banned", help="JSON {code: regex}: visible words this batch adds")
    args = parser.parse_args(argv)
    read = lambda path: json.loads(Path(path).read_text(encoding="utf-8")) if path else None  # noqa: E731
    inputs = {
        "plan_sha256": file_sha(args.plan),
        "consent_sha256": file_sha(args.consent),
        "masks_sha256": file_sha(args.masks),
        "banned_sha256": file_sha(args.banned),
    }
    report = verify(
        args.rootdir,
        read(args.plan),
        read(args.consent),
        read(args.masks),
        read(args.banned),
        inputs,
    )
    assert not validate_report(report), validate_report(report)
    Path(args.out).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=1))
    return 1 if report["summary"]["status"]["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
