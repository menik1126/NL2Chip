import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 100-input AND reduction -/
def test_and100_full {dom : DomainConfig}
    (in_ : Signal dom (BitVec 100)) : Signal dom (BitVec 1) :=
  Signal.map (fun v =>
    let b0 := BitVec.extractLsb' 0 1 v
    let b1 := BitVec.extractLsb' 1 1 v
    let b2 := BitVec.extractLsb' 2 1 v
    let b3 := BitVec.extractLsb' 3 1 v
    let b4 := BitVec.extractLsb' 4 1 v
    let b5 := BitVec.extractLsb' 5 1 v
    let b6 := BitVec.extractLsb' 6 1 v
    let b7 := BitVec.extractLsb' 7 1 v
    let b8 := BitVec.extractLsb' 8 1 v
    let b9 := BitVec.extractLsb' 9 1 v
    let b10 := BitVec.extractLsb' 10 1 v
    let b11 := BitVec.extractLsb' 11 1 v
    let b12 := BitVec.extractLsb' 12 1 v
    let b13 := BitVec.extractLsb' 13 1 v
    let b14 := BitVec.extractLsb' 14 1 v
    let b15 := BitVec.extractLsb' 15 1 v
    let b16 := BitVec.extractLsb' 16 1 v
    let b17 := BitVec.extractLsb' 17 1 v
    let b18 := BitVec.extractLsb' 18 1 v
    let b19 := BitVec.extractLsb' 19 1 v
    let b20 := BitVec.extractLsb' 20 1 v
    let b21 := BitVec.extractLsb' 21 1 v
    let b22 := BitVec.extractLsb' 22 1 v
    let b23 := BitVec.extractLsb' 23 1 v
    let b24 := BitVec.extractLsb' 24 1 v
    let b25 := BitVec.extractLsb' 25 1 v
    let b26 := BitVec.extractLsb' 26 1 v
    let b27 := BitVec.extractLsb' 27 1 v
    let b28 := BitVec.extractLsb' 28 1 v
    let b29 := BitVec.extractLsb' 29 1 v
    let b30 := BitVec.extractLsb' 30 1 v
    let b31 := BitVec.extractLsb' 31 1 v
    let b32 := BitVec.extractLsb' 32 1 v
    let b33 := BitVec.extractLsb' 33 1 v
    let b34 := BitVec.extractLsb' 34 1 v
    let b35 := BitVec.extractLsb' 35 1 v
    let b36 := BitVec.extractLsb' 36 1 v
    let b37 := BitVec.extractLsb' 37 1 v
    let b38 := BitVec.extractLsb' 38 1 v
    let b39 := BitVec.extractLsb' 39 1 v
    let b40 := BitVec.extractLsb' 40 1 v
    let b41 := BitVec.extractLsb' 41 1 v
    let b42 := BitVec.extractLsb' 42 1 v
    let b43 := BitVec.extractLsb' 43 1 v
    let b44 := BitVec.extractLsb' 44 1 v
    let b45 := BitVec.extractLsb' 45 1 v
    let b46 := BitVec.extractLsb' 46 1 v
    let b47 := BitVec.extractLsb' 47 1 v
    let b48 := BitVec.extractLsb' 48 1 v
    let b49 := BitVec.extractLsb' 49 1 v
    let b50 := BitVec.extractLsb' 50 1 v
    let b51 := BitVec.extractLsb' 51 1 v
    let b52 := BitVec.extractLsb' 52 1 v
    let b53 := BitVec.extractLsb' 53 1 v
    let b54 := BitVec.extractLsb' 54 1 v
    let b55 := BitVec.extractLsb' 55 1 v
    let b56 := BitVec.extractLsb' 56 1 v
    let b57 := BitVec.extractLsb' 57 1 v
    let b58 := BitVec.extractLsb' 58 1 v
    let b59 := BitVec.extractLsb' 59 1 v
    let b60 := BitVec.extractLsb' 60 1 v
    let b61 := BitVec.extractLsb' 61 1 v
    let b62 := BitVec.extractLsb' 62 1 v
    let b63 := BitVec.extractLsb' 63 1 v
    let b64 := BitVec.extractLsb' 64 1 v
    let b65 := BitVec.extractLsb' 65 1 v
    let b66 := BitVec.extractLsb' 66 1 v
    let b67 := BitVec.extractLsb' 67 1 v
    let b68 := BitVec.extractLsb' 68 1 v
    let b69 := BitVec.extractLsb' 69 1 v
    let b70 := BitVec.extractLsb' 70 1 v
    let b71 := BitVec.extractLsb' 71 1 v
    let b72 := BitVec.extractLsb' 72 1 v
    let b73 := BitVec.extractLsb' 73 1 v
    let b74 := BitVec.extractLsb' 74 1 v
    let b75 := BitVec.extractLsb' 75 1 v
    let b76 := BitVec.extractLsb' 76 1 v
    let b77 := BitVec.extractLsb' 77 1 v
    let b78 := BitVec.extractLsb' 78 1 v
    let b79 := BitVec.extractLsb' 79 1 v
    let b80 := BitVec.extractLsb' 80 1 v
    let b81 := BitVec.extractLsb' 81 1 v
    let b82 := BitVec.extractLsb' 82 1 v
    let b83 := BitVec.extractLsb' 83 1 v
    let b84 := BitVec.extractLsb' 84 1 v
    let b85 := BitVec.extractLsb' 85 1 v
    let b86 := BitVec.extractLsb' 86 1 v
    let b87 := BitVec.extractLsb' 87 1 v
    let b88 := BitVec.extractLsb' 88 1 v
    let b89 := BitVec.extractLsb' 89 1 v
    let b90 := BitVec.extractLsb' 90 1 v
    let b91 := BitVec.extractLsb' 91 1 v
    let b92 := BitVec.extractLsb' 92 1 v
    let b93 := BitVec.extractLsb' 93 1 v
    let b94 := BitVec.extractLsb' 94 1 v
    let b95 := BitVec.extractLsb' 95 1 v
    let b96 := BitVec.extractLsb' 96 1 v
    let b97 := BitVec.extractLsb' 97 1 v
    let b98 := BitVec.extractLsb' 98 1 v
    let b99 := BitVec.extractLsb' 99 1 v
    b0 &&& b1 &&& b2 &&& b3 &&& b4 &&& b5 &&& b6 &&& b7 &&& b8 &&& b9 &&& b10 &&& b11 &&& b12 &&& b13 &&& b14 &&& b15 &&& b16 &&& b17 &&& b18 &&& b19 &&& b20 &&& b21 &&& b22 &&& b23 &&& b24 &&& b25 &&& b26 &&& b27 &&& b28 &&& b29 &&& b30 &&& b31 &&& b32 &&& b33 &&& b34 &&& b35 &&& b36 &&& b37 &&& b38 &&& b39 &&& b40 &&& b41 &&& b42 &&& b43 &&& b44 &&& b45 &&& b46 &&& b47 &&& b48 &&& b49 &&& b50 &&& b51 &&& b52 &&& b53 &&& b54 &&& b55 &&& b56 &&& b57 &&& b58 &&& b59 &&& b60 &&& b61 &&& b62 &&& b63 &&& b64 &&& b65 &&& b66 &&& b67 &&& b68 &&& b69 &&& b70 &&& b71 &&& b72 &&& b73 &&& b74 &&& b75 &&& b76 &&& b77 &&& b78 &&& b79 &&& b80 &&& b81 &&& b82 &&& b83 &&& b84 &&& b85 &&& b86 &&& b87 &&& b88 &&& b89 &&& b90 &&& b91 &&& b92 &&& b93 &&& b94 &&& b95 &&& b96 &&& b97 &&& b98 &&& b99) in_

#synthesizeVerilog test_and100_full
