import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- Parameterized combinational single-error-correcting Hamming receiver. -/
def hamming_rx {dom : DomainConfig} {DATA_WIDTH PARITY_BIT : Nat}
    (data_in : Signal dom (BitVec (DATA_WIDTH + PARITY_BIT + 1)))
    : Signal dom (BitVec DATA_WIDTH) :=
  let N := DATA_WIDTH + PARITY_BIT + 1
  -- These 256-bit repeating masks select positions whose binary index has
  -- the corresponding bit set. BitVec.ofNat truncates them to N bits.
  let m0 : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N
    0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA)
  let m1 : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N
    0xCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC)
  let m2 : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N
    0xF0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0F0)
  let m3 : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N
    0xFF00FF00FF00FF00FF00FF00FF00FF00FF00FF00FF00FF00FF00FF00FF00FF00)
  let m4 : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N
    0xFFFF0000FFFF0000FFFF0000FFFF0000FFFF0000FFFF0000FFFF0000FFFF0000)
  let m5 : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N
    0xFFFFFFFF00000000FFFFFFFF00000000FFFFFFFF00000000FFFFFFFF00000000)
  let m6 : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N
    0xFFFFFFFFFFFFFFFF0000000000000000FFFFFFFFFFFFFFFF0000000000000000)
  let m7 : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N
    0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000000000000000000000000000)

  let s0 : Signal dom (BitVec 1) := bit (popCount (data_in &&& m0)) 0
  let s1 : Signal dom (BitVec 1) := bit (popCount (data_in &&& m1)) 0
  let s2 : Signal dom (BitVec 1) := bit (popCount (data_in &&& m2)) 0
  let s3 : Signal dom (BitVec 1) := bit (popCount (data_in &&& m3)) 0
  let s4 : Signal dom (BitVec 1) := bit (popCount (data_in &&& m4)) 0
  let s5 : Signal dom (BitVec 1) := bit (popCount (data_in &&& m5)) 0
  let s6 : Signal dom (BitVec 1) := bit (popCount (data_in &&& m6)) 0
  let s7 : Signal dom (BitVec 1) := bit (popCount (data_in &&& m7)) 0
  let syndrome8 : Signal dom (BitVec 8) :=
    s7 ++ s6 ++ s5 ++ s4 ++ s3 ++ s2 ++ s1 ++ s0
  let syndrome : Signal dom (BitVec PARITY_BIT) := Signal.cast syndrome8
  let shiftAmount : Signal dom (BitVec N) := Signal.cast syndrome
  let one : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N 1)
  let corrected := Signal.mux (nonZero syndrome)
    (data_in ^^^ (one <<< shiftAmount)) data_in

  -- Delete bit zero, then parity positions 1, 2, 4, ... after accounting
  -- for bits already removed. A deletion above N is an identity operation.
  let x0 := corrected >>> (BitVec.ofNat N 1)
  let x1 := x0 >>> (BitVec.ofNat N 1)
  let x2 := x1 >>> (BitVec.ofNat N 1)
  let lo1 : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N 0x1)
  let x3 := (x2 &&& lo1) ||| ((x2 >>> (BitVec.ofNat N 1)) &&& ~~~lo1)
  let lo4 : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N 0xF)
  let x4 := (x3 &&& lo4) ||| ((x3 >>> (BitVec.ofNat N 1)) &&& ~~~lo4)
  let lo11 : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N 0x7FF)
  let x5 := (x4 &&& lo11) ||| ((x4 >>> (BitVec.ofNat N 1)) &&& ~~~lo11)
  let lo26 : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N 0x3FFFFFF)
  let x6 := (x5 &&& lo26) ||| ((x5 >>> (BitVec.ofNat N 1)) &&& ~~~lo26)
  let lo57 : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N 0x1FFFFFFFFFFFFFF)
  let x7 := (x6 &&& lo57) ||| ((x6 >>> (BitVec.ofNat N 1)) &&& ~~~lo57)
  let lo120 : Signal dom (BitVec N) := Signal.pure (BitVec.ofNat N
    0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF)
  let x8 := (x7 &&& lo120) ||| ((x7 >>> (BitVec.ofNat N 1)) &&& ~~~lo120)
  Signal.cast x8

#synthesizeParameterizedVerilog hamming_rx [DATA_WIDTH := 4, PARITY_BIT := 3]
