import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- D flip-flop, positive edge triggered, with asynchronous reset "ar".
    When ar is high, q is reset to 0. Otherwise, q captures d on the rising edge of clk. -/
def prob049_m2014_q4b {dom : DomainConfig}
    (ar : Signal dom Bool)
    (d : Signal dom (BitVec 1))
    : Signal dom (BitVec 1) :=
  Signal.loop fun q =>
    let next := Signal.mux ar (Signal.pure 0#1) d
    Signal.register 0#1 next

#synthesizeVerilog prob049_m2014_q4b
