import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 64-bit arithmetic shift register with synchronous load.
    load: loads data into the register.
    ena: enables shifting.
    amount: 00=shift left 1, 01=shift left 8, 10=arith shift right 1, 11=arith shift right 8. -/
def prob115_shift18 {dom : DomainConfig}
    (load : Signal dom Bool)
    (ena : Signal dom Bool)
    (amount : Signal dom (BitVec 2))
    (data : Signal dom (BitVec 64))
    : Signal dom (BitVec 64) :=
  Signal.loop fun (q : Signal dom (BitVec 64)) =>
    -- Compute all four shift results
    -- 2'b00: shift left by 1
    let shl1 : Signal dom (BitVec 64) := q <<< (1#64 : BitVec 64)
    -- 2'b01: shift left by 8
    let shl8 : Signal dom (BitVec 64) := q <<< (8#64 : BitVec 64)
    -- 2'b10: arithmetic shift right by 1 (sign extend q[63])
    let shr1 : Signal dom (BitVec 64) := Signal.map (fun v => BitVec.sshiftRight v 1) q
    -- 2'b11: arithmetic shift right by 8 (sign extend q[63])
    let shr8 : Signal dom (BitVec 64) := Signal.map (fun v => BitVec.sshiftRight v 8) q
    -- Mux based on amount[1:0]
    let amt0 := amount === (Signal.pure 0#2)
    let amt1 := amount === (Signal.pure 1#2)
    let amt2 := amount === (Signal.pure 2#2)
    -- priority: 00 -> shl1, 01 -> shl8, 10 -> shr1, else -> shr8
    let shifted := hw_cond shr8
                    | amt0 => shl1
                    | amt1 => shl8
                    | amt2 => shr1
    -- Compute next value: load overrides, then ena selects shift vs hold
    let afterEna := Signal.mux ena shifted q
    let nextVal := Signal.mux load data afterEna
    Signal.register 0#64 nextVal

#synthesizeVerilog prob115_shift18
