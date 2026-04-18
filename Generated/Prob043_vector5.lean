import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Compute all 25 pairwise one-bit comparisons of 5 input bits. -/
def prob043_vector5 {dom : DomainConfig}
    (a b c d e : Signal dom (BitVec 1)) : Signal dom (BitVec 25) :=
  -- Extend each input to 25 bits
  let a25 := Signal.map (fun x => BitVec.zeroExtend 25 x) a
  let b25 := Signal.map (fun x => BitVec.zeroExtend 25 x) b
  let c25 := Signal.map (fun x => BitVec.zeroExtend 25 x) c
  let d25 := Signal.map (fun x => BitVec.zeroExtend 25 x) d
  let e25 := Signal.map (fun x => BitVec.zeroExtend 25 x) e
  
  -- Replicate each input 5 times by shifting and ORing
  -- For a: we want bits [24:20] all equal to a
  let a_rep := (a25 <<< 24#25) ||| (a25 <<< 23#25) ||| (a25 <<< 22#25) ||| (a25 <<< 21#25) ||| (a25 <<< 20#25)
  let b_rep := (b25 <<< 19#25) ||| (b25 <<< 18#25) ||| (b25 <<< 17#25) ||| (b25 <<< 16#25) ||| (b25 <<< 15#25)
  let c_rep := (c25 <<< 14#25) ||| (c25 <<< 13#25) ||| (c25 <<< 12#25) ||| (c25 <<< 11#25) ||| (c25 <<< 10#25)
  let d_rep := (d25 <<< 9#25) ||| (d25 <<< 8#25) ||| (d25 <<< 7#25) ||| (d25 <<< 6#25) ||| (d25 <<< 5#25)
  let e_rep := (e25 <<< 4#25) ||| (e25 <<< 3#25) ||| (e25 <<< 2#25) ||| (e25 <<< 1#25) ||| e25
  
  let left := a_rep ||| b_rep ||| c_rep ||| d_rep ||| e_rep
  
  -- Build the right side: {5{a,b,c,d,e}}
  -- Pattern is: a at bit 4, b at bit 3, c at bit 2, d at bit 1, e at bit 0
  let pattern := (a25 <<< 4#25) ||| (b25 <<< 3#25) ||| (c25 <<< 2#25) ||| (d25 <<< 1#25) ||| e25
  -- Replicate pattern 5 times
  let right := (pattern <<< 20#25) ||| (pattern <<< 15#25) ||| (pattern <<< 10#25) ||| (pattern <<< 5#25) ||| pattern
  
  -- XOR and invert: ~(left ^ right)
  ~~~(left ^^^ right)

#synthesizeVerilog prob043_vector5
