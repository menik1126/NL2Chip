import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2

def test_fsm2 {dom : DomainConfig}
    (inp : Signal dom Bool)
    : Signal dom (BitVec 3) :=
  let state := Signal.loop fun (state : Signal dom (BitVec 2)) =>
    let next := Signal.mux inp (Signal.pure stB) (Signal.pure stA)
    Signal.register stA next
  Signal.map (fun s => if s == stB then 1#3 else 0#3) state

#synthesizeVerilog test_fsm2
