import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Combinational circuit: select output based on c.
    c=0 → b, c=1 → e, c=2 → a, c=3 → d, default → 0xF -/
def prob130_circuit5 {dom : DomainConfig}
    (a b c d e : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  hw_cond (Signal.pure 0xF#4)
  | (c === Signal.pure 0#4) => b
  | (c === Signal.pure 1#4) => e
  | (c === Signal.pure 2#4) => a
  | (c === Signal.pure 3#4) => d

#synthesizeVerilog prob130_circuit5
