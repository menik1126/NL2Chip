import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test -/
def prob999_test {dom : DomainConfig}
    (reset : Signal dom Bool)
    (inp : Signal dom (BitVec 8))
    : Signal dom (BitVec 24 × BitVec 1) :=
  let out_bytes := Signal.pure 0#24
  let done := Signal.pure 0#1
  bundle2 out_bytes done

#synthesizeVerilog prob999_test
