import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

def symbolicAllOnes {dom : DomainConfig} {W : Nat}
    (value : Signal dom (BitVec W)) : Signal dom (BitVec 1) :=
  boolToBV1 (allOnes value)

#synthesizeParameterizedVerilog symbolicAllOnes [W := 3]
