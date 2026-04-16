import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test simple shift register -/
def test_shift {dom : DomainConfig}
    (enable : Signal dom Bool)
    (S : Signal dom (BitVec 1))
    : Signal dom (BitVec 8) :=
  Signal.loop fun (qReg : Signal dom (BitVec 8)) =>
    let S8 := Signal.map (fun (s : BitVec 1) => s.zeroExtend 8) S
    let shifted := (qReg <<< 1#8) ||| S8
    let nextQ := Signal.mux enable shifted qReg
    Signal.register 0#8 nextQ

#synthesizeVerilog test_shift
