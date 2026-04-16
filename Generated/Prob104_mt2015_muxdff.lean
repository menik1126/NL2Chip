import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Mux+DFF submodule: on each clock edge, Q <= L ? r_in : q_in -/
def prob104_mt2015_muxdff {dom : DomainConfig}
    (L : Signal dom Bool)
    (q_in : Signal dom (BitVec 1))
    (r_in : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  Signal.register 0#1 (Signal.mux L r_in q_in)

#synthesizeVerilog prob104_mt2015_muxdff
