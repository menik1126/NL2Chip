import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Fixes the latch bug by providing explicit else branches for both outputs -/
def prob132_always_if2 {dom : DomainConfig}
    (cpu_overheated : Signal dom Bool)
    (arrived : Signal dom Bool)
    (gas_tank_empty : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1) :=
  let shut_off_computer := Signal.mux cpu_overheated (Signal.pure 1#1) (Signal.pure 0#1)
  let keep_driving := Signal.mux arrived 
    (Signal.pure 0#1) 
    (Signal.mux gas_tank_empty (Signal.pure 0#1) (Signal.pure 1#1))
  bundle2 shut_off_computer keep_driving

#synthesizeVerilog prob132_always_if2
