import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test one mux -/
def prob071_test3 {dom : DomainConfig}
    (inp : Signal dom (BitVec 8)) : Signal dom (BitVec 3) :=
  let bit0 : Signal dom Bool := Signal.map (fun x => x.getLsb 0) inp
  Signal.mux bit0 (Signal.pure 0#3) (Signal.pure 1#3)

#synthesizeVerilog prob071_test3
