import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_concat2 {dom : DomainConfig}
    (a b : Signal dom (BitVec 5))
    : Signal dom (BitVec 32) :=
  let ab := bundle2 a b
  let result := Signal.map (fun x => 
    let a := x.1
    let b := x.2
    let a32 := a.zeroExtend 32
    let b32 := b.zeroExtend 32
    (a32 <<< 27#32) ||| (b32 <<< 22#32) ||| 3#32) ab
  result

#synthesizeVerilog test_concat2
