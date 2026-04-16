import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-bit shift register with active-low synchronous reset.
    Data shifts in from `in_` at the LSB each clock cycle.
    Output is the MSB of the 4-bit shift register.
    When resetn is low (active), the register is synchronously cleared to 0. -/
def prob060_m2014_q4k {dom : DomainConfig}
    (resetn : Signal dom Bool) (in_ : Signal dom (BitVec 1))
    : Signal dom (BitVec 1) :=
  -- Build 4-bit shift register using chained registers
  -- sr[0] = first stage (oldest), sr[3] = last stage (newest = output)
  -- Reference: sr <= {sr[2:0], in}  =>  new sr[3] = old sr[2], ..., sr[0] = in
  let d0 : Signal dom (BitVec 1) :=
    Signal.mux resetn in_ (Signal.pure 0#1)
  let q0 : Signal dom (BitVec 1) := Signal.register 0#1 d0
  let d1 : Signal dom (BitVec 1) :=
    Signal.mux resetn q0 (Signal.pure 0#1)
  let q1 : Signal dom (BitVec 1) := Signal.register 0#1 d1
  let d2 : Signal dom (BitVec 1) :=
    Signal.mux resetn q1 (Signal.pure 0#1)
  let q2 : Signal dom (BitVec 1) := Signal.register 0#1 d2
  let d3 : Signal dom (BitVec 1) :=
    Signal.mux resetn q2 (Signal.pure 0#1)
  Signal.register 0#1 d3

#synthesizeVerilog prob060_m2014_q4k
