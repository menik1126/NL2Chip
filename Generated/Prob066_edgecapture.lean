import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Edge capture: captures 1→0 transitions and holds them until reset. -/
def prob066_edgecapture {dom : DomainConfig}
    (reset : Signal dom Bool)
    (input : Signal dom (BitVec 32)) : Signal dom (BitVec 32) :=
  -- Register the previous input value
  let d_last := Signal.register 0#32 input
  
  -- Loop for the output accumulator
  Signal.loop fun out =>
    -- Detect 1→0 transitions: previous=1 AND current=0
    let falling_edges := (~~~input) &&& d_last
    
    -- Accumulate captures: out | falling_edges
    let next_out := out ||| falling_edges
    
    -- Apply reset
    let final_out := Signal.mux reset (Signal.pure 0#32) next_out
    
    Signal.register 0#32 final_out

#synthesizeVerilog prob066_edgecapture
