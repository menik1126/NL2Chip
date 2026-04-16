import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_triple {dom : DomainConfig}
    (reset : Signal dom Bool)
    (data : Signal dom Bool)
    : Signal dom (BitVec 4) :=
  let stateAndCounters := 
    Signal.loop fun sr =>
      let state := Signal.map (fun x => x.1) sr
      let counter1 := Signal.map (fun x => x.2.1) sr
      let counter2 := Signal.map (fun x => x.2.2) sr
      
      let nextState := Signal.mux data (state + 1#4) state
      let nextCounter1 := counter1 + 1#10
      let nextCounter2 := counter2 + 1#4
      
      let nextStateWithReset := Signal.mux reset (Signal.pure 0#4) nextState
      let nextCounter1WithReset := Signal.mux reset (Signal.pure 0#10) nextCounter1
      let nextCounter2WithReset := Signal.mux reset (Signal.pure 0#4) nextCounter2
      
      let regState := Signal.register 0#4 nextStateWithReset
      let regCounter1 := Signal.register 0#10 nextCounter1WithReset
      let regCounter2 := Signal.register 0#4 nextCounter2WithReset
      
      bundle2 regState (bundle2 regCounter1 regCounter2)
  
  Signal.map (fun x => x.1) stateAndCounters

#synthesizeVerilog test_triple
