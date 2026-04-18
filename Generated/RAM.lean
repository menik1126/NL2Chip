import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Dual-port RAM with depth 8 and width 6 bits.
    Supports simultaneous read and write operations.
    Read data is registered and outputs 0 when read_en is inactive. -/
def RAM {dom : DomainConfig}
    (rst_n : Signal dom Bool)
    (write_en : Signal dom Bool)
    (write_addr : Signal dom (BitVec 8))
    (write_data : Signal dom (BitVec 6))
    (read_en : Signal dom Bool)
    (read_addr : Signal dom (BitVec 8))
    : Signal dom (BitVec 6) :=
  -- Extract lower 3 bits for actual addressing (depth = 8 = 2^3)
  let write_addr_3 := Signal.map (fun a => a.truncate 3) write_addr
  let read_addr_3 := Signal.map (fun a => a.truncate 3) read_addr
  
  -- Memory: 3-bit address, 6-bit data
  let mem_out := Signal.memory write_addr_3 write_data write_en read_addr_3
  
  -- Register the read output with conditional enable
  -- rst_n is active-low: when rst_n=0, reset; when rst_n=1, normal operation
  Signal.loop fun read_data =>
    let next := Signal.mux rst_n
      (Signal.mux read_en mem_out (Signal.pure 0#6))  -- rst_n=1: normal operation
      (Signal.pure 0#6)                                -- rst_n=0: reset
    Signal.register 0#6 next

#synthesizeVerilog RAM
