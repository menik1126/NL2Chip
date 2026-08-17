# P3 Repair Gates

This file records deterministic acceptance gates for the remaining native
parameter work. Agent generation is measured separately; compiler changes are
first evaluated against pinned Lean candidates so a model-side rewrite cannot
be mistaken for a toolchain fix.

## Baseline

- Full toolchain gate: `scripts/verify_p3_toolchain.sh`
- Pinned candidate source: GPT-5.6-sol P3-12 run from 2026-08-16
- Pinned candidates: `experiments/p3_replay_candidates/gpt56sol_20260816`
- Model-free replay: `scripts/replay_p3_cvdp_candidates.py`

The source run produced 6/12 simulation passes. Its six remaining outcomes
were two parameter-contract failures, one Lean symbolic-lowering failure, two
simulation mismatches, and one simulation timeout. A replay result is compiler
evidence only for the exact pinned candidate; fresh agent generation remains a
separate end-to-end measurement.

## Stage Gates

1. Stateful: the same generic module must elaborate at every public parameter
   value for `car_parking_management` and `restoring_division`. Derived widths,
   nested tuple state, reset values, and `Signal.loop` must remain symbolic.
2. Memory: parameterized address/data widths and read/write behavior must agree
   across Lean simulation, specialized CppSim, emitted SystemVerilog, and the
   CVDP `sync_lifo` harness.
3. Indexed construction: unknown-width bit construction and indexed parity
   reduction must lower structurally rather than evaluating a `List.range` at
   the default width. Both Hamming tasks are the benchmark gates.

Every stage must pass its focused unit/behavior tests, the full P3 regression,
and the focused CVDP replay before the next stage is treated as complete.
