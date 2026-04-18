import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Right shifter: 8-bit shift register that shifts right and inserts input at MSB -/
def right_shifter {dom : DomainConfig}
    (d : Signal dom (BitVec 1)) : Signal dom (BitVec 8) :=
  Signal.loop fun (q : Signal dom (BitVec 8)) =>
    let shifted := q >>> 1#8
    -- Insert d into bit 7 (MSB)
    -- We need to extend d to 8 bits and shift it left by 7
    let d_extended := Signal.map (fun (x : BitVec 1) => x.zeroExtend 8) d
    let d_at_msb := d_extended <<< 7#8
    let next := shifted ||| d_at_msb
    Signal.register 0#8 next

#synthesizeVerilog right_shifter
