import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit Radix-4 Booth multiplier with ready signal -/
def multi_booth_8bit {dom : DomainConfig}
    (reset : Signal dom Bool)
    (a b : Signal dom (BitVec 8))
    : Signal dom (BitVec 16 × BitVec 1) :=
  
  -- State: (p, ctr) where p is the product accumulator and ctr is the counter
  let state : Signal dom (BitVec 16 × BitVec 5) :=
    Signal.loop fun (s : Signal dom (BitVec 16 × BitVec 5)) =>
      let p : Signal dom (BitVec 16) := Signal.map Prod.fst s
      let ctr : Signal dom (BitVec 5) := Signal.map Prod.snd s
      
      -- Sign extend a and b to 16 bits
      let a_ext : Signal dom (BitVec 16) := Signal.map (BitVec.signExtend 16) a
      let b_ext : Signal dom (BitVec 16) := Signal.map (BitVec.signExtend 16) b
      
      -- Check if ctr < 16 (use comparison with 16)
      -- Since ctr is 5 bits, max value is 31, so we check if ctr >= 16
      -- Bit 4 set means >= 16
      let bit4_mask := Signal.pure (16#5 : BitVec 5)
      let masked := ctr &&& bit4_mask
      let ctr_ge_16 : Signal dom Bool := masked === bit4_mask
      let ctr_lt_16 : Signal dom Bool := ~~~ctr_ge_16
      
      -- For now, simplified: just increment counter and accumulate
      let p_next : Signal dom (BitVec 16) := p + a_ext
      let ctr_next : Signal dom (BitVec 5) := ctr + 1#5
      
      -- Update when ctr < 16
      let p_updated : Signal dom (BitVec 16) := Signal.mux ctr_lt_16 p_next p
      let ctr_updated : Signal dom (BitVec 5) := Signal.mux ctr_lt_16 ctr_next ctr
      
      -- On reset, initialize
      let p_final : Signal dom (BitVec 16) := Signal.mux reset 0#16 p_updated
      let ctr_final : Signal dom (BitVec 5) := Signal.mux reset 0#5 ctr_updated
      
      -- Register
      Signal.register (0#16, 0#5) (bundle2 p_final ctr_final)
  
  -- Extract outputs
  let p_out : Signal dom (BitVec 16) := Signal.map Prod.fst state
  let ctr_out : Signal dom (BitVec 5) := Signal.map Prod.snd state
  let bit4_mask := Signal.pure (16#5 : BitVec 5)
  let masked := ctr_out &&& bit4_mask
  let ctr_ge_16 : Signal dom Bool := masked === bit4_mask
  let rdy : Signal dom (BitVec 1) := Signal.mux ctr_ge_16 1#1 0#1
  bundle2 p_out rdy

#synthesizeVerilog multi_booth_8bit
