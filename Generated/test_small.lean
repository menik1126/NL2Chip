import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test Conway's Life on 4x4 grid -/
def test_conway {dom : DomainConfig}
    (load : Signal dom Bool) (data : Signal dom (BitVec 16))
    : Signal dom (BitVec 16) :=
  Signal.loop fun (q : Signal dom (BitVec 16)) =>
    -- Extract all 16 cells
    let c0 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨0, by omega⟩) q
    let c1 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨1, by omega⟩) q
    let c2 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨2, by omega⟩) q
    let c3 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨3, by omega⟩) q
    let c4 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨4, by omega⟩) q
    let c5 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨5, by omega⟩) q
    let c6 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨6, by omega⟩) q
    let c7 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨7, by omega⟩) q
    let c8 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨8, by omega⟩) q
    let c9 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨9, by omega⟩) q
    let c10 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨10, by omega⟩) q
    let c11 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨11, by omega⟩) q
    let c12 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨12, by omega⟩) q
    let c13 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨13, by omega⟩) q
    let c14 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨14, by omega⟩) q
    let c15 : Signal dom Bool := Signal.map (fun g => g.getLsb ⟨15, by omega⟩) q

    -- Compute next state for each cell
    let n0_0 := Signal.mux c15 (Signal.pure 1#4) (Signal.pure 0#4)
    let n0_1 := n0_0 + Signal.mux c12 (Signal.pure 1#4) (Signal.pure 0#4)
    let n0_2 := n0_1 + Signal.mux c13 (Signal.pure 1#4) (Signal.pure 0#4)
    let n0_3 := n0_2 + Signal.mux c3 (Signal.pure 1#4) (Signal.pure 0#4)
    let n0_4 := n0_3 + Signal.mux c1 (Signal.pure 1#4) (Signal.pure 0#4)
    let n0_5 := n0_4 + Signal.mux c7 (Signal.pure 1#4) (Signal.pure 0#4)
    let n0_6 := n0_5 + Signal.mux c4 (Signal.pure 1#4) (Signal.pure 0#4)
    let n0_7 := n0_6 + Signal.mux c5 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_0 := n0_7 === 2#4
    let eq3_0 := n0_7 === 3#4
    let stay_0 := eq2_0 &&& c0
    let next0 := stay_0 ||| eq3_0
    let n1_0 := Signal.mux c12 (Signal.pure 1#4) (Signal.pure 0#4)
    let n1_1 := n1_0 + Signal.mux c13 (Signal.pure 1#4) (Signal.pure 0#4)
    let n1_2 := n1_1 + Signal.mux c14 (Signal.pure 1#4) (Signal.pure 0#4)
    let n1_3 := n1_2 + Signal.mux c0 (Signal.pure 1#4) (Signal.pure 0#4)
    let n1_4 := n1_3 + Signal.mux c2 (Signal.pure 1#4) (Signal.pure 0#4)
    let n1_5 := n1_4 + Signal.mux c4 (Signal.pure 1#4) (Signal.pure 0#4)
    let n1_6 := n1_5 + Signal.mux c5 (Signal.pure 1#4) (Signal.pure 0#4)
    let n1_7 := n1_6 + Signal.mux c6 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_1 := n1_7 === 2#4
    let eq3_1 := n1_7 === 3#4
    let stay_1 := eq2_1 &&& c1
    let next1 := stay_1 ||| eq3_1
    let n2_0 := Signal.mux c13 (Signal.pure 1#4) (Signal.pure 0#4)
    let n2_1 := n2_0 + Signal.mux c14 (Signal.pure 1#4) (Signal.pure 0#4)
    let n2_2 := n2_1 + Signal.mux c15 (Signal.pure 1#4) (Signal.pure 0#4)
    let n2_3 := n2_2 + Signal.mux c1 (Signal.pure 1#4) (Signal.pure 0#4)
    let n2_4 := n2_3 + Signal.mux c3 (Signal.pure 1#4) (Signal.pure 0#4)
    let n2_5 := n2_4 + Signal.mux c5 (Signal.pure 1#4) (Signal.pure 0#4)
    let n2_6 := n2_5 + Signal.mux c6 (Signal.pure 1#4) (Signal.pure 0#4)
    let n2_7 := n2_6 + Signal.mux c7 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_2 := n2_7 === 2#4
    let eq3_2 := n2_7 === 3#4
    let stay_2 := eq2_2 &&& c2
    let next2 := stay_2 ||| eq3_2
    let n3_0 := Signal.mux c14 (Signal.pure 1#4) (Signal.pure 0#4)
    let n3_1 := n3_0 + Signal.mux c15 (Signal.pure 1#4) (Signal.pure 0#4)
    let n3_2 := n3_1 + Signal.mux c12 (Signal.pure 1#4) (Signal.pure 0#4)
    let n3_3 := n3_2 + Signal.mux c2 (Signal.pure 1#4) (Signal.pure 0#4)
    let n3_4 := n3_3 + Signal.mux c0 (Signal.pure 1#4) (Signal.pure 0#4)
    let n3_5 := n3_4 + Signal.mux c6 (Signal.pure 1#4) (Signal.pure 0#4)
    let n3_6 := n3_5 + Signal.mux c7 (Signal.pure 1#4) (Signal.pure 0#4)
    let n3_7 := n3_6 + Signal.mux c4 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_3 := n3_7 === 2#4
    let eq3_3 := n3_7 === 3#4
    let stay_3 := eq2_3 &&& c3
    let next3 := stay_3 ||| eq3_3
    let n4_0 := Signal.mux c3 (Signal.pure 1#4) (Signal.pure 0#4)
    let n4_1 := n4_0 + Signal.mux c0 (Signal.pure 1#4) (Signal.pure 0#4)
    let n4_2 := n4_1 + Signal.mux c1 (Signal.pure 1#4) (Signal.pure 0#4)
    let n4_3 := n4_2 + Signal.mux c7 (Signal.pure 1#4) (Signal.pure 0#4)
    let n4_4 := n4_3 + Signal.mux c5 (Signal.pure 1#4) (Signal.pure 0#4)
    let n4_5 := n4_4 + Signal.mux c11 (Signal.pure 1#4) (Signal.pure 0#4)
    let n4_6 := n4_5 + Signal.mux c8 (Signal.pure 1#4) (Signal.pure 0#4)
    let n4_7 := n4_6 + Signal.mux c9 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_4 := n4_7 === 2#4
    let eq3_4 := n4_7 === 3#4
    let stay_4 := eq2_4 &&& c4
    let next4 := stay_4 ||| eq3_4
    let n5_0 := Signal.mux c0 (Signal.pure 1#4) (Signal.pure 0#4)
    let n5_1 := n5_0 + Signal.mux c1 (Signal.pure 1#4) (Signal.pure 0#4)
    let n5_2 := n5_1 + Signal.mux c2 (Signal.pure 1#4) (Signal.pure 0#4)
    let n5_3 := n5_2 + Signal.mux c4 (Signal.pure 1#4) (Signal.pure 0#4)
    let n5_4 := n5_3 + Signal.mux c6 (Signal.pure 1#4) (Signal.pure 0#4)
    let n5_5 := n5_4 + Signal.mux c8 (Signal.pure 1#4) (Signal.pure 0#4)
    let n5_6 := n5_5 + Signal.mux c9 (Signal.pure 1#4) (Signal.pure 0#4)
    let n5_7 := n5_6 + Signal.mux c10 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_5 := n5_7 === 2#4
    let eq3_5 := n5_7 === 3#4
    let stay_5 := eq2_5 &&& c5
    let next5 := stay_5 ||| eq3_5
    let n6_0 := Signal.mux c1 (Signal.pure 1#4) (Signal.pure 0#4)
    let n6_1 := n6_0 + Signal.mux c2 (Signal.pure 1#4) (Signal.pure 0#4)
    let n6_2 := n6_1 + Signal.mux c3 (Signal.pure 1#4) (Signal.pure 0#4)
    let n6_3 := n6_2 + Signal.mux c5 (Signal.pure 1#4) (Signal.pure 0#4)
    let n6_4 := n6_3 + Signal.mux c7 (Signal.pure 1#4) (Signal.pure 0#4)
    let n6_5 := n6_4 + Signal.mux c9 (Signal.pure 1#4) (Signal.pure 0#4)
    let n6_6 := n6_5 + Signal.mux c10 (Signal.pure 1#4) (Signal.pure 0#4)
    let n6_7 := n6_6 + Signal.mux c11 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_6 := n6_7 === 2#4
    let eq3_6 := n6_7 === 3#4
    let stay_6 := eq2_6 &&& c6
    let next6 := stay_6 ||| eq3_6
    let n7_0 := Signal.mux c2 (Signal.pure 1#4) (Signal.pure 0#4)
    let n7_1 := n7_0 + Signal.mux c3 (Signal.pure 1#4) (Signal.pure 0#4)
    let n7_2 := n7_1 + Signal.mux c0 (Signal.pure 1#4) (Signal.pure 0#4)
    let n7_3 := n7_2 + Signal.mux c6 (Signal.pure 1#4) (Signal.pure 0#4)
    let n7_4 := n7_3 + Signal.mux c4 (Signal.pure 1#4) (Signal.pure 0#4)
    let n7_5 := n7_4 + Signal.mux c10 (Signal.pure 1#4) (Signal.pure 0#4)
    let n7_6 := n7_5 + Signal.mux c11 (Signal.pure 1#4) (Signal.pure 0#4)
    let n7_7 := n7_6 + Signal.mux c8 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_7 := n7_7 === 2#4
    let eq3_7 := n7_7 === 3#4
    let stay_7 := eq2_7 &&& c7
    let next7 := stay_7 ||| eq3_7
    let n8_0 := Signal.mux c7 (Signal.pure 1#4) (Signal.pure 0#4)
    let n8_1 := n8_0 + Signal.mux c4 (Signal.pure 1#4) (Signal.pure 0#4)
    let n8_2 := n8_1 + Signal.mux c5 (Signal.pure 1#4) (Signal.pure 0#4)
    let n8_3 := n8_2 + Signal.mux c11 (Signal.pure 1#4) (Signal.pure 0#4)
    let n8_4 := n8_3 + Signal.mux c9 (Signal.pure 1#4) (Signal.pure 0#4)
    let n8_5 := n8_4 + Signal.mux c15 (Signal.pure 1#4) (Signal.pure 0#4)
    let n8_6 := n8_5 + Signal.mux c12 (Signal.pure 1#4) (Signal.pure 0#4)
    let n8_7 := n8_6 + Signal.mux c13 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_8 := n8_7 === 2#4
    let eq3_8 := n8_7 === 3#4
    let stay_8 := eq2_8 &&& c8
    let next8 := stay_8 ||| eq3_8
    let n9_0 := Signal.mux c4 (Signal.pure 1#4) (Signal.pure 0#4)
    let n9_1 := n9_0 + Signal.mux c5 (Signal.pure 1#4) (Signal.pure 0#4)
    let n9_2 := n9_1 + Signal.mux c6 (Signal.pure 1#4) (Signal.pure 0#4)
    let n9_3 := n9_2 + Signal.mux c8 (Signal.pure 1#4) (Signal.pure 0#4)
    let n9_4 := n9_3 + Signal.mux c10 (Signal.pure 1#4) (Signal.pure 0#4)
    let n9_5 := n9_4 + Signal.mux c12 (Signal.pure 1#4) (Signal.pure 0#4)
    let n9_6 := n9_5 + Signal.mux c13 (Signal.pure 1#4) (Signal.pure 0#4)
    let n9_7 := n9_6 + Signal.mux c14 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_9 := n9_7 === 2#4
    let eq3_9 := n9_7 === 3#4
    let stay_9 := eq2_9 &&& c9
    let next9 := stay_9 ||| eq3_9
    let n10_0 := Signal.mux c5 (Signal.pure 1#4) (Signal.pure 0#4)
    let n10_1 := n10_0 + Signal.mux c6 (Signal.pure 1#4) (Signal.pure 0#4)
    let n10_2 := n10_1 + Signal.mux c7 (Signal.pure 1#4) (Signal.pure 0#4)
    let n10_3 := n10_2 + Signal.mux c9 (Signal.pure 1#4) (Signal.pure 0#4)
    let n10_4 := n10_3 + Signal.mux c11 (Signal.pure 1#4) (Signal.pure 0#4)
    let n10_5 := n10_4 + Signal.mux c13 (Signal.pure 1#4) (Signal.pure 0#4)
    let n10_6 := n10_5 + Signal.mux c14 (Signal.pure 1#4) (Signal.pure 0#4)
    let n10_7 := n10_6 + Signal.mux c15 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_10 := n10_7 === 2#4
    let eq3_10 := n10_7 === 3#4
    let stay_10 := eq2_10 &&& c10
    let next10 := stay_10 ||| eq3_10
    let n11_0 := Signal.mux c6 (Signal.pure 1#4) (Signal.pure 0#4)
    let n11_1 := n11_0 + Signal.mux c7 (Signal.pure 1#4) (Signal.pure 0#4)
    let n11_2 := n11_1 + Signal.mux c4 (Signal.pure 1#4) (Signal.pure 0#4)
    let n11_3 := n11_2 + Signal.mux c10 (Signal.pure 1#4) (Signal.pure 0#4)
    let n11_4 := n11_3 + Signal.mux c8 (Signal.pure 1#4) (Signal.pure 0#4)
    let n11_5 := n11_4 + Signal.mux c14 (Signal.pure 1#4) (Signal.pure 0#4)
    let n11_6 := n11_5 + Signal.mux c15 (Signal.pure 1#4) (Signal.pure 0#4)
    let n11_7 := n11_6 + Signal.mux c12 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_11 := n11_7 === 2#4
    let eq3_11 := n11_7 === 3#4
    let stay_11 := eq2_11 &&& c11
    let next11 := stay_11 ||| eq3_11
    let n12_0 := Signal.mux c11 (Signal.pure 1#4) (Signal.pure 0#4)
    let n12_1 := n12_0 + Signal.mux c8 (Signal.pure 1#4) (Signal.pure 0#4)
    let n12_2 := n12_1 + Signal.mux c9 (Signal.pure 1#4) (Signal.pure 0#4)
    let n12_3 := n12_2 + Signal.mux c15 (Signal.pure 1#4) (Signal.pure 0#4)
    let n12_4 := n12_3 + Signal.mux c13 (Signal.pure 1#4) (Signal.pure 0#4)
    let n12_5 := n12_4 + Signal.mux c3 (Signal.pure 1#4) (Signal.pure 0#4)
    let n12_6 := n12_5 + Signal.mux c0 (Signal.pure 1#4) (Signal.pure 0#4)
    let n12_7 := n12_6 + Signal.mux c1 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_12 := n12_7 === 2#4
    let eq3_12 := n12_7 === 3#4
    let stay_12 := eq2_12 &&& c12
    let next12 := stay_12 ||| eq3_12
    let n13_0 := Signal.mux c8 (Signal.pure 1#4) (Signal.pure 0#4)
    let n13_1 := n13_0 + Signal.mux c9 (Signal.pure 1#4) (Signal.pure 0#4)
    let n13_2 := n13_1 + Signal.mux c10 (Signal.pure 1#4) (Signal.pure 0#4)
    let n13_3 := n13_2 + Signal.mux c12 (Signal.pure 1#4) (Signal.pure 0#4)
    let n13_4 := n13_3 + Signal.mux c14 (Signal.pure 1#4) (Signal.pure 0#4)
    let n13_5 := n13_4 + Signal.mux c0 (Signal.pure 1#4) (Signal.pure 0#4)
    let n13_6 := n13_5 + Signal.mux c1 (Signal.pure 1#4) (Signal.pure 0#4)
    let n13_7 := n13_6 + Signal.mux c2 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_13 := n13_7 === 2#4
    let eq3_13 := n13_7 === 3#4
    let stay_13 := eq2_13 &&& c13
    let next13 := stay_13 ||| eq3_13
    let n14_0 := Signal.mux c9 (Signal.pure 1#4) (Signal.pure 0#4)
    let n14_1 := n14_0 + Signal.mux c10 (Signal.pure 1#4) (Signal.pure 0#4)
    let n14_2 := n14_1 + Signal.mux c11 (Signal.pure 1#4) (Signal.pure 0#4)
    let n14_3 := n14_2 + Signal.mux c13 (Signal.pure 1#4) (Signal.pure 0#4)
    let n14_4 := n14_3 + Signal.mux c15 (Signal.pure 1#4) (Signal.pure 0#4)
    let n14_5 := n14_4 + Signal.mux c1 (Signal.pure 1#4) (Signal.pure 0#4)
    let n14_6 := n14_5 + Signal.mux c2 (Signal.pure 1#4) (Signal.pure 0#4)
    let n14_7 := n14_6 + Signal.mux c3 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_14 := n14_7 === 2#4
    let eq3_14 := n14_7 === 3#4
    let stay_14 := eq2_14 &&& c14
    let next14 := stay_14 ||| eq3_14
    let n15_0 := Signal.mux c10 (Signal.pure 1#4) (Signal.pure 0#4)
    let n15_1 := n15_0 + Signal.mux c11 (Signal.pure 1#4) (Signal.pure 0#4)
    let n15_2 := n15_1 + Signal.mux c8 (Signal.pure 1#4) (Signal.pure 0#4)
    let n15_3 := n15_2 + Signal.mux c14 (Signal.pure 1#4) (Signal.pure 0#4)
    let n15_4 := n15_3 + Signal.mux c12 (Signal.pure 1#4) (Signal.pure 0#4)
    let n15_5 := n15_4 + Signal.mux c2 (Signal.pure 1#4) (Signal.pure 0#4)
    let n15_6 := n15_5 + Signal.mux c3 (Signal.pure 1#4) (Signal.pure 0#4)
    let n15_7 := n15_6 + Signal.mux c0 (Signal.pure 1#4) (Signal.pure 0#4)
    let eq2_15 := n15_7 === 2#4
    let eq3_15 := n15_7 === 3#4
    let stay_15 := eq2_15 &&& c15
    let next15 := stay_15 ||| eq3_15

    -- Build result from bits
    let result : Signal dom (BitVec 16) := Signal.pure 0#16
    let bit0 := Signal.mux next0 (Signal.pure (1#16 <<< 0)) (Signal.pure 0#16)
    let result := result ||| bit0
    let bit1 := Signal.mux next1 (Signal.pure (1#16 <<< 1)) (Signal.pure 0#16)
    let result := result ||| bit1
    let bit2 := Signal.mux next2 (Signal.pure (1#16 <<< 2)) (Signal.pure 0#16)
    let result := result ||| bit2
    let bit3 := Signal.mux next3 (Signal.pure (1#16 <<< 3)) (Signal.pure 0#16)
    let result := result ||| bit3
    let bit4 := Signal.mux next4 (Signal.pure (1#16 <<< 4)) (Signal.pure 0#16)
    let result := result ||| bit4
    let bit5 := Signal.mux next5 (Signal.pure (1#16 <<< 5)) (Signal.pure 0#16)
    let result := result ||| bit5
    let bit6 := Signal.mux next6 (Signal.pure (1#16 <<< 6)) (Signal.pure 0#16)
    let result := result ||| bit6
    let bit7 := Signal.mux next7 (Signal.pure (1#16 <<< 7)) (Signal.pure 0#16)
    let result := result ||| bit7
    let bit8 := Signal.mux next8 (Signal.pure (1#16 <<< 8)) (Signal.pure 0#16)
    let result := result ||| bit8
    let bit9 := Signal.mux next9 (Signal.pure (1#16 <<< 9)) (Signal.pure 0#16)
    let result := result ||| bit9
    let bit10 := Signal.mux next10 (Signal.pure (1#16 <<< 10)) (Signal.pure 0#16)
    let result := result ||| bit10
    let bit11 := Signal.mux next11 (Signal.pure (1#16 <<< 11)) (Signal.pure 0#16)
    let result := result ||| bit11
    let bit12 := Signal.mux next12 (Signal.pure (1#16 <<< 12)) (Signal.pure 0#16)
    let result := result ||| bit12
    let bit13 := Signal.mux next13 (Signal.pure (1#16 <<< 13)) (Signal.pure 0#16)
    let result := result ||| bit13
    let bit14 := Signal.mux next14 (Signal.pure (1#16 <<< 14)) (Signal.pure 0#16)
    let result := result ||| bit14
    let bit15 := Signal.mux next15 (Signal.pure (1#16 <<< 15)) (Signal.pure 0#16)
    let result := result ||| bit15

    let nextQ := Signal.mux load data result
    Signal.register 0#16 nextQ

#synthesizeVerilog test_conway

