import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Dual-edge triggered flip-flop: captures d on both rising and falling edges of clk.
    Implemented using a posedge FF (qp), negedge FF (qn), and a clock-level mux. -/
def prob078_dualedge {dom : DomainConfig}
    (d : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  -- Posedge-triggered FF captures d on rising clock edge
  let qp := Signal.register 0#1 d
  -- Negedge-triggered FF captures d on falling clock edge
  let qn := Signal.registerNeg 0#1 d
  -- Output selects qp when clock is high, qn when clock is low
  Signal.mux Signal.clock qp qn

#synthesizeVerilog prob078_dualedge
