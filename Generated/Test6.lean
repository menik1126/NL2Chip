import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test Bool to BitVec conversion -/
def test6 {dom : DomainConfig}
    (a : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  Signal.map (fun b => if b then 1#1 else 0#1) a

#synthesizeVerilog test6
