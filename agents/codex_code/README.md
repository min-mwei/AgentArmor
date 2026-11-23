# Codex: installing AgentArmor's hooks

One installation, made by one command. `agentarmor-monitor hooks-config` prints the hooks for
Codex with the installed program's own path in them; [hooks.json.example](hooks.json.example) and [config.toml.example](config.toml.example) in this folder are what it
prints with a placeholder path, kept equal to it by a test.

**Start in shadow.** A shadow gate stops nothing: for every proposed call it asks the local
server and records what it would have answered. Nothing here is qualified to allow or deny on
its own yet (`policy_ready` is false), so shadow is the mode to install.

1. Install the monitor in a trusted virtual environment and start its server, as the
   [monitor's guide](../../src/monitor/README.md) describes.
2. Print the hooks with the absolute path of the installed program:

   ```sh
   agentarmor-monitor hooks-config --agent codex_code --toml --gate "$(command -v agentarmor-monitor)" --shadow
   ```

3. Merge the printed tables into `~/.codex/config.toml`, or drop `--toml` and save the JSON as `~/.codex/hooks.json`. Install one of the two, so that each hook runs once. If hooks of Summarizer or AgentArmor (an observer or a gate) are already registered for these events, replace them: do not add a second recorder beside them. Leave unrelated settings and hooks as they are. Do not pair the gate with a hook that rewrites tool input: the call that runs must be the call that was asked about.
4. Restart Codex and review the registered commands with `/hooks`.

**What is installed.** One handler for each event that carries session evidence (10 kinds:
prompts, proposed calls, results and failures, compaction, session start and end). The gate
records every event itself and, on a proposed call, asks the server. It takes the place of the
plain observer: do not install the observer beside it, or every event is logged twice. A hook
process loads no model and uses the standard library only.

**Other forms the same command prints.**

- Without `--gate`: the plain observer (`summarizer-hook`), which records and never answers.
- With `--gate` and without `--shadow`: a gate whose answer is acted on. Codex has no setting that stops a call when a hook cannot start or runs out of time: the call then runs, so keep Codex's approvals and sandbox on. Do not install
  it as protection while `policy_ready` is false.

Prefer user-level or managed settings over a repository's own: a repository you do not control
should not decide what runs on your machine.

The [fixture](fixture/README.md) folder holds templates that deny every call without asking a
model. They are for testing the hook entry point and are not an installation.
