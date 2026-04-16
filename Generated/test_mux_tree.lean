import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_mux_tree {dom : DomainConfig}
    (inp : Signal dom Bool)
    (state : Signal dom (BitVec 10))
    : Signal dom (BitVec 10) :=
  let s0 := Signal.map (fun x => x.getLsb 0) state
  let s1 := Signal.map (fun x => x.getLsb 1) state
  
  let inp1_ns_temp1 := Signal.mux s0 (Signal.pure 0x002#10) (Signal.pure 0#10)
  let inp1_ns := Signal.mux s1 (Signal.pure 0x004#10) inp1_ns_temp1
  
  let inp0_ns := Signal.mux s0 (Signal.pure 0x001#10) (Signal.pure 0#10)
  
  Signal.mux inp inp1_ns inp0_ns

#synthesizeVerilog test_mux_tree
