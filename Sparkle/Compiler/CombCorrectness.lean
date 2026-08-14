/-
  Verified phase-one combinational compiler path.

  This module exposes a pure, parameterized source language, lowering into
  Sparkle's existing core IR, independent executable semantics, and a
  kernel-checked preservation theorem for every supported design and legal
  parameter assignment.  Certification applies to the explicit `CombDesign`
  / `CertifiedCombDesign` API in this module.  The metaprogramming frontend in
  `Sparkle.Compiler.Elab` remains a separate, uncertified ingestion path.
-/

import Sparkle.IR.AST
import Sparkle.IR.Type

namespace Sparkle.Compiler.CombCorrectness

open Sparkle.IR.AST
open Sparkle.IR.Type

/-! ## Runtime values and symbolic dimensions -/

/-- A total assignment for elaboration parameters. -/
abbrev Config := String → Nat

/-- A dynamically sized packed value.  The bit width is retained in the type
    of `bits`, so every primitive below is implemented with Lean's `BitVec`. -/
structure PackedValue where
  width : Nat
  bits : BitVec width
  deriving DecidableEq

namespace PackedValue

def ofNat (width value : Nat) : PackedValue :=
  { width, bits := BitVec.ofNat width value }

def ofInt (width : Nat) (value : Int) : PackedValue :=
  { width, bits := BitVec.ofInt width value }

def resize (targetWidth : Nat) (value : PackedValue) : PackedValue :=
  { width := targetWidth, bits := BitVec.setWidth targetWidth value.bits }

def nonzero (value : PackedValue) : Bool :=
  value.bits.toNat != 0

inductive UnaryOp where
  | bitNot
  | neg
  deriving Repr, BEq, DecidableEq

inductive BinaryOp where
  | band
  | bor
  | bxor
  | add
  | sub
  | mul
  deriving Repr, BEq, DecidableEq

inductive CompareOp where
  | eq
  | ult
  | ule
  deriving Repr, BEq, DecidableEq

inductive ShiftOp where
  | left
  | right
  deriving Repr, BEq, DecidableEq

def unary (operator : UnaryOp) (value : PackedValue) : PackedValue :=
  match operator with
  | .bitNot => { width := value.width, bits := ~~~value.bits }
  | .neg => { width := value.width, bits := -value.bits }

/-- Sparkle's current packed binary operators use the maximum operand width. -/
def binary (operator : BinaryOp) (lhs rhs : PackedValue) : PackedValue :=
  let width := max lhs.width rhs.width
  let lhsBits := BitVec.setWidth width lhs.bits
  let rhsBits := BitVec.setWidth width rhs.bits
  let bits := match operator with
    | .band => lhsBits &&& rhsBits
    | .bor => lhsBits ||| rhsBits
    | .bxor => lhsBits ^^^ rhsBits
    | .add => lhsBits + rhsBits
    | .sub => lhsBits - rhsBits
    | .mul => lhsBits * rhsBits
  { width, bits }

def compare (operator : CompareOp) (lhs rhs : PackedValue) : PackedValue :=
  let width := max lhs.width rhs.width
  let lhsBits := BitVec.setWidth width lhs.bits
  let rhsBits := BitVec.setWidth width rhs.bits
  let result := match operator with
    | .eq => lhsBits == rhsBits
    | .ult => lhsBits < rhsBits
    | .ule => lhsBits ≤ rhsBits
  ofNat 1 (if result then 1 else 0)

def shift (operator : ShiftOp) (value amount : PackedValue) : PackedValue :=
  let bits := match operator with
    | .left => value.bits <<< amount.bits.toNat
    | .right => value.bits >>> amount.bits.toNat
  { width := value.width, bits }

def mux (condition thenValue elseValue : PackedValue) : PackedValue :=
  let width := max thenValue.width elseValue.width
  if condition.nonzero then thenValue.resize width else elseValue.resize width

def concat (msb lsb : PackedValue) : PackedValue :=
  { width := msb.width + lsb.width, bits := msb.bits ++ lsb.bits }

def slice (hi lo : Nat) (value : PackedValue) : PackedValue :=
  let width := hi - lo + 1
  { width, bits := BitVec.extractLsb' lo width value.bits }

end PackedValue

/-- Total evaluation of the existing symbolic dimension language. -/
def evalDim (config : Config) : DimExpr → Nat
  | .literal value => value
  | .param name => config name
  | .add lhs rhs => evalDim config lhs + evalDim config rhs
  | .sub lhs rhs => evalDim config lhs - evalDim config rhs
  | .mul lhs rhs => evalDim config lhs * evalDim config rhs
  | .div lhs rhs => evalDim config lhs / evalDim config rhs
  | .mod lhs rhs => evalDim config lhs % evalDim config rhs
  | .pow lhs rhs => evalDim config lhs ^ evalDim config rhs
  | .shl lhs rhs => evalDim config lhs <<< evalDim config rhs
  | .shr lhs rhs => evalDim config lhs >>> evalDim config rhs
  | .bitAnd lhs rhs => evalDim config lhs &&& evalDim config rhs
  | .bitOr lhs rhs => evalDim config lhs ||| evalDim config rhs
  | .bitXor lhs rhs => evalDim config lhs ^^^ evalDim config rhs
  | .clog2 value => DimExpr.clog2Nat (evalDim config value)
  | .min lhs rhs => Nat.min (evalDim config lhs) (evalDim config rhs)
  | .max lhs rhs => Nat.max (evalDim config lhs) (evalDim config rhs)

/-- Every denominator that must be nonzero for the verified configuration.
    Lean's `Nat.div` and `Nat.mod` are total at zero, but the verified hardware
    subset deliberately rejects that backend-sensitive case. -/
def dimDivisors : DimExpr → List DimExpr
  | .literal _ | .param _ => []
  | .add lhs rhs | .sub lhs rhs | .mul lhs rhs | .pow lhs rhs
  | .shl lhs rhs | .shr lhs rhs | .bitAnd lhs rhs | .bitOr lhs rhs
  | .bitXor lhs rhs | .min lhs rhs | .max lhs rhs =>
      dimDivisors lhs ++ dimDivisors rhs
  | .div lhs rhs | .mod lhs rhs =>
      rhs :: (dimDivisors lhs ++ dimDivisors rhs)
  | .clog2 value => dimDivisors value

/-- Total, kernel-reducible parameter collection for certification.  The
    production IR helper has legacy `partial` status, so the verified path uses
    this structurally recursive definition instead. -/
def dimParameters : DimExpr → List String
  | .literal _ => []
  | .param name => [name]
  | .add lhs rhs | .sub lhs rhs | .mul lhs rhs | .div lhs rhs
  | .mod lhs rhs | .pow lhs rhs | .shl lhs rhs | .shr lhs rhs
  | .bitAnd lhs rhs | .bitOr lhs rhs | .bitXor lhs rhs
  | .min lhs rhs | .max lhs rhs =>
      dimParameters lhs ++ dimParameters rhs
  | .clog2 value => dimParameters value

/-! ## A small, arity-safe combinational source language -/

abbrev UnaryOp := PackedValue.UnaryOp
abbrev BinaryOp := PackedValue.BinaryOp
abbrev CompareOp := PackedValue.CompareOp
abbrev ShiftOp := PackedValue.ShiftOp

/-- Unlike core `Expr.op`, these constructors cannot contain a malformed
    operand count.  This is the supported phase-one source subset. -/
inductive CombExpr where
  | ref (name : String)
  | const (value : Int) (width : DimExpr)
  | paramConst (value width : DimExpr)
  | unary (operator : UnaryOp) (value : CombExpr)
  | binary (operator : BinaryOp) (lhs rhs : CombExpr)
  | compare (operator : CompareOp) (lhs rhs : CombExpr)
  | shift (operator : ShiftOp) (value amount : CombExpr)
  | mux (condition thenValue elseValue : CombExpr)
  | concat (msb lsb : CombExpr)
  | resize (width : DimExpr) (value : CombExpr)
  | slice (value : CombExpr) (hi lo : DimExpr)
  deriving Repr

namespace CombExpr

def refs : CombExpr → List String
  | .ref name => [name]
  | .const _ _ | .paramConst _ _ => []
  | .unary _ value | .resize _ value => value.refs
  | .binary _ lhs rhs | .compare _ lhs rhs | .shift _ lhs rhs
  | .concat lhs rhs => lhs.refs ++ rhs.refs
  | .mux condition thenValue elseValue =>
      condition.refs ++ thenValue.refs ++ elseValue.refs
  | .slice value _ _ => value.refs

/-- Width-like dimensions that are required to be positive.  Parameter values
    and slice offsets may legally be zero and are deliberately excluded. -/
def positiveDimensions : CombExpr → List DimExpr
  | .ref _ => []
  | .const _ width | .paramConst _ width => [width]
  | .unary _ value => value.positiveDimensions
  | .binary _ lhs rhs | .compare _ lhs rhs | .shift _ lhs rhs
  | .concat lhs rhs => lhs.positiveDimensions ++ rhs.positiveDimensions
  | .mux condition thenValue elseValue =>
      condition.positiveDimensions ++ thenValue.positiveDimensions ++
        elseValue.positiveDimensions
  | .resize width value => width :: value.positiveDimensions
  | .slice value hi lo =>
      (DimExpr.mkAdd (DimExpr.mkSub hi lo) 1) :: value.positiveDimensions

/-- All dimensions, including parameter-valued constants and slice indexes. -/
def dimensions : CombExpr → List DimExpr
  | .ref _ => []
  | .const _ width => [width]
  | .paramConst value width => [value, width]
  | .unary _ value => value.dimensions
  | .binary _ lhs rhs | .compare _ lhs rhs | .shift _ lhs rhs
  | .concat lhs rhs => lhs.dimensions ++ rhs.dimensions
  | .mux condition thenValue elseValue =>
      condition.dimensions ++ thenValue.dimensions ++ elseValue.dimensions
  | .resize width value => width :: value.dimensions
  | .slice value hi lo => value.dimensions ++ [hi, lo]

/-- Slice extraction is total above the source width (`BitVec.extractLsb'`
    supplies zero bits).  The only rejected ordering is `hi < lo`. -/
def SlicesValid (config : Config) : CombExpr → Prop
  | .ref _ | .const _ _ | .paramConst _ _ => True
  | .unary _ value | .resize _ value => value.SlicesValid config
  | .binary _ lhs rhs | .compare _ lhs rhs | .shift _ lhs rhs
  | .concat lhs rhs => lhs.SlicesValid config ∧ rhs.SlicesValid config
  | .mux condition thenValue elseValue =>
      condition.SlicesValid config ∧ thenValue.SlicesValid config ∧
        elseValue.SlicesValid config
  | .slice value hi lo =>
      value.SlicesValid config ∧ evalDim config lo ≤ evalDim config hi

end CombExpr

structure PortDecl where
  name : String
  width : DimExpr
  deriving Repr

structure Binding where
  name : String
  width : DimExpr
  rhs : CombExpr
  deriving Repr

/-- Wires and outputs are evaluated in source order.  `SupportedComb` below
    enforces that every reference points to an input or an earlier binding. -/
structure CombDesign where
  name : String
  parameters : List Parameter := []
  inputs : List PortDecl
  wires : List Binding := []
  outputs : List Binding
  deriving Repr

abbrev ValueEnv := List (String × PackedValue)

def EnvContains (env : ValueEnv) (names : List String) : Prop :=
  ∀ name ∈ names, ∃ value, env.lookup name = some value

/-- Every declared input is present at exactly its configured packed width.
    Extra environment entries are ignored; scoped source expressions cannot
    refer to them. -/
def ValidInputs (design : CombDesign) (config : Config) (inputs : ValueEnv) : Prop :=
  ∀ port ∈ design.inputs,
    ∃ value, inputs.lookup port.name = some value ∧
      value.width = evalDim config port.width

def evalSourceExpr (config : Config) (env : ValueEnv) : CombExpr → Option PackedValue
  | .ref name => env.lookup name
  | .const value width => some (PackedValue.ofInt (evalDim config width) value)
  | .paramConst value width =>
      some (PackedValue.ofNat (evalDim config width) (evalDim config value))
  | .unary operator value => do
      return PackedValue.unary operator (← evalSourceExpr config env value)
  | .binary operator lhs rhs => do
      return PackedValue.binary operator
        (← evalSourceExpr config env lhs) (← evalSourceExpr config env rhs)
  | .compare operator lhs rhs => do
      return PackedValue.compare operator
        (← evalSourceExpr config env lhs) (← evalSourceExpr config env rhs)
  | .shift operator value amount => do
      return PackedValue.shift operator
        (← evalSourceExpr config env value) (← evalSourceExpr config env amount)
  | .mux condition thenValue elseValue => do
      return PackedValue.mux (← evalSourceExpr config env condition)
        (← evalSourceExpr config env thenValue)
        (← evalSourceExpr config env elseValue)
  | .concat msb lsb => do
      return PackedValue.concat (← evalSourceExpr config env msb)
        (← evalSourceExpr config env lsb)
  | .resize width value => do
      return PackedValue.resize (evalDim config width)
        (← evalSourceExpr config env value)
  | .slice value hi lo => do
      return PackedValue.slice (evalDim config hi) (evalDim config lo)
        (← evalSourceExpr config env value)

/-! ## Pure lowering into the existing Sparkle core IR -/

def lowerUnary : UnaryOp → Operator
  | .bitNot => .not
  | .neg => .neg

def lowerBinary : BinaryOp → Operator
  | .band => .and
  | .bor => .or
  | .bxor => .xor
  | .add => .add
  | .sub => .sub
  | .mul => .mul

def lowerCompare : CompareOp → Operator
  | .eq => .eq
  | .ult => .lt_u
  | .ule => .le_u

def lowerShift : ShiftOp → Operator
  | .left => .shl
  | .right => .shr

def compileExpr : CombExpr → Expr
  | .ref name => .ref name
  | .const value width => .const value width
  | .paramConst value width => .paramConst value width
  | .unary operator value => .op (lowerUnary operator) [compileExpr value]
  | .binary operator lhs rhs =>
      .op (lowerBinary operator) [compileExpr lhs, compileExpr rhs]
  | .compare operator lhs rhs =>
      .op (lowerCompare operator) [compileExpr lhs, compileExpr rhs]
  | .shift operator value amount =>
      .op (lowerShift operator) [compileExpr value, compileExpr amount]
  | .mux condition thenValue elseValue =>
      .op .mux [compileExpr condition, compileExpr thenValue, compileExpr elseValue]
  | .concat msb lsb => .concat [compileExpr msb, compileExpr lsb]
  | .resize width value => .resize width (compileExpr value)
  | .slice value hi lo => .slice (compileExpr value) hi lo

/-! ## Independent executable semantics for the existing core IR subset -/

/-- This interpreter is intentionally defined over `Sparkle.IR.AST.Expr`, not
    through `CombExpr` or `compileExpr`.  It fails closed on operators and
    arities outside the phase-one subset. -/
def evalCoreExpr (config : Config) (env : ValueEnv) : Expr → Option PackedValue
  | .const value width => some (PackedValue.ofInt (evalDim config width) value)
  | .paramConst value width =>
      some (PackedValue.ofNat (evalDim config width) (evalDim config value))
  | .ref name => env.lookup name
  | .op .not [value] => do
      return PackedValue.unary .bitNot (← evalCoreExpr config env value)
  | .op .neg [value] => do
      return PackedValue.unary .neg (← evalCoreExpr config env value)
  | .op .and [lhs, rhs] => do
      return PackedValue.binary .band
        (← evalCoreExpr config env lhs) (← evalCoreExpr config env rhs)
  | .op .or [lhs, rhs] => do
      return PackedValue.binary .bor
        (← evalCoreExpr config env lhs) (← evalCoreExpr config env rhs)
  | .op .xor [lhs, rhs] => do
      return PackedValue.binary .bxor
        (← evalCoreExpr config env lhs) (← evalCoreExpr config env rhs)
  | .op .add [lhs, rhs] => do
      return PackedValue.binary .add
        (← evalCoreExpr config env lhs) (← evalCoreExpr config env rhs)
  | .op .sub [lhs, rhs] => do
      return PackedValue.binary .sub
        (← evalCoreExpr config env lhs) (← evalCoreExpr config env rhs)
  | .op .mul [lhs, rhs] => do
      return PackedValue.binary .mul
        (← evalCoreExpr config env lhs) (← evalCoreExpr config env rhs)
  | .op .eq [lhs, rhs] => do
      return PackedValue.compare .eq
        (← evalCoreExpr config env lhs) (← evalCoreExpr config env rhs)
  | .op .lt_u [lhs, rhs] => do
      return PackedValue.compare .ult
        (← evalCoreExpr config env lhs) (← evalCoreExpr config env rhs)
  | .op .le_u [lhs, rhs] => do
      return PackedValue.compare .ule
        (← evalCoreExpr config env lhs) (← evalCoreExpr config env rhs)
  | .op .shl [value, amount] => do
      return PackedValue.shift .left
        (← evalCoreExpr config env value) (← evalCoreExpr config env amount)
  | .op .shr [value, amount] => do
      return PackedValue.shift .right
        (← evalCoreExpr config env value) (← evalCoreExpr config env amount)
  | .op .mux [condition, thenValue, elseValue] => do
      return PackedValue.mux (← evalCoreExpr config env condition)
        (← evalCoreExpr config env thenValue)
        (← evalCoreExpr config env elseValue)
  | .concat [msb, lsb] => do
      return PackedValue.concat (← evalCoreExpr config env msb)
        (← evalCoreExpr config env lsb)
  | .resize width value => do
      return PackedValue.resize (evalDim config width)
        (← evalCoreExpr config env value)
  | .slice value hi lo => do
      return PackedValue.slice (evalDim config hi) (evalDim config lo)
        (← evalCoreExpr config env value)
  | _ => none

/-! ## Expression correctness -/

theorem compileExpr_correct (config : Config) (env : ValueEnv) (source : CombExpr) :
    evalCoreExpr config env (compileExpr source) =
      evalSourceExpr config env source := by
  induction source with
  | ref => rfl
  | const => rfl
  | paramConst => rfl
  | unary operator value ih =>
      cases operator <;>
        simp only [compileExpr, lowerUnary, evalCoreExpr, evalSourceExpr] <;>
        rw [ih]
  | binary operator lhs rhs lhsIH rhsIH =>
      cases operator <;>
        simp only [compileExpr, lowerBinary, evalCoreExpr, evalSourceExpr] <;>
        rw [lhsIH, rhsIH]
  | compare operator lhs rhs lhsIH rhsIH =>
      cases operator <;>
        simp only [compileExpr, lowerCompare, evalCoreExpr, evalSourceExpr] <;>
        rw [lhsIH, rhsIH]
  | shift operator value amount valueIH amountIH =>
      cases operator <;>
        simp only [compileExpr, lowerShift, evalCoreExpr, evalSourceExpr] <;>
        rw [valueIH, amountIH]
  | mux condition thenValue elseValue conditionIH thenIH elseIH =>
      simp only [compileExpr, evalCoreExpr, evalSourceExpr]
      rw [conditionIH, thenIH, elseIH]
  | concat msb lsb msbIH lsbIH =>
      simp only [compileExpr, evalCoreExpr, evalSourceExpr]
      rw [msbIH, lsbIH]
  | resize width value ih =>
      simp only [compileExpr, evalCoreExpr, evalSourceExpr]
      rw [ih]
  | slice value hi lo ih =>
      simp only [compileExpr, evalCoreExpr, evalSourceExpr]
      rw [ih]

/-- A scoped source expression cannot fail lookup.  This is the expression
    layer of the non-vacuity proof for whole designs. -/
theorem evalSourceExpr_defined (config : Config) (env : ValueEnv) (source : CombExpr)
    (available : ∀ name ∈ source.refs,
      ∃ value, env.lookup name = some value) :
    ∃ value, evalSourceExpr config env source = some value := by
  induction source with
  | ref name =>
      exact available name (by simp [CombExpr.refs])
  | const value width =>
      exact ⟨PackedValue.ofInt (evalDim config width) value, rfl⟩
  | paramConst value width =>
      exact ⟨PackedValue.ofNat (evalDim config width) (evalDim config value), rfl⟩
  | unary operator value ih =>
      have valueAvailable : ∀ name ∈ value.refs,
          ∃ packed, env.lookup name = some packed := by
        simpa [CombExpr.refs] using available
      rcases ih valueAvailable with ⟨packed, packedEq⟩
      exact ⟨PackedValue.unary operator packed, by
        simp [evalSourceExpr, packedEq]⟩
  | binary operator lhs rhs lhsIH rhsIH =>
      have lhsAvailable : ∀ name ∈ lhs.refs,
          ∃ packed, env.lookup name = some packed := by
        intro name member
        exact available name (by simp [CombExpr.refs, member])
      have rhsAvailable : ∀ name ∈ rhs.refs,
          ∃ packed, env.lookup name = some packed := by
        intro name member
        exact available name (by simp [CombExpr.refs, member])
      rcases lhsIH lhsAvailable with ⟨lhsValue, lhsEq⟩
      rcases rhsIH rhsAvailable with ⟨rhsValue, rhsEq⟩
      exact ⟨PackedValue.binary operator lhsValue rhsValue, by
        simp [evalSourceExpr, lhsEq, rhsEq]⟩
  | compare operator lhs rhs lhsIH rhsIH =>
      have lhsAvailable : ∀ name ∈ lhs.refs,
          ∃ packed, env.lookup name = some packed := by
        intro name member
        exact available name (by simp [CombExpr.refs, member])
      have rhsAvailable : ∀ name ∈ rhs.refs,
          ∃ packed, env.lookup name = some packed := by
        intro name member
        exact available name (by simp [CombExpr.refs, member])
      rcases lhsIH lhsAvailable with ⟨lhsValue, lhsEq⟩
      rcases rhsIH rhsAvailable with ⟨rhsValue, rhsEq⟩
      exact ⟨PackedValue.compare operator lhsValue rhsValue, by
        simp [evalSourceExpr, lhsEq, rhsEq]⟩
  | shift operator value amount valueIH amountIH =>
      have valueAvailable : ∀ name ∈ value.refs,
          ∃ packed, env.lookup name = some packed := by
        intro name member
        exact available name (by simp [CombExpr.refs, member])
      have amountAvailable : ∀ name ∈ amount.refs,
          ∃ packed, env.lookup name = some packed := by
        intro name member
        exact available name (by simp [CombExpr.refs, member])
      rcases valueIH valueAvailable with ⟨valueResult, valueEq⟩
      rcases amountIH amountAvailable with ⟨amountResult, amountEq⟩
      exact ⟨PackedValue.shift operator valueResult amountResult, by
        simp [evalSourceExpr, valueEq, amountEq]⟩
  | mux condition thenValue elseValue conditionIH thenIH elseIH =>
      have conditionAvailable : ∀ name ∈ condition.refs,
          ∃ packed, env.lookup name = some packed := by
        intro name member
        exact available name (by simp [CombExpr.refs, member])
      have thenAvailable : ∀ name ∈ thenValue.refs,
          ∃ packed, env.lookup name = some packed := by
        intro name member
        exact available name (by simp [CombExpr.refs, member])
      have elseAvailable : ∀ name ∈ elseValue.refs,
          ∃ packed, env.lookup name = some packed := by
        intro name member
        exact available name (by simp [CombExpr.refs, member])
      rcases conditionIH conditionAvailable with ⟨conditionResult, conditionEq⟩
      rcases thenIH thenAvailable with ⟨thenResult, thenEq⟩
      rcases elseIH elseAvailable with ⟨elseResult, elseEq⟩
      exact ⟨PackedValue.mux conditionResult thenResult elseResult, by
        simp [evalSourceExpr, conditionEq, thenEq, elseEq]⟩
  | concat msb lsb msbIH lsbIH =>
      have msbAvailable : ∀ name ∈ msb.refs,
          ∃ packed, env.lookup name = some packed := by
        intro name member
        exact available name (by simp [CombExpr.refs, member])
      have lsbAvailable : ∀ name ∈ lsb.refs,
          ∃ packed, env.lookup name = some packed := by
        intro name member
        exact available name (by simp [CombExpr.refs, member])
      rcases msbIH msbAvailable with ⟨msbResult, msbEq⟩
      rcases lsbIH lsbAvailable with ⟨lsbResult, lsbEq⟩
      exact ⟨PackedValue.concat msbResult lsbResult, by
        simp [evalSourceExpr, msbEq, lsbEq]⟩
  | resize width value ih =>
      have valueAvailable : ∀ name ∈ value.refs,
          ∃ packed, env.lookup name = some packed := by
        simpa [CombExpr.refs] using available
      rcases ih valueAvailable with ⟨packed, packedEq⟩
      exact ⟨PackedValue.resize (evalDim config width) packed, by
        simp [evalSourceExpr, packedEq]⟩
  | slice value hi lo ih =>
      have valueAvailable : ∀ name ∈ value.refs,
          ∃ packed, env.lookup name = some packed := by
        simpa [CombExpr.refs] using available
      rcases ih valueAvailable with ⟨packed, packedEq⟩
      exact ⟨PackedValue.slice (evalDim config hi) (evalDim config lo) packed, by
        simp [evalSourceExpr, packedEq]⟩

/-! ## Whole-design semantics and correctness -/

def compileBinding (binding : Binding) : Stmt :=
  .assign binding.name (.resize binding.width (compileExpr binding.rhs))

def allBindings (design : CombDesign) : List Binding :=
  design.wires ++ design.outputs

def compileComb (design : CombDesign) : Module :=
  { name := design.name
    parameters := design.parameters
    inputs := design.inputs.map fun port =>
      { name := port.name, ty := .bitVector port.width }
    outputs := design.outputs.map fun output =>
      { name := output.name, ty := .bitVector output.width }
    wires := design.wires.map fun wire =>
      { name := wire.name, ty := .bitVector wire.width }
    body := (allBindings design).map compileBinding }

def evalSourceBinding (config : Config) (env : ValueEnv)
    (binding : Binding) : Option ValueEnv := do
  let value ← evalSourceExpr config env binding.rhs
  return (binding.name, value.resize (evalDim config binding.width)) :: env

def evalCoreStmt (config : Config) (env : ValueEnv) : Stmt → Option ValueEnv
  | .assign name rhs => do
      return (name, (← evalCoreExpr config env rhs)) :: env
  | _ => none

def evalSourceBindings (config : Config) : List Binding → ValueEnv → Option ValueEnv
  | [], env => some env
  | binding :: rest, env => do
      let env ← evalSourceBinding config env binding
      evalSourceBindings config rest env

def evalCoreStmts (config : Config) : List Stmt → ValueEnv → Option ValueEnv
  | [], env => some env
  | statement :: rest, env => do
      let env ← evalCoreStmt config env statement
      evalCoreStmts config rest env

/-! The whole-module observation includes the elaboration environment and the
    complete packed interface.  Consequently, changing a parameter, port
    width, or wire width changes semantics even when output bits happen to be
    equal. -/

abbrev ParameterValue := String × Nat × Nat
abbrev PortValue := String × Nat × Option PackedValue
abbrev NamedWidth := String × Nat

structure Evaluation where
  /-- `(name, declared default, value in this configuration)`. -/
  parameters : List ParameterValue
  inputs : List PortValue
  wires : List NamedWidth
  outputs : List PortValue
  inputsWellTyped : Bool
  outputsWellTyped : Bool
  deriving DecidableEq

def observeParameters (parameters : List Parameter) (config : Config) :
    List ParameterValue :=
  parameters.map fun parameter =>
    (parameter.name, parameter.defaultValue, config parameter.name)

def observeSourcePorts (ports : List PortDecl) (config : Config) (env : ValueEnv) :
    List PortValue :=
  ports.map fun port => (port.name, evalDim config port.width, env.lookup port.name)

def observeSourceBindings (bindings : List Binding) (config : Config)
    (env : ValueEnv) : List PortValue :=
  bindings.map fun binding =>
    (binding.name, evalDim config binding.width, env.lookup binding.name)

def observeSourceWidths (bindings : List Binding) (config : Config) :
    List NamedWidth :=
  bindings.map fun binding => (binding.name, evalDim config binding.width)

/-- Only scalar packed ports belong to the verified core subset. -/
def evalPackedWidth (config : Config) : HWType → Option Nat
  | .bit => some 1
  | .bitVector width => some (evalDim config width)
  | .array _ _ => none

def observeCorePorts (config : Config) (env : ValueEnv) :
    List Port → Option (List PortValue)
  | [] => some []
  | port :: rest => do
      let width ← evalPackedWidth config port.ty
      let rest ← observeCorePorts config env rest
      return (port.name, width, env.lookup port.name) :: rest

def observeCoreWidths (config : Config) :
    List Port → Option (List NamedWidth)
  | [] => some []
  | port :: rest => do
      let width ← evalPackedWidth config port.ty
      let rest ← observeCoreWidths config rest
      return (port.name, width) :: rest

theorem observeCompiledInputs (ports : List PortDecl) (config : Config)
    (env : ValueEnv) :
    observeCorePorts config env
        (ports.map fun port =>
          ({ name := port.name, ty := .bitVector port.width } : Port)) =
      some (observeSourcePorts ports config env) := by
  induction ports with
  | nil => rfl
  | cons port rest ih =>
      simp [observeCorePorts, observeSourcePorts, evalPackedWidth, ih]

theorem observeCompiledBindings (bindings : List Binding) (config : Config)
    (env : ValueEnv) :
    observeCorePorts config env
        (bindings.map fun binding =>
          ({ name := binding.name, ty := .bitVector binding.width } : Port)) =
      some (observeSourceBindings bindings config env) := by
  induction bindings with
  | nil => rfl
  | cons binding rest ih =>
      simp [observeCorePorts, observeSourceBindings, evalPackedWidth, ih]

theorem observeCompiledWidths (bindings : List Binding) (config : Config) :
    observeCoreWidths config
        (bindings.map fun binding =>
          ({ name := binding.name, ty := .bitVector binding.width } : Port)) =
      some (observeSourceWidths bindings config) := by
  induction bindings with
  | nil => rfl
  | cons binding rest ih =>
      simp [observeCoreWidths, observeSourceWidths, evalPackedWidth, ih]

def portValuesWellTyped (ports : List PortValue) : Bool :=
  ports.all fun (_, expectedWidth, value) =>
    match value with
    | some value => value.width == expectedWidth
    | none => false

def evalSourceDesign (design : CombDesign) (config : Config) (inputs : ValueEnv) :
    Option Evaluation := do
  let env ← evalSourceBindings config (allBindings design) inputs
  let inputValues := observeSourcePorts design.inputs config inputs
  let outputValues := observeSourceBindings design.outputs config env
  return {
    parameters := observeParameters design.parameters config
    inputs := inputValues
    wires := observeSourceWidths design.wires config
    outputs := outputValues
    inputsWellTyped := portValuesWellTyped inputValues
    outputsWellTyped := portValuesWellTyped outputValues
  }

/-- Fail-closed executable semantics for the verified core-module subset.
    Primitive modules, native items, assertions, non-assignment statements,
    and array interfaces have no semantics here. -/
def evalCoreModule (module : Module) (config : Config) (inputs : ValueEnv) :
    Option Evaluation :=
  if module.isPrimitive then none
  else if !module.nativeItems.isEmpty then none
  else if !module.assertions.isEmpty then none
  else do
    let inputValues ← observeCorePorts config inputs module.inputs
    let wireWidths ← observeCoreWidths config module.wires
    let env ← evalCoreStmts config module.body inputs
    let outputValues ← observeCorePorts config env module.outputs
    return {
      parameters := observeParameters module.parameters config
      inputs := inputValues
      wires := wireWidths
      outputs := outputValues
      inputsWellTyped := portValuesWellTyped inputValues
      outputsWellTyped := portValuesWellTyped outputValues
    }

theorem compileBinding_correct (config : Config) (env : ValueEnv) (binding : Binding) :
    evalCoreStmt config env (compileBinding binding) =
      evalSourceBinding config env binding := by
  simp only [compileBinding, evalCoreStmt, evalSourceBinding, evalCoreExpr]
  rw [compileExpr_correct]
  cases evalSourceExpr config env binding.rhs <;> rfl

theorem compileBindings_correct (config : Config) (bindings : List Binding)
    (env : ValueEnv) :
    evalCoreStmts config (bindings.map compileBinding) env =
      evalSourceBindings config bindings env := by
  induction bindings generalizing env with
  | nil => rfl
  | cons binding rest ih =>
      simp [evalCoreStmts, evalSourceBindings, compileBinding_correct, ih]

/-- Every reference is to an input or to a previously evaluated binding. -/
def BindingsScoped : List String → List Binding → Prop
  | _, [] => True
  | available, binding :: rest =>
      (∀ name ∈ binding.rhs.refs, name ∈ available) ∧
      binding.name ∉ available ∧
      BindingsScoped (binding.name :: available) rest

/-- The finite domain on which a configuration is semantically observed.
    `Config` is represented as a total function for convenient evaluation;
    values outside this list are irrelevant because `SupportedComb` requires
    every dimension reference to be declared here. -/
def configDomain (design : CombDesign) : List String :=
  design.parameters.map (·.name)

/-- Every dimension expression in the reified source design. -/
def dimensions (design : CombDesign) : List DimExpr :=
  design.inputs.map (·.width) ++
  (allBindings design).flatMap fun binding =>
    binding.width :: binding.rhs.dimensions

def ParametersDeclared (design : CombDesign) : Prop :=
  ∀ dimension ∈ dimensions design,
    ∀ name ∈ dimParameters dimension, name ∈ configDomain design

/-- Structural contract for the phase-one source subset. -/
def SupportedComb (design : CombDesign) : Prop :=
  (configDomain design).Nodup ∧
  (design.inputs.map (·.name)).Nodup ∧
  BindingsScoped (design.inputs.map (·.name)) (allBindings design) ∧
  ParametersDeclared design

/-- Core-module shape accepted by the verified combinational semantics. -/
def CoreCombWellFormed (module : Module) : Prop :=
  module.isPrimitive = false ∧
  (module.parameters.map (·.name)).Nodup ∧
  module.nativeItems = [] ∧
  module.assertions = [] ∧
  (∀ statement ∈ module.body,
    ∃ name rhs, statement = .assign name rhs) ∧
  (∀ port ∈ module.inputs ++ module.outputs ++ module.wires,
    ∃ width, port.ty = .bitVector width)

/-- The pure lowering always produces exactly the fail-closed core shape; the
    structural source premise supplies uniqueness of parameter declarations. -/
theorem compileComb_wellFormed (design : CombDesign)
    (supported : SupportedComb design) :
    CoreCombWellFormed (compileComb design) := by
  constructor
  · rfl
  constructor
  · exact supported.1
  constructor
  · rfl
  constructor
  · rfl
  constructor
  · intro statement member
    rcases List.mem_map.mp member with ⟨binding, _bindingMember, statementEq⟩
    subst statement
    exact ⟨binding.name, .resize binding.width (compileExpr binding.rhs), rfl⟩
  · intro port member
    change port ∈
      (design.inputs.map fun input =>
        ({ name := input.name, ty := .bitVector input.width } : Port)) ++
      (design.outputs.map fun output =>
        ({ name := output.name, ty := .bitVector output.width } : Port)) ++
      (design.wires.map fun wire =>
        ({ name := wire.name, ty := .bitVector wire.width } : Port)) at member
    rcases List.mem_append.mp member with inputOrOutput | wireMember
    · rcases List.mem_append.mp inputOrOutput with inputMember | outputMember
      · rcases List.mem_map.mp inputMember with ⟨input, _, portEq⟩
        subst port
        exact ⟨input.width, rfl⟩
      · rcases List.mem_map.mp outputMember with ⟨output, _, portEq⟩
        subst port
        exact ⟨output.width, rfl⟩
    · rcases List.mem_map.mp wireMember with ⟨wire, _, portEq⟩
      subst port
      exact ⟨wire.width, rfl⟩

/-- Public carrier for the certified production path. -/
structure CertifiedCombDesign where
  design : CombDesign
  supported : SupportedComb design

def compileSupportedComb (certified : CertifiedCombDesign) : Module :=
  compileComb certified.design

theorem compileSupportedComb_wellFormed (certified : CertifiedCombDesign) :
    CoreCombWellFormed (compileSupportedComb certified) :=
  compileComb_wellFormed certified.design certified.supported

/-- Configuration legality is separated from semantic preservation.  It
    requires every packed width materialized by the design to be positive. -/
def positiveDimensions (design : CombDesign) : List DimExpr :=
  design.inputs.map (·.width) ++
  (allBindings design).flatMap fun binding =>
    binding.width :: binding.rhs.positiveDimensions

def divisors (design : CombDesign) : List DimExpr :=
  (dimensions design).flatMap dimDivisors

def ValidConfig (design : CombDesign) (config : Config) : Prop :=
  (∀ width ∈ positiveDimensions design, 0 < evalDim config width) ∧
  (∀ divisor ∈ divisors design, evalDim config divisor ≠ 0) ∧
  (∀ binding ∈ allBindings design, binding.rhs.SlicesValid config)

theorem validInputs_containInputNames
    (design : CombDesign) (config : Config) (inputs : ValueEnv)
    (valid : ValidInputs design config inputs) :
    EnvContains inputs (design.inputs.map (·.name)) := by
  intro name member
  rcases List.mem_map.mp member with ⟨port, portMember, nameEq⟩
  subst name
  rcases valid port portMember with ⟨value, lookup, _width⟩
  exact ⟨value, lookup⟩

theorem envContains_cons
    (name : String) (value : PackedValue) (env : ValueEnv) (names : List String)
    (fresh : name ∉ names) (contains : EnvContains env names) :
    EnvContains ((name, value) :: env) (name :: names) := by
  intro query member
  rcases List.mem_cons.mp member with equal | member
  · subst query
    exact ⟨value, by simp⟩
  · rcases contains query member with ⟨oldValue, lookup⟩
    have different : query ≠ name := by
      intro equal
      apply fresh
      simpa [equal] using member
    have beqFalse : (query == name) = false := beq_false_of_ne different
    exact ⟨oldValue, by simp [List.lookup_cons, beqFalse, lookup]⟩

/-- Sequentially scoped bindings always evaluate to a concrete environment. -/
theorem evalSourceBindings_defined
    (config : Config) (bindings : List Binding) (available : List String)
    (env : ValueEnv) (scopeProof : BindingsScoped available bindings)
    (contains : EnvContains env available) :
    ∃ finalEnv, evalSourceBindings config bindings env = some finalEnv := by
  induction bindings generalizing available env with
  | nil => exact ⟨env, rfl⟩
  | cons binding rest ih =>
      change
        (∀ name ∈ binding.rhs.refs, name ∈ available) ∧
          binding.name ∉ available ∧
          BindingsScoped (binding.name :: available) rest at scopeProof
      rcases scopeProof with ⟨references, fresh, restScope⟩
      have expressionAvailable : ∀ name ∈ binding.rhs.refs,
          ∃ value, env.lookup name = some value := by
        intro name member
        exact contains name (references name member)
      rcases evalSourceExpr_defined config env binding.rhs expressionAvailable with
        ⟨value, valueEq⟩
      let stored := value.resize (evalDim config binding.width)
      have extended : EnvContains ((binding.name, stored) :: env)
          (binding.name :: available) :=
        envContains_cons binding.name stored env available fresh contains
      rcases ih (binding.name :: available) ((binding.name, stored) :: env)
          restScope extended with ⟨finalEnv, finalEq⟩
      exact ⟨finalEnv, by
        simp [evalSourceBindings, evalSourceBinding, valueEq, stored, finalEq]⟩

/-- Supported designs with valid inputs cannot make source evaluation return
    `none`; configuration legality is carried explicitly for the certificate
    even though the BitVec evaluator is total once widths are materialized. -/
theorem evalSourceDesign_total
    (design : CombDesign) (config : Config) (inputs : ValueEnv)
    (supported : SupportedComb design) (_valid : ValidConfig design config)
    (validInputs : ValidInputs design config inputs) :
    ∃ result, evalSourceDesign design config inputs = some result := by
  have scopeProof : BindingsScoped (design.inputs.map (·.name))
      (allBindings design) := supported.2.2.1
  have contains : EnvContains inputs (design.inputs.map (·.name)) :=
    validInputs_containInputNames design config inputs validInputs
  rcases evalSourceBindings_defined config (allBindings design)
      (design.inputs.map (·.name)) inputs scopeProof contains with
    ⟨finalEnv, finalEq⟩
  refine ⟨{
    parameters := observeParameters design.parameters config
    inputs := observeSourcePorts design.inputs config inputs
    wires := observeSourceWidths design.wires config
    outputs := observeSourceBindings design.outputs config finalEnv
    inputsWellTyped := portValuesWellTyped
      (observeSourcePorts design.inputs config inputs)
    outputsWellTyped := portValuesWellTyped
      (observeSourceBindings design.outputs config finalEnv)
  }, ?_⟩
  simp [evalSourceDesign, finalEq]

/-- Stable proposition used by proof certificates.  It quantifies over the
    complete parameter environment, hence in particular over every legal `W`. -/
def CompilerCorrectnessStatement : Prop :=
  ∀ (design : CombDesign) (config : Config) (inputs : ValueEnv),
    SupportedComb design → ValidConfig design config →
    ValidInputs design config inputs →
    evalCoreModule (compileComb design) config inputs =
      evalSourceDesign design config inputs

/-- General phase-one theorem: one proof covers every source design in the
    supported AST and every legal assignment of symbolic parameters. -/
theorem compileComb_correct : CompilerCorrectnessStatement := by
  intro design config inputs _supported _valid _validInputs
  simp only [evalCoreModule, evalSourceDesign, compileComb, Bool.false_eq_true,
    ↓reduceIte, List.isEmpty_nil, Bool.not_true, observeCompiledInputs,
    observeCompiledWidths]
  rw [compileBindings_correct]
  simp only [observeCompiledBindings]
  simp

/-- Stable non-vacuity proposition used by proof certificates. -/
def CompilerCorrectnessTotalStatement : Prop :=
  ∀ (design : CombDesign) (config : Config) (inputs : ValueEnv),
    SupportedComb design → ValidConfig design config →
    ValidInputs design config inputs →
    ∃ result,
      evalSourceDesign design config inputs = some result ∧
      evalCoreModule (compileComb design) config inputs = some result

/-- The preservation equality is inhabited on every supported, legal design:
    both interpreters produce the same concrete observation, rather than both
    failing with `none`. -/
theorem compileComb_correct_total : CompilerCorrectnessTotalStatement := by
  intro design config inputs supported valid validInputs
  rcases evalSourceDesign_total design config inputs supported valid validInputs with
    ⟨result, sourceEq⟩
  refine ⟨result, sourceEq, ?_⟩
  rw [compileComb_correct design config inputs supported valid validInputs, sourceEq]

theorem compileSupportedComb_correct
    (certified : CertifiedCombDesign) (config : Config) (inputs : ValueEnv)
    (valid : ValidConfig certified.design config)
    (validInputs : ValidInputs certified.design config inputs) :
    evalCoreModule (compileSupportedComb certified) config inputs =
      evalSourceDesign certified.design config inputs :=
  compileComb_correct certified.design config inputs certified.supported valid validInputs

theorem compileSupportedComb_correct_total
    (certified : CertifiedCombDesign) (config : Config) (inputs : ValueEnv)
    (valid : ValidConfig certified.design config)
    (validInputs : ValidInputs certified.design config inputs) :
    ∃ result,
      evalSourceDesign certified.design config inputs = some result ∧
      evalCoreModule (compileSupportedComb certified) config inputs = some result :=
  compileComb_correct_total certified.design config inputs
    certified.supported valid validInputs

/-- Update one named parameter while leaving the rest of a configuration
    unchanged.  This makes the explicit `∀ W` specialization convenient for
    downstream theorem statements; `compileComb_correct` itself is stronger
    because it already quantifies over the complete configuration. -/
def overrideConfig (base : Config) (parameter : String) (value : Nat) : Config :=
  fun name => if name == parameter then value else base name

theorem compileComb_correct_for_width
    (design : CombDesign) (base : Config) (parameter : String) (width : Nat)
    (inputs : ValueEnv)
    (supported : SupportedComb design)
    (valid : ValidConfig design (overrideConfig base parameter width))
    (validInputs : ValidInputs design (overrideConfig base parameter width) inputs) :
    evalCoreModule (compileComb design) (overrideConfig base parameter width) inputs =
      evalSourceDesign design (overrideConfig base parameter width) inputs :=
  compileComb_correct design (overrideConfig base parameter width) inputs
    supported valid validInputs

/-! ## A fixed symbolic witness for certificate non-vacuity -/

/-- A one-input passthrough whose packed width is the retained parameter `W`.
    Keeping this witness in the trusted production module lets the certificate
    prove that the supported domain is inhabited at every positive width. -/
def compilerCorrectnessWitnessDesign : CombDesign :=
  { name := "comb_correctness_width_witness"
    parameters := [{ name := "W", defaultValue := 1 }]
    inputs := [{ name := "x", width := .param "W" }]
    outputs := [{ name := "y", width := .param "W", rhs := .ref "x" }] }

theorem compilerCorrectnessWitness_supported :
    SupportedComb compilerCorrectnessWitnessDesign := by
  simp [SupportedComb, configDomain, ParametersDeclared, dimensions,
    compilerCorrectnessWitnessDesign, allBindings, BindingsScoped,
    CombExpr.refs, CombExpr.dimensions, dimParameters]

def compilerCorrectnessWitnessConfig (width : Nat) : Config :=
  fun name => if name == "W" then width else 0

def compilerCorrectnessWitnessInputs (width : Nat) : ValueEnv :=
  [("x", PackedValue.ofNat width 0)]

theorem compilerCorrectnessWitness_validConfig
    (width : Nat) (positive : 0 < width) :
    ValidConfig compilerCorrectnessWitnessDesign
      (compilerCorrectnessWitnessConfig width) := by
  simp [ValidConfig, positiveDimensions, divisors, dimensions, dimDivisors,
    compilerCorrectnessWitnessDesign, allBindings,
    CombExpr.positiveDimensions, CombExpr.dimensions, CombExpr.SlicesValid,
    evalDim, compilerCorrectnessWitnessConfig, positive]

theorem compilerCorrectnessWitness_validInputs (width : Nat) :
    ValidInputs compilerCorrectnessWitnessDesign
      (compilerCorrectnessWitnessConfig width)
      (compilerCorrectnessWitnessInputs width) := by
  simp [ValidInputs, compilerCorrectnessWitnessDesign,
    compilerCorrectnessWitnessConfig, compilerCorrectnessWitnessInputs,
    PackedValue.ofNat, evalDim, List.lookup]

def compilerCorrectnessWitness : CertifiedCombDesign :=
  { design := compilerCorrectnessWitnessDesign
    supported := compilerCorrectnessWitness_supported }

/-- Stable proposition proving that the certified domain is genuinely
    inhabited for every positive symbolic width. -/
def CompilerCorrectnessDomainInhabitedStatement : Prop :=
  ∀ width : Nat, 0 < width →
    ∃ inputs : ValueEnv,
      ValidConfig compilerCorrectnessWitness.design
          (compilerCorrectnessWitnessConfig width) ∧
        ValidInputs compilerCorrectnessWitness.design
          (compilerCorrectnessWitnessConfig width) inputs

theorem compilerCorrectnessDomain_inhabited :
    CompilerCorrectnessDomainInhabitedStatement := by
  intro width positive
  exact ⟨compilerCorrectnessWitnessInputs width,
    compilerCorrectnessWitness_validConfig width positive,
    compilerCorrectnessWitness_validInputs width⟩

end Sparkle.Compiler.CombCorrectness
