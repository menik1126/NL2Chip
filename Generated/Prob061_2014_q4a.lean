import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- One stage of an n-bit shift register with parallel load.
    L=load (takes R), E=shift enable (takes w from prev stage), Q=output.
    Priority: L overrides E. If neither, Q holds. -/
def prob061_2014_q4a {dom : DomainConfig}
    (w : Signal dom Bool)
    (R : Signal dom Bool)
    (E : Signal dom Bool)
    (L : Signal dom Bool)
    : Signal dom Bool :=
  Signal.loop fun (q : Signal dom Bool) =>
    -- If L is set, load R; else if E is set, shift in w; else hold q
    let next := Signal.mux L R (Signal.mux E w q)
    Signal.register false next

#synthesizeVerilog prob061_2014_q4a
