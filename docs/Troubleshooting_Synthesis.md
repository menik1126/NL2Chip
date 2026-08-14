# Known Limitations and Troubleshooting — Sparkle Synthesis

## Imperative Syntax with `Signal.circuit`

**`Signal.circuit` provides imperative-style hardware description with `<~` register assignment.**
It desugars to `Signal.loop` + `Signal.register` + `bundleAll!` at compile time.

```lean
-- ✓ Simple counter with <~
def counter {dom : DomainConfig} : Signal dom (BitVec 8) :=
  Signal.circuit do
    let count ← Signal.reg 0#8;
    count <~ count + 1#8;
    return count

-- ✓ Counter with enable
def counterEn {dom : DomainConfig} (en : Signal dom Bool) : Signal dom (BitVec 8) :=
  Signal.circuit do
    let count ← Signal.reg 0#8;
    let next := count + 1#8;
    count <~ Signal.mux en next count;
    return count

-- ✓ FSM with multiple registers
def fsm {dom : DomainConfig} (start : Signal dom Bool) : Signal dom (BitVec 2) :=
  Signal.circuit do
    let state ← Signal.reg 0#2;
    let count ← Signal.reg 0#8;
    let isIdle := state === (0#2 : Signal dom _);
    let isRunning := state === (1#2 : Signal dom _);
    state <~ hw_cond state
      | (start &&& isIdle) => (1#2 : Signal dom _)
      | (isRunning &&& (count === 255#8)) => (0#2 : Signal dom _);
    count <~ Signal.mux isRunning (count + 1#8) 0#8;
    return state
```

**Syntax rules:**
- `let x ← Signal.reg init;` — declare a register with initial value (semicolon required)
- `x <~ expr;` — assign the register's next-state input (semicolon required)
- `let x := expr;` — local combinational binding (semicolon required)
- `return expr` — the output signal (no semicolon, must be last)

**What it generates:** `Signal.circuit do ...` is a macro that rewrites to:
```lean
let _circuit_result := Signal.loop fun _circuit_state =>
  let count := projN! _circuit_state N 0   -- unpack register outputs
  ...
  bundleAll! [Signal.register 0#8 (count + 1#8), ...]  -- pack register inputs
let count := projN! _circuit_result N 0    -- project for return expression
count
```

**Alternative approaches (still work):**

```lean
-- Dataflow style with Signal.loop (more explicit, no macro)
def counter {dom : DomainConfig} : Signal dom (BitVec 8) :=
  Signal.loop fun cnt =>
    Signal.register 0#8 (cnt + 1#8)

-- Feed-forward: direct dataflow (no feedback needed)
def registerChain (input : Signal Domain (BitVec 16)) : Signal Domain (BitVec 16) :=
  let d1 := Signal.register 0#16 input
  let d2 := Signal.register 0#16 d1
  d2
```

**Key points:**
- Signals are **wire streams** — `<~` is syntactic sugar, not mutable assignment
- Operations use operator syntax: `a + b`, `a &&& 0xFF#8`, `1#64 <<< shift`
- Use `Signal.mux` for conditionals, `hw_cond` for priority muxes
- `Signal.circuit` works with both synthesis (`#synthesizeVerilog`) and simulation

---

## Signal Constants and Domain Inference

**`Signal.pure` in `let` bindings causes domain metavariable errors:**

```lean
-- ❌ WRONG: domain ?m is unresolved
def example_WRONG {dom : DomainConfig} (x : Signal dom (BitVec 16)) : Signal dom (BitVec 16) :=
  let rnd := Signal.pure 32#16     -- ❌ Signal ?m (BitVec 16) — domain unknown
  x + rnd                           -- typeclass instance problem is stuck

-- ✓ RIGHT: Use Signal.lit with explicit domain
def example_RIGHT {dom : DomainConfig} (x : Signal dom (BitVec 16)) : Signal dom (BitVec 16) :=
  let rnd := Signal.lit dom 32#16  -- ✓ Signal dom (BitVec 16)
  x + rnd

-- ✓ BEST: Use mixed operator directly (no let binding needed)
def example_BEST {dom : DomainConfig} (x : Signal dom (BitVec 16)) : Signal dom (BitVec 16) :=
  x + 32#16                        -- ✓ Mixed HAdd instance lifts 32#16 automatically
```

**Why this happens:**
- `Signal.pure 32#16` creates `Signal ?m (BitVec 16)` where `?m` is an unresolved domain
- When used in `let`, Lean can't infer `?m` from context before resolving `HAdd`
- The typeclass resolver gets stuck on `HAdd (Signal dom _) (Signal ?m _) _`

**Solutions (in order of preference):**
1. **Use mixed operators directly**: `x + 32#16`, `255#8 ++ data`, `mask &&& 0xFF#8`
2. **Use `Signal.lit dom`**: `let c := Signal.lit dom 32#16` — domain is explicit
3. **Add type annotation**: `let c : Signal dom (BitVec 16) := Signal.pure 32#16`

---

## Generic Widths and SystemVerilog Parameters

Sparkle can retain a top-level Lean `Nat` binder as a native SystemVerilog
module parameter. Give every exported parameter a nonnegative default either
with a Lean optional binder such as `(width : Nat := 8)` or in the synthesis
command, and keep its Lean name identical to the benchmark parameter:

```lean
def genericIdentity {dom : DomainConfig} {width : Nat}
    (x : Signal dom (BitVec width)) : Signal dom (BitVec width) :=
  x

#synthesizeVerilog genericIdentity parameters [width := 8]
```

Zero is valid for a `Nat` offset or index parameter; every expression used as
a packed width or array length must nevertheless evaluate to a positive value.

This emits a module header such as `parameter width = 8`, while
the port range remains a symbolic expression of `width` and is guarded against
invalid zero-width overrides. Expressions such as
`BitVec (width + 1)` and symbolic `HWVector` sizes are retained as dimension
expressions as well.

`BitVec.zeroExtend` and `BitVec.setWidth` produce an explicit unsigned resize
node rather than relying on an inferred destination width. Consequently,
parameterized narrowing and widening survive IR optimization and are emitted
as SystemVerilog sized casts such as `(width)'($unsigned(x))`: narrowing keeps
the least-significant bits and widening fills the new high bits with zero.
Unsupported signed or context-dependent SystemVerilog cast semantics are
rejected with a diagnostic instead of silently being treated as this unsigned
resize operation.

Parameter-only Lean `Nat` values are retained as first-class `paramConst` IR.
For example, `BitVec.ofNat width (width + 1)`, masks such as
`(1 << bits) - 1`, shifts, division/modulo, powers, and `clog2` are evaluated at
the selected native override rather than frozen at the default. SystemVerilog
emission assigns explicit unsigned working widths to intermediate operations,
including totalized Nat subtraction and division-by-zero behavior. Native Nat
parameters follow a 32-bit unsigned contract; hardware dimensions and Nat
working widths are guarded at 1,048,576 bits so hostile overrides fail before
an HDL frontend attempts an enormous allocation.

For imported SystemVerilog, parameter-dependent `generate if`, the supported
canonical procedural `for` form, and zero-based 1R1W memories with symbolic
depth are retained in native IR. Memory depth is independent of address width,
so non-power-of-two depths remain exact. Generate-for, ambiguous/multi-port
memories, signed or context-dependent sizing, and noncanonical loop forms are
still outside this modeled subset and fail closed instead of being expanded at
defaults or guessed as 8/32-bit logic.

The parameter suffix is available on the IR and SystemVerilog entry points:
`#synthesize`, `#synthesizeVerilog`, `#synthesizeDesign`,
`#synthesizeVerilogDesign`, and `#writeVerilogDesign`. These commands retain
native parameters, and their `parameters [...]` values remain SystemVerilog
defaults. CppSim has a fixed-width C++ ABI, so `#writeCppSimDesign` and the
combined `#writeDesign` command first call
`Sparkle.IR.Specialize.specializeDesign`: their `parameters [...]` values select
the concrete environment, while omitted values use the declarations' defaults.
The pass recursively specializes reachable children, cloning a child when two
instances use different parameter values, and rejects unknown, unresolved, or
zero-valued hardware dimensions. A closed fixed-width Lean wrapper remains a
convenient alternative when a separately named concrete module is desired.
The current CppSim execution backend supports scalar packed operations through
64 bits. Passive, word-aligned concat/copy into wide output containers is also
supported; other wider operations are explicitly rejected by the C++/JIT
commands instead of emitting skipped logic.
Specialization selects a native generate branch while retaining its lexical SV
scope, and retained procedural loops remain native items. Until a normalization
pass lowers those constructs to core IR, CppSim and automatic model extraction
reject them explicitly; use the emitted SystemVerilog for those designs.

The SV-to-Lean verification-model generator is likewise concrete-width today
and rejects a retained-parameter module rather than guessing widths. This does
not prevent manually stated source-level Lean theorems from quantifying over a
generic circuit definition; it only limits automatic verification-model
extraction from parameterized IR/SystemVerilog.

Keep three claims separate when reading evaluator output:

- **Lean source complete** means compilation found no reported `sorry`. It does
  not imply that the file contains a correctness theorem.
- **Finite parameter sweep** means only the exact configurations listed in the
  evidence were exercised. Even if every listed configuration passes, no
  conclusion follows for an unlisted width.
- **Universal Lean source theorem** requires explicit theorem evidence whose
  proposition quantifies over the parameter and states its design-specific
  legal domain. This is a theorem about Lean source semantics; it is not, by
  itself, a proof of the compiler or emitted SystemVerilog.

Universal evidence is independently rechecked by the prebuilt
`sparkle-certify` process against the imported kernel environment. Candidate
log text is used only to discover a requested theorem and is never trusted as
the certificate itself. The checker rejects `sorry` and project-defined axioms;
its fresh nonce, theorem, parameter list, proposition, and axiom list must all
match before the report shows a proved source theorem.

The HTML report never upgrades `has_sorry = false`, a successful sweep, or a
cache hit into a universal theorem. Cached and newly computed results for the
same configuration have the same finite evidence scope; cache provenance only
explains where that configuration's result came from.

The parameter must actually determine the relevant ports, state, memories, or
logic. Merely adding `parameter WIDTH = 8` to a wrapper or to an otherwise
fixed 8-bit core does not implement a sweep; changing the wrapper parameter
would still leave the inner datapath fixed. The CVDP evaluator rejects both
missing parameters and declaration-only/fixed-port parameterization.

Concrete wrappers remain useful when a separate fixed module is desired, but
they are not a substitute for a benchmark that rebuilds one DUT over multiple
parameter values. Functional simulation can exercise a native parameter
sweep. When the harness yields a complete literal configuration matrix, the
PPA runner executes each point in an isolated workspace, with global
parallelism controlled by `--ppa-workers`; successful points are reused from a
content-addressed cache selected by `--ppa-cache-dir`. Cache identity includes
RTL, top, configuration, and the effective flow/tool fingerprint. Failed or
timed-out points remain retryable. If enumeration is incomplete, PPA remains
skipped rather than inventing missing combinations or attaching module-default
metrics to the entire sweep. Persistent reuse across program invocations
requires an immutable `ORFS_DOCKER_IMAGE_DIGEST`; with an unpinned image tag,
the evaluator intentionally adds a process nonce to the fingerprint and only
deduplicates work inside that invocation.

---

## Signal Operator Quick Reference

All operators work between `Signal ↔ Signal`, `Signal ↔ BitVec`, and `BitVec ↔ Signal` (both directions):

| Operation | Signal ↔ Signal | Signal ↔ Constant | Constant ↔ Signal | Example |
|-----------|:-:|:-:|:-:|---------|
| Add | `a + b` | `a + 1#8` | `1#8 + a` | `count + 1#8` |
| Sub | `a - b` | `a - 1#8` | `64#7 - a` | `timer - 1#32` |
| Mul | `a * b` | `a * 4#8` | `3#32 * a` | `idx * 4#4` |
| AND | `a &&& b` | `a &&& 0xFF#8` | `0xFF#8 &&& a` | `data &&& mask` |
| OR | `a \|\|\| b` | `a \|\|\| 0x80#8` | `0x80#8 \|\|\| a` | `flags \|\|\| bit` |
| XOR | `a ^^^ b` | `a ^^^ 0xFF#8` | `0xFF#8 ^^^ a` | `data ^^^ key` |
| NOT | `~~~a` | — | — | `~~~enable` |
| Shift L | `a <<< b` | `a <<< 2#8` | `1#64 <<< a` | `data <<< shift` |
| Shift R | `a >>> b` | `a >>> 2#8` | `0xFF#8 >>> a` | `data >>> shift` |
| Concat | `a ++ b` | `a ++ 0#2` | `0#24 ++ a` | `sign ++ data` |
| Equal | `a === b` | `a === 0#8` | — | `state === IDLE` |
| Neg | `-a` | — | — | `-signed_val` |
| Signed < | `Signal.slt a b` | — | — | `Signal.slt x y` |
| Unsigned < | `Signal.ult a b` | — | — | `Signal.ult x y` |
| Arith shift | `Signal.ashr a b` | — | — | `Signal.ashr x shift` |

**Old style (still works but verbose):**
```lean
(· + ·) <$> a <*> b       -- → a + b
(· + ·) <$> a <*> Signal.pure 1#8  -- → a + 1#8
```

---

---

## Pattern Matching on Tuples

**unbundle2 and pattern matching DO NOT WORK in synthesis:**

```lean
-- ❌ WRONG: This will fail with "Unbound variable" errors
def example_WRONG (input : Signal Domain (BitVec 8 × BitVec 8)) : Signal Domain (BitVec 8) :=
  let (a, b) := unbundle2 input  -- ❌ FAILS!
  (· + ·) <$> a <*> b

-- ✓ RIGHT: Use .fst and .snd projection methods
def example_RIGHT (input : Signal Domain (BitVec 8 × BitVec 8)) : Signal Domain (BitVec 8) :=
  let a := input.fst  -- ✓ Works!
  let b := input.snd  -- ✓ Works!
  (· + ·) <$> a <*> b
```

**Why this happens:**
- `unbundle2` returns a Lean-level tuple `(Signal α × Signal β)`
- Lean compiles pattern matches into intermediate forms during elaboration
- By the time synthesis runs, these patterns are compiled away
- The synthesis compiler cannot track the destructured variables

**Solution:** Use projection methods instead:
- For 2-tuples: `.fst` and `.snd`
- For 3-tuples: `.proj3_1`, `.proj3_2`, `.proj3_3`
- For 4-tuples: `.proj4_1`, `.proj4_2`, `.proj4_3`, `.proj4_4`
- For 5-8 tuples: `unbundle5` through `unbundle8` (but access via tuple projections, not pattern matching)

See [Tests/TestUnbundle2.lean](../Tests/TestUnbundle2.lean) for detailed examples.

---

## If-Then-Else in Signal Contexts

**Standard if-then-else gets compiled to match expressions and doesn't work:**

```lean
-- ❌ WRONG: if-then-else in Signal contexts
def example_WRONG (cond : Bool) (a b : Signal Domain (BitVec 8)) : Signal Domain (BitVec 8) :=
  if cond then a else b  -- ❌ Error: Cannot instantiate Decidable.rec

-- ✓ RIGHT: Use Signal.mux instead
def example_RIGHT (cond : Signal Domain Bool) (a b : Signal Domain (BitVec 8)) : Signal Domain (BitVec 8) :=
  Signal.mux cond a b  -- ✓ Works!
```

**Why this happens:**
- Lean compiles `if-then-else` into `ite` which becomes `Decidable.rec`
- The synthesis compiler cannot handle general recursors
- This is a fundamental limitation of how conditionals are compiled

**Solution:** Always use `Signal.mux` for hardware multiplexers, which generates proper Verilog.

---

## Feedback Loops (Circular Dependencies)

**Simple feedback with `let rec` works:**

```lean
-- ✓ RIGHT: Simple counter with let rec
def counter {dom : DomainConfig} : Signal dom (BitVec 8) :=
  let rec count := Signal.register 0#8 (count.map (· + 1))
  count

#synthesizeVerilog counter  -- ✓ Works!
```

**Complex feedback with multiple signals — use `Signal.loop`:**

```lean
-- ❌ WRONG: Multiple interdependent signals
def stateMachine : Signal Domain State :=
  let next := computeNext state input
  let state := Signal.register Idle next  -- ❌ Forward reference
  state

-- ✓ RIGHT: Use Signal.loop for multi-register state machines
def stateMachine (input : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.loop fun state =>
    -- state is the previous cycle's output (right-nested tuple)
    let next := computeNext state input
    next
-- See Examples/RV32/SoC.lean for a 117-register Signal.loop example
```

**Why this limitation exists:**
- Lean evaluates let-bindings sequentially (no forward references)
- `let rec` works for single self-referential definitions
- Multiple circular bindings use `Signal.loop` which provides the previous state

**Workarounds:**
- **Simple loops**: Use `let rec` (counters, single-register state)
- **Complex feedback**: Use `Signal.loop` for multi-register state machines
- See `Examples/LoopSynthesis.lean` and `Examples/RV32/SoC.lean` for working patterns

---

## Signal.loop vs Signal.loopMemo

**`Signal.loop`** uses recursive stream evaluation. For simulations beyond ~10,000 cycles,
this causes stack overflow because Lean builds a chain of thunks proportional to the cycle count.

**`Signal.loopMemo`** caches the loop output per timestep using C FFI barriers, giving O(1) lookups.
This is required for state machines that run for thousands of cycles (e.g., the RV32I SoC runs
millions of cycles for Linux boot).

**Rule of thumb:**
- Use `Signal.loop` for synthesis (Verilog generation) and short simulations (<1000 cycles)
- Use `Signal.loopMemo` for long simulations (>1000 cycles)
- Both produce identical results; `loopMemo` just avoids stack overflow

**Pattern:**

```lean
-- For synthesis: use Signal.loop
def myCircuit (input : Signal dom (BitVec 32)) : Signal dom (BitVec 32) :=
  Signal.loop fun state =>
    let next := computeNext state input
    next

-- For long simulation: use Signal.loopMemo
def mySimulate (input : Signal dom (BitVec 32)) : IO (Signal dom (BitVec 32)) := do
  let s ← Signal.loopMemo fun state =>
    let next := computeNext state input
    next
  return s
```

**Body extraction pattern** (share the loop body between both):

```lean
-- 1. Define the loop body as a standalone function
private def myBody (input : Signal dom (BitVec 32))
    (state : Signal dom MyState) : Signal dom MyState :=
  let prev := Signal.register initState state
  -- ... compute next state ...
  next

-- 2. Use Signal.loop for synthesis
def myCircuit (input : Signal dom (BitVec 32)) :=
  Signal.loop fun state => myBody input state

-- 3. Use Signal.loopMemo for simulation
def mySimulate (input : Signal dom (BitVec 32)) : IO ... := do
  let s ← Signal.loopMemo (myBody input)
  return ...
```

---

## What's Supported

**Fully supported in synthesis:**
- Basic arithmetic: `+`, `-`, `*`, `&&&`, `|||`, `^^^`
- Comparisons: `==`, `!=`, `<`, `<=`, `>`, `>=`
- Bitwise operations: shifts, rotations
- Signal operations: `map`, `pure`, `<*>` (applicative)
- Registers: `Signal.register`
- Mux: `Signal.mux`
- Tuples: `bundle2`/`bundle3` and `.fst`/`.snd`/`.proj*` projections
- **Arrays/Vectors**: `HWVector α n` with `.get` indexing
- **Memory primitives**: `Signal.memory` for SRAM/BRAM with synchronous read/write
- **Correct overflow**: All bit widths preserve wrap-around semantics
- Hierarchical modules: function calls generate module instantiations
- **Co-simulation**: Verilator integration for validation

**Current Limitations:**
- **No `<~` feedback operator** - Use `let rec` or `Signal.loop`
- **No imperative do-notation** - Use dataflow style with applicative operators
- **No runtime constants** - Arrays, single BitVec values can't be synthesized
- Pattern matching on Signal tuples (use `.fst`/`.snd` instead)
- Recursive let-bindings for complex feedback (use manual IR construction)
- Higher-order functions beyond `map`, `<*>`, and basic combinators
- General match expressions on Signals
- Array writes (only indexing reads supported currently)

---

## Synthesis Compiler Patterns

The `#synthesizeVerilog` compiler can only handle specific Lean expression patterns:

### Supported patterns
- **Binary ops**: `(· op ·) <$> a <*> b` — all primitives in registry + `(· ++ ·)` for concat
- **Unary map**: `(fun x => !x) <$> a`, `.map (BitVec.extractLsb' n m ·)`, `(fun x => ~~~ x) <$> a`
- **Mux**: `Signal.mux cond trueVal falseVal` (NOT if-then-else)
- **Constants**: `Signal.pure val`
- **Register**: `Signal.register initVal input`
- **Memory**: `Signal.memory writeAddr writeData writeEnable readAddr`
- **MemoryComboRead**: `Signal.memoryComboRead writeAddr writeData writeEnable readAddr`
- **Loop**: `Signal.loop fun state => ...` (nested loops become sub-modules)
- **Tuple ops**: `projN! state n i`, `bundleAll! [...]`

### NOT supported (causes "Unbound variable" errors)
- Multi-arg lambdas: `(fun a b => ...) <$> x <*> y` — only `(· op ·)` binary syntax works
- Complex single-arg lambdas: `(fun x => !(x == 0#5))` — must split into two steps
- if-then-else in map: `(fun x => if x then a else b)` — use `Signal.mux` instead
- Multi-step concat in lambda: `(fun v => (0#20 ++ v ++ 0#2))` — break into chained `(· ++ ·)`

### Fix patterns

```lean
-- BAD:  (fun x => !(x == 0#5)) <$> sig
-- GOOD: let isZero := (· == ·) <$> sig <*> Signal.pure 0#5
--       let result := (fun x => !x) <$> isZero

-- BAD:  (fun x => if x then 1#32 else 0#32) <$> sig
-- GOOD: Signal.mux sig (Signal.pure 1#32) (Signal.pure 0#32)

-- BAD:  (fun d => (0#24 ++ d : BitVec 32)) <$> sig
-- GOOD: (· ++ ·) <$> Signal.pure 0#24 <*> sig

-- BAD:  (fun v => (0#20 ++ v ++ 0#2 : BitVec 32)) <$> sig
-- GOOD: let step1 := (· ++ ·) <$> sig <*> Signal.pure 0#2
--       let step2 := (· ++ ·) <$> Signal.pure 0#20 <*> step1
```
