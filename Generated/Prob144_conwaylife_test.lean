import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Conway's Game of Life on a 16x16 toroidal grid -/
def prob144_conwaylife {dom : DomainConfig}
    (load : Signal dom Bool) (data : Signal dom (BitVec 256))
    : Signal dom (BitVec 256) :=
  Signal.loop fun (q : Signal dom (BitVec 256)) =>
    -- Extract bit 0
    let bit0 : Signal dom Bool := Signal.map (fun (grid : BitVec 256) => grid.getLsb 0) q
    
    -- Just pass through q for now
    let nextState := q
    
    -- Load data if load signal is high, otherwise use computed next state
    let nextQ := Signal.mux load data nextState
    Signal.register 0#256 nextQ

#synthesizeVerilog prob144_conwaylife
