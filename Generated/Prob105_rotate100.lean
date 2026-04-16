import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 100-bit left/right rotator with synchronous load and enable control. -/
def prob105_rotate100 {dom : DomainConfig}
    (load : Signal dom Bool)
    (ena : Signal dom (BitVec 2))
    (data : Signal dom (BitVec 100)) : Signal dom (BitVec 100) :=
  Signal.loop fun (q : Signal dom (BitVec 100)) =>
    -- Check ena values
    let ena_is_01 := ena === 1#2  -- rotate right
    let ena_is_10 := ena === 2#2  -- rotate left
    
    -- Rotate right: {q[0], q[99:1]}
    -- Extract LSB, shift right, then put LSB at top
    let lsb := q &&& 1#100
    let shifted_right := q >>> 1#100
    let lsb_at_top := lsb <<< 99#100
    let rotateRight := shifted_right ||| lsb_at_top
    
    -- Rotate left: {q[98:0], q[99]}
    -- Extract MSB, shift left, then put MSB at bottom
    let msb := q >>> 99#100
    let shifted_left := q <<< 1#100
    let rotateLeft := shifted_left ||| msb
    
    -- Select rotation based on ena
    let rotated := Signal.mux ena_is_01 rotateRight
                    (Signal.mux ena_is_10 rotateLeft q)
    
    -- Load overrides rotation
    let nextVal := Signal.mux load data rotated
    
    Signal.register 0#100 nextVal

#synthesizeVerilog prob105_rotate100
