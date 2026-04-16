-- Test popcount approach
private def popcount255 (x : BitVec 255) : BitVec 8 :=
  (List.range 255).foldl (fun acc i =>
    if x.getLsb i then acc + 1#8 else acc) 0#8

#eval popcount255 0xFF#255  -- should be 8
