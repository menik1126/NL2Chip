import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Cellphone ringer/motor controller: when ring=1, turn on ringer (if not vibrate_mode) or motor (if vibrate_mode). -/
def prob036_ringer {dom : DomainConfig}
    (ring vibrate_mode : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let ringer := ring &&& (~~~vibrate_mode)
  let motor  := ring &&& vibrate_mode
  bundle2 ringer motor

#synthesizeVerilog prob036_ringer
