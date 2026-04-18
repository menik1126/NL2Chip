import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

set_option maxRecDepth 5000

/-- Count bits 0 to 15 -/
def popcount_group0 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted0 := input >>> 0#255
  let masked0 := shifted0 &&& 1#255
  let isOne0 := masked0 === 1#255
  let v0 := Signal.mux isOne0 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted1 := input >>> 1#255
  let masked1 := shifted1 &&& 1#255
  let isOne1 := masked1 === 1#255
  let v1 := Signal.mux isOne1 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted2 := input >>> 2#255
  let masked2 := shifted2 &&& 1#255
  let isOne2 := masked2 === 1#255
  let v2 := Signal.mux isOne2 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted3 := input >>> 3#255
  let masked3 := shifted3 &&& 1#255
  let isOne3 := masked3 === 1#255
  let v3 := Signal.mux isOne3 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted4 := input >>> 4#255
  let masked4 := shifted4 &&& 1#255
  let isOne4 := masked4 === 1#255
  let v4 := Signal.mux isOne4 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted5 := input >>> 5#255
  let masked5 := shifted5 &&& 1#255
  let isOne5 := masked5 === 1#255
  let v5 := Signal.mux isOne5 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted6 := input >>> 6#255
  let masked6 := shifted6 &&& 1#255
  let isOne6 := masked6 === 1#255
  let v6 := Signal.mux isOne6 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted7 := input >>> 7#255
  let masked7 := shifted7 &&& 1#255
  let isOne7 := masked7 === 1#255
  let v7 := Signal.mux isOne7 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted8 := input >>> 8#255
  let masked8 := shifted8 &&& 1#255
  let isOne8 := masked8 === 1#255
  let v8 := Signal.mux isOne8 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted9 := input >>> 9#255
  let masked9 := shifted9 &&& 1#255
  let isOne9 := masked9 === 1#255
  let v9 := Signal.mux isOne9 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted10 := input >>> 10#255
  let masked10 := shifted10 &&& 1#255
  let isOne10 := masked10 === 1#255
  let v10 := Signal.mux isOne10 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted11 := input >>> 11#255
  let masked11 := shifted11 &&& 1#255
  let isOne11 := masked11 === 1#255
  let v11 := Signal.mux isOne11 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted12 := input >>> 12#255
  let masked12 := shifted12 &&& 1#255
  let isOne12 := masked12 === 1#255
  let v12 := Signal.mux isOne12 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted13 := input >>> 13#255
  let masked13 := shifted13 &&& 1#255
  let isOne13 := masked13 === 1#255
  let v13 := Signal.mux isOne13 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted14 := input >>> 14#255
  let masked14 := shifted14 &&& 1#255
  let isOne14 := masked14 === 1#255
  let v14 := Signal.mux isOne14 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted15 := input >>> 15#255
  let masked15 := shifted15 &&& 1#255
  let isOne15 := masked15 === 1#255
  let v15 := Signal.mux isOne15 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v0 + v1
  let sum0_1 := v2 + v3
  let sum0_2 := v4 + v5
  let sum0_3 := v6 + v7
  let sum0_4 := v8 + v9
  let sum0_5 := v10 + v11
  let sum0_6 := v12 + v13
  let sum0_7 := v14 + v15
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 16 to 31 -/
def popcount_group1 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted16 := input >>> 16#255
  let masked16 := shifted16 &&& 1#255
  let isOne16 := masked16 === 1#255
  let v16 := Signal.mux isOne16 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted17 := input >>> 17#255
  let masked17 := shifted17 &&& 1#255
  let isOne17 := masked17 === 1#255
  let v17 := Signal.mux isOne17 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted18 := input >>> 18#255
  let masked18 := shifted18 &&& 1#255
  let isOne18 := masked18 === 1#255
  let v18 := Signal.mux isOne18 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted19 := input >>> 19#255
  let masked19 := shifted19 &&& 1#255
  let isOne19 := masked19 === 1#255
  let v19 := Signal.mux isOne19 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted20 := input >>> 20#255
  let masked20 := shifted20 &&& 1#255
  let isOne20 := masked20 === 1#255
  let v20 := Signal.mux isOne20 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted21 := input >>> 21#255
  let masked21 := shifted21 &&& 1#255
  let isOne21 := masked21 === 1#255
  let v21 := Signal.mux isOne21 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted22 := input >>> 22#255
  let masked22 := shifted22 &&& 1#255
  let isOne22 := masked22 === 1#255
  let v22 := Signal.mux isOne22 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted23 := input >>> 23#255
  let masked23 := shifted23 &&& 1#255
  let isOne23 := masked23 === 1#255
  let v23 := Signal.mux isOne23 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted24 := input >>> 24#255
  let masked24 := shifted24 &&& 1#255
  let isOne24 := masked24 === 1#255
  let v24 := Signal.mux isOne24 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted25 := input >>> 25#255
  let masked25 := shifted25 &&& 1#255
  let isOne25 := masked25 === 1#255
  let v25 := Signal.mux isOne25 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted26 := input >>> 26#255
  let masked26 := shifted26 &&& 1#255
  let isOne26 := masked26 === 1#255
  let v26 := Signal.mux isOne26 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted27 := input >>> 27#255
  let masked27 := shifted27 &&& 1#255
  let isOne27 := masked27 === 1#255
  let v27 := Signal.mux isOne27 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted28 := input >>> 28#255
  let masked28 := shifted28 &&& 1#255
  let isOne28 := masked28 === 1#255
  let v28 := Signal.mux isOne28 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted29 := input >>> 29#255
  let masked29 := shifted29 &&& 1#255
  let isOne29 := masked29 === 1#255
  let v29 := Signal.mux isOne29 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted30 := input >>> 30#255
  let masked30 := shifted30 &&& 1#255
  let isOne30 := masked30 === 1#255
  let v30 := Signal.mux isOne30 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted31 := input >>> 31#255
  let masked31 := shifted31 &&& 1#255
  let isOne31 := masked31 === 1#255
  let v31 := Signal.mux isOne31 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v16 + v17
  let sum0_1 := v18 + v19
  let sum0_2 := v20 + v21
  let sum0_3 := v22 + v23
  let sum0_4 := v24 + v25
  let sum0_5 := v26 + v27
  let sum0_6 := v28 + v29
  let sum0_7 := v30 + v31
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 32 to 47 -/
def popcount_group2 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted32 := input >>> 32#255
  let masked32 := shifted32 &&& 1#255
  let isOne32 := masked32 === 1#255
  let v32 := Signal.mux isOne32 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted33 := input >>> 33#255
  let masked33 := shifted33 &&& 1#255
  let isOne33 := masked33 === 1#255
  let v33 := Signal.mux isOne33 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted34 := input >>> 34#255
  let masked34 := shifted34 &&& 1#255
  let isOne34 := masked34 === 1#255
  let v34 := Signal.mux isOne34 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted35 := input >>> 35#255
  let masked35 := shifted35 &&& 1#255
  let isOne35 := masked35 === 1#255
  let v35 := Signal.mux isOne35 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted36 := input >>> 36#255
  let masked36 := shifted36 &&& 1#255
  let isOne36 := masked36 === 1#255
  let v36 := Signal.mux isOne36 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted37 := input >>> 37#255
  let masked37 := shifted37 &&& 1#255
  let isOne37 := masked37 === 1#255
  let v37 := Signal.mux isOne37 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted38 := input >>> 38#255
  let masked38 := shifted38 &&& 1#255
  let isOne38 := masked38 === 1#255
  let v38 := Signal.mux isOne38 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted39 := input >>> 39#255
  let masked39 := shifted39 &&& 1#255
  let isOne39 := masked39 === 1#255
  let v39 := Signal.mux isOne39 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted40 := input >>> 40#255
  let masked40 := shifted40 &&& 1#255
  let isOne40 := masked40 === 1#255
  let v40 := Signal.mux isOne40 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted41 := input >>> 41#255
  let masked41 := shifted41 &&& 1#255
  let isOne41 := masked41 === 1#255
  let v41 := Signal.mux isOne41 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted42 := input >>> 42#255
  let masked42 := shifted42 &&& 1#255
  let isOne42 := masked42 === 1#255
  let v42 := Signal.mux isOne42 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted43 := input >>> 43#255
  let masked43 := shifted43 &&& 1#255
  let isOne43 := masked43 === 1#255
  let v43 := Signal.mux isOne43 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted44 := input >>> 44#255
  let masked44 := shifted44 &&& 1#255
  let isOne44 := masked44 === 1#255
  let v44 := Signal.mux isOne44 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted45 := input >>> 45#255
  let masked45 := shifted45 &&& 1#255
  let isOne45 := masked45 === 1#255
  let v45 := Signal.mux isOne45 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted46 := input >>> 46#255
  let masked46 := shifted46 &&& 1#255
  let isOne46 := masked46 === 1#255
  let v46 := Signal.mux isOne46 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted47 := input >>> 47#255
  let masked47 := shifted47 &&& 1#255
  let isOne47 := masked47 === 1#255
  let v47 := Signal.mux isOne47 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v32 + v33
  let sum0_1 := v34 + v35
  let sum0_2 := v36 + v37
  let sum0_3 := v38 + v39
  let sum0_4 := v40 + v41
  let sum0_5 := v42 + v43
  let sum0_6 := v44 + v45
  let sum0_7 := v46 + v47
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 48 to 63 -/
def popcount_group3 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted48 := input >>> 48#255
  let masked48 := shifted48 &&& 1#255
  let isOne48 := masked48 === 1#255
  let v48 := Signal.mux isOne48 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted49 := input >>> 49#255
  let masked49 := shifted49 &&& 1#255
  let isOne49 := masked49 === 1#255
  let v49 := Signal.mux isOne49 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted50 := input >>> 50#255
  let masked50 := shifted50 &&& 1#255
  let isOne50 := masked50 === 1#255
  let v50 := Signal.mux isOne50 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted51 := input >>> 51#255
  let masked51 := shifted51 &&& 1#255
  let isOne51 := masked51 === 1#255
  let v51 := Signal.mux isOne51 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted52 := input >>> 52#255
  let masked52 := shifted52 &&& 1#255
  let isOne52 := masked52 === 1#255
  let v52 := Signal.mux isOne52 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted53 := input >>> 53#255
  let masked53 := shifted53 &&& 1#255
  let isOne53 := masked53 === 1#255
  let v53 := Signal.mux isOne53 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted54 := input >>> 54#255
  let masked54 := shifted54 &&& 1#255
  let isOne54 := masked54 === 1#255
  let v54 := Signal.mux isOne54 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted55 := input >>> 55#255
  let masked55 := shifted55 &&& 1#255
  let isOne55 := masked55 === 1#255
  let v55 := Signal.mux isOne55 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted56 := input >>> 56#255
  let masked56 := shifted56 &&& 1#255
  let isOne56 := masked56 === 1#255
  let v56 := Signal.mux isOne56 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted57 := input >>> 57#255
  let masked57 := shifted57 &&& 1#255
  let isOne57 := masked57 === 1#255
  let v57 := Signal.mux isOne57 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted58 := input >>> 58#255
  let masked58 := shifted58 &&& 1#255
  let isOne58 := masked58 === 1#255
  let v58 := Signal.mux isOne58 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted59 := input >>> 59#255
  let masked59 := shifted59 &&& 1#255
  let isOne59 := masked59 === 1#255
  let v59 := Signal.mux isOne59 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted60 := input >>> 60#255
  let masked60 := shifted60 &&& 1#255
  let isOne60 := masked60 === 1#255
  let v60 := Signal.mux isOne60 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted61 := input >>> 61#255
  let masked61 := shifted61 &&& 1#255
  let isOne61 := masked61 === 1#255
  let v61 := Signal.mux isOne61 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted62 := input >>> 62#255
  let masked62 := shifted62 &&& 1#255
  let isOne62 := masked62 === 1#255
  let v62 := Signal.mux isOne62 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted63 := input >>> 63#255
  let masked63 := shifted63 &&& 1#255
  let isOne63 := masked63 === 1#255
  let v63 := Signal.mux isOne63 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v48 + v49
  let sum0_1 := v50 + v51
  let sum0_2 := v52 + v53
  let sum0_3 := v54 + v55
  let sum0_4 := v56 + v57
  let sum0_5 := v58 + v59
  let sum0_6 := v60 + v61
  let sum0_7 := v62 + v63
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 64 to 79 -/
def popcount_group4 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted64 := input >>> 64#255
  let masked64 := shifted64 &&& 1#255
  let isOne64 := masked64 === 1#255
  let v64 := Signal.mux isOne64 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted65 := input >>> 65#255
  let masked65 := shifted65 &&& 1#255
  let isOne65 := masked65 === 1#255
  let v65 := Signal.mux isOne65 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted66 := input >>> 66#255
  let masked66 := shifted66 &&& 1#255
  let isOne66 := masked66 === 1#255
  let v66 := Signal.mux isOne66 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted67 := input >>> 67#255
  let masked67 := shifted67 &&& 1#255
  let isOne67 := masked67 === 1#255
  let v67 := Signal.mux isOne67 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted68 := input >>> 68#255
  let masked68 := shifted68 &&& 1#255
  let isOne68 := masked68 === 1#255
  let v68 := Signal.mux isOne68 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted69 := input >>> 69#255
  let masked69 := shifted69 &&& 1#255
  let isOne69 := masked69 === 1#255
  let v69 := Signal.mux isOne69 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted70 := input >>> 70#255
  let masked70 := shifted70 &&& 1#255
  let isOne70 := masked70 === 1#255
  let v70 := Signal.mux isOne70 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted71 := input >>> 71#255
  let masked71 := shifted71 &&& 1#255
  let isOne71 := masked71 === 1#255
  let v71 := Signal.mux isOne71 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted72 := input >>> 72#255
  let masked72 := shifted72 &&& 1#255
  let isOne72 := masked72 === 1#255
  let v72 := Signal.mux isOne72 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted73 := input >>> 73#255
  let masked73 := shifted73 &&& 1#255
  let isOne73 := masked73 === 1#255
  let v73 := Signal.mux isOne73 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted74 := input >>> 74#255
  let masked74 := shifted74 &&& 1#255
  let isOne74 := masked74 === 1#255
  let v74 := Signal.mux isOne74 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted75 := input >>> 75#255
  let masked75 := shifted75 &&& 1#255
  let isOne75 := masked75 === 1#255
  let v75 := Signal.mux isOne75 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted76 := input >>> 76#255
  let masked76 := shifted76 &&& 1#255
  let isOne76 := masked76 === 1#255
  let v76 := Signal.mux isOne76 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted77 := input >>> 77#255
  let masked77 := shifted77 &&& 1#255
  let isOne77 := masked77 === 1#255
  let v77 := Signal.mux isOne77 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted78 := input >>> 78#255
  let masked78 := shifted78 &&& 1#255
  let isOne78 := masked78 === 1#255
  let v78 := Signal.mux isOne78 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted79 := input >>> 79#255
  let masked79 := shifted79 &&& 1#255
  let isOne79 := masked79 === 1#255
  let v79 := Signal.mux isOne79 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v64 + v65
  let sum0_1 := v66 + v67
  let sum0_2 := v68 + v69
  let sum0_3 := v70 + v71
  let sum0_4 := v72 + v73
  let sum0_5 := v74 + v75
  let sum0_6 := v76 + v77
  let sum0_7 := v78 + v79
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 80 to 95 -/
def popcount_group5 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted80 := input >>> 80#255
  let masked80 := shifted80 &&& 1#255
  let isOne80 := masked80 === 1#255
  let v80 := Signal.mux isOne80 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted81 := input >>> 81#255
  let masked81 := shifted81 &&& 1#255
  let isOne81 := masked81 === 1#255
  let v81 := Signal.mux isOne81 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted82 := input >>> 82#255
  let masked82 := shifted82 &&& 1#255
  let isOne82 := masked82 === 1#255
  let v82 := Signal.mux isOne82 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted83 := input >>> 83#255
  let masked83 := shifted83 &&& 1#255
  let isOne83 := masked83 === 1#255
  let v83 := Signal.mux isOne83 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted84 := input >>> 84#255
  let masked84 := shifted84 &&& 1#255
  let isOne84 := masked84 === 1#255
  let v84 := Signal.mux isOne84 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted85 := input >>> 85#255
  let masked85 := shifted85 &&& 1#255
  let isOne85 := masked85 === 1#255
  let v85 := Signal.mux isOne85 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted86 := input >>> 86#255
  let masked86 := shifted86 &&& 1#255
  let isOne86 := masked86 === 1#255
  let v86 := Signal.mux isOne86 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted87 := input >>> 87#255
  let masked87 := shifted87 &&& 1#255
  let isOne87 := masked87 === 1#255
  let v87 := Signal.mux isOne87 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted88 := input >>> 88#255
  let masked88 := shifted88 &&& 1#255
  let isOne88 := masked88 === 1#255
  let v88 := Signal.mux isOne88 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted89 := input >>> 89#255
  let masked89 := shifted89 &&& 1#255
  let isOne89 := masked89 === 1#255
  let v89 := Signal.mux isOne89 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted90 := input >>> 90#255
  let masked90 := shifted90 &&& 1#255
  let isOne90 := masked90 === 1#255
  let v90 := Signal.mux isOne90 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted91 := input >>> 91#255
  let masked91 := shifted91 &&& 1#255
  let isOne91 := masked91 === 1#255
  let v91 := Signal.mux isOne91 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted92 := input >>> 92#255
  let masked92 := shifted92 &&& 1#255
  let isOne92 := masked92 === 1#255
  let v92 := Signal.mux isOne92 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted93 := input >>> 93#255
  let masked93 := shifted93 &&& 1#255
  let isOne93 := masked93 === 1#255
  let v93 := Signal.mux isOne93 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted94 := input >>> 94#255
  let masked94 := shifted94 &&& 1#255
  let isOne94 := masked94 === 1#255
  let v94 := Signal.mux isOne94 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted95 := input >>> 95#255
  let masked95 := shifted95 &&& 1#255
  let isOne95 := masked95 === 1#255
  let v95 := Signal.mux isOne95 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v80 + v81
  let sum0_1 := v82 + v83
  let sum0_2 := v84 + v85
  let sum0_3 := v86 + v87
  let sum0_4 := v88 + v89
  let sum0_5 := v90 + v91
  let sum0_6 := v92 + v93
  let sum0_7 := v94 + v95
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 96 to 111 -/
def popcount_group6 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted96 := input >>> 96#255
  let masked96 := shifted96 &&& 1#255
  let isOne96 := masked96 === 1#255
  let v96 := Signal.mux isOne96 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted97 := input >>> 97#255
  let masked97 := shifted97 &&& 1#255
  let isOne97 := masked97 === 1#255
  let v97 := Signal.mux isOne97 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted98 := input >>> 98#255
  let masked98 := shifted98 &&& 1#255
  let isOne98 := masked98 === 1#255
  let v98 := Signal.mux isOne98 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted99 := input >>> 99#255
  let masked99 := shifted99 &&& 1#255
  let isOne99 := masked99 === 1#255
  let v99 := Signal.mux isOne99 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted100 := input >>> 100#255
  let masked100 := shifted100 &&& 1#255
  let isOne100 := masked100 === 1#255
  let v100 := Signal.mux isOne100 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted101 := input >>> 101#255
  let masked101 := shifted101 &&& 1#255
  let isOne101 := masked101 === 1#255
  let v101 := Signal.mux isOne101 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted102 := input >>> 102#255
  let masked102 := shifted102 &&& 1#255
  let isOne102 := masked102 === 1#255
  let v102 := Signal.mux isOne102 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted103 := input >>> 103#255
  let masked103 := shifted103 &&& 1#255
  let isOne103 := masked103 === 1#255
  let v103 := Signal.mux isOne103 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted104 := input >>> 104#255
  let masked104 := shifted104 &&& 1#255
  let isOne104 := masked104 === 1#255
  let v104 := Signal.mux isOne104 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted105 := input >>> 105#255
  let masked105 := shifted105 &&& 1#255
  let isOne105 := masked105 === 1#255
  let v105 := Signal.mux isOne105 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted106 := input >>> 106#255
  let masked106 := shifted106 &&& 1#255
  let isOne106 := masked106 === 1#255
  let v106 := Signal.mux isOne106 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted107 := input >>> 107#255
  let masked107 := shifted107 &&& 1#255
  let isOne107 := masked107 === 1#255
  let v107 := Signal.mux isOne107 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted108 := input >>> 108#255
  let masked108 := shifted108 &&& 1#255
  let isOne108 := masked108 === 1#255
  let v108 := Signal.mux isOne108 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted109 := input >>> 109#255
  let masked109 := shifted109 &&& 1#255
  let isOne109 := masked109 === 1#255
  let v109 := Signal.mux isOne109 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted110 := input >>> 110#255
  let masked110 := shifted110 &&& 1#255
  let isOne110 := masked110 === 1#255
  let v110 := Signal.mux isOne110 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted111 := input >>> 111#255
  let masked111 := shifted111 &&& 1#255
  let isOne111 := masked111 === 1#255
  let v111 := Signal.mux isOne111 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v96 + v97
  let sum0_1 := v98 + v99
  let sum0_2 := v100 + v101
  let sum0_3 := v102 + v103
  let sum0_4 := v104 + v105
  let sum0_5 := v106 + v107
  let sum0_6 := v108 + v109
  let sum0_7 := v110 + v111
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 112 to 127 -/
def popcount_group7 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted112 := input >>> 112#255
  let masked112 := shifted112 &&& 1#255
  let isOne112 := masked112 === 1#255
  let v112 := Signal.mux isOne112 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted113 := input >>> 113#255
  let masked113 := shifted113 &&& 1#255
  let isOne113 := masked113 === 1#255
  let v113 := Signal.mux isOne113 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted114 := input >>> 114#255
  let masked114 := shifted114 &&& 1#255
  let isOne114 := masked114 === 1#255
  let v114 := Signal.mux isOne114 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted115 := input >>> 115#255
  let masked115 := shifted115 &&& 1#255
  let isOne115 := masked115 === 1#255
  let v115 := Signal.mux isOne115 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted116 := input >>> 116#255
  let masked116 := shifted116 &&& 1#255
  let isOne116 := masked116 === 1#255
  let v116 := Signal.mux isOne116 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted117 := input >>> 117#255
  let masked117 := shifted117 &&& 1#255
  let isOne117 := masked117 === 1#255
  let v117 := Signal.mux isOne117 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted118 := input >>> 118#255
  let masked118 := shifted118 &&& 1#255
  let isOne118 := masked118 === 1#255
  let v118 := Signal.mux isOne118 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted119 := input >>> 119#255
  let masked119 := shifted119 &&& 1#255
  let isOne119 := masked119 === 1#255
  let v119 := Signal.mux isOne119 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted120 := input >>> 120#255
  let masked120 := shifted120 &&& 1#255
  let isOne120 := masked120 === 1#255
  let v120 := Signal.mux isOne120 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted121 := input >>> 121#255
  let masked121 := shifted121 &&& 1#255
  let isOne121 := masked121 === 1#255
  let v121 := Signal.mux isOne121 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted122 := input >>> 122#255
  let masked122 := shifted122 &&& 1#255
  let isOne122 := masked122 === 1#255
  let v122 := Signal.mux isOne122 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted123 := input >>> 123#255
  let masked123 := shifted123 &&& 1#255
  let isOne123 := masked123 === 1#255
  let v123 := Signal.mux isOne123 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted124 := input >>> 124#255
  let masked124 := shifted124 &&& 1#255
  let isOne124 := masked124 === 1#255
  let v124 := Signal.mux isOne124 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted125 := input >>> 125#255
  let masked125 := shifted125 &&& 1#255
  let isOne125 := masked125 === 1#255
  let v125 := Signal.mux isOne125 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted126 := input >>> 126#255
  let masked126 := shifted126 &&& 1#255
  let isOne126 := masked126 === 1#255
  let v126 := Signal.mux isOne126 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted127 := input >>> 127#255
  let masked127 := shifted127 &&& 1#255
  let isOne127 := masked127 === 1#255
  let v127 := Signal.mux isOne127 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v112 + v113
  let sum0_1 := v114 + v115
  let sum0_2 := v116 + v117
  let sum0_3 := v118 + v119
  let sum0_4 := v120 + v121
  let sum0_5 := v122 + v123
  let sum0_6 := v124 + v125
  let sum0_7 := v126 + v127
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 128 to 143 -/
def popcount_group8 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted128 := input >>> 128#255
  let masked128 := shifted128 &&& 1#255
  let isOne128 := masked128 === 1#255
  let v128 := Signal.mux isOne128 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted129 := input >>> 129#255
  let masked129 := shifted129 &&& 1#255
  let isOne129 := masked129 === 1#255
  let v129 := Signal.mux isOne129 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted130 := input >>> 130#255
  let masked130 := shifted130 &&& 1#255
  let isOne130 := masked130 === 1#255
  let v130 := Signal.mux isOne130 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted131 := input >>> 131#255
  let masked131 := shifted131 &&& 1#255
  let isOne131 := masked131 === 1#255
  let v131 := Signal.mux isOne131 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted132 := input >>> 132#255
  let masked132 := shifted132 &&& 1#255
  let isOne132 := masked132 === 1#255
  let v132 := Signal.mux isOne132 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted133 := input >>> 133#255
  let masked133 := shifted133 &&& 1#255
  let isOne133 := masked133 === 1#255
  let v133 := Signal.mux isOne133 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted134 := input >>> 134#255
  let masked134 := shifted134 &&& 1#255
  let isOne134 := masked134 === 1#255
  let v134 := Signal.mux isOne134 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted135 := input >>> 135#255
  let masked135 := shifted135 &&& 1#255
  let isOne135 := masked135 === 1#255
  let v135 := Signal.mux isOne135 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted136 := input >>> 136#255
  let masked136 := shifted136 &&& 1#255
  let isOne136 := masked136 === 1#255
  let v136 := Signal.mux isOne136 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted137 := input >>> 137#255
  let masked137 := shifted137 &&& 1#255
  let isOne137 := masked137 === 1#255
  let v137 := Signal.mux isOne137 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted138 := input >>> 138#255
  let masked138 := shifted138 &&& 1#255
  let isOne138 := masked138 === 1#255
  let v138 := Signal.mux isOne138 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted139 := input >>> 139#255
  let masked139 := shifted139 &&& 1#255
  let isOne139 := masked139 === 1#255
  let v139 := Signal.mux isOne139 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted140 := input >>> 140#255
  let masked140 := shifted140 &&& 1#255
  let isOne140 := masked140 === 1#255
  let v140 := Signal.mux isOne140 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted141 := input >>> 141#255
  let masked141 := shifted141 &&& 1#255
  let isOne141 := masked141 === 1#255
  let v141 := Signal.mux isOne141 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted142 := input >>> 142#255
  let masked142 := shifted142 &&& 1#255
  let isOne142 := masked142 === 1#255
  let v142 := Signal.mux isOne142 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted143 := input >>> 143#255
  let masked143 := shifted143 &&& 1#255
  let isOne143 := masked143 === 1#255
  let v143 := Signal.mux isOne143 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v128 + v129
  let sum0_1 := v130 + v131
  let sum0_2 := v132 + v133
  let sum0_3 := v134 + v135
  let sum0_4 := v136 + v137
  let sum0_5 := v138 + v139
  let sum0_6 := v140 + v141
  let sum0_7 := v142 + v143
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 144 to 159 -/
def popcount_group9 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted144 := input >>> 144#255
  let masked144 := shifted144 &&& 1#255
  let isOne144 := masked144 === 1#255
  let v144 := Signal.mux isOne144 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted145 := input >>> 145#255
  let masked145 := shifted145 &&& 1#255
  let isOne145 := masked145 === 1#255
  let v145 := Signal.mux isOne145 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted146 := input >>> 146#255
  let masked146 := shifted146 &&& 1#255
  let isOne146 := masked146 === 1#255
  let v146 := Signal.mux isOne146 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted147 := input >>> 147#255
  let masked147 := shifted147 &&& 1#255
  let isOne147 := masked147 === 1#255
  let v147 := Signal.mux isOne147 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted148 := input >>> 148#255
  let masked148 := shifted148 &&& 1#255
  let isOne148 := masked148 === 1#255
  let v148 := Signal.mux isOne148 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted149 := input >>> 149#255
  let masked149 := shifted149 &&& 1#255
  let isOne149 := masked149 === 1#255
  let v149 := Signal.mux isOne149 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted150 := input >>> 150#255
  let masked150 := shifted150 &&& 1#255
  let isOne150 := masked150 === 1#255
  let v150 := Signal.mux isOne150 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted151 := input >>> 151#255
  let masked151 := shifted151 &&& 1#255
  let isOne151 := masked151 === 1#255
  let v151 := Signal.mux isOne151 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted152 := input >>> 152#255
  let masked152 := shifted152 &&& 1#255
  let isOne152 := masked152 === 1#255
  let v152 := Signal.mux isOne152 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted153 := input >>> 153#255
  let masked153 := shifted153 &&& 1#255
  let isOne153 := masked153 === 1#255
  let v153 := Signal.mux isOne153 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted154 := input >>> 154#255
  let masked154 := shifted154 &&& 1#255
  let isOne154 := masked154 === 1#255
  let v154 := Signal.mux isOne154 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted155 := input >>> 155#255
  let masked155 := shifted155 &&& 1#255
  let isOne155 := masked155 === 1#255
  let v155 := Signal.mux isOne155 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted156 := input >>> 156#255
  let masked156 := shifted156 &&& 1#255
  let isOne156 := masked156 === 1#255
  let v156 := Signal.mux isOne156 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted157 := input >>> 157#255
  let masked157 := shifted157 &&& 1#255
  let isOne157 := masked157 === 1#255
  let v157 := Signal.mux isOne157 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted158 := input >>> 158#255
  let masked158 := shifted158 &&& 1#255
  let isOne158 := masked158 === 1#255
  let v158 := Signal.mux isOne158 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted159 := input >>> 159#255
  let masked159 := shifted159 &&& 1#255
  let isOne159 := masked159 === 1#255
  let v159 := Signal.mux isOne159 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v144 + v145
  let sum0_1 := v146 + v147
  let sum0_2 := v148 + v149
  let sum0_3 := v150 + v151
  let sum0_4 := v152 + v153
  let sum0_5 := v154 + v155
  let sum0_6 := v156 + v157
  let sum0_7 := v158 + v159
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 160 to 175 -/
def popcount_group10 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted160 := input >>> 160#255
  let masked160 := shifted160 &&& 1#255
  let isOne160 := masked160 === 1#255
  let v160 := Signal.mux isOne160 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted161 := input >>> 161#255
  let masked161 := shifted161 &&& 1#255
  let isOne161 := masked161 === 1#255
  let v161 := Signal.mux isOne161 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted162 := input >>> 162#255
  let masked162 := shifted162 &&& 1#255
  let isOne162 := masked162 === 1#255
  let v162 := Signal.mux isOne162 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted163 := input >>> 163#255
  let masked163 := shifted163 &&& 1#255
  let isOne163 := masked163 === 1#255
  let v163 := Signal.mux isOne163 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted164 := input >>> 164#255
  let masked164 := shifted164 &&& 1#255
  let isOne164 := masked164 === 1#255
  let v164 := Signal.mux isOne164 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted165 := input >>> 165#255
  let masked165 := shifted165 &&& 1#255
  let isOne165 := masked165 === 1#255
  let v165 := Signal.mux isOne165 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted166 := input >>> 166#255
  let masked166 := shifted166 &&& 1#255
  let isOne166 := masked166 === 1#255
  let v166 := Signal.mux isOne166 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted167 := input >>> 167#255
  let masked167 := shifted167 &&& 1#255
  let isOne167 := masked167 === 1#255
  let v167 := Signal.mux isOne167 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted168 := input >>> 168#255
  let masked168 := shifted168 &&& 1#255
  let isOne168 := masked168 === 1#255
  let v168 := Signal.mux isOne168 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted169 := input >>> 169#255
  let masked169 := shifted169 &&& 1#255
  let isOne169 := masked169 === 1#255
  let v169 := Signal.mux isOne169 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted170 := input >>> 170#255
  let masked170 := shifted170 &&& 1#255
  let isOne170 := masked170 === 1#255
  let v170 := Signal.mux isOne170 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted171 := input >>> 171#255
  let masked171 := shifted171 &&& 1#255
  let isOne171 := masked171 === 1#255
  let v171 := Signal.mux isOne171 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted172 := input >>> 172#255
  let masked172 := shifted172 &&& 1#255
  let isOne172 := masked172 === 1#255
  let v172 := Signal.mux isOne172 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted173 := input >>> 173#255
  let masked173 := shifted173 &&& 1#255
  let isOne173 := masked173 === 1#255
  let v173 := Signal.mux isOne173 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted174 := input >>> 174#255
  let masked174 := shifted174 &&& 1#255
  let isOne174 := masked174 === 1#255
  let v174 := Signal.mux isOne174 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted175 := input >>> 175#255
  let masked175 := shifted175 &&& 1#255
  let isOne175 := masked175 === 1#255
  let v175 := Signal.mux isOne175 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v160 + v161
  let sum0_1 := v162 + v163
  let sum0_2 := v164 + v165
  let sum0_3 := v166 + v167
  let sum0_4 := v168 + v169
  let sum0_5 := v170 + v171
  let sum0_6 := v172 + v173
  let sum0_7 := v174 + v175
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 176 to 191 -/
def popcount_group11 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted176 := input >>> 176#255
  let masked176 := shifted176 &&& 1#255
  let isOne176 := masked176 === 1#255
  let v176 := Signal.mux isOne176 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted177 := input >>> 177#255
  let masked177 := shifted177 &&& 1#255
  let isOne177 := masked177 === 1#255
  let v177 := Signal.mux isOne177 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted178 := input >>> 178#255
  let masked178 := shifted178 &&& 1#255
  let isOne178 := masked178 === 1#255
  let v178 := Signal.mux isOne178 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted179 := input >>> 179#255
  let masked179 := shifted179 &&& 1#255
  let isOne179 := masked179 === 1#255
  let v179 := Signal.mux isOne179 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted180 := input >>> 180#255
  let masked180 := shifted180 &&& 1#255
  let isOne180 := masked180 === 1#255
  let v180 := Signal.mux isOne180 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted181 := input >>> 181#255
  let masked181 := shifted181 &&& 1#255
  let isOne181 := masked181 === 1#255
  let v181 := Signal.mux isOne181 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted182 := input >>> 182#255
  let masked182 := shifted182 &&& 1#255
  let isOne182 := masked182 === 1#255
  let v182 := Signal.mux isOne182 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted183 := input >>> 183#255
  let masked183 := shifted183 &&& 1#255
  let isOne183 := masked183 === 1#255
  let v183 := Signal.mux isOne183 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted184 := input >>> 184#255
  let masked184 := shifted184 &&& 1#255
  let isOne184 := masked184 === 1#255
  let v184 := Signal.mux isOne184 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted185 := input >>> 185#255
  let masked185 := shifted185 &&& 1#255
  let isOne185 := masked185 === 1#255
  let v185 := Signal.mux isOne185 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted186 := input >>> 186#255
  let masked186 := shifted186 &&& 1#255
  let isOne186 := masked186 === 1#255
  let v186 := Signal.mux isOne186 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted187 := input >>> 187#255
  let masked187 := shifted187 &&& 1#255
  let isOne187 := masked187 === 1#255
  let v187 := Signal.mux isOne187 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted188 := input >>> 188#255
  let masked188 := shifted188 &&& 1#255
  let isOne188 := masked188 === 1#255
  let v188 := Signal.mux isOne188 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted189 := input >>> 189#255
  let masked189 := shifted189 &&& 1#255
  let isOne189 := masked189 === 1#255
  let v189 := Signal.mux isOne189 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted190 := input >>> 190#255
  let masked190 := shifted190 &&& 1#255
  let isOne190 := masked190 === 1#255
  let v190 := Signal.mux isOne190 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted191 := input >>> 191#255
  let masked191 := shifted191 &&& 1#255
  let isOne191 := masked191 === 1#255
  let v191 := Signal.mux isOne191 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v176 + v177
  let sum0_1 := v178 + v179
  let sum0_2 := v180 + v181
  let sum0_3 := v182 + v183
  let sum0_4 := v184 + v185
  let sum0_5 := v186 + v187
  let sum0_6 := v188 + v189
  let sum0_7 := v190 + v191
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 192 to 207 -/
def popcount_group12 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted192 := input >>> 192#255
  let masked192 := shifted192 &&& 1#255
  let isOne192 := masked192 === 1#255
  let v192 := Signal.mux isOne192 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted193 := input >>> 193#255
  let masked193 := shifted193 &&& 1#255
  let isOne193 := masked193 === 1#255
  let v193 := Signal.mux isOne193 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted194 := input >>> 194#255
  let masked194 := shifted194 &&& 1#255
  let isOne194 := masked194 === 1#255
  let v194 := Signal.mux isOne194 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted195 := input >>> 195#255
  let masked195 := shifted195 &&& 1#255
  let isOne195 := masked195 === 1#255
  let v195 := Signal.mux isOne195 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted196 := input >>> 196#255
  let masked196 := shifted196 &&& 1#255
  let isOne196 := masked196 === 1#255
  let v196 := Signal.mux isOne196 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted197 := input >>> 197#255
  let masked197 := shifted197 &&& 1#255
  let isOne197 := masked197 === 1#255
  let v197 := Signal.mux isOne197 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted198 := input >>> 198#255
  let masked198 := shifted198 &&& 1#255
  let isOne198 := masked198 === 1#255
  let v198 := Signal.mux isOne198 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted199 := input >>> 199#255
  let masked199 := shifted199 &&& 1#255
  let isOne199 := masked199 === 1#255
  let v199 := Signal.mux isOne199 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted200 := input >>> 200#255
  let masked200 := shifted200 &&& 1#255
  let isOne200 := masked200 === 1#255
  let v200 := Signal.mux isOne200 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted201 := input >>> 201#255
  let masked201 := shifted201 &&& 1#255
  let isOne201 := masked201 === 1#255
  let v201 := Signal.mux isOne201 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted202 := input >>> 202#255
  let masked202 := shifted202 &&& 1#255
  let isOne202 := masked202 === 1#255
  let v202 := Signal.mux isOne202 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted203 := input >>> 203#255
  let masked203 := shifted203 &&& 1#255
  let isOne203 := masked203 === 1#255
  let v203 := Signal.mux isOne203 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted204 := input >>> 204#255
  let masked204 := shifted204 &&& 1#255
  let isOne204 := masked204 === 1#255
  let v204 := Signal.mux isOne204 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted205 := input >>> 205#255
  let masked205 := shifted205 &&& 1#255
  let isOne205 := masked205 === 1#255
  let v205 := Signal.mux isOne205 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted206 := input >>> 206#255
  let masked206 := shifted206 &&& 1#255
  let isOne206 := masked206 === 1#255
  let v206 := Signal.mux isOne206 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted207 := input >>> 207#255
  let masked207 := shifted207 &&& 1#255
  let isOne207 := masked207 === 1#255
  let v207 := Signal.mux isOne207 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v192 + v193
  let sum0_1 := v194 + v195
  let sum0_2 := v196 + v197
  let sum0_3 := v198 + v199
  let sum0_4 := v200 + v201
  let sum0_5 := v202 + v203
  let sum0_6 := v204 + v205
  let sum0_7 := v206 + v207
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 208 to 223 -/
def popcount_group13 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted208 := input >>> 208#255
  let masked208 := shifted208 &&& 1#255
  let isOne208 := masked208 === 1#255
  let v208 := Signal.mux isOne208 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted209 := input >>> 209#255
  let masked209 := shifted209 &&& 1#255
  let isOne209 := masked209 === 1#255
  let v209 := Signal.mux isOne209 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted210 := input >>> 210#255
  let masked210 := shifted210 &&& 1#255
  let isOne210 := masked210 === 1#255
  let v210 := Signal.mux isOne210 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted211 := input >>> 211#255
  let masked211 := shifted211 &&& 1#255
  let isOne211 := masked211 === 1#255
  let v211 := Signal.mux isOne211 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted212 := input >>> 212#255
  let masked212 := shifted212 &&& 1#255
  let isOne212 := masked212 === 1#255
  let v212 := Signal.mux isOne212 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted213 := input >>> 213#255
  let masked213 := shifted213 &&& 1#255
  let isOne213 := masked213 === 1#255
  let v213 := Signal.mux isOne213 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted214 := input >>> 214#255
  let masked214 := shifted214 &&& 1#255
  let isOne214 := masked214 === 1#255
  let v214 := Signal.mux isOne214 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted215 := input >>> 215#255
  let masked215 := shifted215 &&& 1#255
  let isOne215 := masked215 === 1#255
  let v215 := Signal.mux isOne215 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted216 := input >>> 216#255
  let masked216 := shifted216 &&& 1#255
  let isOne216 := masked216 === 1#255
  let v216 := Signal.mux isOne216 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted217 := input >>> 217#255
  let masked217 := shifted217 &&& 1#255
  let isOne217 := masked217 === 1#255
  let v217 := Signal.mux isOne217 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted218 := input >>> 218#255
  let masked218 := shifted218 &&& 1#255
  let isOne218 := masked218 === 1#255
  let v218 := Signal.mux isOne218 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted219 := input >>> 219#255
  let masked219 := shifted219 &&& 1#255
  let isOne219 := masked219 === 1#255
  let v219 := Signal.mux isOne219 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted220 := input >>> 220#255
  let masked220 := shifted220 &&& 1#255
  let isOne220 := masked220 === 1#255
  let v220 := Signal.mux isOne220 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted221 := input >>> 221#255
  let masked221 := shifted221 &&& 1#255
  let isOne221 := masked221 === 1#255
  let v221 := Signal.mux isOne221 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted222 := input >>> 222#255
  let masked222 := shifted222 &&& 1#255
  let isOne222 := masked222 === 1#255
  let v222 := Signal.mux isOne222 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted223 := input >>> 223#255
  let masked223 := shifted223 &&& 1#255
  let isOne223 := masked223 === 1#255
  let v223 := Signal.mux isOne223 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v208 + v209
  let sum0_1 := v210 + v211
  let sum0_2 := v212 + v213
  let sum0_3 := v214 + v215
  let sum0_4 := v216 + v217
  let sum0_5 := v218 + v219
  let sum0_6 := v220 + v221
  let sum0_7 := v222 + v223
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 224 to 239 -/
def popcount_group14 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted224 := input >>> 224#255
  let masked224 := shifted224 &&& 1#255
  let isOne224 := masked224 === 1#255
  let v224 := Signal.mux isOne224 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted225 := input >>> 225#255
  let masked225 := shifted225 &&& 1#255
  let isOne225 := masked225 === 1#255
  let v225 := Signal.mux isOne225 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted226 := input >>> 226#255
  let masked226 := shifted226 &&& 1#255
  let isOne226 := masked226 === 1#255
  let v226 := Signal.mux isOne226 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted227 := input >>> 227#255
  let masked227 := shifted227 &&& 1#255
  let isOne227 := masked227 === 1#255
  let v227 := Signal.mux isOne227 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted228 := input >>> 228#255
  let masked228 := shifted228 &&& 1#255
  let isOne228 := masked228 === 1#255
  let v228 := Signal.mux isOne228 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted229 := input >>> 229#255
  let masked229 := shifted229 &&& 1#255
  let isOne229 := masked229 === 1#255
  let v229 := Signal.mux isOne229 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted230 := input >>> 230#255
  let masked230 := shifted230 &&& 1#255
  let isOne230 := masked230 === 1#255
  let v230 := Signal.mux isOne230 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted231 := input >>> 231#255
  let masked231 := shifted231 &&& 1#255
  let isOne231 := masked231 === 1#255
  let v231 := Signal.mux isOne231 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted232 := input >>> 232#255
  let masked232 := shifted232 &&& 1#255
  let isOne232 := masked232 === 1#255
  let v232 := Signal.mux isOne232 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted233 := input >>> 233#255
  let masked233 := shifted233 &&& 1#255
  let isOne233 := masked233 === 1#255
  let v233 := Signal.mux isOne233 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted234 := input >>> 234#255
  let masked234 := shifted234 &&& 1#255
  let isOne234 := masked234 === 1#255
  let v234 := Signal.mux isOne234 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted235 := input >>> 235#255
  let masked235 := shifted235 &&& 1#255
  let isOne235 := masked235 === 1#255
  let v235 := Signal.mux isOne235 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted236 := input >>> 236#255
  let masked236 := shifted236 &&& 1#255
  let isOne236 := masked236 === 1#255
  let v236 := Signal.mux isOne236 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted237 := input >>> 237#255
  let masked237 := shifted237 &&& 1#255
  let isOne237 := masked237 === 1#255
  let v237 := Signal.mux isOne237 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted238 := input >>> 238#255
  let masked238 := shifted238 &&& 1#255
  let isOne238 := masked238 === 1#255
  let v238 := Signal.mux isOne238 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted239 := input >>> 239#255
  let masked239 := shifted239 &&& 1#255
  let isOne239 := masked239 === 1#255
  let v239 := Signal.mux isOne239 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v224 + v225
  let sum0_1 := v226 + v227
  let sum0_2 := v228 + v229
  let sum0_3 := v230 + v231
  let sum0_4 := v232 + v233
  let sum0_5 := v234 + v235
  let sum0_6 := v236 + v237
  let sum0_7 := v238 + v239
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + sum0_7
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Count bits 240 to 254 -/
def popcount_group15 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let shifted240 := input >>> 240#255
  let masked240 := shifted240 &&& 1#255
  let isOne240 := masked240 === 1#255
  let v240 := Signal.mux isOne240 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted241 := input >>> 241#255
  let masked241 := shifted241 &&& 1#255
  let isOne241 := masked241 === 1#255
  let v241 := Signal.mux isOne241 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted242 := input >>> 242#255
  let masked242 := shifted242 &&& 1#255
  let isOne242 := masked242 === 1#255
  let v242 := Signal.mux isOne242 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted243 := input >>> 243#255
  let masked243 := shifted243 &&& 1#255
  let isOne243 := masked243 === 1#255
  let v243 := Signal.mux isOne243 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted244 := input >>> 244#255
  let masked244 := shifted244 &&& 1#255
  let isOne244 := masked244 === 1#255
  let v244 := Signal.mux isOne244 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted245 := input >>> 245#255
  let masked245 := shifted245 &&& 1#255
  let isOne245 := masked245 === 1#255
  let v245 := Signal.mux isOne245 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted246 := input >>> 246#255
  let masked246 := shifted246 &&& 1#255
  let isOne246 := masked246 === 1#255
  let v246 := Signal.mux isOne246 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted247 := input >>> 247#255
  let masked247 := shifted247 &&& 1#255
  let isOne247 := masked247 === 1#255
  let v247 := Signal.mux isOne247 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted248 := input >>> 248#255
  let masked248 := shifted248 &&& 1#255
  let isOne248 := masked248 === 1#255
  let v248 := Signal.mux isOne248 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted249 := input >>> 249#255
  let masked249 := shifted249 &&& 1#255
  let isOne249 := masked249 === 1#255
  let v249 := Signal.mux isOne249 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted250 := input >>> 250#255
  let masked250 := shifted250 &&& 1#255
  let isOne250 := masked250 === 1#255
  let v250 := Signal.mux isOne250 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted251 := input >>> 251#255
  let masked251 := shifted251 &&& 1#255
  let isOne251 := masked251 === 1#255
  let v251 := Signal.mux isOne251 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted252 := input >>> 252#255
  let masked252 := shifted252 &&& 1#255
  let isOne252 := masked252 === 1#255
  let v252 := Signal.mux isOne252 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted253 := input >>> 253#255
  let masked253 := shifted253 &&& 1#255
  let isOne253 := masked253 === 1#255
  let v253 := Signal.mux isOne253 (Signal.pure 1#8) (Signal.pure 0#8)
  let shifted254 := input >>> 254#255
  let masked254 := shifted254 &&& 1#255
  let isOne254 := masked254 === 1#255
  let v254 := Signal.mux isOne254 (Signal.pure 1#8) (Signal.pure 0#8)
  let sum0_0 := v240 + v241
  let sum0_1 := v242 + v243
  let sum0_2 := v244 + v245
  let sum0_3 := v246 + v247
  let sum0_4 := v248 + v249
  let sum0_5 := v250 + v251
  let sum0_6 := v252 + v253
  let sum1_0 := sum0_0 + sum0_1
  let sum1_1 := sum0_2 + sum0_3
  let sum1_2 := sum0_4 + sum0_5
  let sum1_3 := sum0_6 + v254
  let sum2_0 := sum1_0 + sum1_1
  let sum2_1 := sum1_2 + sum1_3
  let sum3_0 := sum2_0 + sum2_1
  sum3_0

/-- Population count: counts the number of '1's in a 255-bit input vector. -/
def prob030_popcount255 {dom : DomainConfig}
    (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let group0_sum := popcount_group0 input
  let group1_sum := popcount_group1 input
  let group2_sum := popcount_group2 input
  let group3_sum := popcount_group3 input
  let group4_sum := popcount_group4 input
  let group5_sum := popcount_group5 input
  let group6_sum := popcount_group6 input
  let group7_sum := popcount_group7 input
  let group8_sum := popcount_group8 input
  let group9_sum := popcount_group9 input
  let group10_sum := popcount_group10 input
  let group11_sum := popcount_group11 input
  let group12_sum := popcount_group12 input
  let group13_sum := popcount_group13 input
  let group14_sum := popcount_group14 input
  let group15_sum := popcount_group15 input
  let final_sum0_0 := group0_sum + group1_sum
  let final_sum0_1 := group2_sum + group3_sum
  let final_sum0_2 := group4_sum + group5_sum
  let final_sum0_3 := group6_sum + group7_sum
  let final_sum0_4 := group8_sum + group9_sum
  let final_sum0_5 := group10_sum + group11_sum
  let final_sum0_6 := group12_sum + group13_sum
  let final_sum0_7 := group14_sum + group15_sum
  let final_sum1_0 := final_sum0_0 + final_sum0_1
  let final_sum1_1 := final_sum0_2 + final_sum0_3
  let final_sum1_2 := final_sum0_4 + final_sum0_5
  let final_sum1_3 := final_sum0_6 + final_sum0_7
  let final_sum2_0 := final_sum1_0 + final_sum1_1
  let final_sum2_1 := final_sum1_2 + final_sum1_3
  let final_sum3_0 := final_sum2_0 + final_sum2_1
  final_sum3_0

#synthesizeVerilog prob030_popcount255
