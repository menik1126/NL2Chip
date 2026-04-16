import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Bugfix for always_if2: adds else clauses to prevent latches -/
def prob132_always_if2 {dom : DomainConfig}
    (cpu_overheated : Signal dom (BitVec 1))
    (arrived : Signal dom (BitVec 1))
    (gas_tank_empty : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  -- shut_off_computer = cpu_overheated (else 0)
  let shut_off_computer := cpu_overheated
  -- keep_driving = ~arrived ? ~gas_tank_empty : 0
  -- Equivalent: (~arrived) AND (~gas_tank_empty)
  let keep_driving := (~~~arrived) &&& (~~~gas_tank_empty)
  bundle2 shut_off_computer keep_driving

#synthesizeVerilog prob132_always_if2
