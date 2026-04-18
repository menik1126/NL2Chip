import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Submodule combining a 2:1 mux and a flip-flop. When L is high, loads r_in; otherwise loads q_in. -/
def prob104_mt2015_muxdff {dom : DomainConfig}
    (L : Signal dom Bool)
    (q_in : Signal dom (BitVec 1))
    (r_in : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  let mux_out := Signal.mux L r_in q_in
  Signal.register 0#1 mux_out

#synthesizeVerilog prob104_mt2015_muxdff
