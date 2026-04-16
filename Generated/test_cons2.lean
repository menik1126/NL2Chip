import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_circuit {dom : DomainConfig}
    (inp : Signal dom (BitVec 8)) : Signal dom (BitVec 9) :=
  Signal.map (fun (v : BitVec 8) =>
    let b : Bool := v.getLsbD 0
    BitVec.cons b v
  ) inp

#synthesizeVerilog test_circuit
