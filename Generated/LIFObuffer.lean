import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- LIFO buffer: 4-bit wide, 4 entries deep stack with push/pop operations -/
def LIFObuffer {dom : DomainConfig}
    (dataIn : Signal dom (BitVec 4))
    (RW : Signal dom Bool)
    (EN : Signal dom Bool)
    (Rst : Signal dom Bool)
    : Signal dom ((BitVec 1 × BitVec 1) × BitVec 4) :=
  -- Use separate registers for each component
  -- SP register (3 bits)
  let sp := Signal.loop fun (q : Signal dom (BitVec 3)) =>
    let sp_bit2 := q >>> 2#3
    let sp_bit2_masked := sp_bit2 &&& 1#3
    let empty_flag := sp_bit2_masked === 1#3
    let full_flag := q === 0#3
    
    let not_empty := Signal.map (fun e : Bool => !e) empty_flag
    let can_read := RW &&& not_empty
    
    let not_rw := Signal.map (fun r : Bool => !r) RW
    let not_full := Signal.map (fun f : Bool => !f) full_flag
    let can_write := not_rw &&& not_full
    
    let sp_minus_1 := q - 1#3
    let sp_plus_1 := q + 1#3
    let sp_after_op := Signal.mux can_write sp_minus_1 (Signal.mux can_read sp_plus_1 q)
    let next_sp := Signal.mux Rst (Signal.pure 4#3) (Signal.mux EN sp_after_op q)
    
    Signal.register 4#3 next_sp
  
  -- Stack entry 0
  let stack0 := Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let sp_bit2 := sp >>> 2#3
    let sp_bit2_masked := sp_bit2 &&& 1#3
    let empty_flag := sp_bit2_masked === 1#3
    let full_flag := sp === 0#3
    
    let not_empty := Signal.map (fun e : Bool => !e) empty_flag
    let can_read := RW &&& not_empty
    
    let not_rw := Signal.map (fun r : Bool => !r) RW
    let not_full := Signal.map (fun f : Bool => !f) full_flag
    let can_write := not_rw &&& not_full
    
    let sp_minus_1 := sp - 1#3
    let write_to_0 := sp_minus_1 === 0#3
    let read_from_0 := sp === 0#3
    
    let should_write := EN &&& can_write &&& write_to_0
    let should_clear := EN &&& can_read &&& read_from_0
    
    let next_val := Signal.mux Rst (Signal.pure 0#4)
      (Signal.mux should_write dataIn
        (Signal.mux should_clear (Signal.pure 0#4) q))
    
    Signal.register 0#4 next_val
  
  -- Stack entry 1
  let stack1 := Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let sp_bit2 := sp >>> 2#3
    let sp_bit2_masked := sp_bit2 &&& 1#3
    let empty_flag := sp_bit2_masked === 1#3
    let full_flag := sp === 0#3
    
    let not_empty := Signal.map (fun e : Bool => !e) empty_flag
    let can_read := RW &&& not_empty
    
    let not_rw := Signal.map (fun r : Bool => !r) RW
    let not_full := Signal.map (fun f : Bool => !f) full_flag
    let can_write := not_rw &&& not_full
    
    let sp_minus_1 := sp - 1#3
    let write_to_1 := sp_minus_1 === 1#3
    let read_from_1 := sp === 1#3
    
    let should_write := EN &&& can_write &&& write_to_1
    let should_clear := EN &&& can_read &&& read_from_1
    
    let next_val := Signal.mux Rst (Signal.pure 0#4)
      (Signal.mux should_write dataIn
        (Signal.mux should_clear (Signal.pure 0#4) q))
    
    Signal.register 0#4 next_val
  
  -- Stack entry 2
  let stack2 := Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let sp_bit2 := sp >>> 2#3
    let sp_bit2_masked := sp_bit2 &&& 1#3
    let empty_flag := sp_bit2_masked === 1#3
    let full_flag := sp === 0#3
    
    let not_empty := Signal.map (fun e : Bool => !e) empty_flag
    let can_read := RW &&& not_empty
    
    let not_rw := Signal.map (fun r : Bool => !r) RW
    let not_full := Signal.map (fun f : Bool => !f) full_flag
    let can_write := not_rw &&& not_full
    
    let sp_minus_1 := sp - 1#3
    let write_to_2 := sp_minus_1 === 2#3
    let read_from_2 := sp === 2#3
    
    let should_write := EN &&& can_write &&& write_to_2
    let should_clear := EN &&& can_read &&& read_from_2
    
    let next_val := Signal.mux Rst (Signal.pure 0#4)
      (Signal.mux should_write dataIn
        (Signal.mux should_clear (Signal.pure 0#4) q))
    
    Signal.register 0#4 next_val
  
  -- Stack entry 3
  let stack3 := Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let sp_bit2 := sp >>> 2#3
    let sp_bit2_masked := sp_bit2 &&& 1#3
    let empty_flag := sp_bit2_masked === 1#3
    let full_flag := sp === 0#3
    
    let not_empty := Signal.map (fun e : Bool => !e) empty_flag
    let can_read := RW &&& not_empty
    
    let not_rw := Signal.map (fun r : Bool => !r) RW
    let not_full := Signal.map (fun f : Bool => !f) full_flag
    let can_write := not_rw &&& not_full
    
    let sp_minus_1 := sp - 1#3
    let write_to_3 := sp_minus_1 === 3#3
    let read_from_3 := sp === 3#3
    
    let should_write := EN &&& can_write &&& write_to_3
    let should_clear := EN &&& can_read &&& read_from_3
    
    let next_val := Signal.mux Rst (Signal.pure 0#4)
      (Signal.mux should_write dataIn
        (Signal.mux should_clear (Signal.pure 0#4) q))
    
    Signal.register 0#4 next_val
  
  -- dataOut register
  let dataOut := Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let sp_bit2 := sp >>> 2#3
    let sp_bit2_masked := sp_bit2 &&& 1#3
    let empty_flag := sp_bit2_masked === 1#3
    
    let not_empty := Signal.map (fun e : Bool => !e) empty_flag
    let can_read := RW &&& not_empty
    
    -- Determine which stack entry to read from
    let read_from_0 := sp === 0#3
    let read_from_1 := sp === 1#3
    let read_from_2 := sp === 2#3
    let read_from_3 := sp === 3#3
    let read_data := Signal.mux read_from_0 stack0
      (Signal.mux read_from_1 stack1
        (Signal.mux read_from_2 stack2
          (Signal.mux read_from_3 stack3 (Signal.pure 0#4))))
    
    let should_update := EN &&& can_read
    let next_val := Signal.mux Rst (Signal.pure 0#4)
      (Signal.mux should_update read_data q)
    
    Signal.register 0#4 next_val
  
  -- Compute output flags
  let sp_bit2_out := sp >>> 2#3
  let empty_flag_out := (sp_bit2_out &&& 1#3) === 1#3
  let empty_out := Signal.mux empty_flag_out (Signal.pure 1#1) (Signal.pure 0#1)
  
  let full_flag_out := sp === 0#3
  let full_out := Signal.mux full_flag_out (Signal.pure 1#1) (Signal.pure 0#1)
  
  let flags := bundle2 empty_out full_out
  bundle2 flags dataOut

#synthesizeVerilog LIFObuffer
