import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_tuple {dom : DomainConfig}
    (inp : Signal dom Bool) : Signal dom (BitVec 4) :=
  let combined := Signal.loop fun (s : Signal dom (BitVec 4 × BitVec 4)) =>
    let a := Signal.map (fun x => x.1) s
    let b := Signal.map (fun x => x.2) s
    let nextA := a + (Signal.pure 1#4 : Signal dom (BitVec 4))
    let nextB := b + (Signal.pure 2#4 : Signal dom (BitVec 4))
    let regA := Signal.register 0#4 nextA
    let regB := Signal.register 0#4 nextB
    bundle2 regA regB
  Signal.map (fun x => x.1) combined

#synthesizeVerilog test_tuple
