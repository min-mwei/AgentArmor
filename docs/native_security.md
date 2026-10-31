# Native macOS development and security gates

The initial platform is macOS on Apple Silicon M4 and newer. M4 uses the
portable Metal path; M5 Max's supported Metal/TensorOps path is the main
optimization target. Dispatch must follow device/OS capabilities. Build a
baseline arm64 executable rather than compiling CPU instructions only M5 can
execute. Numerical correctness and bounded memory take precedence over speed.

## Current status

`src` imports the Kai engine at the source revision recorded in
[ORIGIN.md](../src/ORIGIN.md). The upstream Kai tree remains untouched.
The native executable supports `doctor`, fail-closed hook checks, and explicitly
requested encoded diagnostic serving with `--device cpu|gpu|auto`. The native
text policy path is not qualified and reports `policy_ready: false`.

The engine lives in `src/kai`, executable/transport code in `src/server`, and
weight-free contracts in `tests/native`. Vendored dependencies are isolated in
`third_party`. CMake emits the executable and test helpers under
`build/<config>/bin`.

The Python `armor/` package, HTTP service, agent launchers, installer,
`.env.example`, and project metadata have been removed. Python remains only in
optional developer tests. Canonical MLX training/export/parity tools remain in
Kai; historical AgentArmor code is available at Git commit `466a966`. Synthetic
renderer byte/offset references live in `tests/fixtures`.

## Review fixes in this migration

- Replace the handwritten metadata JSON parser with bounded, strict parsing.
  Reject duplicate keys, malformed numbers/UTF-8/surrogates, excessive nesting,
  and oversized input.
- Verify tensor shape products, negative/overflowing dimensions, byte lengths,
  offsets, alignment, overlapping payloads, readout dimensions, and finite head
  values before inference.
- Reject unknown model families, readout/mask enums, out-of-range encoder
  dimensions, and invalid normalization/RoPE values.
- Validate all engine variants through a common model contract.
- Make scheduler shutdown safe for repeated/concurrent callers.
- Reject symlink/hard-linked/unowned device-lock files and close-on-exec the
  lock descriptor.
- Bound encoded HTTP body parsing, reduce inference waits, keep the diagnostic
  listener on loopback, and suppress raw parser text in native hook failures.
- Preserve ARC for Objective-C++ and RAII for file mappings, locks, and worker
  lifetime. Keep one scheduler worker per resident engine.
- Track Metal source inputs so CMake regenerates embedded shader headers after
  a kernel edit; a normal rebuild must not silently use stale shader text.

These are review findings and regression controls, not proof that the complete
runtime is free of defects. Weight-backed CPU/Metal execution, GPU buffer
boundaries, mapping lifetime under Metal, and peak resource behavior still need
trainer-scheduled qualification.

## Required local checks

Release compilation and weight-free contracts:

```sh
cmake -S . -B build/release -DCMAKE_BUILD_TYPE=Release
cmake --build build/release -j 4
ctest --test-dir build/release --output-on-failure -LE metal-compile
```

Optional Python 3.11+ developer checks (no product runtime dependency):

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest
```

The template tests execute the built native binary from a path containing
spaces with no Python interpreter on `PATH`. They verify explicit denials,
incomplete observations, malformed/duplicate/oversized/invalid-UTF-8 input
refusal, and redaction. They do not launch live Codex/Claude sessions or load
weights. The obsolete Python-service live harness was removed.

AddressSanitizer plus UndefinedBehaviorSanitizer:

```sh
cmake -S . -B build/asan -DCMAKE_BUILD_TYPE=Debug -DAGENTARMOR_METAL=OFF -DAGENTARMOR_SANITIZER=address
cmake --build build/asan -j 4
ctest --test-dir build/asan --output-on-failure
```

ThreadSanitizer must use a separate build:

```sh
cmake -S . -B build/tsan -DCMAKE_BUILD_TYPE=Debug -DAGENTARMOR_METAL=OFF -DAGENTARMOR_SANITIZER=thread
cmake --build build/tsan -j 4
ctest --test-dir build/tsan --output-on-failure
```

Contracts cover packed layout isolation, concurrent transport, queue capacity,
deduplication, guard priority, scan fairness, shutdown, device locks, malformed
model metadata, tensor bounds, Unicode, descriptor lifetime, hook failure
redaction, and 20,000 deterministic protocol mutations. Add regressions for
every security fix. Sanitizer failures block adoption; do not suppress a
finding simply to obtain a green run.

The optional libFuzzer target requires a Clang toolchain that actually includes
the macOS libFuzzer runtime. Apple's installed Clang may lack that archive;
CMake refuses the option explicitly in that case. Deterministic mutation tests
still run without it.

For heap-leak inspection of weight-free helpers, run
`bash scripts/check_leaks.sh build/release`. The script gives temporary copies
of test helpers a debug entitlement so macOS can inspect their memory. It does
not change or add entitlements to the product executable or release assets.

Metal shader compilation is labeled `metal-compile`. It creates pipelines but
does not dispatch a model. Model/parity/Metal validation must follow Kai's
trainer-owned device queue and resource guard.

## Model and memory release gates

Before enabling native policy decisions:

1. Complete the tokenizer, event renderer, package pinning, probability/cutoff,
   and Unicode evidence contract in
   [Kai's trainer requirements](https://github.com/min-mwei/Kai/blob/dev/docs/Kai_requirements.md).
2. Require CPU and Metal probability/decision/evidence parity and explicit
   cutoff-boundary cases for the exact merged artifacts.
3. Test malformed weights and repeated successful/failed loads; check descriptor,
   mmap, heap, Objective-C/Metal resource, and worker lifetime.
4. Exercise concurrent requests, shutdown during active work, canceled callers,
   queue saturation, and restart. Verify pending requests resolve explicitly.
5. Run Metal API validation and GPU capture checks for partial tiles, sequence
   limits, buffer extents, command failures, and M4/M5 dispatch paths.
6. Measure cold/warm and sustained mixed guard/scan load, resident memory,
   allocation growth, and deadline behavior on actual M4 and M5 Max hardware.
7. Port memory/swap supervision into the native process/supervisor before
   enabling qualified model-backed operation. Kai's trainer-owned Python guard
   remains for reference jobs only.

CPU tests with fake engines do not establish model quality or GPU safety.

## Validation recorded on 2026-10-30

Host: Apple M5 Max, macOS 27.0.1, AppleClang 21, macOS SDK 27.0.

| Check | Result | Scope |
| --- | --- | --- |
| Release build with Metal compiled in | 9/9 weight-free CTest contracts passed | Shader/GPU execution test excluded |
| ASan + UBSan CPU build | 9/9 passed; no sanitizer findings | Parser/layout/fake-engine/CLI/installer paths |
| ThreadSanitizer CPU build | 9/9 passed; no race reports | Includes concurrent shutdown and HTTP/scheduler tests |
| macOS `leaks` on inspectable test copies | 0 leaks, 0 leaked bytes for both helpers | Parser/model-metadata loader and concurrent server; no real model |
| Protocol mutation runner | 20,000 cases per suite run | Bounded deterministic mutations, not coverage-guided libFuzzer |
| Python developer tests | 23 passed against each Release, ASan/UBSan, and TSan binary; Ruff lint/format passed | Native templates without Python on PATH, malformed hook input, preserved fixture integrity, and source hygiene |
| Native dependency inspection | System frameworks/libc++ only; no Python runtime | Release executable |
| Staged CMake install | Executable and all 5 license/notice files verified | Temporary prefix; `doctor` runs without Python on PATH |
| Vendored dependency inventory | 4/4 SHA-256 hashes match checked-in files | Header bytes unchanged; license whitespace normalization recorded |

The optional libFuzzer target could not link with this AppleClang distribution
because its libFuzzer archive is absent; configuration now rejects that option
with a toolchain instruction. M4 hardware, real model CPU/Metal parity, GPU
buffer validation, sustained model memory/leak behavior, and latency remain
unmeasured in this review.

## Release assets and installer

Prepare assets locally:

```sh
bash scripts/package_macos.sh --binary build/release/bin/agentarmor --version 0.1.0 --out dist/0.1.0
```

This creates `agentarmor-macos-arm64.tar.gz`, its SHA-256 file, and a
version-stamped `install.sh`. The intended public entry point is:

```sh
curl -fsSL https://github.com/min-mwei/AgentArmor/releases/latest/download/install.sh | bash
```

Publishing those assets is separate from packaging. The installer checks the
platform, download protocol, checksum, archive layout, executable architecture,
and destination before an atomic install to `~/.local/bin`. It downloads no
weights, runs no Python, uses no sudo, and does not mutate agent settings.

Initial releases may be unsigned. `--identity "Developer ID Application: ..."`
optionally signs local packaging output with hardened runtime and a timestamp;
the script verifies that signature. No notarization, stapling, quarantine
removal, or Gatekeeper bypass is performed. Signing and notarization are
separate operations in Apple's [distribution guidance](https://help.apple.com/xcode/mac/current/en.lproj/dev033e997ca.html).
Test the actual downloaded artifact on a clean M4/M5 machine before release.
