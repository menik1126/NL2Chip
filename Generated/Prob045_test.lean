import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Optimized: single register, combinational output -/
def prob045_test {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  let d_last := Signal.register 0#8 input
  input ^^^ d_last

#synthesizeVerilog prob045_test
