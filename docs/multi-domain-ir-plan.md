# Sparkle Multi-Domain IR Plan

## Goal

One synthesized Sparkle module must be able to contain independent physical
clock domains, type-check intentional CDC crossings, emit real multi-clock
SystemVerilog, and simulate clocks with independent schedules. Splitting the
design into unrelated single-clock JIT modules is not completion.

## Required invariants

1. A domain has stable identity independent of frequency. Two 100 MHz clocks
   remain different domains unless explicitly declared identical.
2. Every register and synchronous memory names exactly one declared domain.
3. Clock edge, clock port, reset port, and reset timing are structured IR data.
   They are not encoded in wire-name suffixes.
4. Ordinary combinational operators only accept signals from one domain.
   Crossing a domain requires an audited CDC primitive.
5. Verilog lowering emits one event control per owning domain.
6. CppSim exposes `tickDomain` and `evalTickDomain`; the scheduler advances
   domains according to their periods and deterministic event ordering.
7. Existing single-domain designs continue to compile through a compatibility
   domain named `clk`.

## Implementation sequence

### Phase 1: First-class domain IR

- Add `ClockDomain`, `DomainId`, `ClockEdge`, `ResetKind`, and `RegisterReset`.
- Make register and memory statements refer to `DomainId`.
- Add DRC for duplicate/missing domains and missing one-bit clock/reset ports.
- Keep legacy builder entry points, but translate them immediately to the new
  structured representation.

### Phase 2: Typed elaboration and multi-domain ports

- Preserve the domain expression from every `Signal dom T` binder and result.
- Extend domain declarations with stable public clock/reset names.
- Add a heterogeneous top-level output interface so one module can expose
  outputs owned by different domains without pretending they share a domain.
- Reject undeclared cross-domain expressions before netlist generation.

### Phase 3: CDC primitives

- Add explicit primitives for level synchronization, pulse synchronization,
  reset synchronization, and asynchronous FIFO transport.
- Each primitive owns its source/destination-domain contract. General-purpose
  `unsafeReclock` remains private to the library implementation.
- Add structural DRC checks for synchronizer depth and FIFO pointer crossings.

### Phase 4: Backends and simulation

- Verilog resolves every sequential statement through `Module.clockDomains`.
- CppSim partitions next-state commits by `DomainId` and exports domain-indexed
  tick functions while retaining all-domain `tick()` for legacy callers.
- JIT exposes domain discovery and domain-indexed tick calls.
- Add a deterministic event scheduler using `periodPs`; simultaneous edges use
  evaluate-all, commit-all ordering.

### Phase 5: Validation

- Unit tests: two equal-frequency distinct domains, mixed edges, sync/async/no
  reset, per-domain memory updates, illegal crossings, and deterministic CppSim.
- End-to-end CVDP: `cdc_pulse_synchronizer_0004`,
  `cdc_pulse_synchronizer_0013`, and `fifo_async_0001`.
- Report compile, lint, simulation, token/iteration cost, and wall-clock cost;
  classify any remaining failures as infra or candidate semantics from evidence.

## Current status

All five phases are implemented for the audited CDC protocols used by the CVDP
targets in this plan.

| Requirement | Evidence |
|---|---|
| First-class domains | Registers and memories carry stable `DomainId` ownership; legacy single-clock designs use the compatibility domain. |
| Domain safety | DRC rejects missing/duplicate domains, missing clock/reset ports, unmarked combinational crossings, same-domain CDC markers, and incomplete hierarchy maps. |
| Typed elaboration | Concrete `Signal dom T` binders preserve their physical domain; heterogeneous outputs use `Circuit`. |
| CDC library | Level, pulse, vector-pulse, reset synchronizer, and power-of-two asynchronous FIFO primitives lower to audited IR. |
| Verilog | Every state element uses the owning domain's clock edge and reset contract. Async FIFO storage has a write-domain event control and FWFT read. |
| CppSim/JIT | `tickDomain`, `evalTickDomain`, domain discovery, deterministic independent-period scheduling, hierarchy forwarding, and async-memory introspection are implemented. |
| Parameters | Multi-domain designs retain symbolic widths and FIFO depth in native parameterized Verilog; CppSim specializes explicit parameter values. |
| CVDP adapter | Multiple physical clock ports, heterogeneous outputs, core parameter forwarding, parameter-derived port widths, and observed internal reset outputs are preserved. |

### Scope boundary

This completion claim covers first-class multi-domain structure and the audited
CDC protocols above. It does not claim analog metastability simulation,
arbitrary unsynchronized multi-bit crossings, non-power-of-two FIFO depths, or
automatic inference of an unknown CDC protocol from unconstrained logic. Such
crossings remain rejected or require a new explicit primitive.
