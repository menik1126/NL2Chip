import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-input AND/OR/XOR gates: reduces a 4-bit input with AND, OR, and XOR. -/
def prob051_gates4 {dom : DomainConfig}
    (in_ : Signal dom (BitVec 4))
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  -- Extract individual bits
  let b0 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 0 1) in_
  let b1 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 1 1) in_
  let b2 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 2 1) in_
  let b3 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 3 1) in_
  -- AND reduce: all bits must be 1
  let out_and := b0 &&& b1 &&& b2 &&& b3
  -- OR reduce: any bit must be 1
  let out_or  := b0 ||| b1 ||| b2 ||| b3
  -- XOR reduce: parity of all bits
  let out_xor := b0 ^^^ b1 ^^^ b2 ^^^ b3
  bundle2 out_and (bundle2 out_or out_xor)

#synthesizeVerilog prob051_gates4
