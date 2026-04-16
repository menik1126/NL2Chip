import Sparkle

-- Prove output equivalence for each concrete state value
theorem output_equiv_0 :
    let s := (0#2 : BitVec 2)
    let g0_bool := (s == 1#2)
    let g1_bool := (s == 2#2)
    let g2_bool := (s == 3#2)
    let g0_orig : BitVec 3 := if g0_bool then 1#3 else 0#3
    let g1_orig : BitVec 3 := if g1_bool then 2#3 else 0#3
    let g2_orig : BitVec 3 := if g2_bool then 4#3 else 0#3
    let orig := g0_orig ||| g1_orig ||| g2_orig
    
    let s_ext := s.zeroExtend 3
    let s0 := s_ext &&& 1#3
    let s1 := (s_ext >>> 1) &&& 1#3
    let g0_opt := s0 &&& (~~~s1)
    let g1_opt := ((~~~s0) &&& s1) <<< 1
    let g2_opt := (s0 &&& s1) <<< 2
    let opt := g0_opt ||| g1_opt ||| g2_opt
    
    orig = opt := by decide

theorem output_equiv_1 :
    let s := (1#2 : BitVec 2)
    let g0_bool := (s == 1#2)
    let g1_bool := (s == 2#2)
    let g2_bool := (s == 3#2)
    let g0_orig : BitVec 3 := if g0_bool then 1#3 else 0#3
    let g1_orig : BitVec 3 := if g1_bool then 2#3 else 0#3
    let g2_orig : BitVec 3 := if g2_bool then 4#3 else 0#3
    let orig := g0_orig ||| g1_orig ||| g2_orig
    
    let s_ext := s.zeroExtend 3
    let s0 := s_ext &&& 1#3
    let s1 := (s_ext >>> 1) &&& 1#3
    let g0_opt := s0 &&& (~~~s1)
    let g1_opt := ((~~~s0) &&& s1) <<< 1
    let g2_opt := (s0 &&& s1) <<< 2
    let opt := g0_opt ||| g1_opt ||| g2_opt
    
    orig = opt := by decide

theorem output_equiv_2 :
    let s := (2#2 : BitVec 2)
    let g0_bool := (s == 1#2)
    let g1_bool := (s == 2#2)
    let g2_bool := (s == 3#2)
    let g0_orig : BitVec 3 := if g0_bool then 1#3 else 0#3
    let g1_orig : BitVec 3 := if g1_bool then 2#3 else 0#3
    let g2_orig : BitVec 3 := if g2_bool then 4#3 else 0#3
    let orig := g0_orig ||| g1_orig ||| g2_orig
    
    let s_ext := s.zeroExtend 3
    let s0 := s_ext &&& 1#3
    let s1 := (s_ext >>> 1) &&& 1#3
    let g0_opt := s0 &&& (~~~s1)
    let g1_opt := ((~~~s0) &&& s1) <<< 1
    let g2_opt := (s0 &&& s1) <<< 2
    let opt := g0_opt ||| g1_opt ||| g2_opt
    
    orig = opt := by decide

theorem output_equiv_3 :
    let s := (3#2 : BitVec 2)
    let g0_bool := (s == 1#2)
    let g1_bool := (s == 2#2)
    let g2_bool := (s == 3#2)
    let g0_orig : BitVec 3 := if g0_bool then 1#3 else 0#3
    let g1_orig : BitVec 3 := if g1_bool then 2#3 else 0#3
    let g2_orig : BitVec 3 := if g2_bool then 4#3 else 0#3
    let orig := g0_orig ||| g1_orig ||| g2_orig
    
    let s_ext := s.zeroExtend 3
    let s0 := s_ext &&& 1#3
    let s1 := (s_ext >>> 1) &&& 1#3
    let g0_opt := s0 &&& (~~~s1)
    let g1_opt := ((~~~s0) &&& s1) <<< 1
    let g2_opt := (s0 &&& s1) <<< 2
    let opt := g0_opt ||| g1_opt ||| g2_opt
    
    orig = opt := by decide

#check output_equiv_0
#check output_equiv_1
#check output_equiv_2
#check output_equiv_3
