# P3 Native Parameter Contract

This document defines the acceptance contract for native, width-parameterized
Sparkle designs. P0 finite specialization remains a compatibility fallback;
P3 must emit one generic SystemVerilog module and must not enumerate the tested
widths.

## Semantic Boundary

- Only explicitly retained top-level Lean `Nat` binders become SystemVerilog
  parameters.
- Every retained parameter has a concrete SystemVerilog default, but changing
  the parameter must change all dependent ports, wires, registers, memories,
  slices, and instances.
- A hardware width or array length must be positive after parameter override.
  Other legal-width constraints are design-specific and must be diagnosed.
- Supported dimension expressions are represented symbolically in the IR.
  Unsupported parameter-dependent value computation fails with a diagnostic;
  it must never be evaluated only at the default and silently frozen.
- A native parameterized module is a single module. Generating one fixed module
  per benchmark sweep value is P0 behavior, not P3 behavior.

## Validation Gates

### Gate P1: core dimensions and combinational logic

- One retained parameter controls input, output, and internal wire widths.
- Multiple parameters and derived addition/multiplication widths are preserved.
- Bitwise and arithmetic operations preserve the generic width.
- The same emitted module elaborates and simulates at unseen widths 3, 17, and
  65 without regenerating Lean or SystemVerilog.
- Missing binders, duplicate parameters, zero-width defaults, and unsupported
  dimension expressions produce actionable compiler diagnostics.

### Gate P2: structural and stateful coverage

- Concatenation, slicing, extension, truncation, and indexing preserve symbolic
  dimensions.
- Registers and reset constants retain parameter-dependent widths.
- Memory address width, data width, and depth remain parameterized.
- Hierarchical instances propagate parameter bindings explicitly.
- Width-dependent repetition uses an IR generate/loop construct rather than
  Lean-time enumeration at the default width.

### Gate P3: toolchain closure

- CVDP runs every public parameter sweep configuration against one emitted DUT
  and rejects declaration-only or fixed-inner-core pseudo-parameterization.
- Diagnostics distinguish Lean elaboration, symbolic-dimension lowering,
  Verilog elaboration, simulation mismatch, unsupported backend, and infra
  failures.
- Formal, CppSim, and PPA each declare and enforce a parameter policy. A result
  for only the default width must never be reported as covering the full family.
- Existing concrete-width designs pass the complete regression suite unchanged.
- Hand-written generic designs pass unseen-width tests before agent-generated
  CVDP results are used as compiler evidence.

## Backend Policy Matrix

| Backend | Required P3 policy |
| --- | --- |
| SystemVerilog simulation | Native parameter sweep over one emitted module |
| Formal | Generic proof when supported, otherwise explicit per-configuration specialization |
| CppSim | Explicit per-configuration specialization until its ABI supports symbolic widths |
| PPA | Report per-configuration metrics; never reuse the default module metric for the family |

## CVDP Native Sweep Runner

Use `cktarchon/run.py --native-parameter-sweep` for the P3 path. It is mutually
exclusive with `--finite-parameter-specialization` (the P0 compatibility path).
The runner discovers only public build-parameter combinations, asks for one
`#synthesizeParameterizedVerilog` design, and then enforces these checks before
the benchmark simulation starts:

1. One generated core declares and semantically uses every required parameter.
2. Parameter-dependent public ports remain parameter-dependent on the core.
3. The strict CVDP wrapper explicitly forwards each parameter as
   `.PARAM(PARAM)` and maps every public input/output without zero fallbacks.
4. Icarus elaborates the exact same saved SystemVerilog file once for every
   public parameter combination using top-level `-P` overrides.

The run writes
`native_parameter_sweep/<prob_id>/manifest.json`. Every case carries the same
`sv_sha256`; different hashes would mean regeneration/specialization and are not
a valid native sweep. The manifest also records per-case Verilog elaboration.

Evaluator failures retain the legacy free-form `detail` field and additionally
set `failure_stage` plus structured `diagnostics`. Stages are:

- `lean_elaboration`
- `symbolic_dimension_lowering`
- `verilog_extraction`
- `parameter_contract`
- `verilog_elaboration`
- `simulation_mismatch`
- `unsupported_backend`
- `infrastructure`

## Formal Coverage Policy

CVDP supplies a simulation oracle, not a public Lean functional specification.
Accordingly, a generic circuit definition that merely typechecks is recorded as
formal `unsupported` with `coverage=none`; it is never reported as a functional
proof. A benchmark or integration layer can provide an explicit
`formal_parameter_contract` containing:

- `scope`: the claim proved by the theorem(s)
- `generic_theorem`: one theorem that quantifies/references every retained
  parameter, or
- `case_theorem_template`: a format string such as
  `fifo_w{DATA_WIDTH}_d{DEPTH}_correct`
- `obligation_text`: the public theorem statement/specification shown to the
  agent
- `required`: whether failure must stop evaluation

`--native-formal-policy generic` accepts family coverage only when the named
generic theorem is present and the complete Lean file passes without `sorry` or
`admit`. `per_configuration` requires the rendered theorem for every public
sweep case. Explicit policies fail closed with `unsupported_backend`; `auto`
uses a supplied contract but otherwise records the lack of a formal oracle and
continues simulation. Each run writes `formal/<prob_id>/manifest.json` so
default-width, partial-case, and full-family claims remain distinguishable.

## CppSim Specialization Policy

CppSim currently uses fixed C++ field/ABI types. Native symbolic IR is therefore
specialized explicitly by `Sparkle.IR.Specialize` before C++ emission. The
command

```lean
#writeParameterizedCppSimDesign design [WIDTH := 17] "design_cppsim.h"
```

evaluates symbolic port/wire/register/memory dimensions, unrolls symbolic
per-bit generate loops, and removes hierarchical parameter bindings for exactly
that configuration. Missing dimensions and conflicting hierarchy bindings fail
closed.

`--native-cppsim-policy per_configuration` runs this command for every public
CVDP configuration, compiles each header with a C++17 compiler, executes a
`reset/eval/tick` smoke program, and writes
`cppsim/<prob_id>/manifest.json`. Its declared scope is backend build/smoke
coverage, not CVDP functional correctness. The current behavioral ABI does not
soundly support packed ports wider than 64 bits; such cases are explicitly
`unsupported`, so partial coverage cannot become family coverage.
`--require-native-cppsim` turns any failed/unsupported case into an
`unsupported_backend` evaluation failure.
