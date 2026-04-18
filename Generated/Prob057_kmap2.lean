import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Karnaugh map circuit: implements out = (~c & ~b) | (~d&~a) | (a&c&d) | (b&c&d) -/
def prob057_kmap2 {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  let term1 := (~~~c) &&& (~~~b)  -- ~c & ~b
  let term2 := (~~~d) &&& (~~~a)  -- ~d & ~a
  let term3 := a &&& c &&& d      -- a & c & d
  let term4 := b &&& c &&& d      -- b & c & d
  term1 ||| term2 ||| term3 ||| term4

#synthesizeVerilog prob057_kmap2
