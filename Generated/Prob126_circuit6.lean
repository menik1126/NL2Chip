import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Combinational circuit implementing a lookup table mapping 3-bit input to 16-bit output -/
def prob126_circuit6 {dom : DomainConfig}
    (a : Signal dom (BitVec 3)) : Signal dom (BitVec 16) :=
  let is0 := a === Signal.pure 0#3
  let is1 := a === Signal.pure 1#3
  let is2 := a === Signal.pure 2#3
  let is3 := a === Signal.pure 3#3
  let is4 := a === Signal.pure 4#3
  let is5 := a === Signal.pure 5#3
  let is6 := a === Signal.pure 6#3
  -- is7 is implicit (else case)
  
  let val0 := Signal.pure 4658#16
  let val1 := Signal.pure 44768#16
  let val2 := Signal.pure 10196#16
  let val3 := Signal.pure 23054#16
  let val4 := Signal.pure 8294#16
  let val5 := Signal.pure 25806#16
  let val6 := Signal.pure 50470#16
  let val7 := Signal.pure 12057#16
  
  -- Build mux tree: nested muxes
  Signal.mux is0 val0
    (Signal.mux is1 val1
      (Signal.mux is2 val2
        (Signal.mux is3 val3
          (Signal.mux is4 val4
            (Signal.mux is5 val5
              (Signal.mux is6 val6 val7))))))

#synthesizeVerilog prob126_circuit6
