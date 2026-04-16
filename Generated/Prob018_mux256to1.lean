import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

set_option exponentiation.threshold 2048 in
set_option maxRecDepth 2048 in
/-- 1-bit wide, 256-to-1 multiplexer: selects bit in[sel] from a 256-bit input vector.
    sel=0 selects in[0], sel=1 selects in[1], etc.
    Implementation: extend sel to 256 bits, shift inp right by sel positions, extract bit 0. -/
def prob018_mux256to1 {dom : DomainConfig}
    (inp : Signal dom (BitVec 256)) (sel : Signal dom (BitVec 8))
    : Signal dom (BitVec 1) :=
  -- Zero-extend sel to 256 bits for the shift operation
  let sel256 : Signal dom (BitVec 256) := Signal.map (fun s => BitVec.zeroExtend 256 s) sel
  -- Shift inp right by sel positions
  let shifted : Signal dom (BitVec 256) := inp >>> sel256
  -- Extract the bottom 1 bit
  Signal.map (fun v => BitVec.extractLsb' 0 1 v) shifted

set_option exponentiation.threshold 2048 in
set_option maxRecDepth 2048 in
#synthesizeVerilog prob018_mux256to1
