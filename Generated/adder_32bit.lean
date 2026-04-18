import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 32-bit carry-lookahead adder -/
def adder_32bit {dom : DomainConfig}
    (a b : Signal dom (BitVec 32))
    : Signal dom (BitVec 32 × BitVec 1) :=
  let sum : Signal dom (BitVec 32) := a + b
  
  -- Compute carry: extend to 33 bits, add
  let a_ext : Signal dom (BitVec 33) := Signal.map (·.zeroExtend 33) a
  let b_ext : Signal dom (BitVec 33) := Signal.map (·.zeroExtend 33) b
  let sum_ext : Signal dom (BitVec 33) := a_ext + b_ext
  
  -- Extract carry by shifting right 32 and ANDing with 1
  let carry_shifted : Signal dom (BitVec 33) := sum_ext >>> 32#33
  let carry_masked : Signal dom (BitVec 33) := carry_shifted &&& 1#33
  
  -- Check if carry_masked is non-zero
  let carry_bool : Signal dom Bool := carry_masked === 1#33
  let carry : Signal dom (BitVec 1) := Signal.mux carry_bool 1#1 0#1
  
  bundle2 sum carry

#synthesizeVerilog adder_32bit
