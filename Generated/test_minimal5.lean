import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Full test with BitVec 1 operations and inp: Signal dom Bool
def testE {dom : DomainConfig}
    (inp : Signal dom Bool)
    (state : Signal dom (BitVec 4))
    : Signal dom (BitVec 4 × BitVec 1) :=
  let stA : Signal dom (BitVec 1) := Signal.map (fun s => s.extractLsb' 0 1) state
  let stB : Signal dom (BitVec 1) := Signal.map (fun s => s.extractLsb' 1 1) state
  let stC : Signal dom (BitVec 1) := Signal.map (fun s => s.extractLsb' 2 1) state
  let stD : Signal dom (BitVec 1) := Signal.map (fun s => s.extractLsb' 3 1) state
  -- inp as BitVec 1
  let i   : Signal dom (BitVec 1) := Signal.mux inp (Signal.pure 1#1) (Signal.pure 0#1)
  let noti : Signal dom (BitVec 1) := ~~~i
  -- next_state bit logic
  -- nsA = (stA | stC) & ~in
  let nsA : Signal dom (BitVec 1) := (stA ||| stC) &&& noti
  -- nsB = (stA | stB | stD) & in
  let nsB : Signal dom (BitVec 1) := (stA ||| stB ||| stD) &&& i
  -- nsC = (stB | stD) & ~in
  let nsC_bv : Signal dom (BitVec 1) := (stB ||| stD) &&& noti
  -- nsD = stC & in
  let nsD : Signal dom (BitVec 1) := stC &&& i
  -- out = stD
  let out : Signal dom (BitVec 1) := stD
  -- Build next_state: bit3=nsD, bit2=nsC, bit1=nsB, bit0=nsA
  let next_state : Signal dom (BitVec 4) := nsD ++ nsC_bv ++ nsB ++ nsA
  bundle2 next_state out

#synthesizeVerilog testE
