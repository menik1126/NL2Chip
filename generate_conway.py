#!/usr/bin/env python3
"""Generate unrolled Conway's Life computation for all 256 cells"""

print("""import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Conway's Game of Life on a 16x16 toroidal grid -/
def prob144_conwaylife {dom : DomainConfig}
    (load : Signal dom Bool) (data : Signal dom (BitVec 256))
    : Signal dom (BitVec 256) :=
  Signal.loop fun (q : Signal dom (BitVec 256)) =>
    -- Compute next state using pure function
    let nextState := Signal.map (fun grid : BitVec 256 =>
      -- Helper to get cell at index with bounds check
      let getBit (i : Nat) : Bool :=
        if h : i < 256 then grid.getLsb ⟨i, h⟩ else false
      
      -- Helper to compute next state for cell at (r, c)
      let nextCell (r c : Nat) : Bool :=
        -- Compute wrapped neighbor indices
        let rPrev := if r == 0 then 15 else r - 1
        let rNext := if r == 15 then 0 else r + 1
        let cPrev := if c == 0 then 15 else c - 1
        let cNext := if c == 15 then 0 else c + 1
        
        -- Count neighbors
        let n1 := if getBit (rPrev * 16 + cPrev) then 1 else 0
        let n2 := if getBit (rPrev * 16 + c) then 1 else 0
        let n3 := if getBit (rPrev * 16 + cNext) then 1 else 0
        let n4 := if getBit (r * 16 + cPrev) then 1 else 0
        let n5 := if getBit (r * 16 + cNext) then 1 else 0
        let n6 := if getBit (rNext * 16 + cPrev) then 1 else 0
        let n7 := if getBit (rNext * 16 + c) then 1 else 0
        let n8 := if getBit (rNext * 16 + cNext) then 1 else 0
        
        let count := n1 + n2 + n3 + n4 + n5 + n6 + n7 + n8
        let alive := getBit (r * 16 + c)
        
        -- Conway's rules: 2 neighbors = stay, 3 neighbors = alive, else dead
        if count == 2 then alive
        else if count == 3 then true
        else false
      
      -- Manually unrolled computation for all 256 cells""")

# Generate unrolled code
for idx in range(256):
    r = idx // 16
    c = idx % 16
    if idx == 0:
        print(f"      let b{idx} := if nextCell {r} {c} then 1#256 else 0#256")
    else:
        print(f"      let b{idx} := if nextCell {r} {c} then b{idx-1} ||| (1#256 <<< {idx}) else b{idx-1}")

print(f"""      b255
    ) q
    
    -- Load data if load signal is high, otherwise use computed next state
    let nextQ := Signal.mux load data nextState
    Signal.register 0#256 nextQ

#synthesizeVerilog prob144_conwaylife
""")
