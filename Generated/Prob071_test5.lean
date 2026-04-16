import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test without Signal.pure -/
def prob071_test5 {dom : DomainConfig}
    (inp : Signal dom (BitVec 8)) : Signal dom (BitVec 3) :=
  let bit0 : Signal dom Bool := Signal.map (fun x => x.getLsb 0) inp
  Signal.mux bit0 (0#3 : BitVec 3) (1#3 : BitVec 3)

#synthesizeVerilog prob071_test5
