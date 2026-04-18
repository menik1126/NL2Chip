/-
  VerilogEval Prob122: Karnaugh Map 4-variable

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input  a
   - input  b
   - input  c
   - input  d
   - output out

  The module should implement the Karnaugh map below.

             ab
  cd   00  01  11  10
  00 | 0 | 1 | 0 | 1 |
  01 | 1 | 0 | 1 | 0 |
  11 | 0 | 1 | 0 | 1 |
  10 | 1 | 0 | 1 | 0 |

  Reference Verilog:
  module RefModule (input a, input b, input c, input d, output reg out);
    always @(*) begin
      case({a,b,c,d})
        4'h0: out = 0; 4'h1: out = 1; 4'h3: out = 0; 4'h2: out = 1;
        4'h4: out = 1; 4'h5: out = 0; 4'h7: out = 1; 4'h6: out = 0;
        4'hc: out = 0; 4'hd: out = 1; 4'hf: out = 0; 4'he: out = 1;
        4'h8: out = 1; 4'h9: out = 0; 4'hb: out = 1; 4'ha: out = 0;
      endcase
    end
  endmodule
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Karnaugh map implementation: out = b XOR c XOR d -/
def prob122_kmap4 {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  b ^^^ c ^^^ d

#synthesizeVerilog prob122_kmap4
