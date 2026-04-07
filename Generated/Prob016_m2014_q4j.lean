import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-bit adder with overflow: adds two 4-bit inputs, returns 5-bit sum. -/
def prob016_m2014_q4j {dom : DomainConfig}
    (x y : Signal dom (BitVec 4))
    : Signal dom (BitVec 5) :=
  -- Cast inputs to 5-bit to handle overflow, then add
  let x_ext := Signal.map (fun v => v.zeroExtend 5) x
  let y_ext := Signal.map (fun v => v.zeroExtend 5) y
  x_ext + y_ext

#synthesizeVerilog prob016_m2014_q4j