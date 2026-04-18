import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Find minimum of four 8-bit unsigned values -/
def prob055_conditional {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  -- Start with a as the minimum
  let min_ab := Signal.mux (a.ult b) a b
  let min_abc := Signal.mux (min_ab.ult c) min_ab c
  let min_abcd := Signal.mux (min_abc.ult d) min_abc d
  min_abcd

#synthesizeVerilog prob055_conditional
