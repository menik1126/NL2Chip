import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- Generic combinational Hamming transmitter with a reserved zero at bit 0. -/
def hamming_tx {dom : DomainConfig} {DATA_WIDTH PARITY_BIT : Nat}
    (data_in : Signal dom (BitVec DATA_WIDTH))
    : Signal dom (BitVec (DATA_WIDTH + PARITY_BIT + 1)) :=
  let scattered : Signal dom (BitVec (DATA_WIDTH + PARITY_BIT + 1)) :=
    scatterNonPowerOfTwoBits (PARITYW := PARITY_BIT) data_in
  let parity : Signal dom (BitVec PARITY_BIT) :=
    parityByIndexMask scattered
  placeParityBits scattered parity

#synthesizeParameterizedVerilog hamming_tx [DATA_WIDTH := 4, PARITY_BIT := 3]
