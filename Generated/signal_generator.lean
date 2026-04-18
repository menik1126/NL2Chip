import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Triangle wave signal generator: cycles between 0 and 31 -/
def signal_generator {dom : DomainConfig}
    (rst_n : Signal dom Bool) : Signal dom (BitVec 5) :=
  let packed := Signal.loop fun (p : Signal dom (BitVec 6)) =>
    -- p encodes: bit 5 = state (0=inc, 1=dec), bits 4:0 = wave value
    
    -- Check boundaries
    let atMaxInc := p === 31#6
    let atMinDec := p === 32#6
    
    -- Check if currently decrementing (p in [33, 62])
    let d1 := (p === 33#6) ||| (p === 34#6) ||| (p === 35#6) ||| (p === 36#6)
    let d2 := (p === 37#6) ||| (p === 38#6) ||| (p === 39#6) ||| (p === 40#6)
    let d3 := (p === 41#6) ||| (p === 42#6) ||| (p === 43#6) ||| (p === 44#6)
    let d4 := (p === 45#6) ||| (p === 46#6) ||| (p === 47#6) ||| (p === 48#6)
    let d5 := (p === 49#6) ||| (p === 50#6) ||| (p === 51#6) ||| (p === 52#6)
    let d6 := (p === 53#6) ||| (p === 54#6) ||| (p === 55#6) ||| (p === 56#6)
    let d7 := (p === 57#6) ||| (p === 58#6) ||| (p === 59#6) ||| (p === 60#6)
    let d8 := (p === 61#6) ||| (p === 62#6)
    let isDecrementing := d1 ||| d2 ||| d3 ||| d4 ||| d5 ||| d6 ||| d7 ||| d8
    
    let nextVal := Signal.mux atMaxInc
      (Signal.pure 32#6)
      (Signal.mux atMinDec
        (Signal.pure 1#6)
        (Signal.mux isDecrementing
          (p - 1#6)
          (p + 1#6)))
    
    let nextValReset := Signal.mux rst_n nextVal (Signal.pure 0#6)
    Signal.register 0#6 nextValReset
  
  Signal.map (fun x => BitVec.extractLsb' 0 5 x) packed

#synthesizeVerilog signal_generator
