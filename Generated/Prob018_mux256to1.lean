import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 256-to-1 multiplexer: selects bit sel from the 256-bit input vector. -/
def prob018_mux256to1 {dom : DomainConfig}
    (input : Signal dom (BitVec 256)) (sel : Signal dom (BitVec 8))
    : Signal dom (BitVec 1) :=
  Signal.map (fun pair => 
    BitVec.extractLsb 0 0 (pair.1 >>> pair.2.toNat)
  ) (bundle2 input sel)

#synthesizeVerilog prob018_mux256to1
