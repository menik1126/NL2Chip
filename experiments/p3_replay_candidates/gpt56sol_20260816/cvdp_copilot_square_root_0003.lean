import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- Sequential subtraction-based unsigned integer square root. -/
def square_root_seq {dom : DomainConfig} {WIDTH : Nat}
    (num : Signal dom (BitVec WIDTH))
    (rst : Signal dom Bool)
    (start : Signal dom Bool) :
    Signal dom (Bool × BitVec (WIDTH / 2)) :=
  let ROOT_W := WIDTH / 2
  let STATE_W := WIDTH + WIDTH + ROOT_W + ROOT_W + 1 + 1
  let state : Signal dom (BitVec STATE_W) :=
    Signal.loop fun (s : Signal dom (BitVec STATE_W)) =>
      let active := bitBool s 1
      let result : Signal dom (BitVec ROOT_W) := slice s 2
      let root : Signal dom (BitVec ROOT_W) := slice s (2 + ROOT_W)
      let odd : Signal dom (BitVec WIDTH) := slice s (2 + ROOT_W + ROOT_W)
      let rem : Signal dom (BitVec WIDTH) :=
        slice s (2 + ROOT_W + ROOT_W + WIDTH)
      let canSub := Signal.uge rem odd
      let computeRem := Signal.mux canSub (rem - odd) rem
      let computeOdd := Signal.mux canSub
        (odd + BitVec.ofNat WIDTH 2) odd
      let computeRoot := Signal.mux canSub
        (root + BitVec.ofNat ROOT_W 1) root
      let computeResult := Signal.mux canSub result root
      let computeActive := Signal.mux canSub
        (Signal.pure true) (Signal.pure false)
      let computeDone := Signal.mux canSub
        (Signal.pure false) (Signal.pure true)
      let idleRem := Signal.mux start num rem
      let idleOdd := Signal.mux start
        (Signal.pure (BitVec.ofNat WIDTH 1)) odd
      let idleRoot := Signal.mux start
        (Signal.pure (BitVec.ofNat ROOT_W 0)) root
      let idleActive := Signal.mux start (Signal.pure true) active
      let nextRem := Signal.mux active computeRem idleRem
      let nextOdd := Signal.mux active computeOdd idleOdd
      let nextRoot := Signal.mux active computeRoot idleRoot
      let nextResult := Signal.mux active computeResult result
      let nextActive := Signal.mux active computeActive idleActive
      let nextDone := Signal.mux active computeDone (Signal.pure false)
      let packed : Signal dom (BitVec STATE_W) :=
        nextRem ++ nextOdd ++ nextRoot ++ nextResult ++
          boolToBV1 nextActive ++ boolToBV1 nextDone
      let resetPacked : Signal dom (BitVec STATE_W) :=
        resetHigh (BitVec.ofNat STATE_W 0) rst packed
      Signal.register (BitVec.ofNat STATE_W 0) resetPacked
  let done := bitBool state 0
  let finalRoot : Signal dom (BitVec ROOT_W) := slice state 2
  bundle2 done finalRoot

#synthesizeParameterizedVerilog square_root_seq [WIDTH := 2]
