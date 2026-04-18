import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Keyboard scancode decoder: maps 8-bit scancodes to digits 0-9 with validity flag. -/
def prob114_bugs_case {dom : DomainConfig}
    (code : Signal dom (BitVec 8))
    : Signal dom (BitVec 4 × BitVec 1) :=
  let is_0x45 := code === 0x45#8
  let is_0x16 := code === 0x16#8
  let is_0x1e := code === 0x1e#8
  let is_0x26 := code === 0x26#8
  let is_0x25 := code === 0x25#8
  let is_0x2e := code === 0x2e#8
  let is_0x36 := code === 0x36#8
  let is_0x3d := code === 0x3d#8
  let is_0x3e := code === 0x3e#8
  let is_0x46 := code === 0x46#8
  
  -- Determine output value
  let out := Signal.mux is_0x45 (Signal.pure 0#4)
           (Signal.mux is_0x16 (Signal.pure 1#4)
           (Signal.mux is_0x1e (Signal.pure 2#4)
           (Signal.mux is_0x26 (Signal.pure 3#4)
           (Signal.mux is_0x25 (Signal.pure 4#4)
           (Signal.mux is_0x2e (Signal.pure 5#4)
           (Signal.mux is_0x36 (Signal.pure 6#4)
           (Signal.mux is_0x3d (Signal.pure 7#4)
           (Signal.mux is_0x3e (Signal.pure 8#4)
           (Signal.mux is_0x46 (Signal.pure 9#4)
           (Signal.pure 0#4))))))))))
  
  -- Determine validity
  let valid := Signal.mux (is_0x45 ||| is_0x16 ||| is_0x1e ||| is_0x26 ||| is_0x25 |||
                           is_0x2e ||| is_0x36 ||| is_0x3d ||| is_0x3e ||| is_0x46)
                          (Signal.pure 1#1)
                          (Signal.pure 0#1)
  
  bundle2 out valid

#synthesizeVerilog prob114_bugs_case
