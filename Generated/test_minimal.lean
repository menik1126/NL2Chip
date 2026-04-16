import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def testA {dom : DomainConfig}
    (state : Signal dom (BitVec 4)) (inp : Signal dom Bool)
    : Signal dom (BitVec 4 × BitVec 1) :=
  -- Simple: pass state directly
  let out : Signal dom (BitVec 1) := Signal.mux inp (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 state out

#synthesizeVerilog testA
