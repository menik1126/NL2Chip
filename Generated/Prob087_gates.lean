import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Combinational circuit with 7 logic gates: AND, OR, XOR, NAND, NOR, XNOR, and A-AND-NOT-B. -/
def prob087_gates {dom : DomainConfig}
    (a b : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1 × BitVec 1 × BitVec 1 × BitVec 1 × BitVec 1) :=
  let out_and   := a &&& b
  let out_or    := a ||| b
  let out_xor   := a ^^^ b
  let out_nand  := ~~~(a &&& b)
  let out_nor   := ~~~(a ||| b)
  let out_xnor  := ~~~(a ^^^ b)
  let out_anotb := a &&& (~~~b)
  bundle2 out_and (bundle2 out_or (bundle2 out_xor (bundle2 out_nand (bundle2 out_nor (bundle2 out_xnor out_anotb)))))

#synthesizeVerilog prob087_gates
