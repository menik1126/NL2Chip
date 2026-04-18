import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Serial input data accumulator: accumulates 4 input values and outputs sum -/
def accu {dom : DomainConfig}
    (data_in : Signal dom (BitVec 8))
    (valid_in : Signal dom Bool)
    : Signal dom (Bool × BitVec 10) :=
  -- Counter (2 bits, counts 0-3)
  let counter := Signal.loop fun cnt =>
    let count_is_3 := cnt === 3#2
    let next_cnt := Signal.mux valid_in
      (Signal.mux count_is_3 0#2 (cnt + 1#2))
      cnt
    Signal.register 0#2 next_cnt
  
  -- Data output register (10 bits)
  let data_out := Signal.loop fun dout =>
    let counter_is_0 := counter === 0#2
    let data_in_ext : Signal dom (BitVec 10) := Signal.map (fun d : BitVec 8 => d.zeroExtend 10) data_in
    let next_dout := Signal.mux (valid_in &&& counter_is_0)
      data_in_ext
      (Signal.mux valid_in (dout + data_in_ext) dout)
    Signal.register 0#10 next_dout
  
  -- Valid output register (as BitVec 1, then convert to Bool)
  let valid_out_bv := Signal.loop fun vout =>
    let count_is_3 := counter === 3#2
    let end_cnt := valid_in &&& count_is_3
    let next_vout := Signal.mux end_cnt 1#1 0#1
    Signal.register 0#1 next_vout
  
  -- Convert BitVec 1 to Bool
  let valid_out : Signal dom Bool := valid_out_bv === 1#1
  
  bundle2 valid_out data_out

#synthesizeVerilog accu
