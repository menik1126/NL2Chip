import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Combinational circuit: 4-to-1 mux with c as selector -/
def prob130_circuit5 {dom : DomainConfig}
    (a b c d e : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  let is0 := c === 0#4
  let is1 := c === 1#4
  let is2 := c === 2#4
  let is3 := c === 3#4
  let default := Signal.pure 15#4  -- 0xf
  -- Build nested mux: if c==3 then d, else if c==2 then a, else if c==1 then e, else if c==0 then b, else default
  let result3 := Signal.mux is3 d default
  let result2 := Signal.mux is2 a result3
  let result1 := Signal.mux is1 e result2
  Signal.mux is0 b result1

#synthesizeVerilog prob130_circuit5
