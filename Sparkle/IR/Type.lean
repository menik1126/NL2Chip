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

def mkMin : DimExpr → DimExpr → DimExpr
  | .literal lhs, .literal rhs => .literal (Nat.min lhs rhs)
  | lhs, rhs => if lhs == rhs then lhs else .min lhs rhs

def mkMax : DimExpr → DimExpr → DimExpr
  | .literal lhs, .literal rhs => .literal (Nat.max lhs rhs)
  | lhs, rhs => if lhs == rhs then lhs else .max lhs rhs

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
  | .min lhs rhs => return Nat.min (← eval? lookup lhs) (← eval? lookup rhs)
  | .max lhs rhs => return Nat.max (← eval? lookup lhs) (← eval? lookup rhs)

/-- Return a dimension only when it is independent of module parameters. -/
def toNat? (expr : DimExpr) : Option Nat :=
  expr.eval? (fun _ => none)

def isConcrete (expr : DimExpr) : Bool :=
  expr.toNat?.isSome

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
  | .mod lhs rhs | .pow lhs rhs | .min lhs rhs | .max lhs rhs =>
      (lhs.parameters ++ rhs.parameters).foldl
        (fun names name => if names.contains name then names else names ++ [name]) []

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
