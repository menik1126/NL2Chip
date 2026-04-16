import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_multi {dom : DomainConfig}
    (inp : Signal dom Bool)
    (state : Signal dom (BitVec 10))
    : Signal dom (BitVec 10 × BitVec 1 × BitVec 1) :=
  -- BitVec 1 signals for each state bit
  let s0 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 0 1) state
  let s5 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 5 1) state
  let s7 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 7 1) state
  let s8 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 8 1) state
  let s9 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 9 1) state
  let i : Signal dom (BitVec 1) := Signal.mux inp (Signal.pure 1#1) (Signal.pure 0#1)
  let ni : Signal dom (BitVec 1) := ~~~i
  -- out1 = s8 | s9
  let out1 : Signal dom (BitVec 1) := s8 ||| s9
  -- out2 = s7 | s9
  let out2 : Signal dom (BitVec 1) := s7 ||| s9
  -- Simple ns vector
  let ns0 : Signal dom (BitVec 1) := ni &&& s0
  let ns5 : Signal dom (BitVec 1) := i &&& s5
  -- Assemble 10-bit next_state
  let ns0e : Signal dom (BitVec 10) := Signal.map (fun b => b.zeroExtend 10) ns0
  let ns5e : Signal dom (BitVec 10) := (Signal.map (fun b => b.zeroExtend 10) ns5) <<< (5#10 : BitVec 10)
  let next_state := ns0e ||| ns5e
  bundle2 next_state (bundle2 out1 out2)

#synthesize test_multi
