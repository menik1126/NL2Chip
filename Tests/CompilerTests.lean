/-
  Compiler Improvement Tests

  ① ~~~sig (bitwise complement) for Signal dom (BitVec n)
  ② Complex lambda with constants: (fun d => (0#24 ++ d)) <$> sig
  ③ hw_let tuple destructuring macro
-/

import Sparkle
import Sparkle.Compiler.Elab

set_option maxRecDepth 4096
set_option maxHeartbeats 800000

namespace Tests.CompilerTests

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Core.StateMacro

-- ============================================================================
-- Test ①: Bitwise complement ~~~ for BitVec signals
-- ============================================================================

/-- ~~~sig should synthesize for BitVec 8 signals -/
def testComplement8 {dom : DomainConfig}
    (a : Signal dom (BitVec 8))
    : Signal dom (BitVec 8) :=
  ~~~a

#synthesizeVerilog testComplement8

/-- ~~~sig should synthesize for BitVec 32 signals -/
def testComplement32 {dom : DomainConfig}
    (a : Signal dom (BitVec 32))
    : Signal dom (BitVec 32) :=
  ~~~a

#synthesizeVerilog testComplement32

-- ============================================================================
-- Test ②: Complex lambda with constants
-- ============================================================================

/-- Zero-extend 8-bit to 32-bit via lambda with constant concat -/
def testLambdaConcat {dom : DomainConfig}
    (sig : Signal dom (BitVec 8))
    : Signal dom (BitVec 32) :=
  (fun d => (0#24 ++ d : BitVec 32)) <$> sig

#synthesizeVerilog testLambdaConcat

/-- Add constant in lambda -/
def testLambdaAddConst {dom : DomainConfig}
    (sig : Signal dom (BitVec 8))
    : Signal dom (BitVec 8) :=
  (fun x => x + 1#8) <$> sig

#synthesizeVerilog testLambdaAddConst

-- ============================================================================
-- Test ③: hw_let tuple destructuring
-- ============================================================================

/-- hw_let with 2-tuple -/
def testHwLet2 {dom : DomainConfig}
    (sig : Signal dom (BitVec 8 × BitVec 16))
    : Signal dom (BitVec 8) :=
  hw_let (a, _b) := sig;
  a

#synthesizeVerilog testHwLet2

/-- hw_let with 3-tuple -/
def testHwLet3 {dom : DomainConfig}
    (sig : Signal dom (BitVec 8 × (BitVec 16 × BitVec 32)))
    : Signal dom (BitVec 16) :=
  hw_let (_a, b, _c) := sig;
  b

#synthesizeVerilog testHwLet3

-- ============================================================================
-- Test ④: Native symbolic widths and concrete specialization
-- ============================================================================

/-- A generic helper can be emitted as one native SystemVerilog parameterized module. -/
def testGenericWidth {dom : DomainConfig} {width : Nat}
    (sig : Signal dom (BitVec width)) : Signal dom (BitVec width) :=
  sig

/-- Concrete wrappers are the supported way to synthesize a generic helper. -/
def testGenericWidth4 {dom : DomainConfig}
    (sig : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  testGenericWidth sig

def testGenericWidth16 {dom : DomainConfig}
    (sig : Signal dom (BitVec 16)) : Signal dom (BitVec 16) :=
  testGenericWidth sig

#synthesizeVerilog testGenericWidth4
#synthesizeVerilog testGenericWidth16

#synthesizeVerilog testGenericWidth parameters [width := 8]

/--
error: Unresolved BitVec width width is not a declared module parameter.

Give the top-level Nat binder a SystemVerilog default in the synthesis command, for example: #synthesizeVerilog circuit parameters [W := 8]. Parameter defaults may be zero, but every derived hardware width and array length must be positive.
-/
#guard_msgs in
#synthesizeVerilog testGenericWidth

def testGenericWidthExpr {dom : DomainConfig} {width : Nat}
    (sig : Signal dom (BitVec (width + 1))) : Signal dom (BitVec (width + 1)) :=
  sig

#synthesizeVerilog testGenericWidthExpr parameters [width := 8]

/--
error: Unresolved BitVec width width is not a declared module parameter.

Give the top-level Nat binder a SystemVerilog default in the synthesis command, for example: #synthesizeVerilog circuit parameters [W := 8]. Parameter defaults may be zero, but every derived hardware width and array length must be positive.
-/
#guard_msgs in
#synthesizeVerilog testGenericWidthExpr

def testGenericVector {dom : DomainConfig} {size : Nat}
    (sig : Signal dom (Sparkle.Core.Vector.HWVector (BitVec 8) size))
    : Signal dom (Sparkle.Core.Vector.HWVector (BitVec 8) size) :=
  sig

#synthesizeVerilog testGenericVector parameters [size := 4]

/--
error: Unresolved HWVector size size is not a declared module parameter.

Give the top-level Nat binder a SystemVerilog default in the synthesis command, for example: #synthesizeVerilog circuit parameters [W := 8]. Parameter defaults may be zero, but every derived hardware width and array length must be positive.
-/
#guard_msgs in
#synthesizeVerilog testGenericVector

def testGenericLiteral {width : Nat} : Signal Domain (BitVec width) :=
  Signal.pure (BitVec.ofNat width 0)

#synthesizeVerilog testGenericLiteral parameters [width := 8]

/--
error: Unresolved BitVec parameter constant width width is not a declared module parameter.

Give the top-level Nat binder a SystemVerilog default in the synthesis command, for example: #synthesizeVerilog circuit parameters [W := 8]. Parameter defaults may be zero, but every derived hardware width and array length must be positive.
-/
#guard_msgs in
#synthesizeVerilog testGenericLiteral

/-- Lean optional Nat binders can supply native SystemVerilog defaults. -/
def testDeclaredWidthDefault (width : Nat := 8)
    (sig : Signal Domain (BitVec width)) : Signal Domain (BitVec width) :=
  sig

#synthesizeVerilog testDeclaredWidthDefault

/-- Dependent optional defaults cannot be emitted as independent numeric SV
    defaults without making later overrides semantically stale. -/
def testDependentWidthDefault (width : Nat := 8) (derived : Nat := width + 1)
    (sig : Signal Domain (BitVec derived)) : Signal Domain (BitVec derived) :=
  sig

/--
error: Dependent default for Nat parameter 'derived' is not supported by native SystemVerilog parameter emission.

Express derived hardware dimensions directly from earlier parameters (for example, `BitVec (W + 1)`), or supply an independent concrete default. Sparkle refuses to freeze a dependent default at one elaboration value because a later parameter override would change its semantics.
-/
#guard_msgs in
#synthesizeVerilog testDependentWidthDefault

/-- Zero is legal for a non-size parameter such as a slice offset. -/
def testGenericLowSlice (width : Nat := 8) (start : Nat := 0) (len : Nat := 4)
    (sig : Signal Domain (BitVec width)) : Signal Domain (BitVec len) :=
  sig.map (BitVec.extractLsb' start len)

#synthesizeVerilog testGenericLowSlice
#synthesizeVerilogDesign testGenericLowSlice parameters [width := 16, start := 0, len := 8]

def testDerivedZeroDefault (width : Nat := 1)
    (sig : Signal Domain (BitVec (width - 1)))
    : Signal Domain (BitVec (width - 1)) :=
  sig

/--
error: module 'Tests.CompilerTests.testDerivedZeroDefault' port/wire '_gen_sig' evaluates to zero under the module's default parameters
-/
#guard_msgs in
#synthesizeVerilog testDerivedZeroDefault

def testSanitizedParameterCollision {W' W_prime : Nat}
    (sig : Signal Domain (BitVec (W' + W_prime)))
    : Signal Domain (BitVec (W' + W_prime)) :=
  sig

/--
error: SystemVerilog parameter names 'W'' and 'W_prime' both emit as 'W_prime' in module 'Tests.CompilerTests.testSanitizedParameterCollision'. Rename one binder.
-/
#guard_msgs in
#synthesizeVerilog testSanitizedParameterCollision parameters [W' := 4, W_prime := 4]

/-- A generic child used twice must remain one symbolic module; each instance
    carries its own parameter override. -/
@[irreducible] def testGenericChild {width : Nat}
    (sig : Signal Domain (BitVec width)) : Signal Domain (BitVec width) :=
  sig.map (· + 1)

def testGenericChildTwice (width : Nat := 8)
    (a : Signal Domain (BitVec width))
    (b : Signal Domain (BitVec (width + 1)))
    : Signal Domain (BitVec width × BitVec (width + 1)) :=
  bundle2 (testGenericChild a) (testGenericChild b)

#synthesizeVerilogDesign testGenericChildTwice

namespace SanitizedModuleCollision

namespace A
@[irreducible] def B {width : Nat}
    (sig : Signal Domain (BitVec width)) : Signal Domain (BitVec width) :=
  sig.map (· + 1)
end A

@[irreducible] def A_B {width : Nat}
    (sig : Signal Domain (BitVec width)) : Signal Domain (BitVec width) :=
  sig.map (· + 2)

def top (width : Nat := 8) (sig : Signal Domain (BitVec width)) :=
  bundle2 (A.B sig) (A_B sig)

/--
error: Module names 'Tests.CompilerTests.SanitizedModuleCollision.A.B' and 'Tests.CompilerTests.SanitizedModuleCollision.A_B' both emit as 'Tests_CompilerTests_SanitizedModuleCollision_A_B' in SystemVerilog. Rename one definition or namespace.
-/
#guard_msgs in
#synthesizeVerilogDesign top

end SanitizedModuleCollision

-- ============================================================================
-- Test ⑤: Zero hardware dimensions are rejected without rejecting zero values
-- ============================================================================

def testZeroWidth {dom : DomainConfig}
    (sig : Signal dom (BitVec 0)) : Signal dom (BitVec 0) :=
  sig

/--
error: Cannot synthesize hardware with zero BitVec width.

Packed hardware widths and array lengths must be positive.
-/
#guard_msgs in
#synthesizeVerilog testZeroWidth

def testZeroLengthVector {dom : DomainConfig}
    (sig : Signal dom (Sparkle.Core.Vector.HWVector (BitVec 8) 0))
    : Signal dom (Sparkle.Core.Vector.HWVector (BitVec 8) 0) :=
  sig

/--
error: Cannot synthesize hardware with zero HWVector size.

Packed hardware widths and array lengths must be positive.
-/
#guard_msgs in
#synthesizeVerilog testZeroLengthVector

def testZeroWidthConstant : Signal Domain (BitVec 0) :=
  Signal.pure (BitVec.ofNat 0 0)

/--
error: Cannot synthesize hardware with zero BitVec parameter constant width.

Packed hardware widths and array lengths must be positive.
-/
#guard_msgs in
#synthesizeVerilog testZeroWidthConstant

/-- A zero data value remains legal; only hardware dimensions must be positive. -/
def testZeroValue : Signal Domain (BitVec 8) :=
  Signal.pure (BitVec.ofNat 8 0)

#synthesizeVerilog testZeroValue

end Tests.CompilerTests
