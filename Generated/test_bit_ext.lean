import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test bit extension and shift -/
def test_bit_ext {dom : DomainConfig}
    (a : Signal dom (BitVec 1))
    : Signal dom (BitVec 10) :=
  Signal.map (fun b => b.zeroExtend 10) a

#synthesizeVerilog test_bit_ext
