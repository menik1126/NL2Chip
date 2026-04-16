import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- PS/2 keyboard scancode decoder: maps 16-bit scancode to arrow key outputs.
    Output is packed BitVec 4: {left, down, right, up} = out[3:0] -/
def prob106_always_nolatches {dom : DomainConfig}
    (scancode : Signal dom (BitVec 16))
    : Signal dom (BitVec 4) :=
  let isLeft  := scancode === Signal.pure 0xe06b#16
  let isDown  := scancode === Signal.pure 0xe072#16
  let isRight := scancode === Signal.pure 0xe074#16
  let isUp    := scancode === Signal.pure 0xe075#16
  -- Build packed output {left, down, right, up}
  -- Using priority mux: only one can be true at a time
  hw_cond (Signal.pure 0b0000#4)
    | isLeft  => Signal.pure 0b1000#4
    | isDown  => Signal.pure 0b0100#4
    | isRight => Signal.pure 0b0010#4
    | isUp    => Signal.pure 0b0001#4

#synthesizeVerilog prob106_always_nolatches
