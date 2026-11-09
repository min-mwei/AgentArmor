"""Writes vectors.json and MANIFEST.json for armor-hostfacts-paths-v1 from hand-written cases.

The expected values below were written by hand from POSIX path-resolution and shell-expansion
rules. This builder never imports or calls the reference implementation, so the vectors are an
independent check of both the Python reference and AgentArmor's native port.
Usage: python3 -I make_vectors.py [PATH_TO_paths.py]
"""

import copy
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = "armor-hostfacts-paths-v1"
AT = "2026-11-05T12:00:00Z"
BASE = {
    "cwd": "/work/proj",
    "home": "/homes/ana",
    "env": {
        "HOME": "/homes/ana",
        "DATA": "/srv/data",
        "SPACEY": "/tmp/a b",
        "EMPTY": "",
        "GLOBBY": "/tmp/*",
    },
    "case_sensitive": True,
    "links": {},
    "observed_at": AT,
}
ALL = ["read", "write", "delete", "list", "execute"]


def eff(raw, op="read", final="follow", exp="none", arg="/call/arguments/path"):
    return {"argument": arg, "raw": raw, "operation": op, "final_link": final, "expansion": exp}


def rule(rid, *roots, ops=ALL):
    return {"rule_id": rid, "roots": list(roots), "operations": list(ops)}


CASES = []


def case(cid, why, effects, rules=(), runtime=None, values=None, refused=None, facts=None):
    rt = copy.deepcopy(BASE)
    rt.update(runtime or {})
    request = {
        "profile": PROFILE,
        "runtime": rt,
        "effects": list(effects),
        "path_rules": list(rules),
    }
    if refused:
        expect = {"status": "refused", "code": refused}
    else:
        expect = {"status": "ok", "values": [list(v) for v in values]}
    if facts is not None:
        expect["facts"] = facts
    CASES.append({"id": cid, "why": why, "request": request, "expect": expect})


def ok(path, inside=None, outside=None, k=1):
    """The facts of path effect k: resolved, then the two lists when the policy has path rules."""
    out = [(f"call_path_{k}_resolved", path)]
    if inside is not None:
        out += [
            (f"call_path_{k}_inside_roots_of", inside),
            (f"call_path_{k}_outside_roots_of", outside),
        ]
    return out


def fact(name, value, note):
    return {
        "name": name,
        "value": value,
        "unit": None,
        "provenance": "host_computed",
        "as_of": AT,
        "note": note,
    }


def note_resolved(arg, op, exp_note, final, cwd="/work/proj"):
    return (
        f"Host resolution of call argument {arg} for {op}: working directory {cwd}, {exp_note}, "
        f"symbolic links from the host snapshot, final link {final}."
    )


def tail(op):
    return (
        f"Rules whose paths field does not cover {op} are in neither list. "
        "Containment only: not a rule match, exception or decision."
    )


def note_inside(k, op, case="case-sensitive"):
    return (
        f"Policy rules with a declared root that contains call_path_{k}_resolved "
        f"(equal or beneath, component by component, {case}; roots resolved through the "
        f"same symbolic links). {tail(op)}"
    )


def note_outside(k, op):
    return f"Policy rules none of whose declared roots contains call_path_{k}_resolved. {tail(op)}"


NONE = "no ~ or $VAR expansion (file API)"

# --- plain resolution and containment -------------------------------------------------------
case(
    "abs_plain_inside",
    "absolute path inside a declared root",
    [eff("/srv/data/report.csv")],
    [rule("data-scope", "/srv/data")],
    values=ok("/srv/data/report.csv", ["data-scope"], []),
    facts=[
        fact(
            "call_path_1_resolved",
            "/srv/data/report.csv",
            note_resolved("/call/arguments/path", "read", NONE, "followed"),
        ),
        fact("call_path_1_inside_roots_of", ["data-scope"], note_inside(1, "read")),
        fact("call_path_1_outside_roots_of", [], note_outside(1, "read")),
    ],
)
case(
    "no_path_rules_only_resolution",
    "a policy without path rules gets the resolved path only",
    [eff("notes/todo.md")],
    values=ok("/work/proj/notes/todo.md"),
)
case(
    "no_path_effects_no_facts",
    "a call with no path effect gets no path facts",
    [],
    [rule("data-scope", "/srv/data")],
    values=[],
)
case(
    "dotdot_escapes_root",
    "dot segments leave the project; the surface starts inside it",
    [eff("../../etc/passwd")],
    [rule("project", "/work/proj")],
    values=ok("/etc/passwd", [], ["project"]),
)
case(
    "dotdot_stays_inside",
    "legitimate dot segments that stay inside (allow-side near miss)",
    [eff("src/../docs/./a.md")],
    [rule("project", "/work/proj")],
    values=ok("/work/proj/docs/a.md", ["project"], []),
)
case(
    "dotdot_clamps_at_slash",
    ".. never goes above /",
    [eff("/../../x")],
    values=ok("/x"),
)
case(
    "duplicate_slashes",
    "duplicate separators collapse",
    [eff("//srv///data//f")],
    [rule("data-scope", "/srv/data")],
    values=ok("/srv/data/f", ["data-scope"], []),
)
case(
    "prefix_sibling_dash",
    "a string-prefix sibling is outside (CVE-2025-53110 class)",
    [eff("/srv/data-old/f")],
    [rule("data-scope", "/srv/data")],
    values=ok("/srv/data-old/f", [], ["data-scope"]),
)
case(
    "prefix_sibling_word",
    "a longer sibling name is outside",
    [eff("/srv/database")],
    [rule("data-scope", "/srv/data")],
    values=ok("/srv/database", [], ["data-scope"]),
)
case(
    "root_itself_is_inside",
    "the root directory itself counts as inside",
    [eff("/srv/data")],
    [rule("data-scope", "/srv/data")],
    values=ok("/srv/data", ["data-scope"], []),
)
case(
    "slash_root_contains_all",
    "a declared root of / contains every path",
    [eff("/etc/hosts")],
    [rule("everything", "/")],
    values=ok("/etc/hosts", ["everything"], []),
)
case(
    "percent_not_decoded",
    "file paths are not percent-decoded",
    [eff("/srv/data/%2e%2e/etc")],
    [rule("data-scope", "/srv/data")],
    values=ok("/srv/data/%2e%2e/etc", ["data-scope"], []),
)
case(
    "several_roots_any_contains",
    "a rule with several roots contains a path inside any of them",
    [eff("/srv/cache/x")],
    [rule("work-dirs", "/srv/data", "/srv/cache")],
    values=ok("/srv/cache/x", ["work-dirs"], []),
)
case(
    "lists_follow_policy_order",
    "both lists keep the policy's rule order",
    [eff("/srv/data/in.csv")],
    [rule("z-last", "/srv"), rule("a-secrets", "/homes/ana/.ssh"), rule("m-data", "/srv/data")],
    values=ok("/srv/data/in.csv", ["z-last", "m-data"], ["a-secrets"]),
)

# --- expansion follows the executor ---------------------------------------------------------
case(
    "tilde_literal_file_api",
    "a file API that does not expand ~ takes it literally",
    [eff("~/notes")],
    values=ok("/work/proj/~/notes"),
)
case(
    "tilde_file_api",
    "a file API that expands ~ (expanduser) reaches the home directory",
    [eff("~/notes", exp="tilde")],
    [rule("ssh-keys", "/homes/ana/.ssh")],
    values=ok("/homes/ana/notes", [], ["ssh-keys"]),
)
case(
    "tilde_file_api_keeps_var_literal",
    "the tilde mode does not expand $VAR",
    [eff("~/$DATA", exp="tilde")],
    values=ok("/homes/ana/$DATA"),
)
case(
    "tilde_unquoted_shell",
    "an unquoted shell word expands ~/",
    [eff("~/notes", exp="shell_unquoted")],
    values=ok("/homes/ana/notes"),
)
case(
    "tilde_alone_unquoted",
    "an unquoted ~ alone expands",
    [eff("~", exp="shell_unquoted")],
    values=ok("/homes/ana"),
)
case(
    "tilde_double_quoted_literal",
    "~ inside double quotes stays literal",
    [eff("~/x", exp="shell_double_quoted")],
    values=ok("/work/proj/~/x"),
)
case(
    "var_unquoted",
    "$VAR expands in an unquoted word",
    [eff("$DATA/f", exp="shell_unquoted")],
    [rule("data-scope", "/srv/data")],
    values=ok("/srv/data/f", ["data-scope"], []),
)
case(
    "var_braced_double_quoted",
    "${VAR} expands in a double-quoted word",
    [eff("${DATA}/f", exp="shell_double_quoted")],
    values=ok("/srv/data/f"),
)
case(
    "var_single_quoted_literal",
    "single quotes keep $VAR literal",
    [eff("$DATA/f", exp="shell_single_quoted")],
    values=ok("/work/proj/$DATA/f"),
)
case(
    "var_file_api_literal",
    "a file API keeps $VAR literal",
    [eff("$DATA/f")],
    values=ok("/work/proj/$DATA/f"),
)
case(
    "var_with_space_double_quoted",
    "double quotes keep a value with spaces as one word",
    [eff("$SPACEY/f", exp="shell_double_quoted")],
    values=ok("/tmp/a b/f"),
)
case(
    "empty_var_double_quoted",
    "an empty double-quoted variable leaves the rest: $EMPTY/f is /f",
    [eff("$EMPTY/f", exp="shell_double_quoted")],
    values=ok("/f"),
)
case(
    "glob_literal_file_api",
    "a file API takes * literally",
    [eff("/srv/*.csv")],
    values=ok("/srv/*.csv"),
)
case(
    "refuse_unset_variable",
    "an unset variable is unknown, never empty",
    [eff("$NOPE/f", exp="shell_unquoted")],
    refused="unset_variable",
)
case(
    "refuse_tilde_user_shell",
    "~user needs an account database",
    [eff("~root/x", exp="shell_unquoted")],
    refused="unsupported_tilde",
)
case(
    "refuse_tilde_user_file_api",
    "~user is refused in the tilde mode too",
    [eff("~root/x", exp="tilde")],
    refused="unsupported_tilde",
)
case(
    "refuse_glob_unquoted",
    "an unquoted glob expands to an unknown file list",
    [eff("/srv/*.csv", exp="shell_unquoted")],
    refused="glob",
)
case(
    "refuse_split_unquoted",
    "an unquoted value with a space splits into words",
    [eff("$SPACEY/f", exp="shell_unquoted")],
    refused="word_splitting",
)
case(
    "refuse_empty_unquoted",
    "an unquoted empty variable removes the word",
    [eff("$EMPTY/f", exp="shell_unquoted")],
    refused="empty_expansion",
)
case(
    "refuse_glob_value_unquoted",
    "an unquoted value with * globs",
    [eff("$GLOBBY", exp="shell_unquoted")],
    refused="word_splitting",
)
case(
    "refuse_command_substitution",
    "command substitution is not reproduced",
    [eff("$(whoami)/x", exp="shell_unquoted")],
    refused="command_substitution",
)
case(
    "refuse_brace_expansion",
    "brace expansion is not reproduced",
    [eff("/srv/{a,b}", exp="shell_unquoted")],
    refused="brace_expansion",
)
case(
    "refuse_parameter_operator",
    "${X:-y} is not reproduced",
    [eff("${DATA:-/x}/f", exp="shell_unquoted")],
    refused="unsupported_expansion",
)
case(
    "refuse_metacharacter",
    "a ; means the adapter did not isolate one word",
    [eff("/tmp/a;rm", exp="shell_unquoted")],
    refused="shell_metacharacter",
)
case(
    "refuse_embedded_quote",
    "quote removal inside a word is not reproduced",
    [eff("/tmp/it's", exp="shell_unquoted")],
    refused="unsupported_quoting",
)
case(
    "refuse_unknown_operation",
    "operations come from a closed list",
    [eff("/x", op="chmod")],
    refused="bad_request",
)

# --- symbolic links from the host snapshot --------------------------------------------------
case(
    "link_intermediate_escapes",
    "a link inside the project points outside it",
    [eff("data/f.csv")],
    [rule("project", "/work/proj"), rule("data-scope", "/srv/data")],
    runtime={"links": {"/work/proj/data": "/srv/data"}},
    values=ok("/srv/data/f.csv", ["data-scope"], ["project"]),
)
case(
    "link_final_follow_vs_entry",
    "writing through a link reaches its target; deleting it removes the link",
    [
        eff("out.txt", op="write", arg="/call/arguments/target"),
        eff("out.txt", op="delete", final="entry", arg="/call/arguments/cleanup"),
    ],
    [rule("project", "/work/proj", ops=["write", "delete"])],
    runtime={"links": {"/work/proj/out.txt": "/etc/cron.d/job"}},
    values=ok("/etc/cron.d/job", [], ["project"]) + ok("/work/proj/out.txt", ["project"], [], k=2),
    facts=[
        fact(
            "call_path_1_resolved",
            "/etc/cron.d/job",
            note_resolved("/call/arguments/target", "write", NONE, "followed"),
        ),
        fact("call_path_1_inside_roots_of", [], note_inside(1, "write")),
        fact("call_path_1_outside_roots_of", ["project"], note_outside(1, "write")),
        fact(
            "call_path_2_resolved",
            "/work/proj/out.txt",
            note_resolved("/call/arguments/cleanup", "delete", NONE, "not followed"),
        ),
        fact("call_path_2_inside_roots_of", ["project"], note_inside(2, "delete")),
        fact("call_path_2_outside_roots_of", [], note_outside(2, "delete")),
    ],
)
case(
    "link_relative_target",
    "a relative link target resolves from the link's parent",
    [eff("cfg/app.yml")],
    runtime={"links": {"/work/proj/cfg": "../shared/cfg"}},
    values=ok("/work/shared/cfg/app.yml"),
)
case(
    "link_chain",
    "links are followed through a chain",
    [eff("/a/l1/x")],
    runtime={"links": {"/a/l1": "/a/l2", "/a/l2": "/b/real"}},
    values=ok("/b/real/x"),
)
case(
    "dotdot_after_link_is_physical",
    "up/.. leaves the link's target, not the project (surface misleads)",
    [eff("up/../secret")],
    [rule("project", "/work/proj")],
    runtime={"links": {"/work/proj/up": "/srv/data/deep"}},
    values=ok("/srv/data/secret", [], ["project"]),
)
case(
    "dangling_link_write",
    "a write through a link to a missing file still targets the link's target",
    [eff("new.log", op="write")],
    runtime={"links": {"/work/proj/new.log": "/var/log/app/new.log"}},
    values=ok("/var/log/app/new.log"),
)
case(
    "cwd_through_link",
    "the working directory itself resolves through links (macOS /tmp)",
    [eff("f")],
    runtime={"cwd": "/tmp/w", "links": {"/tmp": "/private/tmp"}},
    values=ok("/private/tmp/w/f"),
)
case(
    "link_to_dot",
    "a link to . names the directory that contains it",
    [eff("/srv/here/f")],
    runtime={"links": {"/srv/here": "."}},
    values=ok("/srv/f"),
)
case(
    "root_through_link",
    "a declared root resolves through the same snapshot",
    [eff("/mnt/vol1/data/x")],
    [rule("data-scope", "/data")],
    runtime={"links": {"/data": "/mnt/vol1/data"}},
    values=ok("/mnt/vol1/data/x", ["data-scope"], []),
)
case(
    "refuse_link_loop",
    "a link cycle refuses as ELOOP",
    [eff("/x/a/f")],
    runtime={"links": {"/x/a": "/x/b", "/x/b": "/x/a"}},
    refused="link_loop",
)
case(
    "refuse_trailing_slash_entry",
    "deleting olddir/ is platform-dependent",
    [eff("olddir/", op="delete", final="entry")],
    refused="trailing_slash_entry",
)
case(
    "refuse_dot_final_entry",
    "deleting a/.. names no entry",
    [eff("a/..", op="delete", final="entry")],
    refused="dot_final_entry",
)
case(
    "refuse_bad_snapshot_key",
    "link locations must be normalized absolute paths",
    [eff("x")],
    runtime={"links": {"/work/proj/./x": "/y"}},
    refused="bad_snapshot",
)

# --- case sensitivity -----------------------------------------------------------------------
case(
    "case_insensitive_inside",
    "on a case-insensitive file system Project and project are one directory",
    [eff("/volumes/work/project/src/a.c")],
    [rule("project", "/Volumes/Work/Project")],
    runtime={"case_sensitive": False},
    values=ok("/volumes/work/project/src/a.c", ["project"], []),
    facts=[
        fact(
            "call_path_1_resolved",
            "/volumes/work/project/src/a.c",
            note_resolved("/call/arguments/path", "read", NONE, "followed"),
        ),
        fact(
            "call_path_1_inside_roots_of",
            ["project"],
            note_inside(1, "read", "ASCII case-insensitive"),
        ),
        fact("call_path_1_outside_roots_of", [], note_outside(1, "read")),
    ],
)
case(
    "case_sensitive_outside",
    "on a case-sensitive file system they differ",
    [eff("/volumes/work/project/src/a.c")],
    [rule("project", "/Volumes/Work/Project")],
    values=ok("/volumes/work/project/src/a.c", [], ["project"]),
)
case(
    "case_insensitive_link_lookup",
    "link lookup folds ASCII case too",
    [eff("/Work/Proj/DATA/f")],
    runtime={"case_sensitive": False, "links": {"/work/proj/data": "/srv/data"}},
    values=ok("/srv/data/f"),
)
case(
    "refuse_non_ascii_case_insensitive",
    "non-ASCII folding is not reproduced",
    [eff("/Volumes/Work/café")],
    runtime={"case_sensitive": False},
    refused="non_ascii_case_insensitive",
)
case(
    "non_ascii_case_sensitive",
    "case-sensitive comparison of non-ASCII names is exact",
    [eff("/srv/data/café.txt")],
    [rule("data-scope", "/srv/data")],
    values=ok("/srv/data/café.txt", ["data-scope"], []),
)

# --- which rules cover which operation ------------------------------------------------------
case(
    "operation_not_covered_in_neither_list",
    "a read is in neither list of a write-only rule",
    [eff("/etc/hosts")],
    [rule("writes-in-project", "/work/proj", ops=["write", "delete"]), rule("everything", "/")],
    values=ok("/etc/hosts", ["everything"], []),
)

# --- call input refusals --------------------------------------------------------------------
case(
    "refuse_control_character",
    "a newline in a path refuses",
    [eff("/tmp/a\nb")],
    refused="control_character",
)
case("refuse_empty_path", "an empty path refuses", [eff("")], refused="empty_path")
case(
    "refuse_relative_compiled_root",
    "compiled roots must be absolute (compile_policy output only)",
    [eff("/x")],
    [rule("bad", "data")],
    refused="bad_path_rules",
)
case(
    "refuse_relative_cwd",
    "the working directory must be absolute",
    [eff("x")],
    runtime={"cwd": "work"},
    refused="bad_runtime",
)
case(
    "refuse_path_too_long",
    "a resolved path longer than 4096 characters refuses",
    [eff("r" * 2000)],
    runtime={"cwd": "/" + "w" * 3000},
    refused="path_too_long",
)
case(
    "refuse_unknown_home",
    "~ without a trusted home directory",
    [eff("~/x", exp="shell_unquoted")],
    runtime={"home": None},
    refused="unknown_home",
)

# --- policy authoring: what users write, compiled once at load time ---------------------------
POLICY = []


def policy(pid, why, rules, expect=None, error=None, home="/homes/ana", workspace="/work/proj"):
    out = {"status": "error", "code": error} if error else {"status": "ok", "path_rules": expect}
    POLICY.append(
        {"id": pid, "why": why, "rules": rules, "home": home, "workspace": workspace, "expect": out}
    )


def r(rid, paths=None, tier="soft_deny"):
    out = {"id": rid, "tier": tier, "description": f"Rule {rid}."}
    if paths is not None:
        out["paths"] = paths
    return out


policy(
    "plain_workspace_and_home",
    "the common forms: ${workspace}, ~/ and a default for",
    [
        r("project-writes", {"within": "${workspace}", "for": ["write", "delete"]}),
        r("ssh-keys", {"within": "~/.ssh"}, tier="hard_deny"),
        r("no-paths"),
    ],
    [
        rule("project-writes", "/work/proj", ops=["write", "delete"]),
        rule("ssh-keys", "/homes/ana/.ssh"),
    ],
)
policy(
    "several_roots_and_absolute",
    "within takes a list; absolute roots stay as written",
    [r("work-dirs", {"within": ["${workspace}/build", "/srv/cache", "${home}/tmp"]})],
    [rule("work-dirs", "/work/proj/build", "/srv/cache", "/homes/ana/tmp")],
)
policy("no_path_rules", "a policy without paths fields compiles to nothing", [r("a"), r("b")], [])
policy(
    "error_relative_root",
    "a relative root gets a ${workspace} hint",
    [r("x", {"within": "data"})],
    error="bad_root",
)
policy(
    "error_glob_root",
    "roots are directories, not globs",
    [r("x", {"within": "~/*.pem"})],
    error="bad_root",
)
policy(
    "error_other_variable",
    "only ${workspace} and ${home} expand",
    [r("x", {"within": "$HOME/data"})],
    error="bad_root",
)
policy(
    "error_other_user",
    "~user is not supported",
    [r("x", {"within": "~bob/data"})],
    error="bad_root",
)
policy(
    "error_unknown_key",
    "a misspelled key is reported",
    [r("x", {"inside": "~/data"})],
    error="bad_paths_field",
)
policy(
    "error_unknown_operation",
    "for lists known operations only",
    [r("x", {"within": "~/data", "for": ["modify"]})],
    error="bad_paths_field",
)
policy(
    "error_no_workspace",
    "${workspace} needs a known workspace",
    [r("x", {"within": "${workspace}"})],
    error="unknown_workspace",
    workspace=None,
)
policy(
    "trailing_slash_roots_normalized",
    "a trailing slash on a root is dropped",
    [r("x", {"within": ["${workspace}/build/", "/srv/cache/"]})],
    [rule("x", "/work/proj/build", "/srv/cache")],
)
policy(
    "error_same_root_twice",
    "${workspace} and ${workspace}/ are the same root",
    [r("x", {"within": ["${workspace}", "${workspace}/"]})],
    error="bad_paths_field",
)
policy(
    "error_bad_home_configuration",
    "the configured home must be a clean absolute path",
    [r("x")],
    error="bad_policy",
    home="/homes/ana\nx",
)
policy(
    "error_duplicate_ids",
    "rule ids are unique",
    [r("x"), r("x")],
    error="bad_policy",
)
policy(
    "error_too_many_path_rules",
    "at most 32 rules declare paths",
    [r(f"r{i}", {"within": f"/srv/{i}"}) for i in range(33)],
    error="too_many_path_rules",
)


def main(argv):
    vectors = {
        "profile": PROFILE,
        "status": "Hand-written expectations. The Python reference (two identical copies, Kai and "
        "AgentArmor) and AgentArmor's native port must reproduce every case exactly.",
        "cases": CASES,
        "policy_cases": POLICY,
    }
    out = HERE / "vectors.json"
    out.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8")
    default = HERE.parents[2] / "train/kai/hostfacts/paths.py"
    reference = Path(argv[1]) if len(argv) > 1 else default
    manifest = {
        "profile": PROFILE,
        "reference_python_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
        "vectors_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": len(CASES),
        "policy_cases": len(POLICY),
        "copies": {
            "kai": ["train/kai/hostfacts/paths.py", "docs/contracts/armor-hostfacts-paths-v1/"],
            "agentarmor": ["src/hostfacts/paths.py", "tests/fixtures/armor-hostfacts-paths-v1/"],
        },
    }
    (HERE / "MANIFEST.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"{len(CASES)} cases, {len(POLICY)} policy cases -> {out}")


if __name__ == "__main__":
    main(sys.argv)
