import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_mux_concat {dom : DomainConfig}
    (a : Signal dom (BitVec 8)) : Signal dom (BitVec 16) :=
  let signBit : Signal dom Bool := Signal.map (fun x => x.getLsb 7) a
  let b : Signal dom (BitVec 8) := Signal.mux signBit (Signal.pure 0xFF#8) (Signal.pure 0#8)
  b ++ a

#synthesizeVerilog test_mux_concat
