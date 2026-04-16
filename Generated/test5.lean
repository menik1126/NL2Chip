import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test defining helper as a def -/
def shift_helper (x : BitVec 100) : BitVec 100 := x >>> 1

def prob092_test5 {dom : DomainConfig}
    (inp : Signal dom (BitVec 100))
    : Signal dom (BitVec 100) :=
  
  inp.map shift_helper

#synthesizeVerilog prob092_test5
