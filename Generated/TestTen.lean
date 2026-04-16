import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test with just 10 bits using simpler syntax -/
def testTen {dom : DomainConfig} (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let bit0 : Signal dom Bool := Signal.map (fun x => x.getLsb 0) input
  let b0 := Signal.mux bit0 (Signal.pure 1#8) (Signal.pure 0#8)
  let bit1 : Signal dom Bool := Signal.map (fun x => x.getLsb 1) input
  let b1 := Signal.mux bit1 (Signal.pure 1#8) (Signal.pure 0#8)
  let bit2 : Signal dom Bool := Signal.map (fun x => x.getLsb 2) input
  let b2 := Signal.mux bit2 (Signal.pure 1#8) (Signal.pure 0#8)
  let bit3 : Signal dom Bool := Signal.map (fun x => x.getLsb 3) input
  let b3 := Signal.mux bit3 (Signal.pure 1#8) (Signal.pure 0#8)
  let bit4 : Signal dom Bool := Signal.map (fun x => x.getLsb 4) input
  let b4 := Signal.mux bit4 (Signal.pure 1#8) (Signal.pure 0#8)
  let bit5 : Signal dom Bool := Signal.map (fun x => x.getLsb 5) input
  let b5 := Signal.mux bit5 (Signal.pure 1#8) (Signal.pure 0#8)
  let bit6 : Signal dom Bool := Signal.map (fun x => x.getLsb 6) input
  let b6 := Signal.mux bit6 (Signal.pure 1#8) (Signal.pure 0#8)
  let bit7 : Signal dom Bool := Signal.map (fun x => x.getLsb 7) input
  let b7 := Signal.mux bit7 (Signal.pure 1#8) (Signal.pure 0#8)
  let bit8 : Signal dom Bool := Signal.map (fun x => x.getLsb 8) input
  let b8 := Signal.mux bit8 (Signal.pure 1#8) (Signal.pure 0#8)
  let bit9 : Signal dom Bool := Signal.map (fun x => x.getLsb 9) input
  let b9 := Signal.mux bit9 (Signal.pure 1#8) (Signal.pure 0#8)
  b0 + b1 + b2 + b3 + b4 + b5 + b6 + b7 + b8 + b9

#synthesizeVerilog testTen
