import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_complex {dom : DomainConfig}
    (reset : Signal dom Bool)
    (data : Signal dom Bool)
    : Signal dom (BitVec 4) :=
  let combined := Signal.loop fun (s : Signal dom ((BitVec 4 × BitVec 10) × BitVec 4)) =>
    let state : Signal dom (BitVec 4) := Signal.map (fun x => x.1.1) s
    let fcount : Signal dom (BitVec 10) := Signal.map (fun x => x.1.2) s
    let scount : Signal dom (BitVec 4) := Signal.map (fun x => x.2) s
    
    let isState0 := state === Signal.pure 0#4
    let nextState := Signal.mux isState0 (Signal.pure 1#4) (state + (Signal.pure 1#4 : Signal dom (BitVec 4)))
    let nextFcount := fcount + (Signal.pure 1#10 : Signal dom (BitVec 10))
    let scountShifted := scount <<< 1#4
    let dataVal := Signal.mux data (Signal.pure 1#4) (Signal.pure 0#4)
    let scountWithData := scountShifted ||| dataVal
    let scountDecr := scount - (Signal.pure 1#4 : Signal dom (BitVec 4))
    let nextScount := Signal.mux isState0 scountWithData scountDecr
    
    let regState := Signal.register 0#4 nextState
    let regFcount := Signal.register 0#10 nextFcount
    let regScount := Signal.register 0#4 nextScount
    
    bundle2 (bundle2 regState regFcount) regScount
  
  Signal.map (fun x => x.2) combined

#synthesizeVerilog test_complex
