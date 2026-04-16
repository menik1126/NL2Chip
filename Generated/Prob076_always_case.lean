import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 6-to-1 multiplexer: sel (3-bit) selects one of data0..data5 (4-bit each); outputs 0 for sel >= 6. -/
def prob076_always_case {dom : DomainConfig}
    (sel   : Signal dom (BitVec 3))
    (data0 : Signal dom (BitVec 4))
    (data1 : Signal dom (BitVec 4))
    (data2 : Signal dom (BitVec 4))
    (data3 : Signal dom (BitVec 4))
    (data4 : Signal dom (BitVec 4))
    (data5 : Signal dom (BitVec 4))
    : Signal dom (BitVec 4) :=
  hw_cond (Signal.pure 0#4)
    | (sel === Signal.pure 0#3) => data0
    | (sel === Signal.pure 1#3) => data1
    | (sel === Signal.pure 2#3) => data2
    | (sel === Signal.pure 3#3) => data3
    | (sel === Signal.pure 4#3) => data4
    | (sel === Signal.pure 5#3) => data5

#synthesizeVerilog prob076_always_case
