import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 16-bit wide, 9-to-1 multiplexer. sel=0 chooses a, sel=1 chooses b, etc.
    For unused cases (sel=9 to 15), output all 1s. -/
def prob097_mux9to1v {dom : DomainConfig}
    (a b c d e f g h i : Signal dom (BitVec 16))
    (sel : Signal dom (BitVec 4))
    : Signal dom (BitVec 16) :=
  let allOnes := Signal.pure 0xFFFF#16
  -- sel=0: a, sel=1: b, sel=2: c, sel=3: d, sel=4: e, sel=5: f, sel=6: g, sel=7: h, sel=8: i, else: all 1s
  let sel_is_0 := sel === Signal.pure 0#4
  let sel_is_1 := sel === Signal.pure 1#4
  let sel_is_2 := sel === Signal.pure 2#4
  let sel_is_3 := sel === Signal.pure 3#4
  let sel_is_4 := sel === Signal.pure 4#4
  let sel_is_5 := sel === Signal.pure 5#4
  let sel_is_6 := sel === Signal.pure 6#4
  let sel_is_7 := sel === Signal.pure 7#4
  let sel_is_8 := sel === Signal.pure 8#4
  -- Build nested mux tree
  let result_8_or_default := Signal.mux sel_is_8 i allOnes
  let result_7_or_above := Signal.mux sel_is_7 h result_8_or_default
  let result_6_or_above := Signal.mux sel_is_6 g result_7_or_above
  let result_5_or_above := Signal.mux sel_is_5 f result_6_or_above
  let result_4_or_above := Signal.mux sel_is_4 e result_5_or_above
  let result_3_or_above := Signal.mux sel_is_3 d result_4_or_above
  let result_2_or_above := Signal.mux sel_is_2 c result_3_or_above
  let result_1_or_above := Signal.mux sel_is_1 b result_2_or_above
  Signal.mux sel_is_0 a result_1_or_above

#synthesizeVerilog prob097_mux9to1v
