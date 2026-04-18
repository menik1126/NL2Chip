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
  -- State: [18:16] = SP (3 bits), [15:0] = memory (4x4 bits)
  let state_reg := Signal.loop fun (state : Signal dom (BitVec 19)) =>
    -- Extract SP: shift right by 16 and mask to 3 bits
    let sp := (state >>> 16#19) &&& 0x7#19
    
    -- Extract memory: mask lower 16 bits
    let mem := state &&& 0xFFFF#19
    
    -- Compute flags
    let sp_ge_4 := sp === 0x4#19
    let empty_flag := Signal.mux sp_ge_4 (Signal.pure 1#1) (Signal.pure 0#1)
    let sp_is_zero := sp === 0x0#19
    let full_flag := Signal.mux sp_is_zero (Signal.pure 1#1) (Signal.pure 0#1)
    
    -- Read operation: increment SP
    let sp_after_read := (sp + 0x1#19) &&& 0x7#19
    let state_after_read := (sp_after_read <<< 16#19) ||| mem
    
    -- Write operation: decrement SP and write data to memory
    let sp_after_write := (sp - 0x1#19) &&& 0x7#19
    -- Write dataIn to memory at sp_after_write position
    let dataIn_ext := Signal.map (fun x : BitVec 4 => x.zeroExtend 19) dataIn
    let mem_with_write := Signal.mux (sp_after_write === 0x0#19)
      ((mem &&& 0xFFF0#19) ||| dataIn_ext)
      (Signal.mux (sp_after_write === 0x1#19)
        ((mem &&& 0xFF0F#19) ||| (dataIn_ext <<< 4#19))
        (Signal.mux (sp_after_write === 0x2#19)
          ((mem &&& 0xF0FF#19) ||| (dataIn_ext <<< 8#19))
          (Signal.mux (sp_after_write === 0x3#19)
            ((mem &&& 0x0FFF#19) ||| (dataIn_ext <<< 12#19))
            mem)))
    let state_after_write := (sp_after_write <<< 16#19) ||| mem_with_write
    
    let state_next := Signal.mux RW
      (Signal.mux (empty_flag === Signal.pure 1#1) state state_after_read)
      (Signal.mux (full_flag === Signal.pure 1#1) state state_after_write)
    
    let state_with_rst := Signal.mux Rst (Signal.pure 0x40000#19) state_next
    let state_with_en := Signal.mux EN state_with_rst state
    
    Signal.register 0x40000#19 state_with_en
  
  -- Extract outputs
  let out_sp := (state_reg >>> 16#19) &&& 0x7#19
  let out_sp_ge_4 := out_sp === 0x4#19
  let out_empty := Signal.mux out_sp_ge_4 (Signal.pure 1#1) (Signal.pure 0#1)
  let out_sp_is_zero := out_sp === 0x0#19
  let out_full := Signal.mux out_sp_is_zero (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Read data from memory at SP position
  let mem_out := state_reg &&& 0xFFFF#19
  let data_0 := mem_out &&& 0xF#19
  let data_1 := (mem_out >>> 4#19) &&& 0xF#19
  let data_2 := (mem_out >>> 8#19) &&& 0xF#19
  let data_3 := (mem_out >>> 12#19) &&& 0xF#19
  let out_data_19 := Signal.mux (out_sp === 0x0#19) data_0
    (Signal.mux (out_sp === 0x1#19) data_1
      (Signal.mux (out_sp === 0x2#19) data_2
        (Signal.mux (out_sp === 0x3#19) data_3 (Signal.pure 0#19))))
  let out_data := Signal.map (fun x : BitVec 19 => x.truncate 4) out_data_19
  
  bundle2 (bundle2 out_empty out_full) out_data

#synthesizeVerilog LIFObuffer
