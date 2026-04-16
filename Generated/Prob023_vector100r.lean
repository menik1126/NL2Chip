import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Reverse the bit ordering of a 100-bit input signal. -/
def prob023_vector100r {dom : DomainConfig}
    (inp : Signal dom (BitVec 100)) : Signal dom (BitVec 100) :=
  Signal.map (fun v =>
    BitVec.extractLsb' 0 1 v ++
    BitVec.extractLsb' 1 1 v ++
    BitVec.extractLsb' 2 1 v ++
    BitVec.extractLsb' 3 1 v ++
    BitVec.extractLsb' 4 1 v ++
    BitVec.extractLsb' 5 1 v ++
    BitVec.extractLsb' 6 1 v ++
    BitVec.extractLsb' 7 1 v ++
    BitVec.extractLsb' 8 1 v ++
    BitVec.extractLsb' 9 1 v ++
    BitVec.extractLsb' 10 1 v ++
    BitVec.extractLsb' 11 1 v ++
    BitVec.extractLsb' 12 1 v ++
    BitVec.extractLsb' 13 1 v ++
    BitVec.extractLsb' 14 1 v ++
    BitVec.extractLsb' 15 1 v ++
    BitVec.extractLsb' 16 1 v ++
    BitVec.extractLsb' 17 1 v ++
    BitVec.extractLsb' 18 1 v ++
    BitVec.extractLsb' 19 1 v ++
    BitVec.extractLsb' 20 1 v ++
    BitVec.extractLsb' 21 1 v ++
    BitVec.extractLsb' 22 1 v ++
    BitVec.extractLsb' 23 1 v ++
    BitVec.extractLsb' 24 1 v ++
    BitVec.extractLsb' 25 1 v ++
    BitVec.extractLsb' 26 1 v ++
    BitVec.extractLsb' 27 1 v ++
    BitVec.extractLsb' 28 1 v ++
    BitVec.extractLsb' 29 1 v ++
    BitVec.extractLsb' 30 1 v ++
    BitVec.extractLsb' 31 1 v ++
    BitVec.extractLsb' 32 1 v ++
    BitVec.extractLsb' 33 1 v ++
    BitVec.extractLsb' 34 1 v ++
    BitVec.extractLsb' 35 1 v ++
    BitVec.extractLsb' 36 1 v ++
    BitVec.extractLsb' 37 1 v ++
    BitVec.extractLsb' 38 1 v ++
    BitVec.extractLsb' 39 1 v ++
    BitVec.extractLsb' 40 1 v ++
    BitVec.extractLsb' 41 1 v ++
    BitVec.extractLsb' 42 1 v ++
    BitVec.extractLsb' 43 1 v ++
    BitVec.extractLsb' 44 1 v ++
    BitVec.extractLsb' 45 1 v ++
    BitVec.extractLsb' 46 1 v ++
    BitVec.extractLsb' 47 1 v ++
    BitVec.extractLsb' 48 1 v ++
    BitVec.extractLsb' 49 1 v ++
    BitVec.extractLsb' 50 1 v ++
    BitVec.extractLsb' 51 1 v ++
    BitVec.extractLsb' 52 1 v ++
    BitVec.extractLsb' 53 1 v ++
    BitVec.extractLsb' 54 1 v ++
    BitVec.extractLsb' 55 1 v ++
    BitVec.extractLsb' 56 1 v ++
    BitVec.extractLsb' 57 1 v ++
    BitVec.extractLsb' 58 1 v ++
    BitVec.extractLsb' 59 1 v ++
    BitVec.extractLsb' 60 1 v ++
    BitVec.extractLsb' 61 1 v ++
    BitVec.extractLsb' 62 1 v ++
    BitVec.extractLsb' 63 1 v ++
    BitVec.extractLsb' 64 1 v ++
    BitVec.extractLsb' 65 1 v ++
    BitVec.extractLsb' 66 1 v ++
    BitVec.extractLsb' 67 1 v ++
    BitVec.extractLsb' 68 1 v ++
    BitVec.extractLsb' 69 1 v ++
    BitVec.extractLsb' 70 1 v ++
    BitVec.extractLsb' 71 1 v ++
    BitVec.extractLsb' 72 1 v ++
    BitVec.extractLsb' 73 1 v ++
    BitVec.extractLsb' 74 1 v ++
    BitVec.extractLsb' 75 1 v ++
    BitVec.extractLsb' 76 1 v ++
    BitVec.extractLsb' 77 1 v ++
    BitVec.extractLsb' 78 1 v ++
    BitVec.extractLsb' 79 1 v ++
    BitVec.extractLsb' 80 1 v ++
    BitVec.extractLsb' 81 1 v ++
    BitVec.extractLsb' 82 1 v ++
    BitVec.extractLsb' 83 1 v ++
    BitVec.extractLsb' 84 1 v ++
    BitVec.extractLsb' 85 1 v ++
    BitVec.extractLsb' 86 1 v ++
    BitVec.extractLsb' 87 1 v ++
    BitVec.extractLsb' 88 1 v ++
    BitVec.extractLsb' 89 1 v ++
    BitVec.extractLsb' 90 1 v ++
    BitVec.extractLsb' 91 1 v ++
    BitVec.extractLsb' 92 1 v ++
    BitVec.extractLsb' 93 1 v ++
    BitVec.extractLsb' 94 1 v ++
    BitVec.extractLsb' 95 1 v ++
    BitVec.extractLsb' 96 1 v ++
    BitVec.extractLsb' 97 1 v ++
    BitVec.extractLsb' 98 1 v ++
    BitVec.extractLsb' 99 1 v
  ) inp

#synthesizeVerilog prob023_vector100r