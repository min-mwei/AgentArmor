# Path rules in a policy file

Most policy rules are plain sentences, and Kai reads them as written. When a rule is about where
files are, add one optional `paths` field. The Python host-fact reference resolves
the supplied path effects and reports whether they are inside the rule's directories. These facts
can be supplied to Kai; automatic hook-to-service policy assembly is not implemented yet.

## Writing a path rule

Each line of the policy file is one rule. A path rule adds `paths`:

```jsonl
{"id":"project-writes","tier":"soft_deny","description":"Writes outside this project are held for my approval.","paths":{"within":"${workspace}","for":["write","delete"]}}
{"id":"ssh-keys","tier":"hard_deny","description":"Never read or change my SSH keys.","paths":{"within":"~/.ssh"}}
{"id":"build-dirs","tier":"allow","description":"Build output may be written anywhere under these directories.","paths":{"within":["${workspace}/build","~/.cache/myapp"],"for":["write"]}}
{"id":"delegated-task","tier":"allow","description":"Calls needed for the user's authorized task within the delegated scope."}
```

- **`within`** is one directory or a list of up to 8. Write each one as:
  - `${workspace}` or `${workspace}/sub/dir`, the project AgentArmor is protecting;
  - `~` or `~/dir`, your home directory (`${home}` works too);
  - an absolute path such as `/srv/data`.
- **`for`** is optional and lists which operations the rule is about: `read`, `write`, `delete`, `list`
  or `execute`. Leave it out to cover all of them.
- **The description still says what the rule means.** `paths` only tells AgentArmor which
  directories to check. Whether being inside or outside matters comes from your sentence: "writes
  outside this project" and "never touch my SSH keys" both use `within`.
- **Rules without `paths` work as before.**

## What AgentArmor checks for you

For every file a call touches, AgentArmor resolves the path the way the tool itself will:
- the working directory;
- `~` and `$VAR`, only where that tool or shell actually expands them;
- `..` and duplicate slashes;
- symbolic links, including links inside your project that point elsewhere.

It then compares the result against each rule's directories one path component at a time. So:
- `/srv/data-old` is not inside `/srv/data`;
- `../../etc/passwd` is outside your project;
- a link in your project that points to `/etc` counts as `/etc`.

On a case-insensitive Mac volume, `Project` and `project` are the same directory.

If AgentArmor cannot work out a path exactly, it reports that instead of guessing. Examples are an
unset variable, a glob in a shell command or a symbolic-link loop.

## Checking a policy

Use `src/hostfacts/paths.py` from a checkout. `compile_policy(rules, home=...,
workspace=...)` accepts parsed policy rows and trusted absolute roots. It returns
compiled path rules or raises `PolicyError` with a rule-specific explanation.

```python
from hostfacts.paths import compile_policy

rules = [{"id": "project", "paths": {"within": "${workspace}", "for": ["write"]}}]
compiled = compile_policy(rules, home="/srv/user", workspace="/srv/project")
```

Set `PYTHONPATH=src` when running this example from the repository. This is a
reference API, not a server endpoint. The retired `agentarmor policy-check` and
`agentarmor hostfacts` commands have been removed with the C++ client.
Compile policies once at load time and surface errors before evaluating calls.

## Limits

- Up to 32 rules may have `paths`, each with up to 8 directories.
- Only `${workspace}`, `${home}` and `~` expand inside `within`. Globs and other variables are
  rejected, with a hint saying what to write instead.

The computation is shared with Kai's training data. `src/hostfacts/paths.py` is byte-identical to
Kai's `train/kai/hostfacts/paths.py`. Both Python copies must reproduce the hand-written cases in `tests/fixtures/armor-hostfacts-paths-v1/`.
