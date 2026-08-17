import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- Synchronous parameterized last-in, first-out memory. -/
def sync_lifo {dom : DomainConfig} {ADDR_WIDTH : Nat} {DATA_WIDTH : Nat}
    (data_in : Signal dom (BitVec DATA_WIDTH))
    (read_en : Signal dom Bool)
    (reset : Signal dom Bool)
    (write_en : Signal dom Bool) :
    Signal dom (BitVec (DATA_WIDTH + 1 + 1)) :=
  let COUNT_WIDTH := ADDR_WIDTH + 1
  let STATE_WIDTH := COUNT_WIDTH + DATA_WIDTH
  let state : Signal dom (BitVec STATE_WIDTH) :=
    Signal.loop fun state =>
      let count : Signal dom (BitVec COUNT_WIDTH) := slice state DATA_WIDTH
      let oldData : Signal dom (BitVec DATA_WIDTH) := slice state 0
      let emptyNow := count === BitVec.ofNat COUNT_WIDTH 0
      let fullNow := bitBool count ADDR_WIDTH
      let canWrite := Signal.mux write_en (~~~fullNow) (Signal.pure false)
      let canRead := Signal.mux read_en (~~~emptyNow) (Signal.pure false)
      let one := BitVec.ofNat COUNT_WIDTH 1
      let incremented := count + one
      let decremented := count - one
      let nextCount :=
        Signal.mux canWrite
          (Signal.mux canRead count incremented)
          (Signal.mux canRead decremented count)
      let readAddrWide := count - one
      let readAddr : Signal dom (BitVec ADDR_WIDTH) := slice readAddrWide 0
      let writeAddr : Signal dom (BitVec ADDR_WIDTH) := slice count 0
      let memData : Signal dom (BitVec DATA_WIDTH) :=
        regFile1R1W writeAddr data_in canWrite readAddr
      let nextData := Signal.mux canRead memData oldData
      let updated := nextCount ++ nextData
      let resetNext := resetLow (BitVec.ofNat STATE_WIDTH 0) reset updated
      Signal.register (BitVec.ofNat STATE_WIDTH 0) resetNext
  let count : Signal dom (BitVec COUNT_WIDTH) := slice state DATA_WIDTH
  let data_out : Signal dom (BitVec DATA_WIDTH) := slice state 0
  let empty := boolToBV1 (count === BitVec.ofNat COUNT_WIDTH 0)
  let full := boolToBV1 (bitBool count ADDR_WIDTH)
  data_out ++ empty ++ full

#synthesizeParameterizedVerilog sync_lifo [ADDR_WIDTH := 2, DATA_WIDTH := 4]
