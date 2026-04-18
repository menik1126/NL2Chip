import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Serial input data accumulator: accumulates 4 input values and outputs sum -/
def accu {dom : DomainConfig}
    (data_in : Signal dom (BitVec 8))
    (valid_in : Signal dom Bool)
    : Signal dom (Bool × BitVec 10) :=
  Signal.circuit do
    -- State registers
    let counter ← Signal.reg 0#2;
    let accum ← Signal.reg 0#10;
    
    -- Check conditions
    let at_count_3 := counter === 3#2;
    let at_count_0 := counter === 0#2;
    
    -- Next counter: reset to 0 if at count 3 and valid_in, else increment if valid_in
    let next_counter := Signal.mux valid_in
      (Signal.mux at_count_3 (Signal.pure 0#2) (counter + 1#2))
      counter;
    
    -- Extend data_in to 10 bits
    let data_in_extended := Signal.map (fun x => x.zeroExtend 10) data_in;
    
    -- Next accumulator: 
    -- If at count 0 and valid_in: start fresh with data_in
    -- If valid_in and not at count 0: add data_in to accumulator
    let next_accum := Signal.mux valid_in
      (Signal.mux at_count_0 
        data_in_extended
        (accum + data_in_extended))
      accum;
    
    -- Update registers
    counter <~ next_counter;
    accum <~ next_accum;
    
    -- Outputs
    let valid_out := at_count_3 &&& valid_in;
    return bundle2 valid_out accum

#synthesizeVerilog accu
