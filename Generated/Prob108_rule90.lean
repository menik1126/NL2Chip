import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test with 512-bit using map -/
def test_shift {dom : DomainConfig}
    (q : Signal dom (BitVec 512)) : Signal dom (BitVec 512) :=
  Signal.map (fun (x : BitVec 512) => x >>> 1) q

#synthesizeVerilog test_shift
