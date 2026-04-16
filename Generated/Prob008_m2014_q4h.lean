import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Wire: passes the 1-bit input directly to the output (combinational assignment). -/
def prob008_m2014_q4h {dom : DomainConfig}
    (in_ : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  in_

#synthesizeVerilog prob008_m2014_q4h
