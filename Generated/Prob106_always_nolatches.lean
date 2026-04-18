import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- PS/2 keyboard scancode decoder for arrow keys -/
def prob106_always_nolatches {dom : DomainConfig}
    (scancode : Signal dom (BitVec 16))
    : Signal dom ((BitVec 1 × BitVec 1) × (BitVec 1 × BitVec 1)) :=
  let left  := Signal.mux (scancode === 0xe06b#16) (Signal.pure 1#1) (Signal.pure 0#1)
  let down  := Signal.mux (scancode === 0xe072#16) (Signal.pure 1#1) (Signal.pure 0#1)
  let right := Signal.mux (scancode === 0xe074#16) (Signal.pure 1#1) (Signal.pure 0#1)
  let up    := Signal.mux (scancode === 0xe075#16) (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 (bundle2 left down) (bundle2 right up)

#synthesizeVerilog prob106_always_nolatches
