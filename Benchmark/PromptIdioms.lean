/-
  Small, standalone Sparkle idioms for retrieval-augmented generation prompts.

  These examples are deliberately specification-neutral. They demonstrate
  legal frontend shapes without implementing any benchmark algorithm. Keep
  each marked region self-contained enough to quote as an in-context example.
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

/- CKTARCHON_IDIOM_BEGIN symbolic_constants -/
/-- Use runtime-width constructors for constants whose width is a Nat parameter. -/
def promptIdiomSymbolicConstants {dom : DomainConfig} {WIDTH : Nat}
    (input : Signal dom (BitVec WIDTH)) : Signal dom (BitVec WIDTH) :=
  let one : Signal dom (BitVec WIDTH) :=
    Signal.pure (BitVec.ofNat WIDTH 1)
  input + one

#synthesizeVerilog promptIdiomSymbolicConstants parameters [WIDTH := 13]
/- CKTARCHON_IDIOM_END symbolic_constants -/

/- CKTARCHON_IDIOM_BEGIN derived_width -/
/-- Keep a positive derived width symbolic instead of branching on a Nat parameter. -/
def promptIdiomDerivedWidth {dom : DomainConfig} {ITEMS : Nat}
    (input : Signal dom
      (BitVec (Sparkle.IR.Type.DimExpr.clog2Nat ITEMS + 1)))
    : Signal dom (BitVec (Sparkle.IR.Type.DimExpr.clog2Nat ITEMS + 1)) :=
  let encodedItems : Signal dom
      (BitVec (Sparkle.IR.Type.DimExpr.clog2Nat ITEMS + 1)) :=
    Signal.pure (BitVec.ofNat
      (Sparkle.IR.Type.DimExpr.clog2Nat ITEMS + 1) ITEMS)
  input + encodedItems

#synthesizeVerilog promptIdiomDerivedWidth parameters [ITEMS := 11]
/- CKTARCHON_IDIOM_END derived_width -/

/- CKTARCHON_IDIOM_BEGIN slice_resize -/
/-- Give slice/truncation results an explicit symbolic result type. -/
def promptIdiomSliceResize {dom : DomainConfig} {IN_WIDTH OUT_WIDTH : Nat}
    (input : Signal dom (BitVec IN_WIDTH))
    : Signal dom (BitVec OUT_WIDTH) :=
  let lowBits : Signal dom (BitVec OUT_WIDTH) := trunc input
  lowBits

#synthesizeVerilog promptIdiomSliceResize parameters
  [IN_WIDTH := 17, OUT_WIDTH := 5]
/- CKTARCHON_IDIOM_END slice_resize -/

/- CKTARCHON_IDIOM_BEGIN bounded_stages -/
/--
  Spell a small bounded hardware network as named stages. Do not replace this
  shape with parameter-dependent host recursion, List.fold, or a Lean `if`.
-/
def promptIdiomBoundedStages {dom : DomainConfig} {WIDTH : Nat}
    (input : Signal dom (BitVec WIDTH))
    (select0 select1 select2 select3 : Signal dom Bool)
    : Signal dom (BitVec WIDTH) :=
  let mask0 : Signal dom (BitVec WIDTH) :=
    Signal.pure (BitVec.ofNat WIDTH 1)
  let mask1 : Signal dom (BitVec WIDTH) :=
    Signal.pure (BitVec.ofNat WIDTH 2)
  let mask2 : Signal dom (BitVec WIDTH) :=
    Signal.pure (BitVec.ofNat WIDTH 4)
  let mask3 : Signal dom (BitVec WIDTH) :=
    Signal.pure (BitVec.ofNat WIDTH 8)
  let stage0 : Signal dom (BitVec WIDTH) :=
    Signal.mux select0 (input ^^^ mask0) input
  let stage1 : Signal dom (BitVec WIDTH) :=
    Signal.mux select1 (stage0 ^^^ mask1) stage0
  let stage2 : Signal dom (BitVec WIDTH) :=
    Signal.mux select2 (stage1 ^^^ mask2) stage1
  let stage3 : Signal dom (BitVec WIDTH) :=
    Signal.mux select3 (stage2 ^^^ mask3) stage2
  stage3

#synthesizeVerilog promptIdiomBoundedStages parameters [WIDTH := 13]
/- CKTARCHON_IDIOM_END bounded_stages -/

/- CKTARCHON_IDIOM_BEGIN named_packed_outputs -/
/--
  Preserve semantic output names and concatenate them explicitly MSB first.
  Do not shadow these names or replace the packed return with a tuple/bundle.
-/
def promptIdiomNamedPackedOutputs {dom : DomainConfig} {WIDTH : Nat}
    (left right : Signal dom (BitVec WIDTH))
    : Signal dom (BitVec (1 + WIDTH)) :=
  -- Replace these illustrative locals with the typed scaffold's exact output names.
  let lower_output_example : Signal dom (BitVec WIDTH) := left ^^^ right
  let upper_output_example : Signal dom (BitVec 1) := boolToBV1 (left === right)
  upper_output_example ++ lower_output_example

#synthesizeVerilog promptIdiomNamedPackedOutputs parameters [WIDTH := 13]
/- CKTARCHON_IDIOM_END named_packed_outputs -/

/- CKTARCHON_IDIOM_BEGIN packed_state_high -/
/--
  A feedback loop returns exactly one packed state Signal. Extract visible
  fields only after the loop and keep their source names stable.
-/
def promptIdiomPackedStateHigh {dom : DomainConfig} {DATA_WIDTH : Nat}
    (rst enable : Signal dom Bool)
    (input : Signal dom (BitVec DATA_WIDTH))
    : Signal dom (BitVec (DATA_WIDTH + 1)) :=
  let packed_state : Signal dom (BitVec (DATA_WIDTH + 1)) :=
    Signal.loop fun state =>
      let held_data : Signal dom (BitVec DATA_WIDTH) :=
        Signal.map (fun value => BitVec.extractLsb' 1 DATA_WIDTH value) state
      let held_valid_bv : Signal dom (BitVec 1) :=
        Signal.map (fun value => BitVec.extractLsb' 0 1 value) state
      let held_valid : Signal dom Bool :=
        held_valid_bv === Signal.pure 1#1
      let next_data : Signal dom (BitVec DATA_WIDTH) :=
        Signal.mux enable input held_data
      let next_valid : Signal dom Bool :=
        enable ||| held_valid
      let next_valid_bv : Signal dom (BitVec 1) :=
        Signal.mux next_valid (Signal.pure 1#1) (Signal.pure 0#1)
      let next_state : Signal dom (BitVec (DATA_WIDTH + 1)) :=
        next_data ++ next_valid_bv
      Signal.register (BitVec.ofNat (DATA_WIDTH + 1) 0)
        (Signal.mux rst
          (Signal.pure (BitVec.ofNat (DATA_WIDTH + 1) 0)) next_state)
  let data_out : Signal dom (BitVec DATA_WIDTH) :=
    Signal.map (fun value => BitVec.extractLsb' 1 DATA_WIDTH value) packed_state
  let valid : Signal dom (BitVec 1) :=
    Signal.map (fun value => BitVec.extractLsb' 0 1 value) packed_state
  data_out ++ valid

#synthesizeVerilog promptIdiomPackedStateHigh parameters [DATA_WIDTH := 9]
/- CKTARCHON_IDIOM_END packed_state_high -/

/- CKTARCHON_IDIOM_BEGIN packed_state_low -/
/-- The same packed feedback pattern with an active-low D-path reset. -/
def promptIdiomPackedStateLow {dom : DomainConfig} {DATA_WIDTH : Nat}
    (reset_n enable : Signal dom Bool)
    (input : Signal dom (BitVec DATA_WIDTH))
    : Signal dom (BitVec (DATA_WIDTH + 1)) :=
  let packed_state : Signal dom (BitVec (DATA_WIDTH + 1)) :=
    Signal.loop fun state =>
      let held_data : Signal dom (BitVec DATA_WIDTH) :=
        Signal.map (fun value => BitVec.extractLsb' 1 DATA_WIDTH value) state
      let held_valid_bv : Signal dom (BitVec 1) :=
        Signal.map (fun value => BitVec.extractLsb' 0 1 value) state
      let held_valid : Signal dom Bool :=
        held_valid_bv === Signal.pure 1#1
      let next_data : Signal dom (BitVec DATA_WIDTH) :=
        Signal.mux enable input held_data
      let next_valid : Signal dom Bool :=
        enable ||| held_valid
      let next_valid_bv : Signal dom (BitVec 1) :=
        Signal.mux next_valid (Signal.pure 1#1) (Signal.pure 0#1)
      let next_state : Signal dom (BitVec (DATA_WIDTH + 1)) :=
        next_data ++ next_valid_bv
      Signal.register (BitVec.ofNat (DATA_WIDTH + 1) 0)
        (Signal.mux reset_n next_state
          (Signal.pure (BitVec.ofNat (DATA_WIDTH + 1) 0)))
  let data_out : Signal dom (BitVec DATA_WIDTH) :=
    Signal.map (fun value => BitVec.extractLsb' 1 DATA_WIDTH value) packed_state
  let valid : Signal dom (BitVec 1) :=
    Signal.map (fun value => BitVec.extractLsb' 0 1 value) packed_state
  data_out ++ valid

#synthesizeVerilog promptIdiomPackedStateLow parameters [DATA_WIDTH := 9]
/- CKTARCHON_IDIOM_END packed_state_low -/

/- CKTARCHON_IDIOM_BEGIN memory_1r1w -/
/-- Use the checked 1R1W helper instead of encoding an array in a loop tuple. -/
def promptIdiomMemory1R1W {dom : DomainConfig}
    {ADDR_WIDTH DATA_WIDTH : Nat}
    (write_enable : Signal dom Bool)
    (write_address read_address : Signal dom (BitVec ADDR_WIDTH))
    (write_data : Signal dom (BitVec DATA_WIDTH))
    : Signal dom (BitVec DATA_WIDTH) :=
  let read_data : Signal dom (BitVec DATA_WIDTH) :=
    regFile1R1W write_address write_data write_enable read_address
  read_data

#synthesizeVerilog promptIdiomMemory1R1W parameters
  [ADDR_WIDTH := 3, DATA_WIDTH := 9]
/- CKTARCHON_IDIOM_END memory_1r1w -/
