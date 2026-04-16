import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Simple test without shifts -/
def simpleTest (g : BitVec 16) : BitVec 16 :=
  let r0 := 0#16
  let r1 := r0 ||| 1#16
  let r2 := r1 ||| 2#16
  r2

/-- Test function -/
def test_simple {dom : DomainConfig}
    (data : Signal dom (BitVec 16))
    : Signal dom (BitVec 16) :=
  Signal.map simpleTest data

#synthesizeVerilog test_simple
