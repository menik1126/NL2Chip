import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- Selectively reverse all bits, or reverse bits independently in 2, 4, or 8 equal sections. -/
def nbit_swizzling {dom : DomainConfig} {DATA_WIDTH : Nat}
    (data_in : Signal dom (BitVec DATA_WIDTH))
    (sel : Signal dom (BitVec 2)) : Signal dom (BitVec DATA_WIDTH) :=
  let reversedAll : Signal dom (BitVec DATA_WIDTH) := reverseBits data_in
  let reversedHalves : Signal dom (BitVec DATA_WIDTH) :=
    reverseBlocks (BLOCKS := 2) data_in
  let reversedQuarters : Signal dom (BitVec DATA_WIDTH) :=
    reverseBlocks (BLOCKS := 4) data_in
  let reversedEighths : Signal dom (BitVec DATA_WIDTH) :=
    reverseBlocks (BLOCKS := 8) data_in
  let selected23 := Signal.mux (sel === 2#2) reversedQuarters reversedEighths
  let selected123 := Signal.mux (sel === 1#2) reversedHalves selected23
  Signal.mux (sel === 0#2) reversedAll selected123

#synthesizeParameterizedVerilog nbit_swizzling [DATA_WIDTH := 16]
