import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-bit adder with overflow bit -/
def prob016_m2014_q4j {dom : DomainConfig}
    (x y : Signal dom (BitVec 4))
    : Signal dom (BitVec 5) :=
  let x5 := Signal.map (fun v => v.zeroExtend 5) x
  let y5 := Signal.map (fun v => v.zeroExtend 5) y
  x5 + y5

#synthesizeVerilog prob016_m2014_q4j
