import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Priority encoder: given an 8-bit input, outputs the position (0-7) of the least significant '1' bit.
    Reports 0 if no bits are high. -/
def prob071_always_casez {dom : DomainConfig}
    (in_ : Signal dom (BitVec 8)) : Signal dom (BitVec 3) :=
  -- Extract each individual bit as a signal
  let b0 : Signal dom (BitVec 1) := Signal.map (fun v => BitVec.extractLsb' 0 1 v) in_
  let b1 : Signal dom (BitVec 1) := Signal.map (fun v => BitVec.extractLsb' 1 1 v) in_
  let b2 : Signal dom (BitVec 1) := Signal.map (fun v => BitVec.extractLsb' 2 1 v) in_
  let b3 : Signal dom (BitVec 1) := Signal.map (fun v => BitVec.extractLsb' 3 1 v) in_
  let b4 : Signal dom (BitVec 1) := Signal.map (fun v => BitVec.extractLsb' 4 1 v) in_
  let b5 : Signal dom (BitVec 1) := Signal.map (fun v => BitVec.extractLsb' 5 1 v) in_
  let b6 : Signal dom (BitVec 1) := Signal.map (fun v => BitVec.extractLsb' 6 1 v) in_
  let b7 : Signal dom (BitVec 1) := Signal.map (fun v => BitVec.extractLsb' 7 1 v) in_
  -- Priority: bit 0 has highest priority (LSB first)
  hw_cond (Signal.pure 0#3)
    | (b0 === Signal.pure 1#1) => Signal.pure 0#3
    | (b1 === Signal.pure 1#1) => Signal.pure 1#3
    | (b2 === Signal.pure 1#1) => Signal.pure 2#3
    | (b3 === Signal.pure 1#1) => Signal.pure 3#3
    | (b4 === Signal.pure 1#1) => Signal.pure 4#3
    | (b5 === Signal.pure 1#1) => Signal.pure 5#3
    | (b6 === Signal.pure 1#1) => Signal.pure 6#3
    | (b7 === Signal.pure 1#1) => Signal.pure 7#3

#synthesizeVerilog prob071_always_casez
