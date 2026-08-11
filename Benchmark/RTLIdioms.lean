/-
  Extra few-shot RTL idioms for NL/CVDP-style tasks.

  These examples are intentionally small and boring: they show stable Sparkle
  patterns for code that LLMs otherwise tend to rewrite incorrectly.
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Bit slicing, zero extension, and multi-output packing. -/
def idiomSlicing {dom : DomainConfig}
    (x : Signal dom (BitVec 16))
    : Signal dom (BitVec 8 × BitVec 8 × BitVec 1) :=
  let lo : Signal dom (BitVec 8) := Sparkle.Library.RTL.slice x 0
  let hi : Signal dom (BitVec 8) := Sparkle.Library.RTL.slice x 8
  let nonzero := Sparkle.Library.RTL.boolToBV1 (Sparkle.Library.RTL.nonZero x)
  bundleAll! [hi, lo, nonzero]

#synthesizeVerilog idiomSlicing

/-- Reverse bits with a named helper instead of hand-writing many temporary wires. -/
def idiomReverse8 {dom : DomainConfig}
    (x : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Sparkle.Library.RTL.reverseBits8 x

#synthesizeVerilog idiomReverse8

/-- Popcount and LSB-priority encoder helpers. -/
def idiomBitScans {dom : DomainConfig}
    (x : Signal dom (BitVec 8)) : Signal dom (BitVec 4 × BitVec 3) :=
  let count := Sparkle.Library.RTL.popCount8 x
  let firstSet := Sparkle.Library.RTL.priorityEncodeLsb8 x
  bundle2 count firstSet

#synthesizeVerilog idiomBitScans

/-- Small register-file style memory with combinational read. -/
def idiomRegFile {dom : DomainConfig}
    (wen : Signal dom Bool)
    (waddr : Signal dom (BitVec 4))
    (wdata : Signal dom (BitVec 8))
    (raddr : Signal dom (BitVec 4))
    : Signal dom (BitVec 8) :=
  Sparkle.Library.RTL.regFile1R1W waddr wdata wen raddr

#synthesizeVerilog idiomRegFile

/-- Active-low reset, enable, and registered state update. -/
def idiomCounterEnableReset {dom : DomainConfig}
    (rstN en : Signal dom Bool) : Signal dom (BitVec 8) :=
  Signal.loop fun q =>
    let inc := q + 1#8
    let next := Sparkle.Library.RTL.resetLow 0#8 rstN inc
    Sparkle.Library.RTL.dffe 0#8 en next

#synthesizeVerilog idiomCounterEnableReset
