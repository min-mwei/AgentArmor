# Native Kai source provenance

Imported production engine and weight-free C++ tests from `Kai/infer` at
commit `164ef847ea920a5297167e5c078dff6a7d704297`.
The Kai working tree and trainer-owned files were not moved or edited.

Local changes begin with the macOS build, native command surface, model parsing,
and security regression tests. Preserve this provenance and the accompanying
NOTICE and third-party license files when updating the snapshot.

Kai continues to own training, checkpoint selection, merged exports, and MLX
reference/parity tooling. AgentArmor owns the production executable and its
CPU/Metal runtime. Synchronize future engine changes through explicit reviewed
updates; do not let two independent production copies evolve.
