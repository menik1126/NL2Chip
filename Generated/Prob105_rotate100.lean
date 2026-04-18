import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 100-bit left/right rotator with synchronous load.
    ena=01 rotates right, ena=10 rotates left, ena=00/11 holds. -/
def prob105_rotate100 {dom : DomainConfig}
    (load : Signal dom Bool)
    (ena : Signal dom (BitVec 2))
    (data : Signal dom (BitVec 100)) : Signal dom (BitVec 100) :=
  Signal.loop fun (q : Signal dom (BitVec 100)) =>
    -- Rotate right: {q[0], q[99:1]} = (q >>> 1) | (q[0] << 99)
    let lsb := q &&& 1#100
    let rotateRight := (q >>> 1#100) ||| (lsb <<< 99#100)
    
    -- Rotate left: {q[98:0], q[99]} = (q << 1) | (q >>> 99)
    let rotateLeft := (q <<< 1#100) ||| (q >>> 99#100)
    
    -- Select based on ena
    let ena01 := ena === 1#2
    let ena10 := ena === 2#2
    
    -- Priority: if ena==01 then rotateRight, else if ena==10 then rotateLeft, else q
    let afterRotate := Signal.mux ena01 rotateRight (Signal.mux ena10 rotateLeft q)
    
    -- If load, use data; otherwise use afterRotate
    let nextVal := Signal.mux load data afterRotate
    
    Signal.register 0#100 nextVal

#synthesizeVerilog prob105_rotate100
