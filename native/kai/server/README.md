# AgentArmor native runtime

This is the adopted Kai engine scheduler and encoded diagnostic transport.
Build through the root CMake project; the executable is `agentarmor`.

See [the root guide](../../../README.md) for current capabilities and
[native security gates](../../../docs/native_security.md) for sanitizer,
threading, malformed-input, release-packaging, and qualification requirements.

The encoded API is loopback-only and requires explicit `--encoded-only` startup.
It returns logits and is not the qualified text policy service.
