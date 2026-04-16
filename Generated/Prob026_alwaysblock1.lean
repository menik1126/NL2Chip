import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- AND gate implemented two ways: out_assign and out_alwaysblock are both a & b. -/
def prob026_alwaysblock1 {dom : DomainConfig}
    (a b : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let out_assign      := a &&& b
  let out_alwaysblock := a &&& b
  bundle2 out_assign out_alwaysblock

#synthesizeVerilog prob026_alwaysblock1
