import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 1-bit full adder: adds three 1-bit inputs (a, b, cin), returns bundled (sum, cout). -/
def add1 {dom : DomainConfig}
    (a b cin : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let sum  := a ^^^ b ^^^ cin
  let cout := (a &&& b) ||| (a &&& cin) ||| (b &&& cin)
  bundle2 sum cout

/-- 2-bit adder using two 1-bit adders -/
def add2 {dom : DomainConfig}
    (a b : Signal dom (BitVec 2))
    (cin : Signal dom (BitVec 1))
    : Signal dom (BitVec 2 × BitVec 1) :=
  -- Extract bits
  let a0 := Signal.map (fun x => x.extractLsb 0 0) a
  let a1 := Signal.map (fun x => x.extractLsb 1 1) a
  let b0 := Signal.map (fun x => x.extractLsb 0 0) b
  let b1 := Signal.map (fun x => x.extractLsb 1 1) b
  -- Lower bit adder
  let result0 := add1 a0 b0 cin
  let y0 := Signal.map Prod.fst result0
  let co_temp := Signal.map Prod.snd result0
  -- Upper bit adder
  let result1 := add1 a1 b1 co_temp
  let y1 := Signal.map Prod.fst result1
  let co := Signal.map Prod.snd result1
  -- Combine results
  let y := Signal.map (fun p => p.1 ++ p.2) (bundle2 y1 y0)
  bundle2 y co

/-- 4-bit adder using two 2-bit adders -/
def add4 {dom : DomainConfig}
    (a b : Signal dom (BitVec 4))
    (cin : Signal dom (BitVec 1))
    : Signal dom (BitVec 4 × BitVec 1) :=
  -- Extract lower and upper 2 bits
  let a_lo := Signal.map (fun x => x.extractLsb 1 0) a
  let a_hi := Signal.map (fun x => x.extractLsb 3 2) a
  let b_lo := Signal.map (fun x => x.extractLsb 1 0) b
  let b_hi := Signal.map (fun x => x.extractLsb 3 2) b
  -- Lower 2-bit adder
  let result_lo := add2 a_lo b_lo cin
  let y_lo := Signal.map Prod.fst result_lo
  let co_temp := Signal.map Prod.snd result_lo
  -- Upper 2-bit adder
  let result_hi := add2 a_hi b_hi co_temp
  let y_hi := Signal.map Prod.fst result_hi
  let co := Signal.map Prod.snd result_hi
  -- Combine results
  let y := Signal.map (fun p => p.1 ++ p.2) (bundle2 y_hi y_lo)
  bundle2 y co

/-- 8-bit adder using two 4-bit adders -/
def add8 {dom : DomainConfig}
    (a b : Signal dom (BitVec 8))
    (cin : Signal dom (BitVec 1))
    : Signal dom (BitVec 8 × BitVec 1) :=
  -- Extract lower and upper 4 bits
  let a_lo := Signal.map (fun x => x.extractLsb 3 0) a
  let a_hi := Signal.map (fun x => x.extractLsb 7 4) a
  let b_lo := Signal.map (fun x => x.extractLsb 3 0) b
  let b_hi := Signal.map (fun x => x.extractLsb 7 4) b
  -- Lower 4-bit adder
  let result_lo := add4 a_lo b_lo cin
  let y_lo := Signal.map Prod.fst result_lo
  let co_temp := Signal.map Prod.snd result_lo
  -- Upper 4-bit adder
  let result_hi := add4 a_hi b_hi co_temp
  let y_hi := Signal.map Prod.fst result_hi
  let co := Signal.map Prod.snd result_hi
  -- Combine results
  let y := Signal.map (fun p => p.1 ++ p.2) (bundle2 y_hi y_lo)
  bundle2 y co

/-- 16-bit full adder using two 8-bit adders -/
def adder_16bit {dom : DomainConfig}
    (a b : Signal dom (BitVec 16))
    (cin : Signal dom (BitVec 1))
    : Signal dom (BitVec 16 × BitVec 1) :=
  -- Extract lower and upper 8 bits
  let a_lo := Signal.map (fun x => x.extractLsb 7 0) a
  let a_hi := Signal.map (fun x => x.extractLsb 15 8) a
  let b_lo := Signal.map (fun x => x.extractLsb 7 0) b
  let b_hi := Signal.map (fun x => x.extractLsb 15 8) b
  -- Lower 8-bit adder
  let result_lo := add8 a_lo b_lo cin
  let y_lo := Signal.map Prod.fst result_lo
  let co_temp := Signal.map Prod.snd result_lo
  -- Upper 8-bit adder
  let result_hi := add8 a_hi b_hi co_temp
  let y_hi := Signal.map Prod.fst result_hi
  let co := Signal.map Prod.snd result_hi
  -- Combine results
  let y := Signal.map (fun p => p.1 ++ p.2) (bundle2 y_hi y_lo)
  bundle2 y co

#synthesizeVerilog adder_16bit
