import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Keyboard scancode decoder: maps 8-bit scancodes for keys 0-9 to 4-bit output and valid flag. -/
def prob114_bugs_case {dom : DomainConfig}
    (code : Signal dom (BitVec 8))
    : Signal dom (BitVec 4 × BitVec 1) :=
  let out : Signal dom (BitVec 4) :=
    hw_cond (Signal.pure 0#4)
      | code === Signal.pure 0x45#8 => Signal.pure 0#4
      | code === Signal.pure 0x16#8 => Signal.pure 1#4
      | code === Signal.pure 0x1e#8 => Signal.pure 2#4
      | code === Signal.pure 0x26#8 => Signal.pure 3#4
      | code === Signal.pure 0x25#8 => Signal.pure 4#4
      | code === Signal.pure 0x2e#8 => Signal.pure 5#4
      | code === Signal.pure 0x36#8 => Signal.pure 6#4
      | code === Signal.pure 0x3d#8 => Signal.pure 7#4
      | code === Signal.pure 0x3e#8 => Signal.pure 8#4
      | code === Signal.pure 0x46#8 => Signal.pure 9#4
  let valid : Signal dom (BitVec 1) :=
    hw_cond (Signal.pure 0#1)
      | code === Signal.pure 0x45#8 => Signal.pure 1#1
      | code === Signal.pure 0x16#8 => Signal.pure 1#1
      | code === Signal.pure 0x1e#8 => Signal.pure 1#1
      | code === Signal.pure 0x26#8 => Signal.pure 1#1
      | code === Signal.pure 0x25#8 => Signal.pure 1#1
      | code === Signal.pure 0x2e#8 => Signal.pure 1#1
      | code === Signal.pure 0x36#8 => Signal.pure 1#1
      | code === Signal.pure 0x3d#8 => Signal.pure 1#1
      | code === Signal.pure 0x3e#8 => Signal.pure 1#1
      | code === Signal.pure 0x46#8 => Signal.pure 1#1
  bundle2 out valid

#synthesizeVerilog prob114_bugs_case
