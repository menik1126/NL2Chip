import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Find the minimum of four 8-bit unsigned input values. -/
def prob055_conditional {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  -- min(a, b)
  let min_ab := Signal.mux (Signal.ult a b) a b
  -- min(c, d)
  let min_cd := Signal.mux (Signal.ult c d) c d
  -- min of the two intermediate results
  Signal.mux (Signal.ult min_ab min_cd) min_ab min_cd

#synthesizeVerilog prob055_conditional
