import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- ROM module: 256x16-bit read-only memory with predefined data at addresses 0-3. -/
def ROM {dom : DomainConfig}
    (addr : Signal dom (BitVec 8)) : Signal dom (BitVec 16) :=
  let is0 := addr === Signal.pure 0#8
  let is1 := addr === Signal.pure 1#8
  let is2 := addr === Signal.pure 2#8
  let is3 := addr === Signal.pure 3#8
  let val0 := Signal.pure 0xA0A0#16
  let val1 := Signal.pure 0xB1B1#16
  let val2 := Signal.pure 0xC2C2#16
  let val3 := Signal.pure 0xD3D3#16
  let default := Signal.pure 0#16
  -- Nested mux: if addr==0 then val0 else (if addr==1 then val1 else ...)
  Signal.mux is0 val0
    (Signal.mux is1 val1
      (Signal.mux is2 val2
        (Signal.mux is3 val3 default)))

#synthesizeVerilog ROM
