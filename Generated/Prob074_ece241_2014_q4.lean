import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM with three DFFs and NOR output.
    s[2] <= s[2] XOR x
    s[1] <= (NOT s[1]) AND x
    s[0] <= (NOT s[0]) OR x
    z = NOR(s[2], s[1], s[0]) = (s == 0) -/
def prob074_ece241_2014_q4 {dom : DomainConfig}
    (x : Signal dom Bool) : Signal dom Bool :=
  -- State: 3-bit register s[2:0], initially 0
  let state : Signal dom (BitVec 3) :=
    Signal.loop fun (s : Signal dom (BitVec 3)) =>
      -- Extract individual state bits as 1-bit bitvectors
      let s2 : Signal dom (BitVec 1) := Signal.map (fun v => v.extractLsb' 2 1) s
      let s1 : Signal dom (BitVec 1) := Signal.map (fun v => v.extractLsb' 1 1) s
      let s0 : Signal dom (BitVec 1) := Signal.map (fun v => v.extractLsb' 0 1) s
      -- Convert x to 1-bit bitvector
      let xb : Signal dom (BitVec 1) := Signal.mux x (Signal.pure 1#1) (Signal.pure 0#1)
      -- Compute next state bits using bitvector operations
      -- n2 = s[2] XOR x
      let n2 : Signal dom (BitVec 1) := s2 ^^^ xb
      -- n1 = (~s[1]) AND x
      let n1 : Signal dom (BitVec 1) := (~~~s1) &&& xb
      -- n0 = (~s[0]) OR x
      let n0 : Signal dom (BitVec 1) := (~~~s0) ||| xb
      -- Pack three 1-bit signals into BitVec 3 via concatenation
      -- n2 ++ n1 ++ n0 → {n2, n1, n0} = s[2:0]
      let nextState : Signal dom (BitVec 3) := n2 ++ n1 ++ n0
      Signal.register 0#3 nextState
  -- Output z = NOR(s) = 1 iff state == 0
  state === (Signal.pure 0#3)

#synthesizeVerilog prob074_ece241_2014_q4
