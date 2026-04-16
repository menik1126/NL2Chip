import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- K-map implementation: outputs mux_in[3:0] for a 4-to-1 mux selected by {a,b}.
    mux_in[0] = c | d, mux_in[1] = 0, mux_in[2] = ~d, mux_in[3] = c & d -/
def prob093_ece241_2014_q3 {dom : DomainConfig}
    (c d : Signal dom (BitVec 1)) : Signal dom (BitVec 4) :=
  -- Compute each mux input bit
  let mi0 := c ||| d                    -- mux_in[0] = c | d
  let mi1 : Signal dom (BitVec 1) := Signal.pure 0#1  -- mux_in[1] = 0
  let mi2 := ~~~d                        -- mux_in[2] = ~d
  let mi3 := c &&& d                    -- mux_in[3] = c & d
  -- Pack into 4-bit vector: {mi3, mi2, mi1, mi0}
  (mi3 ++ mi2) ++ (mi1 ++ mi0)

#synthesizeVerilog prob093_ece241_2014_q3
