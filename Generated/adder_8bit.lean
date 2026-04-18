import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit ripple-carry adder: adds two 8-bit inputs with carry-in, returns (sum, cout). -/
def adder_8bit {dom : DomainConfig}
    (a b : Signal dom (BitVec 8))
    (cin : Signal dom (BitVec 1))
    : Signal dom (BitVec 8 × BitVec 1) :=
  -- Extend inputs to 9 bits to capture carry
  let a_ext := Signal.map (fun x => x.zeroExtend 9) a
  let b_ext := Signal.map (fun x => x.zeroExtend 9) b
  let cin_ext := Signal.map (fun x => x.zeroExtend 9) cin
  
  -- Perform 9-bit addition
  let result := a_ext + b_ext + cin_ext
  
  -- Extract sum (lower 8 bits) and cout (bit 8)
  let sum := Signal.map (fun x => x.extractLsb 7 0) result
  let cout := Signal.map (fun x => x.extractLsb 8 8) result
  
  bundle2 sum cout

#synthesizeVerilog adder_8bit
