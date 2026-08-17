import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/-- Parameterized parking occupancy tracker with availability and decimal displays. -/
def car_parking_system {dom : DomainConfig} {TOTAL_SPACES : Nat}
    (reset vehicle_entry_sensor vehicle_exit_sensor : Signal dom Bool)
    : Signal dom
        (BitVec (clog2 TOTAL_SPACES) × BitVec (clog2 TOTAL_SPACES) ×
         BitVec 1 × BitVec 7 × BitVec 7 × BitVec 7 × BitVec 7) :=
  let W := clog2 TOTAL_SPACES
  let total : Signal dom (BitVec W) :=
    Signal.pure (BitVec.ofNat W TOTAL_SPACES)
  let count : Signal dom (BitVec W) :=
    Signal.loop fun (q : Signal dom (BitVec W)) =>
      let full := q === total
      let empty := isZero q
      let entered := Signal.mux full q (q + BitVec.ofNat W 1)
      let exited := Signal.mux empty q (q - BitVec.ofNat W 1)
      let sensorNext :=
        Signal.mux vehicle_entry_sensor entered
          (Signal.mux vehicle_exit_sensor exited q)
      let next := resetHigh (BitVec.ofNat W 0) reset sensorNext
      dff (BitVec.ofNat W 0) next
  let available : Signal dom (BitVec W) := total - count
  let led : Signal dom (BitVec 1) :=
    Signal.mux (isZero available) (Signal.pure 0#1) (Signal.pure 1#1)
  let ten : Signal dom (BitVec W) := Signal.pure (BitVec.ofNat W 10)
  let availableUnits : Signal dom (BitVec W) := available % ten
  let countUnits : Signal dom (BitVec W) := count % ten
  let decimalTens := fun (x : Signal dom (BitVec W)) =>
    Signal.mux (Signal.uge x (Signal.pure (BitVec.ofNat W 90)))
      (Signal.pure (BitVec.ofNat W 9))
      (Signal.mux (Signal.uge x (Signal.pure (BitVec.ofNat W 80)))
        (Signal.pure (BitVec.ofNat W 8))
        (Signal.mux (Signal.uge x (Signal.pure (BitVec.ofNat W 70)))
          (Signal.pure (BitVec.ofNat W 7))
          (Signal.mux (Signal.uge x (Signal.pure (BitVec.ofNat W 60)))
            (Signal.pure (BitVec.ofNat W 6))
            (Signal.mux (Signal.uge x (Signal.pure (BitVec.ofNat W 50)))
              (Signal.pure (BitVec.ofNat W 5))
              (Signal.mux (Signal.uge x (Signal.pure (BitVec.ofNat W 40)))
                (Signal.pure (BitVec.ofNat W 4))
                (Signal.mux (Signal.uge x (Signal.pure (BitVec.ofNat W 30)))
                  (Signal.pure (BitVec.ofNat W 3))
                  (Signal.mux (Signal.uge x (Signal.pure (BitVec.ofNat W 20)))
                    (Signal.pure (BitVec.ofNat W 2))
                    (Signal.mux (Signal.uge x ten)
                      (Signal.pure (BitVec.ofNat W 1))
                      (Signal.pure (BitVec.ofNat W 0))))))))))
  let encodeDigit := fun (x : Signal dom (BitVec W)) =>
    Signal.mux (x === BitVec.ofNat W 0) (Signal.pure 126#7)
      (Signal.mux (x === BitVec.ofNat W 1) (Signal.pure 48#7)
        (Signal.mux (x === BitVec.ofNat W 2) (Signal.pure 109#7)
          (Signal.mux (x === BitVec.ofNat W 3) (Signal.pure 121#7)
            (Signal.mux (x === BitVec.ofNat W 4) (Signal.pure 51#7)
              (Signal.mux (x === BitVec.ofNat W 5) (Signal.pure 91#7)
                (Signal.mux (x === BitVec.ofNat W 6) (Signal.pure 95#7)
                  (Signal.mux (x === BitVec.ofNat W 7) (Signal.pure 112#7)
                    (Signal.mux (x === BitVec.ofNat W 8) (Signal.pure 127#7)
                      (Signal.mux (x === BitVec.ofNat W 9) (Signal.pure 123#7)
                        (Signal.pure 0#7))))))))))
  let availableTensDisplay := encodeDigit (decimalTens available)
  let availableUnitsDisplay := encodeDigit availableUnits
  let countTensDisplay := encodeDigit (decimalTens count)
  let countUnitsDisplay := encodeDigit countUnits
  bundleAll! [available, count, led, availableTensDisplay,
    availableUnitsDisplay, countTensDisplay, countUnitsDisplay]

#synthesizeParameterizedVerilog car_parking_system [TOTAL_SPACES := 9]
