import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Square wave generator: produces a square wave signal toggling at the specified frequency.
    Takes an 8-bit frequency control and outputs a 1-bit wave signal.
    The wave toggles every 'freq' clock cycles. -/
def square_wave {dom : DomainConfig}
    (freq : Signal dom (BitVec 8)) : Signal dom (BitVec 1) :=
  -- Counter register
  let count : Signal dom (BitVec 8) :=
    Signal.loop fun (count : Signal dom (BitVec 8)) =>
      let atMax := count === (freq - 1#8)
      let nextCount := Signal.mux atMax (Signal.pure 0#8) (count + 1#8)
      Signal.register 0#8 nextCount
  
  -- Wave output register
  let wave : Signal dom (BitVec 1) :=
    Signal.loop fun (wave : Signal dom (BitVec 1)) =>
      let atMax := count === (freq - 1#8)
      let nextWave := Signal.mux atMax (~~~wave) wave
      Signal.register 0#1 nextWave
  
  wave

#synthesizeVerilog square_wave
