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
  x === (Signal.pure (onesBV w))

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

end Sparkle.Library.RTL
