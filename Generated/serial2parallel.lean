import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Serial to parallel converter: accumulates 8 serial bits into parallel output.
    Receives din_serial with din_valid control. After 8 valid inputs, outputs
    8-bit parallel data with dout_valid=1. Serial data fills MSB to LSB. -/
def serial2parallel {dom : DomainConfig}
    (din_serial : Signal dom (BitVec 1))
    (din_valid : Signal dom Bool)
    : Signal dom (BitVec 8 × BitVec 1) :=
  -- Counter state
  let cnt : Signal dom (BitVec 4) :=
    Signal.loop fun (cnt : Signal dom (BitVec 4)) =>
      let cnt_is_8 := cnt === 8#4
      let next_cnt := Signal.mux din_valid
        (Signal.mux cnt_is_8 (Signal.pure 0#4) (cnt + 1#4))
        (Signal.pure 0#4)
      Signal.register 0#4 next_cnt
  
  -- Shift register state
  let din_tmp : Signal dom (BitVec 8) :=
    Signal.loop fun (din_tmp : Signal dom (BitVec 8)) =>
      let cnt_is_8 := cnt === 8#4
      let cnt_le_7 := ~~~cnt_is_8
      let should_shift := din_valid &&& cnt_le_7
      -- Shift left and insert new bit
      let din_serial_ext := Signal.map (fun b => b.zeroExtend 8) din_serial
      let shifted := (din_tmp <<< 1#8) ||| din_serial_ext
      let next_tmp := Signal.mux should_shift shifted din_tmp
      Signal.register 0#8 next_tmp
  
  -- Output
  let cnt_is_8 := cnt === 8#4
  let dout_valid := Signal.mux cnt_is_8 (Signal.pure 1#1) (Signal.pure 0#1)
  let dout_parallel := Signal.mux cnt_is_8 din_tmp (Signal.pure 0#8)
  bundle2 dout_parallel dout_valid

#synthesizeVerilog serial2parallel
