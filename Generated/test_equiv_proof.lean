import Sparkle

-- Helper: prove the state-to-output mapping is correct
theorem state_output_map_A : 
    let bin_state := (0#2 : BitVec 2)
    let oh_state := (0#3 : BitVec 3)
    let output_from_bin := 
      let g0 := if bin_state == 1#2 then 1#3 else 0#3
      let g1 := if bin_state == 2#2 then 2#3 else 0#3
      let g2 := if bin_state == 3#2 then 4#3 else 0#3
      g0 ||| g1 ||| g2
    output_from_bin = oh_state := by decide

theorem state_output_map_B : 
    let bin_state := (1#2 : BitVec 2)
    let oh_state := (1#3 : BitVec 3)
    let output_from_bin := 
      let g0 := if bin_state == 1#2 then 1#3 else 0#3
      let g1 := if bin_state == 2#2 then 2#3 else 0#3
      let g2 := if bin_state == 3#2 then 4#3 else 0#3
      g0 ||| g1 ||| g2
    output_from_bin = oh_state := by decide

theorem state_output_map_C : 
    let bin_state := (2#2 : BitVec 2)
    let oh_state := (2#3 : BitVec 3)
    let output_from_bin := 
      let g0 := if bin_state == 1#2 then 1#3 else 0#3
      let g1 := if bin_state == 2#2 then 2#3 else 0#3
      let g2 := if bin_state == 3#2 then 4#3 else 0#3
      g0 ||| g1 ||| g2
    output_from_bin = oh_state := by decide

theorem state_output_map_D : 
    let bin_state := (3#2 : BitVec 2)
    let oh_state := (4#3 : BitVec 3)
    let output_from_bin := 
      let g0 := if bin_state == 1#2 then 1#3 else 0#3
      let g1 := if bin_state == 2#2 then 2#3 else 0#3
      let g2 := if bin_state == 3#2 then 4#3 else 0#3
      g0 ||| g1 ||| g2
    output_from_bin = oh_state := by decide

#check state_output_map_A
#check state_output_map_B
#check state_output_map_C
#check state_output_map_D
