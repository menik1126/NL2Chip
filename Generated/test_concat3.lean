import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_concat3 {dom : DomainConfig}
    (a b : Signal dom (BitVec 5))
    : Signal dom (BitVec 10) :=
  let ab := bundle2 a b
  let result := Signal.map (fun x => 
    let a := x.1
    let b := x.2
    let a10 := a.zeroExtend 10
    let b10 := b.zeroExtend 10
    (a10 <<< b10) ||| b10) ab
  result

#synthesizeVerilog test_concat3
