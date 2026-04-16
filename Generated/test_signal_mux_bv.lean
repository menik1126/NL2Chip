import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: Signal.mux to convert Bool signal to BitVec 1 signal
def test_mux_bv {dom : DomainConfig}
    (q : Signal dom (BitVec 8)) : Signal dom (BitVec 2) :=
  let bit0 : Signal dom Bool := Signal.map (fun (v : BitVec 8) =>
    v.getLsbD 0) q
  let bit1 : Signal dom Bool := Signal.map (fun (v : BitVec 8) =>
    v.getLsbD 1) q
  let bv0 : Signal dom (BitVec 1) := Signal.mux bit0 (Signal.pure (1 : BitVec 1)) (Signal.pure (0 : BitVec 1))
  let bv1 : Signal dom (BitVec 1) := Signal.mux bit1 (Signal.pure (1 : BitVec 1)) (Signal.pure (0 : BitVec 1))
  bv1 ++ bv0

#synthesizeVerilog test_mux_bv
