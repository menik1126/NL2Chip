import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Thermostat controller: controls heater, air conditioner, and fan based on mode and temperature. -/
def prob072_thermostat {dom : DomainConfig}
    (mode too_cold too_hot fan_on : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  let heater := mode &&& too_cold
  let aircon := (~~~mode) &&& too_hot
  let fan := (heater ||| aircon) ||| fan_on
  bundle2 heater (bundle2 aircon fan)

#synthesizeVerilog prob072_thermostat
