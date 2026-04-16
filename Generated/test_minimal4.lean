import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: using extractLsb' to get BitVec 1 signals, then concat them
def testD {dom : DomainConfig}
    (state : Signal dom (BitVec 4)) (inp : Signal dom Bool)
    : Signal dom (BitVec 4 × BitVec 1) :=
  -- Extract individual bits as BitVec 1
  let stA : Signal dom (BitVec 1) := Signal.map (fun s => s.extractLsb' 0 1) state
  let stD : Signal dom (BitVec 1) := Signal.map (fun s => s.extractLsb' 3 1) state
  -- inp as BitVec 1
  let inp_bv : Signal dom (BitVec 1) := Signal.mux inp (Signal.pure 1#1) (Signal.pure 0#1)
  -- Build next state
  let nsA : Signal dom (BitVec 1) := (stA ||| stD) -- simplified for test
  let ns : Signal dom (BitVec 4) := stD ++ stD ++ stD ++ nsA
  let out : Signal dom (BitVec 1) := stD
  bundle2 ns out

#synthesizeVerilog testD
