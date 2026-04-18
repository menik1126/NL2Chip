import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Vector gates: bitwise OR, logical OR, and concatenated NOT of two 3-bit vectors. -/
def prob044_vectorgates {dom : DomainConfig}
    (a b : Signal dom (BitVec 3))
    : Signal dom (BitVec 3 × (BitVec 1 × BitVec 6)) :=
  let out_or_bitwise := a ||| b
  let a_nonzero := ~~~(a === 0#3)
  let b_nonzero := ~~~(b === 0#3)
  let out_or_logical := Signal.mux (a_nonzero ||| b_nonzero) (Signal.pure 1#1) (Signal.pure 0#1)
  let not_a := ~~~a
  let not_b := ~~~b
  let out_not := Signal.ap (Signal.map BitVec.append not_b) not_a
  bundle2 out_or_bitwise (bundle2 out_or_logical out_not)

#synthesizeVerilog prob044_vectorgates
