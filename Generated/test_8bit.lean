import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test with 24-bit data -/
def test_24bit {dom : DomainConfig}
    (inp : Signal dom (BitVec 8))
    : Signal dom (BitVec 24 × BitVec 1) :=
  let stateAndData : Signal dom (BitVec 2 × BitVec 24) :=
    Signal.loop fun (sd : Signal dom (BitVec 2 × BitVec 24)) =>
      let state := Signal.map Prod.fst sd
      let data := Signal.map Prod.snd sd
      
      let nextState := state + 1#2
      let inp_ext := Signal.map (fun x : BitVec 8 => x.zeroExtend 24) inp
      let lower16 := data &&& 0xFFFF#24
      let nextData := (lower16 <<< 8#24) ||| inp_ext
      
      let regState := Signal.register 0#2 nextState
      let regData := Signal.register 0#24 nextData
      
      bundle2 regState regData
  
  let state := Signal.map Prod.fst stateAndData
  let data := Signal.map Prod.snd stateAndData
  let done := Signal.pure 1#1
  
  bundle2 data done

#synthesizeVerilog test_24bit
