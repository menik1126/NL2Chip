import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test Conway's Life on 2x2 grid -/
def test_conway {dom : DomainConfig}
    (load : Signal dom Bool) (data : Signal dom (BitVec 4))
    : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let c0 := Signal.map (fun g => g.getLsb ⟨0, by omega⟩) q
    let c1 := Signal.map (fun g => g.getLsb ⟨1, by omega⟩) q
    let c2 := Signal.map (fun g => g.getLsb ⟨2, by omega⟩) q
    let c3 := Signal.map (fun g => g.getLsb ⟨3, by omega⟩) q
    
    -- Compute next state for cell 0
    let n0_0 := Signal.mux c3 (Signal.pure 1#4) (Signal.pure 0#4)
    let n0_1 := n0_0 + Signal.mux c2 (Signal.pure 1#4) (Signal.pure 0#4)
    let n0_2 := n0_1 + Signal.mux c3 (Signal.pure 1#4) (Signal.pure 0#4)
    let n0_3 := n0_2 + Signal.mux c1 (Signal.pure 1#4) (Signal.pure 0#4)
    let n0_4 := n0_3 + Signal.mux c1 (Signal.pure 1#4) (Signal.pure 0#4)
    let n0_5 := n0_4 + Signal.mux c3 (Signal.pure 1#4) (Signal.pure 0#4)
    let n0_6 := n0_5 + Signal.mux c2 (Signal.pure 1#4) (Signal.pure 0#4)
    let n0_7 := n0_6 + Signal.mux c3 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_0 := n0_7 === 2#4
    let eq3_0 := n0_7 === 3#4
    let stay_0 := eq2_0 &&& c0
    let next0 := stay_0 ||| eq3_0
    
    -- Build result using Signal.map to combine all bits
    let result := Signal.map (fun (b0 : Bool) =>
      let r := 0#4
      let r := if b0 then r ||| (1#4 <<< 0) else r
      r
    ) next0
    
    let nextQ := Signal.mux load data result
    Signal.register 0#4 nextQ

#synthesizeVerilog test_conway
