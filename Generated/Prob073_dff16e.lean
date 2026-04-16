import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 16-bit register with byte-enable and synchronous active-low reset.
    byteena[1] controls upper byte d[15:8], byteena[0] controls lower byte d[7:0].
    resetn is synchronous active-low reset (resets to 0 when low). -/
def prob073_dff16e {dom : DomainConfig}
    (resetn  : Signal dom Bool)
    (byteena : Signal dom (BitVec 2))
    (d       : Signal dom (BitVec 16))
    : Signal dom (BitVec 16) :=
  Signal.loop fun (q : Signal dom (BitVec 16)) =>
    -- Extract byte enable bits as Bool signals
    let en0 : Signal dom Bool := (byteena &&& 1#2) === 1#2
    let en1 : Signal dom Bool := (byteena &&& 2#2) === 2#2
    -- Extract lower byte from d and q
    let d_lo : Signal dom (BitVec 8) := Signal.map (fun v => BitVec.extractLsb' 0 8 v) d
    let d_hi : Signal dom (BitVec 8) := Signal.map (fun v => BitVec.extractLsb' 8 8 v) d
    let q_lo : Signal dom (BitVec 8) := Signal.map (fun v => BitVec.extractLsb' 0 8 v) q
    let q_hi : Signal dom (BitVec 8) := Signal.map (fun v => BitVec.extractLsb' 8 8 v) q
    -- Compute next lower byte: if !resetn → 0, elif en0 → d_lo, else → q_lo
    let next_lo := Signal.mux resetn
      (Signal.mux en0 d_lo q_lo)
      (Signal.pure 0#8)
    -- Compute next upper byte: if !resetn → 0, elif en1 → d_hi, else → q_hi
    let next_hi := Signal.mux resetn
      (Signal.mux en1 d_hi q_hi)
      (Signal.pure 0#8)
    -- Concatenate upper ++ lower to form 16-bit next value
    let next : Signal dom (BitVec 16) := next_hi ++ next_lo
    Signal.register 0#16 next

#synthesizeVerilog prob073_dff16e
