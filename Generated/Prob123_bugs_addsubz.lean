import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Adder-subtractor with zero flag (bug fix): computes out = a+b or a-b based on do_sub,
    and result_is_zero = (out == 0). The original bug was using bitwise NOT instead of equality. -/
def prob123_bugs_addsubz {dom : DomainConfig}
    (do_sub : Signal dom Bool)
    (a b : Signal dom (BitVec 8))
    : Signal dom (BitVec 8 × BitVec 1) :=
  let out := Signal.mux do_sub (a - b) (a + b)
  let result_is_zero := Signal.mux (out === Signal.pure 0#8) (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 out result_is_zero

#synthesizeVerilog prob123_bugs_addsubz
