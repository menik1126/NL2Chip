import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Combinational lookup table: maps 3-bit input a to 16-bit output q. -/
def prob126_circuit6 {dom : DomainConfig}
    (a : Signal dom (BitVec 3)) : Signal dom (BitVec 16) :=
  hw_cond (Signal.pure 12057#16)
    | (a === Signal.pure 0#3) => Signal.pure 4658#16
    | (a === Signal.pure 1#3) => Signal.pure 44768#16
    | (a === Signal.pure 2#3) => Signal.pure 10196#16
    | (a === Signal.pure 3#3) => Signal.pure 23054#16
    | (a === Signal.pure 4#3) => Signal.pure 8294#16
    | (a === Signal.pure 5#3) => Signal.pure 25806#16
    | (a === Signal.pure 6#3) => Signal.pure 50470#16

#synthesizeVerilog prob126_circuit6
