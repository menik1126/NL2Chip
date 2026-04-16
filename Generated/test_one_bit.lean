import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test - just compute one bit -/
def test_one_bit {dom : DomainConfig}
    (load : Signal dom Bool) (data : Signal dom (BitVec 4))
    : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let c0 := Signal.map (fun g => g.getLsb ⟨0, by omega⟩) q
    let next0 := ~~~c0
    let result := Signal.mux next0 (Signal.pure 1#4) (Signal.pure 0#4)
    let nextQ := Signal.mux load data result
    Signal.register 0#4 nextQ

#synthesizeVerilog test_one_bit
