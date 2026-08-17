import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- Counts the bit positions at which the two input vectors differ. -/
def Bit_Difference_Counter {dom : DomainConfig} {BIT_WIDTH : Nat}
    (input_A input_B : Signal dom (BitVec BIT_WIDTH)) :
    Signal dom (BitVec (clog2 (BIT_WIDTH + 1))) :=
  popCount (input_A ^^^ input_B)

#synthesizeParameterizedVerilog Bit_Difference_Counter [BIT_WIDTH := 3]
