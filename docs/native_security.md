# Local runtime validation

The Qwen refactor replaced the EG2 engine and its architecture-specific tests.
Previous validation tables apply only to that historical implementation. Current
native code is in `src/native`; Python baseline code is isolated in `src/vllm`.

## Required local checks

```sh
cmake -S . -B build/release -DCMAKE_BUILD_TYPE=Release
cmake --build build/release -j 4
ctest --test-dir build/release --output-on-failure -LE gpu

cmake -S . -B build/asan -DCMAKE_BUILD_TYPE=Release -DAGENTARMOR_METAL=OFF -DAGENTARMOR_SANITIZER=address
cmake --build build/asan -j 4
ctest --test-dir build/asan --output-on-failure

cmake -S . -B build/tsan -DCMAKE_BUILD_TYPE=Release -DAGENTARMOR_METAL=OFF -DAGENTARMOR_SANITIZER=thread
cmake --build build/tsan -j 4
ctest --test-dir build/tsan --output-on-failure

bash scripts/check_leaks.sh build/release
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest
```

Native tests cover analytic norms/pointer scores, DeltaNet's recurrence,
immutable prefix states, chunk continuation, invalid dimensions, finite values,
concurrent CPU calls, fail-closed hooks, packaging and installation. Python tests
cover canonical option order, delimiter escaping, temperature, full-input
admission, incomplete model outputs and HTTP denial behavior without model weights.

## Metal and model validation

```sh
.venv/bin/python scripts/check_metal.py build/release
```

This optional weight-free test takes Kai's device lock nonblockingly and refuses
to run if a trainer job owns it. It compares Metal operators against the CPU
references. Do not bypass the lock or stop another model job. Compile/CPU checks
alone do not validate Metal execution, model parity, performance or evidence.
Leak checks above cover CPU lifetimes; Metal resource lifetime checks require
the optional GPU test under an available device window.

Model-backed vLLM and native jobs require a trainer-owned qualification window
and immutable export. Follow the remaining gates in
[runtime refactor](runtime_refactor.md). Neither diagnostic answers nor primitive
tests authorize policy allows. Every synchronous hook remains an explicit denial;
background observations are advisory and report incomplete coverage.
