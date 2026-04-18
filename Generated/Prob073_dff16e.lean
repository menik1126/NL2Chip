import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 16-bit register with byte-enable control and active-low synchronous reset.
    byteena[0] controls lower byte d[7:0], byteena[1] controls upper byte d[15:8]. -/
def prob073_dff16e {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (byteena : Signal dom (BitVec 2))
    (d : Signal dom (BitVec 16)) : Signal dom (BitVec 16) :=
  Signal.loop fun (q : Signal dom (BitVec 16)) =>
    -- Extract byte enable bits
    let en0 := (byteena &&& 1#2) === 1#2  -- byteena[0]
    let en1 := (byteena >>> 1#2) === 1#2  -- byteena[1]
    
    -- Extract bytes from q and d
    let q_lo := q &&& 0xFF#16
    let q_hi := (q >>> 8#16) &&& 0xFF#16
    let d_lo := d &&& 0xFF#16
    let d_hi := (d >>> 8#16) &&& 0xFF#16
    
    -- Select new byte values based on byte enable
    let new_lo := Signal.mux en0 d_lo q_lo
    let new_hi := Signal.mux en1 d_hi q_hi
    
    -- Combine bytes back into 16-bit value
    let next_q := (new_hi <<< 8#16) ||| new_lo
    
    -- Apply reset (active-low)
    let final_q := Signal.mux resetn next_q (Signal.pure 0#16)
    
    Signal.register 0#16 final_q

#synthesizeVerilog prob073_dff16e
