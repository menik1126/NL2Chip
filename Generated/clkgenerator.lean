import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Clock generator: toggles output every clock cycle (clock divider by 2) -/
def clkgenerator {dom : DomainConfig}
    : Signal dom (BitVec 1) :=
  Signal.loop fun (clk_out : Signal dom (BitVec 1)) =>
    let next := ~~~clk_out
    Signal.register 0#1 next

#synthesizeVerilog clkgenerator
