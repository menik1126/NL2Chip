import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Original spec (preserved for verification) -/
def prob085_shift4_spec {dom : DomainConfig}
    (areset : Signal dom Bool)
    (load : Signal dom Bool)
    (ena : Signal dom Bool)
    (data : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    -- Shift right: MSB becomes 0, shift existing bits
    -- q[3:1] → q[2:0], q[3] ← 0
    let shifted := q >>> 1#4  -- This shifts right, MSB becomes 0
    
    -- Priority: load > ena > hold
    -- If load: use data
    -- Else if ena: shift right  
    -- Else: hold current value
    let nextVal := Signal.mux load data 
                    (Signal.mux ena shifted q)
    
    -- Apply async reset (modeled as sync): areset → 0
    let nextWithReset := Signal.mux areset (Signal.pure 0#4) nextVal
    
    -- Register with initial value 0 (matches areset behavior)
    Signal.register 0#4 nextWithReset

/-- Optimized version: use hw_cond for single priority chain instead of nested mux -/
def prob085_shift4 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (load : Signal dom Bool)
    (ena : Signal dom Bool)
    (data : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let shifted := q >>> 1#4
    -- Single priority chain: areset > load > ena > hold
    let nextVal := hw_cond q 
      | areset => Signal.pure 0#4
      | load => data
      | ena => shifted
    Signal.register 0#4 nextVal

/-- Equivalence proof: optimized version = original spec -/
theorem prob085_shift4_equiv {dom : DomainConfig} 
    (areset : Signal dom Bool)
    (load : Signal dom Bool) 
    (ena : Signal dom Bool)
    (data : Signal dom (BitVec 4)) :
    prob085_shift4 areset load ena data = prob085_shift4_spec areset load ena data := by
  -- They are definitionally equal after macro expansion
  rfl

#synthesizeVerilog prob085_shift4