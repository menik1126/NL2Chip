/-
  VerilogEval Prob022: 2-to-1 Multiplexer

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input  a
   - input  b
   - input  sel
   - output out
  The module should implement a one-bit wide, 2-to-1 multiplexer.
  When sel=0, choose a. When sel=1, choose b.

  Reference Verilog:
  module RefModule (input a, input b, input sel, output out);
    assign out = sel ? b : a;
  endmodule
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 2-to-1 multiplexer: sel=true selects b, sel=false selects a. -/
def prob022_mux2to1 {dom : DomainConfig}
    (a b : Signal dom (BitVec 1)) (sel : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  Signal.mux sel b a

#synthesizeVerilog prob022_mux2to1
