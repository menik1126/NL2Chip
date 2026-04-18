import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Instruction register with two 8-bit registers, conditional fetch based on control signal -/
def instr_reg {dom : DomainConfig}
    (rst : Signal dom Bool)
    (fetch : Signal dom (BitVec 2))
    (data : Signal dom (BitVec 8))
    : Signal dom ((BitVec 3 × BitVec 5) × BitVec 8) :=
  -- Use a 16-bit register to hold both 8-bit values: [ins_p1 : ins_p2]
  let state := Signal.loop fun (s : Signal dom (BitVec 16)) =>
    -- Extract current values
    let curr_p1 := s >>> 8#16  -- High 8 bits
    let curr_p2 := s &&& 255#16  -- Low 8 bits (mask with 0xFF)
    
    -- Extend data to 16 bits for comparison
    let data16 := Signal.map (fun x => BitVec.zeroExtend 16 x) data
    
    -- Determine next values based on fetch signal
    let fetch_is_01 := fetch === 1#2
    let fetch_is_10 := fetch === 2#2
    
    let next_p1 := Signal.mux fetch_is_01 data16 curr_p1
    let next_p2 := Signal.mux fetch_is_10 data16 curr_p2
    
    -- Active-low reset: when rst is false (0), reset to zero
    let reset_p1 := Signal.mux rst next_p1 (Signal.pure 0#16)
    let reset_p2 := Signal.mux rst next_p2 (Signal.pure 0#16)
    
    -- Combine back into 16-bit value: [p1 : p2]
    let next_state := (reset_p1 <<< 8#16) ||| reset_p2
    
    Signal.register 0#16 next_state
  
  -- Extract outputs from the state
  let ins_p1 := state >>> 8#16  -- High 8 bits
  let ins_p2 := state &&& 255#16  -- Low 8 bits
  
  -- ins = ins_p1[7:5] (high 3 bits of ins_p1) - shift right by 5 more
  let ins := Signal.map (fun x => BitVec.truncate 3 x) (ins_p1 >>> 5#16)
  -- ad1 = ins_p1[4:0] (low 5 bits of ins_p1)
  let ad1 := Signal.map (fun x => BitVec.truncate 5 x) ins_p1
  -- ad2 = ins_p2 (low 8 bits)
  let ad2 := Signal.map (fun x => BitVec.truncate 8 x) ins_p2
  
  bundle2 (bundle2 ins ad1) ad2

#synthesizeVerilog instr_reg
