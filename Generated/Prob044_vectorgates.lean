import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Vector gates: bitwise-OR, logical-OR, and bitwise-NOT of two 3-bit inputs.
    out_or_bitwise = a | b (3 bits)
    out_or_logical = a || b (1 bit, nonzero if either vector is nonzero)
    out_not = {~b, ~a} (6 bits: NOT b in upper half, NOT a in lower half) -/
def prob044_vectorgates {dom : DomainConfig}
    (a b : Signal dom (BitVec 3))
    : Signal dom (BitVec 3 × BitVec 1 × BitVec 6) :=
  let out_or_bitwise := a ||| b
  let out_or_logical_bool := out_or_bitwise === (0#3 : Signal dom _)
  let out_or_logical := Signal.mux out_or_logical_bool (Signal.pure 0#1) (Signal.pure 1#1)
  let not_b := ~~~b
  let not_a := ~~~a
  let out_not := not_b ++ not_a
  bundle2 out_or_bitwise (bundle2 out_or_logical out_not)

#synthesizeVerilog prob044_vectorgates
