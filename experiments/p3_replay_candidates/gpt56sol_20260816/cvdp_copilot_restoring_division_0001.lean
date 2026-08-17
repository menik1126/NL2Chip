import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- Iterative unsigned restoring divider with a one-cycle completion pulse. -/
def restoring_division {dom : DomainConfig} {WIDTH : Nat}
    (dividend divisor : Signal dom (BitVec WIDTH))
    (rst start : Signal dom Bool) :
    Signal dom (BitVec WIDTH × BitVec WIDTH × Bool) :=
  let RW := WIDTH + 1
  let CW := WIDTH
  let state :=
    Signal.loop fun (st : Signal dom
        ((BitVec WIDTH × BitVec WIDTH × BitVec RW × BitVec WIDTH × BitVec CW) ×
         (Bool × BitVec WIDTH × BitVec WIDTH × Bool))) =>
      let data := st.fst
      let ctrl := st.snd
      let dvd := projN! data 5 0
      let dvs := projN! data 5 1
      let rem := projN! data 5 2
      let quo := projN! data 5 3
      let count := projN! data 5 4
      let busy := projN! ctrl 4 0
      let oldQ := projN! ctrl 4 1
      let oldR := projN! ctrl 4 2
      let shiftedDvd := dvd <<< BitVec.ofNat WIDTH 1
      let reversedDvd := reverseBits dvd
      let incomingBit : Signal dom (BitVec 1) := trunc reversedDvd
      let incoming : Signal dom (BitVec RW) := zext incomingBit
      let shiftedRem := (rem <<< BitVec.ofNat RW 1) ||| incoming
      let extDiv : Signal dom (BitVec RW) := zext dvs
      let canSubtract := Signal.ule extDiv shiftedRem
      let subtracted := shiftedRem - extDiv
      let newRem := Signal.mux canSubtract subtracted shiftedRem
      let qshift := quo <<< BitVec.ofNat WIDTH 1
      let newQ := Signal.mux canSubtract
        (qshift ||| BitVec.ofNat WIDTH 1) qshift
      let reversedCount := reverseBits count
      let countMsb : Signal dom (BitVec 1) := trunc reversedCount
      let last := countMsb === (1#1 : BitVec 1)
      let nextCount := count <<< BitVec.ofNat CW 1
      let busyAfterIteration := Signal.mux last
        (Signal.pure false) (Signal.pure true)
      let busyNext := Signal.mux busy busyAfterIteration start
      let zeroW : Signal dom (BitVec WIDTH) :=
        Signal.pure (BitVec.ofNat WIDTH 0)
      let zeroR : Signal dom (BitVec RW) :=
        Signal.pure (BitVec.ofNat RW 0)
      let startCount : Signal dom (BitVec CW) :=
        Signal.pure (BitVec.ofNat CW 1)
      let nextDvd := Signal.mux busy shiftedDvd dividend
      let nextDvs := Signal.mux busy dvs divisor
      let nextRem := Signal.mux busy newRem zeroR
      let nextQ := Signal.mux busy newQ zeroW
      let nextCount' := Signal.mux busy nextCount startCount
      let validNext := Signal.mux busy last (Signal.pure false)
      let finalR : Signal dom (BitVec WIDTH) := trunc newRem
      let outQNext := Signal.mux validNext newQ oldQ
      let outRNext := Signal.mux validNext finalR oldR
      let rDvd := resetLow (BitVec.ofNat WIDTH 0) rst nextDvd
      let rDvs := resetLow (BitVec.ofNat WIDTH 0) rst nextDvs
      let rRem := resetLow (BitVec.ofNat RW 0) rst nextRem
      let rQuo := resetLow (BitVec.ofNat WIDTH 0) rst nextQ
      let rCount := resetLow (BitVec.ofNat CW 0) rst nextCount'
      let rBusy := resetLow false rst busyNext
      let rOutQ := resetLow (BitVec.ofNat WIDTH 0) rst outQNext
      let rOutR := resetLow (BitVec.ofNat WIDTH 0) rst outRNext
      let rValid := resetLow false rst validNext
      let nextData := bundle2 rDvd (bundle2 rDvs
        (bundle2 rRem (bundle2 rQuo rCount)))
      let nextCtrl := bundle2 rBusy (bundle2 rOutQ (bundle2 rOutR rValid))
      let nextState := bundle2 nextData nextCtrl
      Signal.register
        ((BitVec.ofNat WIDTH 0,
            (BitVec.ofNat WIDTH 0,
              (BitVec.ofNat RW 0,
                (BitVec.ofNat WIDTH 0, BitVec.ofNat CW 0)))),
          (false,
            (BitVec.ofNat WIDTH 0,
              (BitVec.ofNat WIDTH 0, false))))
        nextState
  let ctrl := state.snd
  let quotient := projN! ctrl 4 1
  let remainder := projN! ctrl 4 2
  let valid := projN! ctrl 4 3
  bundle2 quotient (bundle2 remainder valid)

#synthesizeParameterizedVerilog restoring_division [WIDTH := 3]
