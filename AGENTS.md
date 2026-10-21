# Working on AgentArmor

- Work on `dev`. `main` is reserved for reviewed releases. Keep these as the two
  permanent branches.
- GitHub Actions is disabled for this repository. Run checks locally.
- Keep agent-specific templates and guides in `agents/codex_code` and
  `agents/claude_code`; share client behavior in `armor/hooks.py` and
  `armor/scan_client.py`.
- Kai is a decision model. Preserve its pointer readout, delimiter escaping,
  isolated question rows, checkpoint temperature, and evidence offsets. Do not
  substitute generated text or vocabulary-logit classification for its head.
- Use lazy imports for GPU dependencies so local development and hook clients
  work without vLLM. Do not commit model weights, chat logs, secrets, or `.env`.
- Synchronous checks must emit an explicit denial on errors. An allowed check
  abstains from the agent's existing permission flow. Background scans are
  advisory and must not claim to prevent a completed tool's side effects.
- Preserve whole-input scan coverage. Refuse oversized or unencodable input;
  never silently truncate a document or return a clear verdict after a partial scan.
- Run `ruff check .`, `ruff format --check .`, and `pytest` with the project
  environment. Distinguish CPU contract tests from actual GPU/model validation.
