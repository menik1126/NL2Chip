import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev st0 : BitVec 3 := 0#3
private abbrev st1 : BitVec 3 := 1#3

def test_with_external {dom : DomainConfig}
    (s : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  Signal.loop fun (state : Signal dom (BitVec 3)) =>
    let is0 := state === Signal.pure st0
    let next := Signal.mux is0
      (Signal.mux (Signal.map (fun x => x.getLsb 0) s) (Signal.pure st1) (Signal.pure st0))
      (Signal.pure st0)
    Signal.register st0 next

#synthesizeVerilog test_with_external
