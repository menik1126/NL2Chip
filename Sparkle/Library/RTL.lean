/-
  Small RTL-oriented helper layer for Sparkle.

  These definitions intentionally wrap existing Signal/BitVec primitives rather
  than changing compiler lowering. They give LLM-generated designs stable names
  for common RTL idioms: bit slicing, flag conversion, register enables, and
  simple 1R1W memories/register files.
-/

import Sparkle.Core.Domain
import Sparkle.Core.Signal

open Sparkle.Core.Domain
open Sparkle.Core.Signal

namespace Sparkle.Library.RTL

variable {dom : DomainConfig}

/-- Convert a hardware Bool signal to a 1-bit BitVec signal. -/
def boolToBV1 (b : Signal dom Bool) : Signal dom (BitVec 1) :=
  Signal.mux b (Signal.pure 1#1) (Signal.pure 0#1)

/-- Interpret a 1-bit BitVec signal as a hardware Bool signal. -/
def bv1ToBool (x : Signal dom (BitVec 1)) : Signal dom Bool :=
  x === (Signal.pure 1#1)

/-- Constant zero with width inferred from the result type. -/
def zeroBV (w : Nat) : BitVec w :=
  BitVec.ofNat w 0
/-- Type-level ceiling log2 for parameter-derived hardware dimensions. -/
def clog2 (value : Nat) : Nat :=
  if value <= 1 then 0 else Nat.log2 (value - 1) + 1


/-- Constant all-ones mask with width inferred from the result type. -/
def onesBV (w : Nat) : BitVec w :=
  BitVec.ofNat w (2 ^ w - 1)

/-- Hardware zero comparison. Useful for reduction-OR style checks. -/
def isZero {w : Nat} (x : Signal dom (BitVec w)) : Signal dom Bool :=
  x === (Signal.pure (zeroBV w))

/-- Hardware nonzero comparison. -/
def nonZero {w : Nat} (x : Signal dom (BitVec w)) : Signal dom Bool :=
  (fun b => !b) <$> isZero x

/-- Hardware all-ones comparison. Useful for reduction-AND style checks. -/
def allOnes {w : Nat} (x : Signal dom (BitVec w)) : Signal dom Bool :=
  -- Modular increment wraps exactly the all-ones vector to zero.  This form
  -- preserves a symbolic `w`; materializing `2 ^ w - 1` as a host value does
  -- not lower reliably through the native parameter backend.
  isZero (x + BitVec.ofNat w 1)

/-- Extract one bit as a 1-bit BitVec signal. `i = 0` is the LSB. -/
def bit {w : Nat} (x : Signal dom (BitVec w)) (i : Nat) : Signal dom (BitVec 1) :=
  Signal.map (fun v => BitVec.extractLsb' i 1 v) x

/-- Extract one bit as a Bool signal. `i = 0` is the LSB. -/
def bitBool {w : Nat} (x : Signal dom (BitVec w)) (i : Nat) : Signal dom Bool :=
  bv1ToBool (bit x i)

/-- Extract `len` bits starting at bit `lo` as a BitVec signal. -/
def slice {w len : Nat} (x : Signal dom (BitVec w)) (lo : Nat) : Signal dom (BitVec len) :=
  Signal.map (fun v => BitVec.extractLsb' lo len v) x

/-- Keep the low `outW` bits of a BitVec signal. -/
def trunc {w outW : Nat} (x : Signal dom (BitVec w)) : Signal dom (BitVec outW) :=
  slice x 0

/-- Zero-extend or truncate a BitVec signal to `outW` bits. -/
def zext {w outW : Nat} (x : Signal dom (BitVec w)) : Signal dom (BitVec outW) :=
  Signal.map (fun v => v.zeroExtend outW) x

/-- Sign-extend or truncate a two's-complement packed vector. Prefer this to
    zext whenever the vector represents a signed quantity. -/
def signExtend {w outW : Nat}
    (x : Signal dom (BitVec w)) : Signal dom (BitVec outW) :=
  Signal.signExtend x

/-- Arithmetic right shift for a two's-complement packed vector. -/
def arithShiftRight {w : Nat}
    (x amount : Signal dom (BitVec w)) : Signal dom (BitVec w) :=
  Signal.ashr x amount

/-- Arithmetic right shift by a concrete, exact-width packed amount. -/
def arithShiftRightC {w : Nat}
    (x : Signal dom (BitVec w)) (amount : BitVec w) : Signal dom (BitVec w) :=
  Signal.ashr x (Signal.pure amount)

/-- Signed two's-complement comparisons. -/
def signedLT {w : Nat}
    (lhs rhs : Signal dom (BitVec w)) : Signal dom Bool :=
  Signal.slt lhs rhs

def signedLE {w : Nat}
    (lhs rhs : Signal dom (BitVec w)) : Signal dom Bool :=
  Signal.sle lhs rhs

def signedGT {w : Nat}
    (lhs rhs : Signal dom (BitVec w)) : Signal dom Bool :=
  Signal.sgt lhs rhs

def signedGE {w : Nat}
    (lhs rhs : Signal dom (BitVec w)) : Signal dom Bool :=
  Signal.sge lhs rhs

/-- Signed addition with a caller-selected result width. Both operands are
    converted to `outW` before the modular addition, avoiding proof obligations
    between propositionally equal symbolic widths such as `W + W` and `2 * W`.
    Choose `outW` large enough when mathematical overflow must be preserved. -/
def signedAddTo {lhsW rhsW outW : Nat}
    (lhs : Signal dom (BitVec lhsW))
    (rhs : Signal dom (BitVec rhsW)) : Signal dom (BitVec outW) :=
  let lhsOut : Signal dom (BitVec outW) := signExtend lhs
  let rhsOut : Signal dom (BitVec outW) := signExtend rhs
  lhsOut + rhsOut

/-- Signed subtraction with a caller-selected result width. -/
def signedSubTo {lhsW rhsW outW : Nat}
    (lhs : Signal dom (BitVec lhsW))
    (rhs : Signal dom (BitVec rhsW)) : Signal dom (BitVec outW) :=
  let lhsOut : Signal dom (BitVec outW) := signExtend lhs
  let rhsOut : Signal dom (BitVec outW) := signExtend rhs
  lhsOut - rhsOut

/-- Signed multiplication with a caller-selected result width. The result is
    the low `outW` bits of the exact two's-complement product. -/
def signedMulTo {lhsW rhsW outW : Nat}
    (lhs : Signal dom (BitVec lhsW))
    (rhs : Signal dom (BitVec rhsW)) : Signal dom (BitVec outW) :=
  let lhsOut : Signal dom (BitVec outW) := signExtend lhs
  let rhsOut : Signal dom (BitVec outW) := signExtend rhs
  lhsOut * rhsOut

/-- Dot product of two packed signed lane vectors. Lane zero occupies the
    least-significant chunk. The final sum wraps at the caller-selected `accW`.
    Specify all symbolic lane dimensions explicitly at call sites when Lean
    cannot infer a factorization of the packed widths. -/
def signedDotPacked {lhsW rhsW accW lanes : Nat}
    (lhs : Signal dom (BitVec (lanes * lhsW)))
    (rhs : Signal dom (BitVec (lanes * rhsW))) : Signal dom (BitVec accW) :=
  Signal.signedDotChunks lhs rhs

/-- Unsigned division with explicit zero-denominator behavior. -/
def unsignedDivOr {w : Nat}
    (numerator denominator fallback : Signal dom (BitVec w))
    : Signal dom (BitVec w) :=
  Signal.mux (isZero denominator) fallback (Signal.udiv numerator denominator)

/-- Signed division with truncation toward zero and explicit zero-denominator
    behavior. -/
def signedDivOr {w : Nat}
    (numerator denominator fallback : Signal dom (BitVec w))
    : Signal dom (BitVec w) :=
  Signal.mux (isZero denominator) fallback (Signal.sdiv numerator denominator)

/-- Unsigned division with caller-selected work and result widths. -/
def unsignedDivOrTo {numW denW workW outW : Nat}
    (numerator : Signal dom (BitVec numW))
    (denominator : Signal dom (BitVec denW))
    (fallback : Signal dom (BitVec outW)) : Signal dom (BitVec outW) :=
  let numeratorWork : Signal dom (BitVec workW) := zext numerator
  let denominatorWork : Signal dom (BitVec workW) := zext denominator
  let fallbackWork : Signal dom (BitVec workW) := zext fallback
  zext (unsignedDivOr numeratorWork denominatorWork fallbackWork)

/-- Signed division with caller-selected work and result widths. The quotient
    truncates toward zero, matching SystemVerilog signed division. -/
def signedDivOrTo {numW denW workW outW : Nat}
    (numerator : Signal dom (BitVec numW))
    (denominator : Signal dom (BitVec denW))
    (fallback : Signal dom (BitVec outW)) : Signal dom (BitVec outW) :=
  let numeratorWork : Signal dom (BitVec workW) := signExtend numerator
  let denominatorWork : Signal dom (BitVec workW) := signExtend denominator
  let fallbackWork : Signal dom (BitVec workW) := signExtend fallback
  signExtend (signedDivOr numeratorWork denominatorWork fallbackWork)

/-- Absolute value of a two's-complement input, represented at an explicit
    output width. The most-negative value still wraps when `outW` is not wide
    enough to represent its positive magnitude. -/
def signedAbsTo {inW outW : Nat}
    (value : Signal dom (BitVec inW)) : Signal dom (BitVec outW) :=
  let widened : Signal dom (BitVec outW) := signExtend value
  let zero : Signal dom (BitVec outW) := Signal.pure (zeroBV outW)
  Signal.mux (signedLT widened zero) (zero - widened) widened

/-- Mean of two signed values using SystemVerilog-style division semantics:
    the sum is formed at `workW`, divided by two, and rounded toward zero. -/
def signedMeanTowardZeroTo {lhsW rhsW workW outW : Nat}
    (lhs : Signal dom (BitVec lhsW))
    (rhs : Signal dom (BitVec rhsW)) : Signal dom (BitVec outW) :=
  let sum : Signal dom (BitVec workW) := signedAddTo lhs rhs
  let two : Signal dom (BitVec workW) := Signal.pure (BitVec.ofNat workW 2)
  let zero : Signal dom (BitVec workW) := Signal.pure (zeroBV workW)
  signExtend (signedDivOr sum two zero)

/-- Mean of two signed values rounded toward negative infinity. This is the
    arithmetic-shift interpretation and differs from signed division for a
    negative odd sum. -/
def signedMeanFloorTo {lhsW rhsW workW outW : Nat}
    (lhs : Signal dom (BitVec lhsW))
    (rhs : Signal dom (BitVec rhsW)) : Signal dom (BitVec outW) :=
  let sum : Signal dom (BitVec workW) := signedAddTo lhs rhs
  signExtend (arithShiftRightC sum (BitVec.ofNat workW 1))

/-- Divide a signed value by `2^amount`, rounding toward zero. This is useful
    for fixed-point rescaling when arithmetic right shift's floor behavior is
    not the desired contract. -/
def signedDivPow2TowardZeroTo {inW workW outW : Nat}
    (value : Signal dom (BitVec inW))
    (amount : Signal dom (BitVec workW)) : Signal dom (BitVec outW) :=
  let valueWork : Signal dom (BitVec workW) := signExtend value
  let one : Signal dom (BitVec workW) := Signal.pure (BitVec.ofNat workW 1)
  let denominator := one <<< amount
  let zero : Signal dom (BitVec workW) := Signal.pure (zeroBV workW)
  signExtend (signedDivOr valueWork denominator zero)

/-- Full-width signed product of two W-bit two's-complement values. -/
def signedMulWide {w : Nat}
    (lhs rhs : Signal dom (BitVec w)) : Signal dom (BitVec (w + w)) :=
  let lhsExt : Signal dom (BitVec (w + w)) := signExtend lhs
  let rhsExt : Signal dom (BitVec (w + w)) := signExtend rhs
  lhsExt * rhsExt

/-- Signed product retaining the low OUTW bits. -/
def signedMulTrunc {w outW : Nat}
    (lhs rhs : Signal dom (BitVec w)) : Signal dom (BitVec outW) :=
  trunc (signedMulWide lhs rhs)

/-- Fixed-point signed product: widen, arithmetic-shift, then retain low OUTW
    bits. The shift amount must use the widened product width. -/
def signedMulShiftTrunc {w outW : Nat}
    (lhs rhs : Signal dom (BitVec w))
    (amount : Signal dom (BitVec (w + w))) : Signal dom (BitVec outW) :=
  trunc (arithShiftRight (signedMulWide lhs rhs) amount)

/-- Clamp a two's-complement value to explicit signed lower and upper bounds.
    Callers must supply bounds such that lower <= upper in signed order. -/
def signedSaturate {w : Nat}
    (value lower upper : Signal dom (BitVec w)) : Signal dom (BitVec w) :=
  Signal.mux (signedLT value lower) lower
    (Signal.mux (signedGT value upper) upper value)

/-- Constant-bound signed saturation. -/
def signedSaturateC {w : Nat}
    (value : Signal dom (BitVec w)) (lower upper : BitVec w)
    : Signal dom (BitVec w) :=
  signedSaturate value (Signal.pure lower) (Signal.pure upper)

/-- D flip-flop alias. Prefer this when mirroring Verilog `q <= d`. -/
def dff {α : Type} (init : α) (d : Signal dom α) : Signal dom α :=
  Signal.register init d

/-- D flip-flop with enable; holds the previous value when `en` is false. -/
def dffe {α : Type} (init : α) (en : Signal dom Bool) (d : Signal dom α) : Signal dom α :=
  Signal.registerWithEnable init en d

/-- Active-high reset mux for next-state logic before a register. -/
def resetHigh {α : Type} (init : α) (rst : Signal dom Bool) (next : Signal dom α) : Signal dom α :=
  Signal.mux rst (Signal.pure init) next

/-- Active-low reset mux for next-state logic before a register. -/
def resetLow {α : Type} (init : α) (rstN : Signal dom Bool) (next : Signal dom α) : Signal dom α :=
  Signal.mux rstN next (Signal.pure init)

/-- One synchronous 1-read/1-write RAM. Read data has one-cycle latency. -/
def syncRam1R1W {addrWidth dataWidth : Nat}
    (writeAddr : Signal dom (BitVec addrWidth))
    (writeData : Signal dom (BitVec dataWidth))
    (writeEnable : Signal dom Bool)
    (readAddr : Signal dom (BitVec addrWidth))
    : Signal dom (BitVec dataWidth) :=
  Signal.memory writeAddr writeData writeEnable readAddr

/--
  One 1-read/1-write combinational-read register file style memory.
  Reads see writes from previous cycles, not the write currently being issued.
-/
def regFile1R1W {addrWidth dataWidth : Nat}
    (writeAddr : Signal dom (BitVec addrWidth))
    (writeData : Signal dom (BitVec dataWidth))
    (writeEnable : Signal dom Bool)
    (readAddr : Signal dom (BitVec addrWidth))
    : Signal dom (BitVec dataWidth) :=
  Signal.memoryComboRead writeAddr writeData writeEnable readAddr

/-- Count asserted bits in a packed vector. The result width is preserved as
    clog2 (W + 1), so the Verilog backend can retain W as a parameter. -/
def popCount {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec (clog2 (W + 1))) :=
  Signal.map (fun value =>
    BitVec.ofNat (clog2 (W + 1)) <|
      (List.range W).foldl (fun count index =>
        count + if value.getLsbD index then 1 else 0) 0
  ) x

/-
  Fixed-width helpers below avoid generic `List.range` code in the synthesized
  expression. Current Sparkle lowering handles explicit bit-slice chains more
  reliably than higher-order folds.
-/

private def bitTo8 (x : Signal dom (BitVec 8)) (i : Nat) : Signal dom (BitVec 8) :=
  zext (bit x i)

private def bitTo16 (x : Signal dom (BitVec 16)) (i : Nat) : Signal dom (BitVec 16) :=
  zext (bit x i)

private def bitTo32 (x : Signal dom (BitVec 32)) (i : Nat) : Signal dom (BitVec 32) :=
  zext (bit x i)

def reverseBits8 (x : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  let b0 : Signal dom (BitVec 8) := bitTo8 x 0 <<< 7#8
  let b1 : Signal dom (BitVec 8) := bitTo8 x 1 <<< 6#8
  let b2 : Signal dom (BitVec 8) := bitTo8 x 2 <<< 5#8
  let b3 : Signal dom (BitVec 8) := bitTo8 x 3 <<< 4#8
  let b4 : Signal dom (BitVec 8) := bitTo8 x 4 <<< 3#8
  let b5 : Signal dom (BitVec 8) := bitTo8 x 5 <<< 2#8
  let b6 : Signal dom (BitVec 8) := bitTo8 x 6 <<< 1#8
  let b7 : Signal dom (BitVec 8) := bitTo8 x 7 <<< 0#8
  b0 ||| b1 ||| b2 ||| b3 ||| b4 ||| b5 ||| b6 ||| b7

def reverseBits16 (x : Signal dom (BitVec 16)) : Signal dom (BitVec 16) :=
  let b0 : Signal dom (BitVec 16) := bitTo16 x 0 <<< 15#16
  let b1 : Signal dom (BitVec 16) := bitTo16 x 1 <<< 14#16
  let b2 : Signal dom (BitVec 16) := bitTo16 x 2 <<< 13#16
  let b3 : Signal dom (BitVec 16) := bitTo16 x 3 <<< 12#16
  let b4 : Signal dom (BitVec 16) := bitTo16 x 4 <<< 11#16
  let b5 : Signal dom (BitVec 16) := bitTo16 x 5 <<< 10#16
  let b6 : Signal dom (BitVec 16) := bitTo16 x 6 <<< 9#16
  let b7 : Signal dom (BitVec 16) := bitTo16 x 7 <<< 8#16
  let b8 : Signal dom (BitVec 16) := bitTo16 x 8 <<< 7#16
  let b9 : Signal dom (BitVec 16) := bitTo16 x 9 <<< 6#16
  let b10 : Signal dom (BitVec 16) := bitTo16 x 10 <<< 5#16
  let b11 : Signal dom (BitVec 16) := bitTo16 x 11 <<< 4#16
  let b12 : Signal dom (BitVec 16) := bitTo16 x 12 <<< 3#16
  let b13 : Signal dom (BitVec 16) := bitTo16 x 13 <<< 2#16
  let b14 : Signal dom (BitVec 16) := bitTo16 x 14 <<< 1#16
  let b15 : Signal dom (BitVec 16) := bitTo16 x 15 <<< 0#16
  b0 ||| b1 ||| b2 ||| b3 ||| b4 ||| b5 ||| b6 ||| b7 |||
  b8 ||| b9 ||| b10 ||| b11 ||| b12 ||| b13 ||| b14 ||| b15

def reverseBits32 (x : Signal dom (BitVec 32)) : Signal dom (BitVec 32) :=
  let b0 : Signal dom (BitVec 32) := bitTo32 x 0 <<< 31#32
  let b1 : Signal dom (BitVec 32) := bitTo32 x 1 <<< 30#32
  let b2 : Signal dom (BitVec 32) := bitTo32 x 2 <<< 29#32
  let b3 : Signal dom (BitVec 32) := bitTo32 x 3 <<< 28#32
  let b4 : Signal dom (BitVec 32) := bitTo32 x 4 <<< 27#32
  let b5 : Signal dom (BitVec 32) := bitTo32 x 5 <<< 26#32
  let b6 : Signal dom (BitVec 32) := bitTo32 x 6 <<< 25#32
  let b7 : Signal dom (BitVec 32) := bitTo32 x 7 <<< 24#32
  let b8 : Signal dom (BitVec 32) := bitTo32 x 8 <<< 23#32
  let b9 : Signal dom (BitVec 32) := bitTo32 x 9 <<< 22#32
  let b10 : Signal dom (BitVec 32) := bitTo32 x 10 <<< 21#32
  let b11 : Signal dom (BitVec 32) := bitTo32 x 11 <<< 20#32
  let b12 : Signal dom (BitVec 32) := bitTo32 x 12 <<< 19#32
  let b13 : Signal dom (BitVec 32) := bitTo32 x 13 <<< 18#32
  let b14 : Signal dom (BitVec 32) := bitTo32 x 14 <<< 17#32
  let b15 : Signal dom (BitVec 32) := bitTo32 x 15 <<< 16#32
  let b16 : Signal dom (BitVec 32) := bitTo32 x 16 <<< 15#32
  let b17 : Signal dom (BitVec 32) := bitTo32 x 17 <<< 14#32
  let b18 : Signal dom (BitVec 32) := bitTo32 x 18 <<< 13#32
  let b19 : Signal dom (BitVec 32) := bitTo32 x 19 <<< 12#32
  let b20 : Signal dom (BitVec 32) := bitTo32 x 20 <<< 11#32
  let b21 : Signal dom (BitVec 32) := bitTo32 x 21 <<< 10#32
  let b22 : Signal dom (BitVec 32) := bitTo32 x 22 <<< 9#32
  let b23 : Signal dom (BitVec 32) := bitTo32 x 23 <<< 8#32
  let b24 : Signal dom (BitVec 32) := bitTo32 x 24 <<< 7#32
  let b25 : Signal dom (BitVec 32) := bitTo32 x 25 <<< 6#32
  let b26 : Signal dom (BitVec 32) := bitTo32 x 26 <<< 5#32
  let b27 : Signal dom (BitVec 32) := bitTo32 x 27 <<< 4#32
  let b28 : Signal dom (BitVec 32) := bitTo32 x 28 <<< 3#32
  let b29 : Signal dom (BitVec 32) := bitTo32 x 29 <<< 2#32
  let b30 : Signal dom (BitVec 32) := bitTo32 x 30 <<< 1#32
  let b31 : Signal dom (BitVec 32) := bitTo32 x 31 <<< 0#32
  b0 ||| b1 ||| b2 ||| b3 ||| b4 ||| b5 ||| b6 ||| b7 |||
  b8 ||| b9 ||| b10 ||| b11 ||| b12 ||| b13 ||| b14 ||| b15 |||
  b16 ||| b17 ||| b18 ||| b19 ||| b20 ||| b21 ||| b22 ||| b23 |||
  b24 ||| b25 ||| b26 ||| b27 ||| b28 ||| b29 ||| b30 ||| b31

private def popCountBV8 (x : BitVec 8) : BitVec 4 :=
  (BitVec.extractLsb' 0 1 x).zeroExtend 4 +
  (BitVec.extractLsb' 1 1 x).zeroExtend 4 +
  (BitVec.extractLsb' 2 1 x).zeroExtend 4 +
  (BitVec.extractLsb' 3 1 x).zeroExtend 4 +
  (BitVec.extractLsb' 4 1 x).zeroExtend 4 +
  (BitVec.extractLsb' 5 1 x).zeroExtend 4 +
  (BitVec.extractLsb' 6 1 x).zeroExtend 4 +
  (BitVec.extractLsb' 7 1 x).zeroExtend 4

private def popCountBV16 (x : BitVec 16) : BitVec 5 :=
  (BitVec.extractLsb' 0 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 1 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 2 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 3 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 4 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 5 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 6 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 7 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 8 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 9 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 10 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 11 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 12 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 13 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 14 1 x).zeroExtend 5 +
  (BitVec.extractLsb' 15 1 x).zeroExtend 5

private def popCountBV32 (x : BitVec 32) : BitVec 6 :=
  (BitVec.extractLsb' 0 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 1 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 2 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 3 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 4 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 5 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 6 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 7 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 8 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 9 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 10 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 11 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 12 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 13 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 14 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 15 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 16 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 17 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 18 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 19 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 20 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 21 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 22 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 23 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 24 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 25 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 26 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 27 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 28 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 29 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 30 1 x).zeroExtend 6 +
  (BitVec.extractLsb' 31 1 x).zeroExtend 6

def popCount8 (x : Signal dom (BitVec 8)) : Signal dom (BitVec 4) :=
  Signal.map popCountBV8 x

def popCount16 (x : Signal dom (BitVec 16)) : Signal dom (BitVec 5) :=
  Signal.map popCountBV16 x

def popCount32 (x : Signal dom (BitVec 32)) : Signal dom (BitVec 6) :=
  Signal.map popCountBV32 x

def priorityEncodeLsb8 (x : Signal dom (BitVec 8)) : Signal dom (BitVec 3) :=
  hw_cond (Signal.pure 0#3)
    | bitBool x 0 => Signal.pure 0#3
    | bitBool x 1 => Signal.pure 1#3
    | bitBool x 2 => Signal.pure 2#3
    | bitBool x 3 => Signal.pure 3#3
    | bitBool x 4 => Signal.pure 4#3
    | bitBool x 5 => Signal.pure 5#3
    | bitBool x 6 => Signal.pure 6#3
    | bitBool x 7 => Signal.pure 7#3

def priorityEncodeLsb16 (x : Signal dom (BitVec 16)) : Signal dom (BitVec 4) :=
  hw_cond (Signal.pure 0#4)
    | bitBool x 0 => Signal.pure 0#4
    | bitBool x 1 => Signal.pure 1#4
    | bitBool x 2 => Signal.pure 2#4
    | bitBool x 3 => Signal.pure 3#4
    | bitBool x 4 => Signal.pure 4#4
    | bitBool x 5 => Signal.pure 5#4
    | bitBool x 6 => Signal.pure 6#4
    | bitBool x 7 => Signal.pure 7#4
    | bitBool x 8 => Signal.pure 8#4
    | bitBool x 9 => Signal.pure 9#4
    | bitBool x 10 => Signal.pure 10#4
    | bitBool x 11 => Signal.pure 11#4
    | bitBool x 12 => Signal.pure 12#4
    | bitBool x 13 => Signal.pure 13#4
    | bitBool x 14 => Signal.pure 14#4
    | bitBool x 15 => Signal.pure 15#4

def priorityEncodeLsb32 (x : Signal dom (BitVec 32)) : Signal dom (BitVec 5) :=
  hw_cond (Signal.pure 0#5)
    | bitBool x 0 => Signal.pure 0#5
    | bitBool x 1 => Signal.pure 1#5
    | bitBool x 2 => Signal.pure 2#5
    | bitBool x 3 => Signal.pure 3#5
    | bitBool x 4 => Signal.pure 4#5
    | bitBool x 5 => Signal.pure 5#5
    | bitBool x 6 => Signal.pure 6#5
    | bitBool x 7 => Signal.pure 7#5
    | bitBool x 8 => Signal.pure 8#5
    | bitBool x 9 => Signal.pure 9#5
    | bitBool x 10 => Signal.pure 10#5
    | bitBool x 11 => Signal.pure 11#5
    | bitBool x 12 => Signal.pure 12#5
    | bitBool x 13 => Signal.pure 13#5
    | bitBool x 14 => Signal.pure 14#5
    | bitBool x 15 => Signal.pure 15#5
    | bitBool x 16 => Signal.pure 16#5
    | bitBool x 17 => Signal.pure 17#5
    | bitBool x 18 => Signal.pure 18#5
    | bitBool x 19 => Signal.pure 19#5
    | bitBool x 20 => Signal.pure 20#5
    | bitBool x 21 => Signal.pure 21#5
    | bitBool x 22 => Signal.pure 22#5
    | bitBool x 23 => Signal.pure 23#5
    | bitBool x 24 => Signal.pure 24#5
    | bitBool x 25 => Signal.pure 25#5
    | bitBool x 26 => Signal.pure 26#5
    | bitBool x 27 => Signal.pure 27#5
    | bitBool x 28 => Signal.pure 28#5
    | bitBool x 29 => Signal.pure 29#5
    | bitBool x 30 => Signal.pure 30#5
    | bitBool x 31 => Signal.pure 31#5

/-- Reverse the bit order of a packed vector while retaining its symbolic width. -/
def reverseBits {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Signal.map BitVec.reverse x

/-- Reverse bits independently within a fixed number of equal-sized blocks.
    `W` must be divisible by `BLOCKS` for the generated structural mapping. -/
def reverseBlocks {W BLOCKS : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Signal.map (fun value =>
    BitVec.ofNat W <|
      (List.range W).foldl (fun result outputIndex =>
        let blockWidth := W / BLOCKS
        let blockBase := (outputIndex / blockWidth) * blockWidth
        let sourceIndex := blockBase + (blockWidth - 1 - (outputIndex % blockWidth))
        if value.getLsbD sourceIndex then result + 2 ^ outputIndex else result
      ) 0
  ) x

/-- Reverse the order of equal-width lanes while preserving the bit order
    inside each lane. Lane zero is the least-significant `LANEW`-bit chunk.
    Both `LANES` and `LANEW` may be retained hardware parameters. -/
def reversePackedLanes {LANES LANEW : Nat}
    (x : Signal dom (BitVec (LANES * LANEW)))
    : Signal dom (BitVec (LANES * LANEW)) :=
  reverseBlocks (BLOCKS := LANES) (reverseBits x)

/-- Repeat a packed `W`-bit value `N` times into a `N * W`-bit vector.
    The compiler lowers this to a SystemVerilog generate-for loop, so `N`
    remains a retained hardware parameter rather than a Lean elaboration-time
    specialization. -/
def repeatVector {W N : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec (N * W)) :=
  Signal.map (fun value =>
    BitVec.ofNat (N * W) <|
      (List.range N).foldl (fun acc _ => acc * 2 ^ W + value.toNat) 0
  ) x

/-- Sum signed packed lanes at an explicit accumulator width. -/
def signedSumPacked {laneW accW lanes : Nat}
    (values : Signal dom (BitVec (lanes * laneW))) : Signal dom (BitVec accW) :=
  let one : Signal dom (BitVec laneW) := Signal.pure (BitVec.ofNat laneW 1)
  let coefficients : Signal dom (BitVec (lanes * laneW)) :=
    repeatVector (N := lanes) one
  signedDotPacked values coefficients

/-- Build packed lanes with the one-based sequence `[1, 2, ..., N]`.
    Lane zero occupies the least-significant `W` bits. The compiler lowers
    this to a SystemVerilog generate-for, retaining both `W` and `N`. -/
def iotaVector1 {W N : Nat} : Signal dom (BitVec (N * W)) :=
  Signal.pure <| BitVec.ofNat (N * W) <|
    (List.range N).foldl (fun acc index =>
      acc + (index + 1) * 2 ^ (index * W)
    ) 0

private def isReservedHammingPosition (position : Nat) : Bool :=
  position == 0 || (position &&& (position - 1)) == 0

/-- Scatter payload bits into the non-power-of-two positions of an extended
    Hamming word. Position zero and positions 1, 2, 4, ... are initialized to
    zero for the overall and indexed parity bits. -/
def scatterNonPowerOfTwoBits {DATAW PARITYW : Nat}
    (data : Signal dom (BitVec DATAW))
    : Signal dom (BitVec (DATAW + PARITYW + 1)) :=
  let encodedWidth := DATAW + PARITYW + 1
  Signal.map (fun value =>
    let final := (List.range encodedWidth).foldl (fun state position =>
      let dataIndex := state.1
      let result := state.2
      if isReservedHammingPosition position then state
      else
        let result := if value.getLsbD dataIndex
          then result + 2 ^ position else result
        (dataIndex + 1, result)
    ) (0, 0)
    BitVec.ofNat encodedWidth final.2
  ) data

/-- Compute one parity bit for each binary position-index mask. Output bit `p`
    is the XOR of input positions whose index has bit `p` set. -/
def parityByIndexMask {W PARITYW : Nat}
    (value : Signal dom (BitVec W)) : Signal dom (BitVec PARITYW) :=
  Signal.map (fun input =>
    BitVec.ofNat PARITYW <|
      (List.range PARITYW).foldl (fun result parityIndex =>
        let parity := (List.range W).foldl (fun acc position =>
          if ((position / (2 ^ parityIndex)) % 2 == 1) && input.getLsbD position
          then !acc else acc
        ) false
        if parity then result + 2 ^ parityIndex else result
      ) 0
  ) value

/-- Replace the power-of-two positions 1, 2, 4, ... of an encoded word with
    the corresponding bits of a packed parity vector. Position zero and all
    data positions are preserved. -/
def placeParityBits {DATAW PARITYW : Nat}
    (base : Signal dom (BitVec (DATAW + PARITYW + 1)))
    (parity : Signal dom (BitVec PARITYW))
    : Signal dom (BitVec (DATAW + PARITYW + 1)) :=
  let encodedWidth := DATAW + PARITYW + 1
  (fun baseValue parityValue =>
    BitVec.ofNat encodedWidth <|
      (List.range encodedWidth).foldl (fun result position =>
        let bitValue :=
          if position != 0 && isReservedHammingPosition position then
            parityValue.getLsbD (Nat.log2 position)
          else baseValue.getLsbD position
        if bitValue then result + 2 ^ position else result
      ) 0
  ) <$> base <*> parity

/-- Gather the non-power-of-two positions of an extended Hamming word into a
    densely packed payload vector. This is the inverse layout operation of
    `scatterNonPowerOfTwoBits`. -/
def gatherNonPowerOfTwoBits {DATAW PARITYW : Nat}
    (encoded : Signal dom (BitVec (DATAW + PARITYW + 1)))
    : Signal dom (BitVec DATAW) :=
  let encodedWidth := DATAW + PARITYW + 1
  Signal.map (fun value =>
    let final := (List.range encodedWidth).foldl (fun state position =>
      let dataIndex := state.1
      let result := state.2
      if isReservedHammingPosition position then state
      else
        let result := if value.getLsbD position
          then result + 2 ^ dataIndex else result
        (dataIndex + 1, result)
    ) (0, 0)
    BitVec.ofNat DATAW final.2
  ) encoded

end Sparkle.Library.RTL
