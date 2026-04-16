import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test: using loop with BitVec concatenation to simulate tuple -/
def prob045_test {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  -- Use a 16-bit state: upper 8 bits = d_last, lower 8 bits = anyedge
  let state := Signal.loop fun (s : Signal dom (BitVec 16)) =>
    let d_last := Signal.map (fun x => x >>> 8#16) s  -- Extract upper 8 bits
    let d_last_8 := Signal.map (fun x => x.truncate 8) d_last
    let next_d_last := input
    let next_anyedge := input ^^^ d_last_8
    -- Concatenate: next_d_last in upper 8 bits, next_anyedge in lower 8 bits
    let next_state := Signal.map (fun (d : BitVec 8) => 
      Signal.map (fun (a : BitVec 8) => 
        (d.zeroExtend 16 <<< 8#16) ||| a.zeroExtend 16
      ) next_anyedge
    ) next_d_last
    sorry  -- This won't work due to nested Signal.map
  Signal.map (fun x => x.truncate 8) state  -- Extract lower 8 bits (anyedge)

#synthesizeVerilog prob045_test
