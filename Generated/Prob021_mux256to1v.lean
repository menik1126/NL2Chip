import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

set_option exponentiation.threshold 2048 in
set_option maxRecDepth 2048 in
/-- 4-bit wide 256-to-1 multiplexer: selects 4 bits from a 1024-bit input vector based on an 8-bit selector.
    sel=0 selects in[3:0], sel=1 selects in[7:4], sel=2 selects in[11:8], etc.
    Implementation: extend sel to 1024 bits, shift left by 2 to get bit offset (sel*4),
    then shift inp right by that amount and extract bottom 4 bits. -/
def prob021_mux256to1v {dom : DomainConfig}
    (inp : Signal dom (BitVec 1024)) (sel : Signal dom (BitVec 8))
    : Signal dom (BitVec 4) :=
  -- Extend sel to 1024 bits
  let sel1024 : Signal dom (BitVec 1024) := Signal.map (fun s => BitVec.zeroExtend 1024 s) sel
  -- Shift left by 2 to get bit offset (sel * 4)
  let bitOffset : Signal dom (BitVec 1024) := sel1024 <<< (2 : BitVec 1024)
  -- Shift inp right by bitOffset positions
  let shifted : Signal dom (BitVec 1024) := inp >>> bitOffset
  -- Extract the bottom 4 bits
  Signal.map (fun v => BitVec.extractLsb' 0 4 v) shifted

set_option exponentiation.threshold 2048 in
set_option maxRecDepth 2048 in
#synthesizeVerilog prob021_mux256to1v
