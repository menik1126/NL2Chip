import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- Parameterized first-in-last-out buffer with empty-stack feedthrough. -/
def FILO_RTL {dom : DomainConfig} {DATA_WIDTH : Nat} {FILO_DEPTH : Nat}
    (data_in : Signal dom (BitVec DATA_WIDTH))
    (pop push reset : Signal dom Bool) : Signal dom (BitVec DATA_WIDTH) :=
  let COUNT_WIDTH := clog2 (FILO_DEPTH + 1)
  let ADDR_WIDTH := clog2 FILO_DEPTH
  let state : Signal dom (BitVec COUNT_WIDTH × BitVec DATA_WIDTH) :=
    Signal.loop fun state =>
      let count := state.fst
      let oldDataOut := state.snd
      let empty := count === BitVec.ofNat COUNT_WIDTH 0
      let full := count === BitVec.ofNat COUNT_WIDTH FILO_DEPTH
      let notEmpty := Signal.mux empty (Signal.pure false) (Signal.pure true)
      let notFull := Signal.mux full (Signal.pure false) (Signal.pure true)
      let popValid := Signal.mux pop notEmpty (Signal.pure false)
      let pushValid := Signal.mux push notFull (Signal.pure false)
      let bothValid := Signal.mux pushValid popValid (Signal.pure false)
      let emptyPushPop :=
        Signal.mux empty
          (Signal.mux push pop (Signal.pure false))
          (Signal.pure false)
      let decremented := count - BitVec.ofNat COUNT_WIDTH 1
      let incremented := count + BitVec.ofNat COUNT_WIDTH 1
      let changedCount :=
        Signal.mux bothValid count
          (Signal.mux pushValid incremented
            (Signal.mux popValid decremented count))
      let nextCount := Signal.mux emptyPushPop count changedCount
      let stackIndex := Signal.mux popValid decremented count
      let writeAddr : Signal dom (BitVec ADDR_WIDTH) := Signal.cast stackIndex
      let readAddr : Signal dom (BitVec ADDR_WIDTH) := Signal.cast decremented
      let suppressWrite := Signal.mux pop empty (Signal.pure false)
      let writeEnable := Signal.mux suppressWrite (Signal.pure false) pushValid
      let poppedData := regFile1R1W writeAddr data_in writeEnable readAddr
      let poppedOrHeld := Signal.mux popValid poppedData oldDataOut
      let nextDataOut := Signal.mux emptyPushPop data_in poppedOrHeld
      let resetCount :=
        Signal.mux reset (Signal.pure (BitVec.ofNat COUNT_WIDTH 0)) nextCount
      let resetDataOut :=
        Signal.mux reset (Signal.pure (BitVec.ofNat DATA_WIDTH 0)) nextDataOut
      let resetState := bundle2 resetCount resetDataOut
      Signal.register
        (BitVec.ofNat COUNT_WIDTH 0, BitVec.ofNat DATA_WIDTH 0)
        resetState
  state.snd

#synthesizeParameterizedVerilog FILO_RTL [DATA_WIDTH := 8, FILO_DEPTH := 8]
