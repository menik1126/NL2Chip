import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Dual-port RAM with depth 8, width 6 bits, active-low reset -/
def RAM {dom : DomainConfig}
    (rst_n : Signal dom Bool)
    (write_en : Signal dom Bool)
    (write_addr : Signal dom (BitVec 8))
    (write_data : Signal dom (BitVec 6))
    (read_en : Signal dom Bool)
    (read_addr : Signal dom (BitVec 8))
    : Signal dom (BitVec 6) :=
  -- Use only lower 3 bits of address for depth 8
  let write_addr_3 := Signal.map (fun a => a.truncate 3) write_addr
  let read_addr_3 := Signal.map (fun a => a.truncate 3) read_addr
  
  -- Memory with registered read (initialized to all zeros)
  let mem_out := Signal.memory write_addr_3 write_data write_en read_addr_3
  
  -- Register the read output with enable control and reset
  Signal.loop fun read_data_reg =>
    let next_read_data := Signal.mux rst_n
      (Signal.mux read_en mem_out (Signal.pure 0#6))  -- Normal operation
      (Signal.pure 0#6)  -- Reset active (rst_n = 0)
    Signal.register 0#6 next_read_data

#synthesizeVerilog RAM
