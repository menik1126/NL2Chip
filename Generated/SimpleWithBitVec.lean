import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev st0 : BitVec 3 := 0#3
private abbrev st1 : BitVec 3 := 1#3

def simple_with_bitvec {dom : DomainConfig}
    (s : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  let s0 : Signal dom Bool := Signal.map (fun x => x.getLsb 0) s
  Signal.loop fun (state : Signal dom (BitVec 3)) =>
    let next := Signal.mux s0 (Signal.pure st1) (Signal.pure st0)
    Signal.register st0 next

#synthesizeVerilog simple_with_bitvec
