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
