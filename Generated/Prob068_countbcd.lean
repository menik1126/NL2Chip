import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-digit BCD counter with synchronous active-high reset.
    q[3:0]=ones, q[7:4]=tens, q[11:8]=hundreds, q[15:12]=thousands.
    Output ena[2:0]: bit 0=ones==9, bit 1=tens&ones==99, bit 2=hunds&tens&ones==999. -/
def prob068_countbcd {dom : DomainConfig}
    (reset : Signal dom Bool) : Signal dom (BitVec 3 × BitVec 16) :=
  -- Keep a single 16-bit state register for all 4 BCD digits
  let q : Signal dom (BitVec 16) :=
    Signal.loop fun (q : Signal dom (BitVec 16)) =>
      -- Extract each 4-bit BCD digit using Signal.map with lambda
      let ones   : Signal dom (BitVec 4) := Signal.map (fun v => BitVec.extractLsb' 0  4 v) q
      let tens   : Signal dom (BitVec 4) := Signal.map (fun v => BitVec.extractLsb' 4  4 v) q
      let hunds  : Signal dom (BitVec 4) := Signal.map (fun v => BitVec.extractLsb' 8  4 v) q
      let thous  : Signal dom (BitVec 4) := Signal.map (fun v => BitVec.extractLsb' 12 4 v) q
      -- Enable signals (Bool)
      let ena1 : Signal dom Bool := ones  === (9#4 : BitVec 4)
      let ena2 : Signal dom Bool := ena1  &&& (tens  === (9#4 : BitVec 4))
      let ena3 : Signal dom Bool := ena2  &&& (hunds === (9#4 : BitVec 4))
      -- Next ones: always increments; wraps to 0 at 9
      let nextOnes  : Signal dom (BitVec 4) :=
        Signal.mux reset (Signal.pure 0#4)
          (Signal.mux ena1 (Signal.pure 0#4) (ones + 1#4))
      -- Next tens: increments when ena1; wraps at 9
      let nextTens  : Signal dom (BitVec 4) :=
        Signal.mux reset (Signal.pure 0#4)
          (Signal.mux ena1
            (Signal.mux (tens === (9#4 : BitVec 4)) (Signal.pure 0#4) (tens + 1#4))
            tens)
      -- Next hundreds: increments when ena2; wraps at 9
      let nextHunds : Signal dom (BitVec 4) :=
        Signal.mux reset (Signal.pure 0#4)
          (Signal.mux ena2
            (Signal.mux (hunds === (9#4 : BitVec 4)) (Signal.pure 0#4) (hunds + 1#4))
            hunds)
      -- Next thousands: increments when ena3; wraps at 9
      let nextThous : Signal dom (BitVec 4) :=
        Signal.mux reset (Signal.pure 0#4)
          (Signal.mux ena3
            (Signal.mux (thous === (9#4 : BitVec 4)) (Signal.pure 0#4) (thous + 1#4))
            thous)
      -- Pack back to 16 bits: {thous[3:0], hunds[3:0], tens[3:0], ones[3:0]}
      let nextQ : Signal dom (BitVec 16) :=
        (nextThous ++ nextHunds ++ nextTens ++ nextOnes : Signal dom (BitVec 16))
      Signal.register 0#16 nextQ
  -- Extract digit signals for enable computation
  let ones   : Signal dom (BitVec 4) := Signal.map (fun v => BitVec.extractLsb' 0  4 v) q
  let tens   : Signal dom (BitVec 4) := Signal.map (fun v => BitVec.extractLsb' 4  4 v) q
  let hunds  : Signal dom (BitVec 4) := Signal.map (fun v => BitVec.extractLsb' 8  4 v) q
  -- Compute enable bits as 1-bit signals, then pack to 3 bits
  -- ena[0] = q[3:0]==9, ena[1] = q[7:0]==0x99, ena[2] = q[11:0]==0x999
  let e1 : Signal dom Bool := ones  === (9#4 : BitVec 4)
  let e2 : Signal dom Bool := e1    &&& (tens  === (9#4 : BitVec 4))
  let e3 : Signal dom Bool := e2    &&& (hunds === (9#4 : BitVec 4))
  -- Convert Bool signals to 1-bit BitVec signals
  let e1bv : Signal dom (BitVec 1) := Signal.mux e1 (Signal.pure 1#1) (Signal.pure 0#1)
  let e2bv : Signal dom (BitVec 1) := Signal.mux e2 (Signal.pure 1#1) (Signal.pure 0#1)
  let e3bv : Signal dom (BitVec 1) := Signal.mux e3 (Signal.pure 1#1) (Signal.pure 0#1)
  -- Pack: ena = {e3, e2, e1} as 3-bit value
  let ena : Signal dom (BitVec 3) := (e3bv ++ e2bv ++ e1bv : Signal dom (BitVec 3))
  bundle2 ena q

#synthesizeVerilog prob068_countbcd
