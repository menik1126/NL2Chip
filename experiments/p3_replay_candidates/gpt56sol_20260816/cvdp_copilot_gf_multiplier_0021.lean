import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- Byte-wise GF(2^8) multiply-accumulate with parameter validity outputs. -/
def gf_mac {dom : DomainConfig} {WIDTH : Nat}
    (a b : Signal dom (BitVec WIDTH))
    : Signal dom (BitVec 1 × BitVec 8 × BitVec 1) :=
  let lanes := WIDTH / 8
  let bytes01 : Signal dom (BitVec (lanes * 8)) :=
    repeatVector (N := lanes) (Signal.pure 1#8)
  let bytes7f : Signal dom (BitVec (lanes * 8)) :=
    repeatVector (N := lanes) (Signal.pure 127#8)
  let bytes80 : Signal dom (BitVec (lanes * 8)) :=
    repeatVector (N := lanes) (Signal.pure 128#8)
  let bytesfe : Signal dom (BitVec (lanes * 8)) :=
    repeatVector (N := lanes) (Signal.pure 254#8)
  let mask01 : Signal dom (BitVec WIDTH) := Signal.cast bytes01
  let mask7f : Signal dom (BitVec WIDTH) := Signal.cast bytes7f
  let mask80 : Signal dom (BitVec WIDTH) := Signal.cast bytes80
  let maskfe : Signal dom (BitVec WIDTH) := Signal.cast bytesfe
  let zeroW : Signal dom (BitVec WIDTH) := Signal.pure (BitVec.ofNat WIDTH 0)
  let onesW := ~~~zeroW
  let widthCount := popCount onesW
  let widthRem := widthCount % (BitVec.ofNat (clog2 (WIDTH + 1)) 8)
  let widthValid := isZero widthRem

  let s0 := b &&& mask01
  let select0 := s0 ||| (s0 <<< (BitVec.ofNat WIDTH 1)) |||
    (s0 <<< (BitVec.ofNat WIDTH 2)) ||| (s0 <<< (BitVec.ofNat WIDTH 3)) |||
    (s0 <<< (BitVec.ofNat WIDTH 4)) ||| (s0 <<< (BitVec.ofNat WIDTH 5)) |||
    (s0 <<< (BitVec.ofNat WIDTH 6)) ||| (s0 <<< (BitVec.ofNat WIDTH 7))
  let acc1 := a &&& select0
  let c0 := (a &&& mask80) >>> (BitVec.ofNat WIDTH 7)
  let red0 := c0 ||| (c0 <<< (BitVec.ofNat WIDTH 1)) |||
    (c0 <<< (BitVec.ofNat WIDTH 3)) ||| (c0 <<< (BitVec.ofNat WIDTH 4))
  let x1 := ((a <<< (BitVec.ofNat WIDTH 1)) &&& maskfe) ^^^ red0
  let y1 := (b >>> (BitVec.ofNat WIDTH 1)) &&& mask7f

  let s1 := y1 &&& mask01
  let select1 := s1 ||| (s1 <<< (BitVec.ofNat WIDTH 1)) |||
    (s1 <<< (BitVec.ofNat WIDTH 2)) ||| (s1 <<< (BitVec.ofNat WIDTH 3)) |||
    (s1 <<< (BitVec.ofNat WIDTH 4)) ||| (s1 <<< (BitVec.ofNat WIDTH 5)) |||
    (s1 <<< (BitVec.ofNat WIDTH 6)) ||| (s1 <<< (BitVec.ofNat WIDTH 7))
  let acc2 := acc1 ^^^ (x1 &&& select1)
  let c1 := (x1 &&& mask80) >>> (BitVec.ofNat WIDTH 7)
  let red1 := c1 ||| (c1 <<< (BitVec.ofNat WIDTH 1)) |||
    (c1 <<< (BitVec.ofNat WIDTH 3)) ||| (c1 <<< (BitVec.ofNat WIDTH 4))
  let x2 := ((x1 <<< (BitVec.ofNat WIDTH 1)) &&& maskfe) ^^^ red1
  let y2 := (y1 >>> (BitVec.ofNat WIDTH 1)) &&& mask7f

  let s2 := y2 &&& mask01
  let select2 := s2 ||| (s2 <<< (BitVec.ofNat WIDTH 1)) |||
    (s2 <<< (BitVec.ofNat WIDTH 2)) ||| (s2 <<< (BitVec.ofNat WIDTH 3)) |||
    (s2 <<< (BitVec.ofNat WIDTH 4)) ||| (s2 <<< (BitVec.ofNat WIDTH 5)) |||
    (s2 <<< (BitVec.ofNat WIDTH 6)) ||| (s2 <<< (BitVec.ofNat WIDTH 7))
  let acc3 := acc2 ^^^ (x2 &&& select2)
  let c2 := (x2 &&& mask80) >>> (BitVec.ofNat WIDTH 7)
  let red2 := c2 ||| (c2 <<< (BitVec.ofNat WIDTH 1)) |||
    (c2 <<< (BitVec.ofNat WIDTH 3)) ||| (c2 <<< (BitVec.ofNat WIDTH 4))
  let x3 := ((x2 <<< (BitVec.ofNat WIDTH 1)) &&& maskfe) ^^^ red2
  let y3 := (y2 >>> (BitVec.ofNat WIDTH 1)) &&& mask7f

  let s3 := y3 &&& mask01
  let select3 := s3 ||| (s3 <<< (BitVec.ofNat WIDTH 1)) |||
    (s3 <<< (BitVec.ofNat WIDTH 2)) ||| (s3 <<< (BitVec.ofNat WIDTH 3)) |||
    (s3 <<< (BitVec.ofNat WIDTH 4)) ||| (s3 <<< (BitVec.ofNat WIDTH 5)) |||
    (s3 <<< (BitVec.ofNat WIDTH 6)) ||| (s3 <<< (BitVec.ofNat WIDTH 7))
  let acc4 := acc3 ^^^ (x3 &&& select3)
  let c3 := (x3 &&& mask80) >>> (BitVec.ofNat WIDTH 7)
  let red3 := c3 ||| (c3 <<< (BitVec.ofNat WIDTH 1)) |||
    (c3 <<< (BitVec.ofNat WIDTH 3)) ||| (c3 <<< (BitVec.ofNat WIDTH 4))
  let x4 := ((x3 <<< (BitVec.ofNat WIDTH 1)) &&& maskfe) ^^^ red3
  let y4 := (y3 >>> (BitVec.ofNat WIDTH 1)) &&& mask7f

  let s4 := y4 &&& mask01
  let select4 := s4 ||| (s4 <<< (BitVec.ofNat WIDTH 1)) |||
    (s4 <<< (BitVec.ofNat WIDTH 2)) ||| (s4 <<< (BitVec.ofNat WIDTH 3)) |||
    (s4 <<< (BitVec.ofNat WIDTH 4)) ||| (s4 <<< (BitVec.ofNat WIDTH 5)) |||
    (s4 <<< (BitVec.ofNat WIDTH 6)) ||| (s4 <<< (BitVec.ofNat WIDTH 7))
  let acc5 := acc4 ^^^ (x4 &&& select4)
  let c4 := (x4 &&& mask80) >>> (BitVec.ofNat WIDTH 7)
  let red4 := c4 ||| (c4 <<< (BitVec.ofNat WIDTH 1)) |||
    (c4 <<< (BitVec.ofNat WIDTH 3)) ||| (c4 <<< (BitVec.ofNat WIDTH 4))
  let x5 := ((x4 <<< (BitVec.ofNat WIDTH 1)) &&& maskfe) ^^^ red4
  let y5 := (y4 >>> (BitVec.ofNat WIDTH 1)) &&& mask7f

  let s5 := y5 &&& mask01
  let select5 := s5 ||| (s5 <<< (BitVec.ofNat WIDTH 1)) |||
    (s5 <<< (BitVec.ofNat WIDTH 2)) ||| (s5 <<< (BitVec.ofNat WIDTH 3)) |||
    (s5 <<< (BitVec.ofNat WIDTH 4)) ||| (s5 <<< (BitVec.ofNat WIDTH 5)) |||
    (s5 <<< (BitVec.ofNat WIDTH 6)) ||| (s5 <<< (BitVec.ofNat WIDTH 7))
  let acc6 := acc5 ^^^ (x5 &&& select5)
  let c5 := (x5 &&& mask80) >>> (BitVec.ofNat WIDTH 7)
  let red5 := c5 ||| (c5 <<< (BitVec.ofNat WIDTH 1)) |||
    (c5 <<< (BitVec.ofNat WIDTH 3)) ||| (c5 <<< (BitVec.ofNat WIDTH 4))
  let x6 := ((x5 <<< (BitVec.ofNat WIDTH 1)) &&& maskfe) ^^^ red5
  let y6 := (y5 >>> (BitVec.ofNat WIDTH 1)) &&& mask7f

  let s6 := y6 &&& mask01
  let select6 := s6 ||| (s6 <<< (BitVec.ofNat WIDTH 1)) |||
    (s6 <<< (BitVec.ofNat WIDTH 2)) ||| (s6 <<< (BitVec.ofNat WIDTH 3)) |||
    (s6 <<< (BitVec.ofNat WIDTH 4)) ||| (s6 <<< (BitVec.ofNat WIDTH 5)) |||
    (s6 <<< (BitVec.ofNat WIDTH 6)) ||| (s6 <<< (BitVec.ofNat WIDTH 7))
  let acc7 := acc6 ^^^ (x6 &&& select6)
  let c6 := (x6 &&& mask80) >>> (BitVec.ofNat WIDTH 7)
  let red6 := c6 ||| (c6 <<< (BitVec.ofNat WIDTH 1)) |||
    (c6 <<< (BitVec.ofNat WIDTH 3)) ||| (c6 <<< (BitVec.ofNat WIDTH 4))
  let x7 := ((x6 <<< (BitVec.ofNat WIDTH 1)) &&& maskfe) ^^^ red6
  let y7 := (y6 >>> (BitVec.ofNat WIDTH 1)) &&& mask7f

  let s7 := y7 &&& mask01
  let select7 := s7 ||| (s7 <<< (BitVec.ofNat WIDTH 1)) |||
    (s7 <<< (BitVec.ofNat WIDTH 2)) ||| (s7 <<< (BitVec.ofNat WIDTH 3)) |||
    (s7 <<< (BitVec.ofNat WIDTH 4)) ||| (s7 <<< (BitVec.ofNat WIDTH 5)) |||
    (s7 <<< (BitVec.ofNat WIDTH 6)) ||| (s7 <<< (BitVec.ofNat WIDTH 7))
  let products := acc7 ^^^ (x7 &&& select7)

  let fold8 := products ^^^ (products >>> (BitVec.ofNat WIDTH 8))
  let fold16 := fold8 ^^^ (fold8 >>> (BitVec.ofNat WIDTH 16))
  let fold32 := fold16 ^^^ (fold16 >>> (BitVec.ofNat WIDTH 32))
  let fold64 := fold32 ^^^ (fold32 >>> (BitVec.ofNat WIDTH 64))
  let reduced : Signal dom (BitVec 8) := trunc fold64
  let result := Signal.mux widthValid reduced (Signal.pure 0#8)
  let valid_result := boolToBV1 widthValid
  let error_flag := boolToBV1 (Signal.mux widthValid (Signal.pure false) (Signal.pure true))
  bundleAll! [error_flag, result, valid_result]

#synthesizeParameterizedVerilog gf_mac [WIDTH := 24]
