import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 6-to-1 multiplexer with 3-bit selector. Outputs corresponding data input for sel 0-5, otherwise 0. -/
def prob076_always_case {dom : DomainConfig}
    (sel : Signal dom (BitVec 3))
    (data0 data1 data2 data3 data4 data5 : Signal dom (BitVec 4))
    : Signal dom (BitVec 4) :=
  let is0 := sel === 0#3
  let is1 := sel === 1#3
  let is2 := sel === 2#3
  let is3 := sel === 3#3
  let is4 := sel === 4#3
  let is5 := sel === 5#3
  -- Build nested mux: check 0, then 1, then 2, then 3, then 4, then 5, else 0
  Signal.mux is0 data0
    (Signal.mux is1 data1
      (Signal.mux is2 data2
        (Signal.mux is3 data3
          (Signal.mux is4 data4
            (Signal.mux is5 data5 (Signal.pure 0#4))))))

#synthesizeVerilog prob076_always_case
