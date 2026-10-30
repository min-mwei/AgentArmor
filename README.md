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

Kai is the official compact firewall model on the EmbeddingGemma-2 text encoder, approximately 270M text parameters.
It answers typed authorization and prompt-injection questions through its trained pointer and evidence heads, without
text generation. AgentArmor integrates only with this implementation. The earlier Qwen 0.8B model and its benchmark
results are historical references.

The current integration uses the canonical Kai MLX implementation on Apple Silicon. It binds checkpoint, tokenizer,
model code, numerical dependencies, question text and calibrated cutoffs in one deployment manifest. A source refactor
and CPU tests do not qualify checkpoint accuracy, span quality or latency. Those require the registered guarded read.

## Get started

Start with **Python 3.10+** and Codex and/or Claude Code installed.

```sh
python3 install.py --backend kai --kai-source /path/to/Kai
```

The installer sets up Python, AgentArmor and the local official Kai source package. Follow the
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
Scan warnings are advisory; they cannot undo completed actions. An unqualified checkpoint can flag
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
