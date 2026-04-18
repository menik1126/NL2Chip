import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Multi-bit MUX-based synchronizer.
    Models a CDC synchronizer pattern with register stages for data and enable,
    followed by a two-stage synchronizer and output register. -/
def synchronizer {dom : DomainConfig}
    (data_in : Signal dom (BitVec 4))
    (data_en : Signal dom Bool)
    : Signal dom (BitVec 4) :=
  -- Stage 1: register data_in and data_en (conceptually in clock domain A)
  let data_reg := Signal.register 0#4 data_in
  let en_data_reg := Signal.register false data_en
  
  -- Stage 2-3: two-stage synchronizer for enable (conceptually in clock domain B)
  let en_clap_one := Signal.register false en_data_reg
  let en_clap_two := Signal.register false en_clap_one
  
  -- Stage 4: output register with mux (conceptually in clock domain B)
  Signal.loop fun dataout =>
    let next_out := Signal.mux en_clap_two data_reg dataout
    Signal.register 0#4 next_out

#synthesizeVerilog synchronizer
