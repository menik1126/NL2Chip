import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test with just 2-to-1 mux selecting between two 4-bit slices -/
def test_mux2to1 {dom : DomainConfig}
    (input : Signal dom (BitVec 1024)) (sel : Signal dom Bool)
    : Signal dom (BitVec 4) :=
  let inp0 := Signal.map (fun inp => inp.truncate 4) input  -- bits [3:0]
  let inp1 := Signal.map (fun inp => (inp >>> 4#1024).truncate 4) input  -- bits [7:4]
  Signal.mux sel inp1 inp0

#synthesizeVerilog test_mux2to1
