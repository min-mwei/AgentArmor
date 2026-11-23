"""The host facts a policy requires of one call: profile agentarmor-hostfacts-profile/v1 (MR195-1).

One computation, for the monitor and for Kai's preparation alike. The monitor writes its result
into the state as the "Host facts:" section, records it, and refuses an allow when a required
fact is not there, without asking the decision model. Kai's preparation computes it again from
the same rules and call and labels `facts_sufficient` from it, never from an author's claim.

A policy rule is {"id", "tier", "description"}, and may carry exactly two more fields, each
compiled by its pinned reference module (`src/hostfacts`, kept byte-identical with Kai and pinned
by SHA-256 in its fixture's MANIFEST.json): "paths" (armor-hostfacts-paths-v1) and "limit"
(armor-hostfacts-measures-v1). Any other field refuses the policy at load time, with the rule's
id: it is never ignored, so a misspelled predicate never becomes "none required".

A rule with "paths" or "limit" requires host-computed facts of the calls it covers. Which effects
and measures a call has comes from the trusted tool profile and the host's own snapshot, never
from the policy's prose or the caller. This host computes none of them yet (COMPUTED is empty),
so a policy that declares one is validated by its reference compiler and then refused at load
time ("host_facts_not_computed"), rather than judged with facts it cannot give. The default
policy carries neither field: under this profile it requires no host fact of any call. That is a
property of this explicit profile, recorded with its identity, and says nothing of a policy that
carries predicates.

Nothing here is a decision, a consent or a rule match: a resolution says only which host facts
the profile requires of the call and whether they are there.
"""

import hashlib
import importlib.util
import json
from pathlib import Path

PROFILE = "agentarmor-hostfacts-profile/v1"
FIELDS = ("id", "tier", "description", "paths", "limit")
KINDS = {"paths": "armor-hostfacts-paths-v1", "limit": "armor-hostfacts-measures-v1"}
COMPUTED = ()  # the predicate fields whose facts this host computes for a call: none yet
# What a resolution says, and what Kai's `facts_sufficient` derivation is for each.
STATUSES = {
    "none_required": "profile: none required",
    "present": "profile: required present",
    "missing": "profile: required missing",
    "stale": "profile: required stale",
    "unusable": "profile: required unusable",
}
ALLOWING = ("none_required", "present")
_ROOT = Path(__file__).resolve().parents[3]  # the repository, when this runs from a checkout


class ProfileError(ValueError):
    """A policy this host cannot judge calls under, said once at load time with its rule id."""

    def __init__(self, code, rule_id, message, hint):
        self.code, self.rule_id, self.hint = code, rule_id, hint
        where = f"rule {rule_id!r}: " if rule_id else ""
        super().__init__(f"{code}: {where}{message} ({hint})")


def _reference(field):
    """The pinned reference module that compiles one predicate field, checked by its SHA-256."""
    name = {"paths": "paths", "limit": "measures"}[field]
    source = _ROOT / "src" / "hostfacts" / f"{name}.py"
    manifest = _ROOT / "tests" / "fixtures" / KINDS[field] / "MANIFEST.json"
    if not source.is_file() or not manifest.is_file():
        raise ProfileError(
            "reference_unavailable",
            None,
            f"the {KINDS[field]} reference module is not in this installation",
            "run the monitor from a checkout that carries src/hostfacts",
        )
    pinned = json.loads(manifest.read_text(encoding="utf-8"))["reference_python_sha256"]
    if hashlib.sha256(source.read_bytes()).hexdigest() != pinned:
        raise ProfileError(
            "reference_changed",
            None,
            f"src/hostfacts/{name}.py is not the file its manifest pins",
            "restore the pinned file, or update both copies, the port and the manifest together",
        )
    spec = importlib.util.spec_from_file_location(f"agentarmor_hostfacts_{name}", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compile_profile(rules, *, home=None, workspace=None, measures_config=None):
    """The profile a policy compiles to: {"profile", "fields", "sha256"}. Raises ProfileError.

    Every rule holds only FIELDS. A "paths" or "limit" field is compiled by its reference module,
    so a malformed one is reported as that module reports it; one that is well formed is then
    refused, since this host computes none of those facts (COMPUTED). The identity is the
    SHA-256 of the canonical profile, the same in every process that compiles the same rules."""
    if not isinstance(rules, list) or not rules:
        raise ProfileError("bad_policy", None, "a policy holds one rule or more", "one per line")
    for rule in rules:
        extra = sorted(set(rule) - set(FIELDS)) if isinstance(rule, dict) else []
        if extra:
            raise ProfileError(
                "unknown_rule_field",
                rule.get("id"),
                f"unknown field {extra[0]!r}",
                "a rule holds id, tier and description, and may hold paths or limit",
            )
    declared = [field for field in KINDS if any(field in rule for rule in rules)]
    for field in declared:
        module = _reference(field)
        try:
            if field == "paths":
                module.compile_policy(rules, home=home, workspace=workspace)
            elif measures_config is None:
                first = next(rule.get("id") for rule in rules if "limit" in rule)
                raise ProfileError(
                    "measures_not_configured",
                    first,
                    "a limit needs the operator's trusted measures configuration",
                    "configure the measures the tool schemas produce, or remove the limit",
                )
            else:
                module.compile_limits(rules, config=measures_config)
        except module.PolicyError as error:
            raise ProfileError(error.code, error.rule_id, error.message, error.hint) from error
        if field not in COMPUTED:
            first = next(rule.get("id") for rule in rules if field in rule)
            raise ProfileError(
                "host_facts_not_computed",
                first,
                f"this host does not compute {KINDS[field]} facts for a call yet",
                f"remove the {field} field, or run a host that computes them",
            )
    body = {"profile": PROFILE, "fields": declared, "computed": list(COMPUTED)}
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=True)
    return body | {"sha256": hashlib.sha256(canonical.encode("ascii")).hexdigest()}


def resolve(rules, call=None):
    """{"profile", "sha256", "status", "facts", "derivation"} for one call under the rules.

    call is (tool, arguments as JSON text), or None for a state with no call. A profile that
    declares no predicate requires no host fact of any call ("none_required"). A profile with a
    predicate is refused by compile_profile while COMPUTED is empty, so no call is resolved under
    one here; should one ever be compiled with a field this host does not compute, every fact it
    requires is "unusable", never "none_required"."""
    profile = compile_profile(rules)
    if not profile["fields"]:
        status, facts = "none_required", []
    else:  # not reached while COMPUTED is empty: compile_profile refuses such a policy
        status = "unusable"
        facts = [{"kind": KINDS[f], "why": "not_computed_here"} for f in profile["fields"]]
    told = {"profile": PROFILE, "sha256": profile["sha256"], "status": status, "facts": facts}
    return told | {"derivation": STATUSES[status]}


def allows(resolution):
    """Whether an allow may stand on this resolution: every required host fact is there."""
    return resolution.get("status") in ALLOWING


def section(resolution):
    """The state's "Host facts:" lines, written from the resolution and from nothing else."""
    named = f"({resolution['profile']}, profile {resolution['sha256'][:12]})"
    if resolution["status"] == "none_required":
        return ["Host facts:", f"none required by these rules for this call {named}"]
    lines = ["Host facts:", f"{resolution['status'].replace('_', ' ')} {named}"]
    return lines + [f"  - {fact['kind']}: {fact['why']}" for fact in resolution["facts"]]
