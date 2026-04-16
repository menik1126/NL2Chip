import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 16-bit wide 9-to-1 multiplexer: sel=0 selects a, sel=1 selects b, ..., sel=8 selects i.
    For unused cases sel=9 to 15, output is all 1s (0xFFFF). -/
def prob097_mux9to1v {dom : DomainConfig}
    (a b c d e f g h i : Signal dom (BitVec 16))
    (sel : Signal dom (BitVec 4))
    : Signal dom (BitVec 16) :=
  hw_cond (Signal.pure 0xFFFF#16)
    | sel === Signal.pure 0#4 => a
    | sel === Signal.pure 1#4 => b
    | sel === Signal.pure 2#4 => c
    | sel === Signal.pure 3#4 => d
    | sel === Signal.pure 4#4 => e
    | sel === Signal.pure 5#4 => f
    | sel === Signal.pure 6#4 => g
    | sel === Signal.pure 7#4 => h
    | sel === Signal.pure 8#4 => i

#synthesizeVerilog prob097_mux9to1v
