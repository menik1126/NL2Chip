import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private def reverseBits100 (x : BitVec 100) : BitVec 100 :=
  let b0 := ((x.extractLsb 0 0).zeroExtend 100).shiftLeft 99
  let b1 := ((x.extractLsb 1 1).zeroExtend 100).shiftLeft 98
  let b2 := ((x.extractLsb 2 2).zeroExtend 100).shiftLeft 97
  let b3 := ((x.extractLsb 3 3).zeroExtend 100).shiftLeft 96
  let b4 := ((x.extractLsb 4 4).zeroExtend 100).shiftLeft 95
  let b5 := ((x.extractLsb 5 5).zeroExtend 100).shiftLeft 94
  let b6 := ((x.extractLsb 6 6).zeroExtend 100).shiftLeft 93
  let b7 := ((x.extractLsb 7 7).zeroExtend 100).shiftLeft 92
  let b8 := ((x.extractLsb 8 8).zeroExtend 100).shiftLeft 91
  let b9 := ((x.extractLsb 9 9).zeroExtend 100).shiftLeft 90
  let b10 := ((x.extractLsb 10 10).zeroExtend 100).shiftLeft 89
  let b11 := ((x.extractLsb 11 11).zeroExtend 100).shiftLeft 88
  let b12 := ((x.extractLsb 12 12).zeroExtend 100).shiftLeft 87
  let b13 := ((x.extractLsb 13 13).zeroExtend 100).shiftLeft 86
  let b14 := ((x.extractLsb 14 14).zeroExtend 100).shiftLeft 85
  let b15 := ((x.extractLsb 15 15).zeroExtend 100).shiftLeft 84
  let b16 := ((x.extractLsb 16 16).zeroExtend 100).shiftLeft 83
  let b17 := ((x.extractLsb 17 17).zeroExtend 100).shiftLeft 82
  let b18 := ((x.extractLsb 18 18).zeroExtend 100).shiftLeft 81
  let b19 := ((x.extractLsb 19 19).zeroExtend 100).shiftLeft 80
  let b20 := ((x.extractLsb 20 20).zeroExtend 100).shiftLeft 79
  let b21 := ((x.extractLsb 21 21).zeroExtend 100).shiftLeft 78
  let b22 := ((x.extractLsb 22 22).zeroExtend 100).shiftLeft 77
  let b23 := ((x.extractLsb 23 23).zeroExtend 100).shiftLeft 76
  let b24 := ((x.extractLsb 24 24).zeroExtend 100).shiftLeft 75
  let b25 := ((x.extractLsb 25 25).zeroExtend 100).shiftLeft 74
  let b26 := ((x.extractLsb 26 26).zeroExtend 100).shiftLeft 73
  let b27 := ((x.extractLsb 27 27).zeroExtend 100).shiftLeft 72
  let b28 := ((x.extractLsb 28 28).zeroExtend 100).shiftLeft 71
  let b29 := ((x.extractLsb 29 29).zeroExtend 100).shiftLeft 70
  let b30 := ((x.extractLsb 30 30).zeroExtend 100).shiftLeft 69
  let b31 := ((x.extractLsb 31 31).zeroExtend 100).shiftLeft 68
  let b32 := ((x.extractLsb 32 32).zeroExtend 100).shiftLeft 67
  let b33 := ((x.extractLsb 33 33).zeroExtend 100).shiftLeft 66
  let b34 := ((x.extractLsb 34 34).zeroExtend 100).shiftLeft 65
  let b35 := ((x.extractLsb 35 35).zeroExtend 100).shiftLeft 64
  let b36 := ((x.extractLsb 36 36).zeroExtend 100).shiftLeft 63
  let b37 := ((x.extractLsb 37 37).zeroExtend 100).shiftLeft 62
  let b38 := ((x.extractLsb 38 38).zeroExtend 100).shiftLeft 61
  let b39 := ((x.extractLsb 39 39).zeroExtend 100).shiftLeft 60
  let b40 := ((x.extractLsb 40 40).zeroExtend 100).shiftLeft 59
  let b41 := ((x.extractLsb 41 41).zeroExtend 100).shiftLeft 58
  let b42 := ((x.extractLsb 42 42).zeroExtend 100).shiftLeft 57
  let b43 := ((x.extractLsb 43 43).zeroExtend 100).shiftLeft 56
  let b44 := ((x.extractLsb 44 44).zeroExtend 100).shiftLeft 55
  let b45 := ((x.extractLsb 45 45).zeroExtend 100).shiftLeft 54
  let b46 := ((x.extractLsb 46 46).zeroExtend 100).shiftLeft 53
  let b47 := ((x.extractLsb 47 47).zeroExtend 100).shiftLeft 52
  let b48 := ((x.extractLsb 48 48).zeroExtend 100).shiftLeft 51
  let b49 := ((x.extractLsb 49 49).zeroExtend 100).shiftLeft 50
  let b50 := ((x.extractLsb 50 50).zeroExtend 100).shiftLeft 49
  let b51 := ((x.extractLsb 51 51).zeroExtend 100).shiftLeft 48
  let b52 := ((x.extractLsb 52 52).zeroExtend 100).shiftLeft 47
  let b53 := ((x.extractLsb 53 53).zeroExtend 100).shiftLeft 46
  let b54 := ((x.extractLsb 54 54).zeroExtend 100).shiftLeft 45
  let b55 := ((x.extractLsb 55 55).zeroExtend 100).shiftLeft 44
  let b56 := ((x.extractLsb 56 56).zeroExtend 100).shiftLeft 43
  let b57 := ((x.extractLsb 57 57).zeroExtend 100).shiftLeft 42
  let b58 := ((x.extractLsb 58 58).zeroExtend 100).shiftLeft 41
  let b59 := ((x.extractLsb 59 59).zeroExtend 100).shiftLeft 40
  let b60 := ((x.extractLsb 60 60).zeroExtend 100).shiftLeft 39
  let b61 := ((x.extractLsb 61 61).zeroExtend 100).shiftLeft 38
  let b62 := ((x.extractLsb 62 62).zeroExtend 100).shiftLeft 37
  let b63 := ((x.extractLsb 63 63).zeroExtend 100).shiftLeft 36
  let b64 := ((x.extractLsb 64 64).zeroExtend 100).shiftLeft 35
  let b65 := ((x.extractLsb 65 65).zeroExtend 100).shiftLeft 34
  let b66 := ((x.extractLsb 66 66).zeroExtend 100).shiftLeft 33
  let b67 := ((x.extractLsb 67 67).zeroExtend 100).shiftLeft 32
  let b68 := ((x.extractLsb 68 68).zeroExtend 100).shiftLeft 31
  let b69 := ((x.extractLsb 69 69).zeroExtend 100).shiftLeft 30
  let b70 := ((x.extractLsb 70 70).zeroExtend 100).shiftLeft 29
  let b71 := ((x.extractLsb 71 71).zeroExtend 100).shiftLeft 28
  let b72 := ((x.extractLsb 72 72).zeroExtend 100).shiftLeft 27
  let b73 := ((x.extractLsb 73 73).zeroExtend 100).shiftLeft 26
  let b74 := ((x.extractLsb 74 74).zeroExtend 100).shiftLeft 25
  let b75 := ((x.extractLsb 75 75).zeroExtend 100).shiftLeft 24
  let b76 := ((x.extractLsb 76 76).zeroExtend 100).shiftLeft 23
  let b77 := ((x.extractLsb 77 77).zeroExtend 100).shiftLeft 22
  let b78 := ((x.extractLsb 78 78).zeroExtend 100).shiftLeft 21
  let b79 := ((x.extractLsb 79 79).zeroExtend 100).shiftLeft 20
  let b80 := ((x.extractLsb 80 80).zeroExtend 100).shiftLeft 19
  let b81 := ((x.extractLsb 81 81).zeroExtend 100).shiftLeft 18
  let b82 := ((x.extractLsb 82 82).zeroExtend 100).shiftLeft 17
  let b83 := ((x.extractLsb 83 83).zeroExtend 100).shiftLeft 16
  let b84 := ((x.extractLsb 84 84).zeroExtend 100).shiftLeft 15
  let b85 := ((x.extractLsb 85 85).zeroExtend 100).shiftLeft 14
  let b86 := ((x.extractLsb 86 86).zeroExtend 100).shiftLeft 13
  let b87 := ((x.extractLsb 87 87).zeroExtend 100).shiftLeft 12
  let b88 := ((x.extractLsb 88 88).zeroExtend 100).shiftLeft 11
  let b89 := ((x.extractLsb 89 89).zeroExtend 100).shiftLeft 10
  let b90 := ((x.extractLsb 90 90).zeroExtend 100).shiftLeft 9
  let b91 := ((x.extractLsb 91 91).zeroExtend 100).shiftLeft 8
  let b92 := ((x.extractLsb 92 92).zeroExtend 100).shiftLeft 7
  let b93 := ((x.extractLsb 93 93).zeroExtend 100).shiftLeft 6
  let b94 := ((x.extractLsb 94 94).zeroExtend 100).shiftLeft 5
  let b95 := ((x.extractLsb 95 95).zeroExtend 100).shiftLeft 4
  let b96 := ((x.extractLsb 96 96).zeroExtend 100).shiftLeft 3
  let b97 := ((x.extractLsb 97 97).zeroExtend 100).shiftLeft 2
  let b98 := ((x.extractLsb 98 98).zeroExtend 100).shiftLeft 1
  let b99 := ((x.extractLsb 99 99).zeroExtend 100).shiftLeft 0
  b0 ||| b1 ||| b2 ||| b3 ||| b4 ||| b5 ||| b6 ||| b7 ||| b8 ||| b9 |||
  b10 ||| b11 ||| b12 ||| b13 ||| b14 ||| b15 ||| b16 ||| b17 ||| b18 ||| b19 |||
  b20 ||| b21 ||| b22 ||| b23 ||| b24 ||| b25 ||| b26 ||| b27 ||| b28 ||| b29 |||
  b30 ||| b31 ||| b32 ||| b33 ||| b34 ||| b35 ||| b36 ||| b37 ||| b38 ||| b39 |||
  b40 ||| b41 ||| b42 ||| b43 ||| b44 ||| b45 ||| b46 ||| b47 ||| b48 ||| b49 |||
  b50 ||| b51 ||| b52 ||| b53 ||| b54 ||| b55 ||| b56 ||| b57 ||| b58 ||| b59 |||
  b60 ||| b61 ||| b62 ||| b63 ||| b64 ||| b65 ||| b66 ||| b67 ||| b68 ||| b69 |||
  b70 ||| b71 ||| b72 ||| b73 ||| b74 ||| b75 ||| b76 ||| b77 ||| b78 ||| b79 |||
  b80 ||| b81 ||| b82 ||| b83 ||| b84 ||| b85 ||| b86 ||| b87 ||| b88 ||| b89 |||
  b90 ||| b91 ||| b92 ||| b93 ||| b94 ||| b95 ||| b96 ||| b97 ||| b98 ||| b99

/-- Reverse the bit ordering of a 100-bit input vector. -/
def prob023_vector100r {dom : DomainConfig}
    (input : Signal dom (BitVec 100)) : Signal dom (BitVec 100) :=
  Signal.map reverseBits100 input

#synthesizeVerilog prob023_vector100r
