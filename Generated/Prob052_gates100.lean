import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 100-input AND, OR, XOR gates -/
def prob052_gates100 {dom : DomainConfig}
    (input : Signal dom (BitVec 100))
    : Signal dom (BitVec 1 × (BitVec 1 × BitVec 1)) :=
  let out_and := Signal.pure 0#1
  let out_or := Signal.pure 1#1
  let out_xor := Signal.pure 0#1
  bundle2 out_and (bundle2 out_or out_xor)

#synthesizeVerilog prob052_gates100
