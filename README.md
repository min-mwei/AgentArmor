# AgentArmor

AgentArmor is an **open-source security layer for AI agents**. It checks
tool calls against your policy before execution and scans files, tool output,
and chat logs for prompt injection. It can integrate with any agent through its
[API](armor/README.md#http-interface), with adapters included for Codex and Claude Code.

## Why AgentArmor

AI agents use trusted tools while reading untrusted content. AgentArmor
gives teams a separate policy gate across supported agents, with a security
model they can host on their own infrastructure.

## Kai

[Kai](https://huggingface.co/min-h-wei/Kai) is a compact **0.8B decision model for
fast reasoning**, specialized in agent security. It evaluates policy and agent
context to produce structured decisions for tool authorization and cross-prompt
injection (XPIA) detection. Kai is designed for deployment from cloud to edge.

Initial v0 development results compare Kai with [Kev-4B](https://github.com/jaredpalmer/kev),
a general-purpose decision model with **5× Kai's parameter count**:

| Development metric         | Kai-0.8B  | Kev-4B |
| -------------------------- | --------: | -----: |
| ToolCall accuracy          | **86.7%** | 72.1%  |
| XPIA attack recall (BIPIA) | **86.6%** | 39.1%  |

**ToolCall accuracy** is the percentage of tool calls correctly allowed or denied
under the security policy. **XPIA attack recall** is the percentage of indirect
prompt-injection attacks detected.

These results are preliminary. See [methods and limitations](docs/benchmarks.md)
and the labeled [benchset examples](benchset/benchset.jsonl).

## Get started

Start with **Python 3.10+** and Codex and/or Claude Code installed.

```sh
python3 install.py
```

The installer sets up Python and the vLLM runtime. Follow the
[server guide](armor/README.md#gpu-setup) for deployment requirements and startup,
then connect [Codex](agents/codex_code/README.md) or
[Claude Code](agents/claude_code/README.md) and configure
[your policy](agents/policy.example.json).

## Example policy

The [included example](agents/policy.smoke.json) has one rule: never create
or modify `forbidden-marker.txt`, even if the user asks.

| Proposed tool call                               | Expected result                                             |
| ------------------------------------------------ | ----------------------------------------------------------- |
| `cat benign.txt` or Claude's `Read` on that file | Accepted by the guard; normal agent permissions still apply |
| `printf blocked > forbidden-marker.txt`          | Rejected before execution                                   |
| Any tool call while Kai is unavailable           | Rejected because the policy check could not complete        |

An allowed check preserves the agent's existing permissions. Hooks must be
installed and enabled for these checks to run.

## Scan files and chat logs

```sh
agentarmor scan /path/to/document.txt --wait
agentarmor scan /path/to/session.jsonl --source chat_log
```

Installed hooks also scan tool output and conversation logs asynchronously.
Scan warnings are advisory; they cannot undo completed actions. Kai v0 can flag
benign text, and chat/tool-output scanning is experimental.
See [scan behavior](armor/README.md#asynchronous-scans).

## Development

Work on `dev`; `main` is reserved for releases.

```sh
python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
pytest
python scripts/test_live_agents.py --url http://127.0.0.1:8000
```

The native smoke harness uses GPT-6-Luna / low and Claude Haiku 5.5 / low.
Omit `--url` for control tests without a model.

## Contributing

- Add another agent under `agents/<agent_name>/` with a hook runner, configuration
  template, setup guide, and allow/deny plus file/chat-log smoke tests.
- [File an issue](https://github.com/min-mwei/AgentArmor/issues/new) to share
  **false positives and hard negatives**, with a sanitized example, policy/task,
  expected and actual results, and model/runtime versions.
