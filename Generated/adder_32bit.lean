import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 32-bit carry-lookahead adder -/
def adder_32bit {dom : DomainConfig}
    (a b : Signal dom (BitVec 32))
    : Signal dom (BitVec 32 × BitVec 1) :=
  -- Compute the full 33-bit result using Signal operations
  let sum_extended := Signal.map (fun ab => 
    let a := ab.1
    let b := ab.2
    -- Extend to 33 bits to capture carry
    let a_ext := BitVec.zeroExtend 33 a
    let b_ext := BitVec.zeroExtend 33 b
    a_ext + b_ext
  ) (bundle2 a b)
  
  -- Extract 32-bit sum and 1-bit carry
  let sum := Signal.map (fun x => x.extractLsb 31 0) sum_extended
  let carry := Signal.map (fun x => x.extractLsb 32 32) sum_extended
  
  bundle2 sum carry

#synthesizeVerilog adder_32bit
