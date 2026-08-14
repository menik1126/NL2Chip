/-
  Hardware Type System

  Defines the concrete types that can be represented in synthesizable hardware.
  This is a subset of Lean types that excludes higher-order functions, dependent types, etc.
-/

import Sparkle.Data.BitPack

namespace Sparkle.IR.Type

open Sparkle.Data.BitPack

/--
  A synthesizable hardware dimension.

  Unlike a plain `Nat`, a `DimExpr` can retain a top-level Lean natural-number
  parameter through the hardware IR.  The SystemVerilog backend emits these
  expressions as constant parameter expressions, so a single generated module
  can be elaborated at multiple widths.
-/
inductive DimExpr where
  | literal (value : Nat) : DimExpr
  | param (name : String) : DimExpr
  | add (lhs rhs : DimExpr) : DimExpr
  | sub (lhs rhs : DimExpr) : DimExpr
  | mul (lhs rhs : DimExpr) : DimExpr
  | div (lhs rhs : DimExpr) : DimExpr
  | mod (lhs rhs : DimExpr) : DimExpr
  | pow (base exponent : DimExpr) : DimExpr
  | shl (lhs rhs : DimExpr) : DimExpr
  | shr (lhs rhs : DimExpr) : DimExpr
  | bitAnd (lhs rhs : DimExpr) : DimExpr
  | bitOr (lhs rhs : DimExpr) : DimExpr
  | bitXor (lhs rhs : DimExpr) : DimExpr
  | clog2 (value : DimExpr) : DimExpr
  | min (lhs rhs : DimExpr) : DimExpr
  | max (lhs rhs : DimExpr) : DimExpr
  deriving Repr, BEq, DecidableEq, Inhabited

namespace DimExpr

/-- Construct a simplified symbolic sum. -/
def mkAdd : DimExpr → DimExpr → DimExpr
  | .literal 0, rhs => rhs
  | lhs, .literal 0 => lhs
  | .literal lhs, .literal rhs => .literal (lhs + rhs)
  | lhs, rhs => .add lhs rhs

/-- Construct a simplified (natural-number, hence saturating) difference. -/
def mkSub : DimExpr → DimExpr → DimExpr
  | lhs, .literal 0 => lhs
  | .literal lhs, .literal rhs => .literal (lhs - rhs)
  | lhs, rhs => if lhs == rhs then .literal 0 else .sub lhs rhs

/-- Construct a simplified symbolic product. -/
def mkMul : DimExpr → DimExpr → DimExpr
  | .literal 0, _ => .literal 0
  | _, .literal 0 => .literal 0
  | .literal 1, rhs => rhs
  | lhs, .literal 1 => lhs
  | .literal lhs, .literal rhs => .literal (lhs * rhs)
  | lhs, rhs => .mul lhs rhs

/-- Construct a simplified symbolic quotient. -/
def mkDiv : DimExpr → DimExpr → DimExpr
  | lhs, .literal 1 => lhs
  | .literal lhs, .literal rhs => .literal (lhs / rhs)
  | lhs, rhs => .div lhs rhs

/-- Construct a simplified symbolic remainder. -/
def mkMod : DimExpr → DimExpr → DimExpr
  | .literal lhs, .literal rhs => .literal (lhs % rhs)
  | lhs, rhs => .mod lhs rhs

/-- Construct a simplified symbolic power. -/
def mkPow : DimExpr → DimExpr → DimExpr
  | _, .literal 0 => .literal 1
  | lhs, .literal 1 => lhs
  | .literal lhs, .literal rhs => .literal (lhs ^ rhs)
  | lhs, rhs => .pow lhs rhs

/-- Construct a simplified symbolic natural-number left shift. -/
def mkShl : DimExpr → DimExpr → DimExpr
  | lhs, .literal 0 => lhs
  | .literal lhs, .literal rhs => .literal (lhs <<< rhs)
  | .literal 0, _ => .literal 0
  | lhs, rhs => .shl lhs rhs

/-- Construct a simplified symbolic natural-number logical right shift. -/
def mkShr : DimExpr → DimExpr → DimExpr
  | lhs, .literal 0 => lhs
  | .literal lhs, .literal rhs => .literal (lhs >>> rhs)
  | .literal 0, _ => .literal 0
  | lhs, rhs => .shr lhs rhs

def mkBitAnd : DimExpr → DimExpr → DimExpr
  | .literal lhs, .literal rhs => .literal (lhs &&& rhs)
  | .literal 0, _ | _, .literal 0 => .literal 0
  | lhs, rhs => if lhs == rhs then lhs else .bitAnd lhs rhs

def mkBitOr : DimExpr → DimExpr → DimExpr
  | .literal lhs, .literal rhs => .literal (lhs ||| rhs)
  | .literal 0, rhs => rhs
  | lhs, .literal 0 => lhs
  | lhs, rhs => if lhs == rhs then lhs else .bitOr lhs rhs

def mkBitXor : DimExpr → DimExpr → DimExpr
  | .literal lhs, .literal rhs => .literal (lhs ^^^ rhs)
  | .literal 0, rhs => rhs
  | lhs, .literal 0 => lhs
  | lhs, rhs => if lhs == rhs then .literal 0 else .bitXor lhs rhs

/-- SystemVerilog/RTL-style ceiling log2 over naturals.  Both zero and one
    require zero address bits; larger values use `ceil(log2 value)`. -/
def clog2Nat (value : Nat) : Nat :=
  if value ≤ 1 then 0 else Nat.log2 (value - 1) + 1

def mkClog2 : DimExpr → DimExpr
  | .literal value => .literal (clog2Nat value)
  | value => .clog2 value

def mkMin : DimExpr → DimExpr → DimExpr
  | .literal lhs, .literal rhs => .literal (Nat.min lhs rhs)
  | lhs, rhs => if lhs == rhs then lhs else .min lhs rhs

def mkMax : DimExpr → DimExpr → DimExpr
  | .literal lhs, .literal rhs => .literal (Nat.max lhs rhs)
  | lhs, rhs => if lhs == rhs then lhs else .max lhs rhs

/-- Recursively put a dimension expression into the canonical form produced by
    the smart constructors.  This matters for native SystemVerilog round trips:
    the emitted work-width proof and the recovered value must describe the same
    simplified expression. -/
partial def normalize : DimExpr → DimExpr
  | .literal value => .literal value
  | .param name => .param name
  | .add lhs rhs => mkAdd lhs.normalize rhs.normalize
  | .sub lhs rhs => mkSub lhs.normalize rhs.normalize
  | .mul lhs rhs => mkMul lhs.normalize rhs.normalize
  | .div lhs rhs => mkDiv lhs.normalize rhs.normalize
  | .mod lhs rhs => mkMod lhs.normalize rhs.normalize
  | .pow lhs rhs => mkPow lhs.normalize rhs.normalize
  | .shl lhs rhs => mkShl lhs.normalize rhs.normalize
  | .shr lhs rhs => mkShr lhs.normalize rhs.normalize
  | .bitAnd lhs rhs => mkBitAnd lhs.normalize rhs.normalize
  | .bitOr lhs rhs => mkBitOr lhs.normalize rhs.normalize
  | .bitXor lhs rhs => mkBitXor lhs.normalize rhs.normalize
  | .clog2 value => mkClog2 value.normalize
  | .min lhs rhs => mkMin lhs.normalize rhs.normalize
  | .max lhs rhs => mkMax lhs.normalize rhs.normalize

instance (n : Nat) : OfNat DimExpr n where
  ofNat := .literal n

instance : Coe Nat DimExpr where
  coe := .literal

instance : Add DimExpr where
  add := mkAdd

instance : Sub DimExpr where
  sub := mkSub

instance : Mul DimExpr where
  mul := mkMul

/-- Evaluate a dimension under a parameter environment. -/
partial def eval? (lookup : String → Option Nat) : DimExpr → Option Nat
  | .literal value => some value
  | .param name => lookup name
  | .add lhs rhs => return (← eval? lookup lhs) + (← eval? lookup rhs)
  | .sub lhs rhs => return (← eval? lookup lhs) - (← eval? lookup rhs)
  | .mul lhs rhs => return (← eval? lookup lhs) * (← eval? lookup rhs)
  | .div lhs rhs => return (← eval? lookup lhs) / (← eval? lookup rhs)
  | .mod lhs rhs => return (← eval? lookup lhs) % (← eval? lookup rhs)
  | .pow lhs rhs => return (← eval? lookup lhs) ^ (← eval? lookup rhs)
  | .shl lhs rhs => return (← eval? lookup lhs) <<< (← eval? lookup rhs)
  | .shr lhs rhs => return (← eval? lookup lhs) >>> (← eval? lookup rhs)
  | .bitAnd lhs rhs => return (← eval? lookup lhs) &&& (← eval? lookup rhs)
  | .bitOr lhs rhs => return (← eval? lookup lhs) ||| (← eval? lookup rhs)
  | .bitXor lhs rhs => return (← eval? lookup lhs) ^^^ (← eval? lookup rhs)
  | .clog2 value => return clog2Nat (← eval? lookup value)
  | .min lhs rhs => return Nat.min (← eval? lookup lhs) (← eval? lookup rhs)
  | .max lhs rhs => return Nat.max (← eval? lookup lhs) (← eval? lookup rhs)

/-- Return a dimension only when it is independent of module parameters. -/
def toNat? (expr : DimExpr) : Option Nat :=
  expr.eval? (fun _ => none)

def isConcrete (expr : DimExpr) : Bool :=
  expr.toNat?.isSome

/--
Return a strictly positive, conservative working width for evaluating a
natural-number expression as an unsigned SystemVerilog constant expression.

This is deliberately a bound on the *value computation*, not the packed width
at which an `Expr.paramConst` is finally materialized.  Retained parameters are
modeled as 32-bit unsigned natural numbers, matching the current emitted
parameter contract.  Operations use simple compositional upper bounds rather
than relying on SystemVerilog's context-dependent sizing rules; notably, a
left shift grows by the (possibly symbolic) shift value.
-/
partial def natValueBitWidthBound : DimExpr → DimExpr
  | .literal value => .literal (Nat.max 1 (Nat.log2 value + 1))
  | .param _ => .literal 32
  | .add lhs rhs =>
      mkAdd (mkMax lhs.natValueBitWidthBound rhs.natValueBitWidthBound) 1
  | .sub lhs _ | .div lhs _ | .mod lhs _ =>
      -- Nat subtraction/division/remainder never exceed the left operand
      -- (`a % 0 = a` in Lean), even though evaluation may need a wider common
      -- operand context.  Each child is materialized independently below.
      lhs.natValueBitWidthBound
  | .bitAnd lhs rhs | .bitOr lhs rhs | .bitXor lhs rhs
  | .min lhs rhs | .max lhs rhs =>
      mkMax lhs.natValueBitWidthBound rhs.natValueBitWidthBound
  | .mul lhs rhs =>
      mkAdd lhs.natValueBitWidthBound rhs.natValueBitWidthBound
  | .shl lhs rhs =>
      mkAdd lhs.natValueBitWidthBound rhs
  | .shr lhs _ => lhs.natValueBitWidthBound
  | .pow base exponent =>
      mkMax 1 (mkMul base.natValueBitWidthBound exponent)
  | .clog2 value =>
      -- If `value` fits in B bits, `clog2 value` is at most B, whose
      -- representation needs only `clog2 (B + 1)` bits.
      mkClog2 (mkAdd value.natValueBitWidthBound 1)

/-- Shared safety limit for evaluating retained mathematical `Nat`
    expressions.  This bounds the number of packed bits an elaborator or Lean
    itself may be asked to construct while checking a parameter environment. -/
def maxNatWorkWidth : Nat := 1048576

/-- Evaluate a conservative upper bound without ever constructing a natural
    number larger than `cap + 1`.  The latter is the saturation sentinel.

    This is intentionally conservative for shrinking operations.  It is used
    to validate `natValueBitWidthBound` *before* ordinary `eval?`; consequently
    a hostile override such as `K = 0xffffffff` cannot make validation allocate
    the value of `1 << K` before the backend has a chance to reject it. -/
partial def evalUpperBoundCapped? (lookup : String → Option Nat) (cap : Nat) :
    DimExpr → Option Nat
  | .literal value => some (Nat.min value (cap + 1))
  | .param name => (lookup name).map fun value => Nat.min value (cap + 1)
  | .add lhs rhs => do
      let lhs ← evalUpperBoundCapped? lookup cap lhs
      let rhs ← evalUpperBoundCapped? lookup cap rhs
      if lhs > cap || rhs > cap || lhs > cap - rhs then some (cap + 1)
      else some (lhs + rhs)
  | .sub lhs rhs | .div lhs rhs | .mod lhs rhs | .shr lhs rhs => do
      let lhs ← evalUpperBoundCapped? lookup cap lhs
      let _ ← evalUpperBoundCapped? lookup cap rhs
      some lhs
  | .mul lhs rhs => do
      let lhs ← evalUpperBoundCapped? lookup cap lhs
      let rhs ← evalUpperBoundCapped? lookup cap rhs
      if lhs == 0 || rhs == 0 then some 0
      else if lhs > cap || rhs > cap || lhs > cap / rhs then some (cap + 1)
      else some (lhs * rhs)
  | .shl lhs rhs => do
      let lhs ← evalUpperBoundCapped? lookup cap lhs
      let rhs ← evalUpperBoundCapped? lookup cap rhs
      if lhs == 0 then some 0
      else if lhs > cap || rhs > Nat.log2 cap + 1 then some (cap + 1)
      else
        let factor := 1 <<< rhs
        if lhs > cap / factor then some (cap + 1) else some (lhs * factor)
  | .pow base exponent => do
      let base ← evalUpperBoundCapped? lookup cap base
      let exponent ← evalUpperBoundCapped? lookup cap exponent
      if exponent == 0 then some 1
      else if base == 0 then some 0
      else if base == 1 then some 1
      else if base > cap || exponent > Nat.log2 cap + 1 then some (cap + 1)
      else
        let rec go (remaining acc : Nat) : Nat :=
          if remaining == 0 then acc
          else if acc > cap / base then cap + 1
          else go (remaining - 1) (acc * base)
        some (go exponent 1)
  | .bitAnd lhs rhs | .min lhs rhs => do
      let lhs ← evalUpperBoundCapped? lookup cap lhs
      let rhs ← evalUpperBoundCapped? lookup cap rhs
      some (Nat.min lhs rhs)
  | .bitOr lhs rhs | .bitXor lhs rhs => do
      let lhs ← evalUpperBoundCapped? lookup cap lhs
      let rhs ← evalUpperBoundCapped? lookup cap rhs
      let greatest := Nat.max lhs rhs
      if greatest > cap then some (cap + 1)
      else if greatest == 0 then some 0
      else
        let width := Nat.log2 greatest + 1
        let upper := (1 <<< width) - 1
        some (Nat.min upper (cap + 1))
  | .clog2 value => do
      let value ← evalUpperBoundCapped? lookup cap value
      if value > cap then some (cap + 1)
      else some (clog2Nat value)
  | .max lhs rhs => do
      let lhs ← evalUpperBoundCapped? lookup cap lhs
      let rhs ← evalUpperBoundCapped? lookup cap rhs
      some (Nat.max lhs rhs)

/-- Replace named parameters while retaining any parameters not present in the
    environment.  Smart constructors keep common specialized expressions
    compact (for example, `W + 0` becomes `W`). -/
partial def substitute (lookup : String → Option DimExpr) : DimExpr → DimExpr
  | .literal value => .literal value
  | .param name => (lookup name).getD (.param name)
  | .add lhs rhs => mkAdd (lhs.substitute lookup) (rhs.substitute lookup)
  | .sub lhs rhs => mkSub (lhs.substitute lookup) (rhs.substitute lookup)
  | .mul lhs rhs => mkMul (lhs.substitute lookup) (rhs.substitute lookup)
  | .div lhs rhs => mkDiv (lhs.substitute lookup) (rhs.substitute lookup)
  | .mod lhs rhs => mkMod (lhs.substitute lookup) (rhs.substitute lookup)
  | .pow lhs rhs => mkPow (lhs.substitute lookup) (rhs.substitute lookup)
  | .shl lhs rhs => mkShl (lhs.substitute lookup) (rhs.substitute lookup)
  | .shr lhs rhs => mkShr (lhs.substitute lookup) (rhs.substitute lookup)
  | .bitAnd lhs rhs => mkBitAnd (lhs.substitute lookup) (rhs.substitute lookup)
  | .bitOr lhs rhs => mkBitOr (lhs.substitute lookup) (rhs.substitute lookup)
  | .bitXor lhs rhs => mkBitXor (lhs.substitute lookup) (rhs.substitute lookup)
  | .clog2 value => mkClog2 (value.substitute lookup)
  | .min lhs rhs => mkMin (lhs.substitute lookup) (rhs.substitute lookup)
  | .max lhs rhs => mkMax (lhs.substitute lookup) (rhs.substitute lookup)

/-- Require a dimension to be concrete instead of guessing a fallback width. -/
def requireNat (role : String) (expr : DimExpr) : Except String Nat :=
  match expr.toNat? with
  | some value => .ok value
  | none => .error s!"{role} '{reprStr expr}' is symbolic; specialize the module before using this concrete-only operation"

/-- Collect parameter references in first-use order. -/
partial def parameters : DimExpr → List String
  | .literal _ => []
  | .param name => [name]
  | .add lhs rhs | .sub lhs rhs | .mul lhs rhs | .div lhs rhs
  | .mod lhs rhs | .pow lhs rhs | .shl lhs rhs | .shr lhs rhs
  | .bitAnd lhs rhs | .bitOr lhs rhs | .bitXor lhs rhs
  | .min lhs rhs | .max lhs rhs =>
      (lhs.parameters ++ rhs.parameters).foldl
        (fun names name => if names.contains name then names else names ++ [name]) []
  | .clog2 value => value.parameters

/-- Human-readable form used by IR diagnostics. -/
partial def toString : DimExpr → String
  | .literal value => reprStr value
  | .param name => name
  | .add lhs rhs => s!"({lhs.toString} + {rhs.toString})"
  | .sub lhs rhs => s!"({lhs.toString} - {rhs.toString})"
  | .mul lhs rhs => s!"({lhs.toString} * {rhs.toString})"
  | .div lhs rhs => s!"({lhs.toString} / {rhs.toString})"
  | .mod lhs rhs => s!"({lhs.toString} % {rhs.toString})"
  | .pow lhs rhs => s!"({lhs.toString} ^ {rhs.toString})"
  | .shl lhs rhs => s!"({lhs.toString} << {rhs.toString})"
  | .shr lhs rhs => s!"({lhs.toString} >> {rhs.toString})"
  | .bitAnd lhs rhs => s!"({lhs.toString} & {rhs.toString})"
  | .bitOr lhs rhs => s!"({lhs.toString} | {rhs.toString})"
  | .bitXor lhs rhs => s!"({lhs.toString} xor {rhs.toString})"
  | .clog2 value => s!"clog2({value.toString})"
  | .min lhs rhs => s!"min({lhs.toString}, {rhs.toString})"
  | .max lhs rhs => s!"max({lhs.toString}, {rhs.toString})"

instance : ToString DimExpr where
  toString := DimExpr.toString

end DimExpr

/--
  Hardware Type: The subset of types that can be synthesized to hardware.

  - Bit: Single bit (wire)
  - BitVector: n-bit vector
  - Array: Fixed-size array (for memories/ROMs)
-/
inductive HWType where
  | bit : HWType
  | bitVector (width : DimExpr) : HWType
  | array (size : DimExpr) (elemType : HWType) : HWType
  deriving Repr, BEq, DecidableEq, Inhabited


namespace HWType

/-- Get the (possibly symbolic) bit width of a hardware type. -/
def width : HWType → DimExpr
  | bit => .literal 1
  | bitVector w => w
  | array size elemType => size * elemType.width

/-- Get the concrete bit width, when the type has no symbolic parameters.  The
    `Option` result deliberately prevents symbolic dimensions from silently
    becoming a zero-width concrete value. -/
def bitWidth (ty : HWType) : Option Nat :=
  ty.width.toNat?

/-- Compatibility alias with the conventional `?` suffix. -/
def bitWidth? (ty : HWType) : Option Nat :=
  ty.bitWidth

/-- Require a concrete flattened bit width.  Concrete-only consumers should
    use this checked API rather than `bitWidth`, whose legacy signature cannot
    communicate failure. -/
def requireBitWidth (role : String) (ty : HWType) : Except String Nat :=
  ty.width.requireNat role

/-- Replace symbolic dimensions throughout a hardware type. -/
def substituteDimensions (lookup : String → Option DimExpr) : HWType → HWType
  | .bit => .bit
  | .bitVector width => .bitVector (width.substitute lookup)
  | .array size elemType =>
      .array (size.substitute lookup) (elemType.substituteDimensions lookup)

/-- Every packed width and array length represented by a hardware type. -/
def dimensions (role : String) : HWType → List (String × DimExpr)
  | .bit => []
  | .bitVector width => [(role, width)]
  | .array size elemType =>
      (s!"{role} array length", size) :: elemType.dimensions s!"{role} element"

/-- Check if a hardware type is a single bit -/
def isBit : HWType → Bool
  | bit => true
  | _ => false

/-- Check if a hardware type is a bit vector -/
def isBitVector : HWType → Bool
  | bitVector _ => true
  | _ => false

/-- Check if a hardware type is an array -/
def isArray : HWType → Bool
  | array _ _ => true
  | _ => false

/-- Convert hardware type to a human-readable string -/
def toString : HWType → String
  | bit => "Bit"
  | bitVector (.literal 1) => "Bit"
  | bitVector w => s!"BitVec{w}"
  | array size elemType => s!"Array[{size}]({elemType.toString})"

instance : ToString HWType where
  toString := HWType.toString

end HWType

/-- Convert a Lean type with BitPack instance to HWType -/
def toHWType (α : Type u) (n : Nat) [BitPack α n] : HWType :=
  if n == 1 then
    .bit
  else
    .bitVector n

/-- Helper to infer HWType from a Nat width -/
def hwTypeFromWidth (w : Nat) : HWType :=
  if w == 1 then .bit else .bitVector w

/-- 8-bit hardware type -/
def byte : HWType := .bitVector 8

/-- 16-bit hardware type -/
def word16 : HWType := .bitVector 16

/-- 32-bit hardware type -/
def word32 : HWType := .bitVector 32

/-- 64-bit hardware type -/
def word64 : HWType := .bitVector 64

/-- Boolean hardware type -/
def hwBool : HWType := .bit

end Sparkle.IR.Type
