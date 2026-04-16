import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test Conway's Game of Life on a 2x2 grid (4 bits). -/
def test_conway_2x2 {dom : DomainConfig}
    (load : Signal dom Bool)
    (data : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let getBit3 (idx : Nat) : Signal dom (BitVec 3) :=
      Signal.map (fun bv => BitVec.zeroExtend 3 (BitVec.extractLsb idx idx bv)) q
    
    let boolToBitAt (b : Signal dom Bool) (pos : Nat) : Signal dom (BitVec 4) :=
      Signal.mux b ((1#4 : BitVec 4) <<< pos) (0#4 : BitVec 4)

    let n0_sum : Signal dom (BitVec 3) := getBit3 3 + getBit3 2 + getBit3 3 + getBit3 1 + getBit3 1 + getBit3 3 + getBit3 2 + getBit3 3
    let n0_alive := getBit3 0
    let n0_code := (n0_sum &&& 7#3) ||| n0_alive
    let n0_next : Signal dom Bool := n0_code === 3#3
    let n1_sum : Signal dom (BitVec 3) := getBit3 2 + getBit3 3 + getBit3 2 + getBit3 0 + getBit3 0 + getBit3 2 + getBit3 3 + getBit3 2
    let n1_alive := getBit3 1
    let n1_code := (n1_sum &&& 7#3) ||| n1_alive
    let n1_next : Signal dom Bool := n1_code === 3#3
    let n2_sum : Signal dom (BitVec 3) := getBit3 1 + getBit3 0 + getBit3 1 + getBit3 3 + getBit3 3 + getBit3 1 + getBit3 0 + getBit3 1
    let n2_alive := getBit3 2
    let n2_code := (n2_sum &&& 7#3) ||| n2_alive
    let n2_next : Signal dom Bool := n2_code === 3#3
    let n3_sum : Signal dom (BitVec 3) := getBit3 0 + getBit3 1 + getBit3 0 + getBit3 2 + getBit3 2 + getBit3 0 + getBit3 1 + getBit3 0
    let n3_alive := getBit3 3
    let n3_code := (n3_sum &&& 7#3) ||| n3_alive
    let n3_next : Signal dom Bool := n3_code === 3#3
    let result0 := boolToBitAt n0_next 0
    let result1 := result0 ||| boolToBitAt n1_next 1
    let result2 := result1 ||| boolToBitAt n2_next 2
    let result3 := result2 ||| boolToBitAt n3_next 3
    
    let nextVal := Signal.mux load data result3
    Signal.register 0#4 nextVal

#synthesizeVerilog test_conway_2x2

