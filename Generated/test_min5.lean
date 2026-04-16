import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test zeroExtend then &&& inside lambda
def test_min5 {dom : DomainConfig}
    (q : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.map (fun (v : BitVec 4) =>
    let z := v.zeroExtend 4
    z &&& 7#4
  ) q

#synthesizeVerilog test_min5
