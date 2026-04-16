import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def testC {dom : DomainConfig}
    (state : Signal dom (BitVec 4)) (inp : Signal dom Bool)
    : Signal dom (BitVec 4 × BitVec 1) :=
  -- Extract bit 0 as BitVec 1 using extractLsb'
  let sA_bv : Signal dom (BitVec 1) := Signal.map (fun s => s.extractLsb' 0 1) state
  let out : Signal dom (BitVec 1) := Signal.mux inp (Signal.pure 1#1) (sA_bv)
  bundle2 state out

#synthesizeVerilog testC
