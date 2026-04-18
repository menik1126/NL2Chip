import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Dual-edge triggered flip-flop using posedge and negedge registers with clock-based mux -/
def prob078_dualedge {dom : DomainConfig}
    (d : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  let qp := Signal.register 0#1 d
  let qn := Signal.registerNeg 0#1 d
  Signal.mux Signal.clock qp qn

#synthesizeVerilog prob078_dualedge
