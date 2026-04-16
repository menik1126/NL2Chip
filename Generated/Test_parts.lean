import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test 1: Just bitwise OR -/
def test1 {dom : DomainConfig}
    (a b : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  a ||| b

/-- Test 2: Logical OR -/
def test2 {dom : DomainConfig}
    (a b : Signal dom (BitVec 3))
    : Signal dom (BitVec 1) :=
  let a_nonzero := ~~~(a === (Signal.pure 0#3))
  let b_nonzero := ~~~(b === (Signal.pure 0#3))
  let out_or_logical_bool := a_nonzero ||| b_nonzero
  Signal.mux out_or_logical_bool (Signal.pure 1#1) (Signal.pure 0#1)

/-- Test 3: Concatenation -/
def test3 {dom : DomainConfig}
    (a b : Signal dom (BitVec 3))
    : Signal dom (BitVec 6) :=
  let not_a := ~~~a
  let not_b := ~~~b
  not_b ++ not_a

/-- Test 4: Bundle2 -/
def test4 {dom : DomainConfig}
    (a b : Signal dom (BitVec 3))
    : Signal dom (BitVec 3 × BitVec 1) :=
  let out1 := a ||| b
  let out2 := Signal.pure 1#1
  bundle2 out1 out2

#synthesizeVerilog test1
#synthesizeVerilog test2
#synthesizeVerilog test3
#synthesizeVerilog test4
