import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit ripple-carry adder: adds two 8-bit inputs with carry-in, returns (sum, cout). -/
def adder_8bit {dom : DomainConfig}
    (a b : Signal dom (BitVec 8))
    (cin : Signal dom (BitVec 1))
    : Signal dom (BitVec 8 × BitVec 1) :=
  -- Extend all to 9 bits and add
  let a9 := Signal.map (fun x => x.zeroExtend 9) a
  let b9 := Signal.map (fun x => x.zeroExtend 9) b
  let cin9 := Signal.map (fun x => x.zeroExtend 9) cin
  let sum9 := a9 + b9 + cin9
  -- Extract sum (lower 8 bits) and cout (bit 8)
  let sum := Signal.map (fun x => x.extractLsb 7 0) sum9
  let cout := Signal.map (fun x => x.extractLsb 8 8) sum9
  bundle2 sum cout

#synthesizeVerilog adder_8bit
