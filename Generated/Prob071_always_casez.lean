import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Priority encoder: finds the position of the least significant bit that is 1 in an 8-bit input. -/
def prob071_always_casez {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 3) :=
  -- Extract each bit by masking and comparing
  let bit0 : Signal dom Bool := (input &&& 1#8) === 1#8
  let bit1 : Signal dom Bool := (input &&& 2#8) === 2#8
  let bit2 : Signal dom Bool := (input &&& 4#8) === 4#8
  let bit3 : Signal dom Bool := (input &&& 8#8) === 8#8
  let bit4 : Signal dom Bool := (input &&& 16#8) === 16#8
  let bit5 : Signal dom Bool := (input &&& 32#8) === 32#8
  let bit6 : Signal dom Bool := (input &&& 64#8) === 64#8
  let bit7 : Signal dom Bool := (input &&& 128#8) === 128#8
  
  -- Extract lower 3 bits and use arithmetic to create other constants
  let lower3 : Signal dom (BitVec 3) := Signal.map (fun x => x.extractLsb 2 0) input
  let zero : Signal dom (BitVec 3) := lower3 - lower3  -- 0
  let one : Signal dom (BitVec 3) := zero + 1#3
  let two : Signal dom (BitVec 3) := one + 1#3
  let three : Signal dom (BitVec 3) := two + 1#3
  let four : Signal dom (BitVec 3) := three + 1#3
  let five : Signal dom (BitVec 3) := four + 1#3
  let six : Signal dom (BitVec 3) := five + 1#3
  let seven : Signal dom (BitVec 3) := six + 1#3
  
  -- Priority mux: check from LSB to MSB
  Signal.mux bit0 zero
    (Signal.mux bit1 one
      (Signal.mux bit2 two
        (Signal.mux bit3 three
          (Signal.mux bit4 four
            (Signal.mux bit5 five
              (Signal.mux bit6 six
                (Signal.mux bit7 seven zero)))))))

#synthesizeVerilog prob071_always_casez
