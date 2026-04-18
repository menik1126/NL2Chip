import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Module A: z = (x^y) & x -/
def moduleA {dom : DomainConfig}
    (x y : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  (x ^^^ y) &&& x

/-- Module B: registered version of Module A -/
def moduleB {dom : DomainConfig}
    (x y : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  Signal.register 1#1 (moduleA x y)

/-- Top-level module implementing z = x|~y using submodules -/
def prob131_mt2015_q4 {dom : DomainConfig}
    (x y : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  let a1 := moduleA x y
  let b1 := moduleB x y
  let a2 := moduleA x y
  let b2 := moduleB x y
  let or_out := a1 ||| b1
  let and_out := a2 &&& b2
  or_out ^^^ and_out

#synthesizeVerilog prob131_mt2015_q4
