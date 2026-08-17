import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- Multi-die roller with independent 16-bit LFSR state and latched results. -/
def digital_dice_roller {dom : DomainConfig} {DICE_MAX : Nat} {NUM_DICE : Nat}
    (button reset : Signal dom Bool)
    : Signal dom (BitVec (NUM_DICE * (clog2 DICE_MAX + 1))) :=
  let BIT_WIDTH := clog2 DICE_MAX + 1
  let SEED_WIDTH := NUM_DICE * 16
  let VALUE_WIDTH := NUM_DICE * BIT_WIDTH
  let seeds : Signal dom (BitVec SEED_WIDTH) :=
    Signal.loop fun (q : Signal dom (BitVec SEED_WIDTH)) =>
      let wide : Signal dom (BitVec 48) := Signal.cast q
      let s0 : Signal dom (BitVec 16) := slice wide 0
      let s1 : Signal dom (BitVec 16) := slice wide 16
      let s2 : Signal dom (BitVec 16) := slice wide 32
      let step0 := (slice s0 0 : Signal dom (BitVec 15)) ++
        (bit s0 15 ^^^ bit s0 4 ^^^ bit s0 3 ^^^ bit s0 2)
      let step1 := (slice s1 0 : Signal dom (BitVec 15)) ++
        (bit s1 15 ^^^ bit s1 4 ^^^ bit s1 3 ^^^ bit s1 2)
      let step2 := (slice s2 0 : Signal dom (BitVec 15)) ++
        (bit s2 15 ^^^ bit s2 4 ^^^ bit s2 3 ^^^ bit s2 2)
      let steppedWide := step2 ++ step1 ++ step0
      let stepped : Signal dom (BitVec SEED_WIDTH) := Signal.cast steppedWide
      let advanced := Signal.mux button stepped q
      let resetSeeds : Signal dom (BitVec SEED_WIDTH) := iotaVector1 (W := 16) (N := NUM_DICE)
      let next := Signal.mux reset advanced resetSeeds
      Signal.register (BitVec.ofNat SEED_WIDTH 0) next
  let counters : Signal dom (BitVec VALUE_WIDTH) :=
    Signal.loop fun (q : Signal dom (BitVec VALUE_WIDTH)) =>
      let wideSeeds : Signal dom (BitVec 48) := Signal.cast seeds
      let s0 : Signal dom (BitVec 16) := slice wideSeeds 0
      let s1 : Signal dom (BitVec 16) := slice wideSeeds 16
      let s2 : Signal dom (BitVec 16) := slice wideSeeds 32
      let modulus : Signal dom (BitVec 16) := Signal.pure (BitVec.ofNat 16 DICE_MAX)
      let r0 := (s0 % modulus) + 1#16
      let r1 := (s1 % modulus) + 1#16
      let r2 := (s2 % modulus) + 1#16
      let v0 : Signal dom (BitVec BIT_WIDTH) := Signal.cast r0
      let v1 : Signal dom (BitVec BIT_WIDTH) := Signal.cast r1
      let v2 : Signal dom (BitVec BIT_WIDTH) := Signal.cast r2
      let rolledWide := v2 ++ v1 ++ v0
      let rolled : Signal dom (BitVec VALUE_WIDTH) := Signal.cast rolledWide
      let advanced := Signal.mux button rolled q
      let resetValues : Signal dom (BitVec VALUE_WIDTH) :=
        repeatVector (W := BIT_WIDTH) (N := NUM_DICE)
          (Signal.pure (BitVec.ofNat BIT_WIDTH 1))
      let next := Signal.mux reset advanced resetValues
      Signal.register (BitVec.ofNat VALUE_WIDTH 0) next
  let latched : Signal dom (BitVec VALUE_WIDTH) :=
    Signal.loop fun (q : Signal dom (BitVec VALUE_WIDTH)) =>
      let held := Signal.mux button q counters
      let cleared := Signal.pure (BitVec.ofNat VALUE_WIDTH 0)
      let next := Signal.mux reset held cleared
      Signal.register (BitVec.ofNat VALUE_WIDTH 0) next
  reverseBits (reverseBlocks (BLOCKS := NUM_DICE) latched)

#synthesizeParameterizedVerilog digital_dice_roller [DICE_MAX := 6, NUM_DICE := 2]
