import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test concatenation -/
def prob143_concat_test {dom : DomainConfig}
    (a b : Signal dom (BitVec 1))
    : Signal dom (BitVec 2) :=
  a ++ b

#synthesizeVerilog prob143_concat_test
