/-
  SystemVerilog AST → Sparkle IR Lowering

  Converts parsed SV AST into Sparkle's native IR (Sparkle.IR.AST),
  enabling JIT execution without Verilator.

  Key transformations:
  - always @(posedge clk) with if/else reset → Stmt.register
  - assign lhs = rhs → Stmt.assign
  - SVExpr operators → Sparkle Expr.op with IR Operator
  - Port widths → HWType
-/

import Tools.SVParser.AST
import Tools.SVParser.Parser
import Sparkle.IR.AST
import Sparkle.IR.Type

open Tools.SVParser.AST
open Sparkle.IR.AST
open Sparkle.IR.Type

namespace Tools.SVParser.Lower

-- ============================================================================
-- Type conversion
-- ============================================================================

/-- Convert a Verilog `[hi:lo]` range to its packed width.  Canonicalize the
    overwhelmingly common `[base-1:0]` form to `base`; expanding it as
    `(base-1)+1` would incorrectly turn the invalid Nat override `base=0` into
    a one-bit declaration because subtraction is saturating. -/
def rangeWidth : DimExpr → DimExpr → DimExpr
  | .sub base (.literal 1), .literal 0 => base
  | hi, lo => hi - lo + 1

/-- Convert Verilog bit range to HWType -/
def widthToHWType : Option (DimExpr × DimExpr) → HWType
  | none => .bit
  | some (hi, lo) => .bitVector (rangeWidth hi lo)

/-- Get bit width from SV port/decl width -/
def widthToBits : Option (DimExpr × DimExpr) → Option Nat
  | none => some 1
  | some (hi, lo) => (rangeWidth hi lo).toNat?

def specializeWidth (parameterValues : List (String × Nat))
    (width : Option (DimExpr × DimExpr)) : Option (DimExpr × DimExpr) :=
  width.map fun (hi, lo) =>
    let substitute := fun name => parameterValues.find? (fun entry => entry.1 == name)
      |>.map fun entry => DimExpr.literal entry.2
    (hi.substitute substitute, lo.substitute substitute)

-- ============================================================================
-- Environment for tracking declarations
-- ============================================================================

structure LowerEnv where
  portWidths : List (String × Option (DimExpr × DimExpr))  -- port name → width
  wireWidths : List (String × Option (DimExpr × DimExpr))  -- wire name → width
  regNames   : List String                          -- names declared as reg

def LowerEnv.empty : LowerEnv := { portWidths := [], wireWidths := [], regNames := [] }

def LowerEnv.getWidth (env : LowerEnv) (name : String) : Option (DimExpr × DimExpr) :=
  (env.portWidths.find? (·.1 == name) |>.map (·.2)).join <|>
  (env.wireWidths.find? (·.1 == name) |>.map (·.2)).join

def LowerEnv.getHWType (env : LowerEnv) (name : String) : HWType :=
  widthToHWType (env.getWidth name)

def LowerEnv.isReg (env : LowerEnv) (name : String) : Bool :=
  env.regNames.any (· == name)

-- ============================================================================
-- Expression lowering
-- ============================================================================

def lowerUnaryOp : SVUnaryOp → Operator
  | .logNot    => .not
  | .bitNot    => .not
  | .neg       => .neg
  | .reductAnd => .and  -- reduction ops treated as bitwise for now
  | .reductOr  => .or
  | .signed    => .not  -- unreachable: handled in lowerExpr
  | .unsigned  => .not  -- unreachable: handled in lowerExpr

def lowerBinOp : SVBinOp → Operator
  | .add    => .add
  | .sub    => .sub
  | .mul    => .mul
  | .pow    => .mul  -- only used in dimensions; data-path use is rejected below
  | .bitAnd => .and
  | .bitOr  => .or
  | .bitXor => .xor
  | .shl    => .shl
  | .shr    => .shr
  | .asr    => .asr
  | .eq     => .eq
  | .neq    => .eq  -- will need NOT wrapper
  | .lt     => .lt_u
  | .le     => .le_u
  | .gt     => .gt_u
  | .ge     => .ge_u
  | .logAnd => .and  -- unreachable: handled in lowerExpr as (a!=0) & (b!=0)
  | .logOr  => .or   -- unreachable: handled in lowerExpr as (a!=0) | (b!=0)

private def natBitLength (value : Nat) : Nat :=
  if value == 0 then 1 else Nat.log2 value + 1

/-- IEEE unsized literals are at least 32 bits, but grow when their value does
    not fit.  Decimal literals retain a sign bit; based literals are unsigned. -/
private def naturalLiteralWidth : SVLiteral → Nat
  | .decimal (some width) _ | .hex (some width) _ | .binary (some width) _ => width
  | .decimal none value => max 32 (natBitLength value + 1)
  | .hex none value | .binary none value => max 32 (natBitLength value)

def literalToConst : SVLiteral → Expr
  | .decimal (some w) v => .const (Int.ofNat v) w
  | literal@(.decimal none v) => .const (Int.ofNat v) (naturalLiteralWidth literal)
  | .hex (some w) v     => .const (Int.ofNat v) w
  | literal@(.hex none v) => .const (Int.ofNat v) (naturalLiteralWidth literal)
  | .binary (some w) v  => .const (Int.ofNat v) w
  | literal@(.binary none v) => .const (Int.ofNat v) (naturalLiteralWidth literal)

/-- Set of array-typed register names for distinguishing bit-select vs array access -/
private def arrayNames : List String := []  -- populated per-module during lowering

private def indexToConst : SVExpr → Option Nat
  | .lit (.decimal _ n) => some n
  | .lit (.hex _ n) => some n
  | .lit (.binary _ n) => some n
  | _ => none

private def isArrayName (name : String) : Bool :=
  -- Heuristic: names ending in common array patterns or known arrays
  name == "cpuregs" || name == "memory" || name == "mem" ||
  name.endsWith "_mem" || name.endsWith "_ram"

private def truncateNatToWidth (width value : Nat) : Option Nat :=
  if width == 0 then none else some (value % (2 ^ width))

/-- Evaluate a simple SVExpr to a Nat constant (handles literals, add, sub).
    Explicit literal widths and sized casts are applied instead of erased. -/
private partial def svExprToNat : SVExpr → Option Nat
  | .lit (.decimal width v) | .lit (.hex width v) | .lit (.binary width v) =>
      match width with
      | some concreteWidth => truncateNatToWidth concreteWidth v
      | none => some v
  | .binary .add a b => do let va ← svExprToNat a; let vb ← svExprToNat b; some (va + vb)
  | .binary .sub a b => do
      let va ← svExprToNat a
      let vb ← svExprToNat b
      -- Nat subtraction would otherwise silently saturate an unsupported
      -- negative SystemVerilog constant to zero.
      if vb <= va then some (va - vb) else none
  | .binary .mul a b => do let va ← svExprToNat a; let vb ← svExprToNat b; some (va * vb)
  | .binary .pow a b => do let va ← svExprToNat a; let vb ← svExprToNat b; some (va ^ vb)
  | .unary .unsigned a => svExprToNat a
  | .unary .neg _ => none
  | .sizedCast width value => do
      let concreteWidth ← width.toNat?
      truncateNatToWidth concreteWidth (← svExprToNat value)
  | _ => none

private def concatWidth : SVExpr → Nat
  | .concat args => args.foldl (fun acc a => acc + concatWidth a) 0
  | .slice _ hi lo => (hi - lo + 1).toNat?.getD 1
  | .partSelectPlus _ _ widthExpr => svExprToNat widthExpr |>.getD 1
  | .index _ _ => 1  -- single bit select
  | .lit literal => naturalLiteralWidth literal
  | .sizedCast width _ => width.toNat?.getD 0
  | _ => 32  -- default: assume 32-bit

partial def lowerExpr (e : SVExpr) : Expr :=
  match e with
  | .lit l => literalToConst l
  | .ident name => .ref name
  | .unary .reductAnd arg =>
    -- Reduction AND: &x iff the width-preserving complement is zero.  A
    -- fixed 32-bit all-ones XOR mask silently fails for native widths > 32.
    .op .eq [.op .not [lowerExpr arg], .const 0 32]
  | .unary .reductOr arg =>
    -- Reduction OR: |x → any bit set → x != 0
    .op .not [.op .eq [lowerExpr arg, .const 0 32]]
  | .unary .logNot arg =>
    -- Logical NOT: !x → (x == 0) — reduces multi-bit to bool
    .op .eq [lowerExpr arg, .const 0 32]
  | .unary .bitNot arg =>
    -- IR `.not` is a width-preserving bitwise complement.  A fixed 32-bit
    -- XOR mask corrupts native widths above 32 bits.
    .op .not [lowerExpr arg]
  | .unary .signed arg =>
    -- $signed(x): sign-extend concat immediates from their natural width to 32.
    -- For single wire refs (already 32-bit), pass through unchanged.
    let innerWidth := concatWidth arg
    let lowered := lowerExpr arg
    if innerWidth >= 32 || innerWidth == 0 then lowered
    else
      -- Sign extend: shift left then arithmetic shift right
      let shiftAmt := 32 - innerWidth
      .op .asr [.op .shl [lowered, .const (Int.ofNat shiftAmt) 32], .const (Int.ofNat shiftAmt) 32]
  | .unary .unsigned arg =>
    -- Sparkle IR packed values are unsigned by definition.  Keep this case
    -- separate so a surrounding sized cast does not mistake `$unsigned(-1)`
    -- for a sign-extending unsized negative literal.
    lowerExpr arg
  | .unary op arg => .op (lowerUnaryOp op) [lowerExpr arg]
  | .binary .neq lhs rhs => .op .not [.op .eq [lowerExpr lhs, lowerExpr rhs]]
  | .binary .logAnd lhs rhs =>
    -- Logical AND: a && b → (a != 0) & (b != 0) — must reduce multi-bit operands to bool
    let la := .op .not [.op .eq [lowerExpr lhs, .const 0 32]]
    let lb := .op .not [.op .eq [lowerExpr rhs, .const 0 32]]
    .op .and [la, lb]
  | .binary .logOr lhs rhs =>
    -- Logical OR: a || b → (a != 0) | (b != 0)
    let la := .op .not [.op .eq [lowerExpr lhs, .const 0 32]]
    let lb := .op .not [.op .eq [lowerExpr rhs, .const 0 32]]
    .op .or [la, lb]
  | .binary op lhs rhs => .op (lowerBinOp op) [lowerExpr lhs, lowerExpr rhs]
  | .ternary cond t el => .op .mux [lowerExpr cond, lowerExpr t, lowerExpr el]
  | .index arr idx =>
    match indexToConst idx with
    | some n => .slice (lowerExpr arr) n n  -- constant bit select
    | none =>
      -- Check if base is an array-typed register (real array access)
      -- vs scalar bit-select
      match arr with
      | .ident name =>
        if isArrayName name then
          .index (lowerExpr arr) (lowerExpr idx)  -- array access
        else
          .op .and [.op .shr [lowerExpr arr, lowerExpr idx], .const 1 1]  -- bit select
      | _ =>
        .op .and [.op .shr [lowerExpr arr, lowerExpr idx], .const 1 1]  -- dynamic
  | .slice expr hi lo => .slice (lowerExpr expr) hi lo
  | .partSelectPlus expr base widthExpr =>
    -- [base +: width] = (expr >> base) & ((1 << width) - 1)
    let width := svExprToNat widthExpr |>.getD 1
    let mask := (1 <<< width) - 1
    .op .and [.op .shr [lowerExpr expr, lowerExpr base], .const (Int.ofNat mask) width]
  | .concat args => .concat (args.map lowerExpr)
  | .repeat_ count value =>
    -- {N{expr}}: replicate expr N times (bit replication)
    -- For 1-bit expr repeated N times: result = (0 - expr) & ((1 << N) - 1)
    -- For multi-bit expr: concatenate N copies via shift-and-OR.
    match svExprToNat count with
    | none | some 0 =>
      -- `lowerModule` rejects this case before expression lowering.  Keep an
      -- invalid zero-width sentinel here so direct callers of `lowerExpr`
      -- also fail dimension validation instead of silently assuming one copy.
      .const 0 0
    | some 1 => lowerExpr value
    | some n =>
      let valExpr := lowerExpr value
      let elemIsProvablyOneBit := match value with
        | .lit (.decimal (some 1) _)
        | .lit (.hex (some 1) _)
        | .lit (.binary (some 1) _) => true
        | .index _ _ => true
        | .slice _ hi lo => (hi - lo + 1).toNat? == some 1
        | .partSelectPlus _ _ width => svExprToNat width == some 1
        | .sizedCast width _ => width.toNat? == some 1
        | _ => false
      if elemIsProvablyOneBit then
        -- Special case: 1-bit replication → (0 - val) & mask
        let totalBits := n * 1
        let mask := (1 <<< totalBits) - 1
        .op .and [.op .sub [.const 0 totalBits, valExpr], .const (Int.ofNat mask) totalBits]
      else
        -- Multi-bit or unknown width: build concat of N copies
        .concat (List.replicate n valExpr)
  | .sizedCast width value =>
    match value with
    | .lit (.decimal none v) | .lit (.hex none v) | .lit (.binary none v) =>
      -- Unsized integer literals may need more than 32 bits.  Casting the
      -- mathematical value directly avoids the parser's generic 32-bit
      -- fallback truncating values such as 4294967297 before a 64-bit cast.
      .const (Int.ofNat v) width
    | .unary .unsigned (.lit (.decimal none v))
    | .unary .unsigned (.lit (.hex none v))
    | .unary .unsigned (.lit (.binary none v)) =>
      -- `$unsigned` changes signing but not the literal's natural value width.
      -- The generic literal lowerer uses legacy 32/1-bit fallbacks, which
      -- would truncate 4294967297 or unsized 'b101 before the outer cast.
      .const (Int.ofNat v) width
    | .unary .neg (.lit (.decimal (some sourceWidth) v))
    | .unary .neg (.lit (.hex (some sourceWidth) v))
    | .unary .neg (.lit (.binary (some sourceWidth) v)) =>
      -- Explicitly sized literals are truncated before unary minus receives
      -- the cast context: `(16)'(-8'd511)` negates 8'hff and is 16'hff01.
      let sourceValue := v % (2 ^ sourceWidth)
      .const (-(Int.ofNat sourceValue)) width
    | .unary .neg (.lit (.decimal none v))
    | .unary .neg (.lit (.hex none v))
    | .unary .neg (.lit (.binary none v)) =>
      -- A sized cast supplies the evaluation context for a direct unary-minus
      -- literal.  Materialize the mathematical negative at that width: for
      -- example, `(16)'(-1)` evaluates to 16'hffff before signedness checks.
      .const (-(Int.ofNat v)) width
    | _ => .resize width (lowerExpr value)

-- ============================================================================
-- Extract target name from LHS expression
-- ============================================================================

def exprToName : SVExpr → Option String
  | .ident name => if isArrayName name then none else some name
  | .index (.ident name) _ => if isArrayName name then none else some name
  | .slice (.ident name) _ _ => some name
  -- Concat LHS handled separately by lowerConcatLhsAssign (needs bit scatter)
  | _ => none

/-- Extract target name from concat LHS (all elements must reference same register) -/
def concatLhsName : SVExpr → Option String
  | .concat elems =>
    let names := elems.filterMap fun e => match e with
      | .ident name => some name
      | .index (.ident name) _ => some name
      | .slice (.ident name) _ _ => some name
      | _ => none
    match names with
    | name :: rest => if rest.all (· == name) then some name else none
    | [] => none
  | _ => none

-- ============================================================================
-- Register extraction from always @(posedge clk) blocks
-- ============================================================================

/-- A register assignment found inside an always block -/
structure RegInfo where
  name      : String
  initValue : Int
  dataExpr  : Expr
  deriving Repr

/-- Interpret a literal after applying its own explicit source width. -/
private def materializeSourceLiteral (literal : SVLiteral) : Except String Nat := do
  let sourceValue ← match literal with
    | .decimal width value | .hex width value | .binary width value =>
        match width with
        | none => pure value
        | some sourceWidth => match truncateNatToWidth sourceWidth value with
            | some truncated => pure truncated
            | none => throw "reset cast has a zero-width source literal"
  pure sourceValue

/-- Materialize the unsigned target-width bit pattern of a direct literal
    under a concrete sized cast.  For a negative literal, source truncation
    happens before negation and target-width reduction. -/
private def materializeSizedLiteral (target : DimExpr) (negative : Bool)
    (literal : SVLiteral) : Except String Int := do
  let targetWidth ← match target.toNat? with
    | some width => pure width
    | none => throw s!"literal reset cast has a symbolic target width: {target}"
  if targetWidth == 0 then
    throw "literal reset cast has zero target width"
  let sourceValue ← materializeSourceLiteral literal
  let modulus := 2 ^ targetWidth
  let residue := sourceValue % modulus
  if negative then
    pure (Int.ofNat (if residue == 0 then 0 else modulus - residue))
  else
    pure (Int.ofNat residue)

private def materializeSizedNegativeLiteral
    (target : DimExpr) (literal : SVLiteral) : Except String Int :=
  materializeSizedLiteral target true literal

/-- A symbolic reset cast can be retained only when its target is exactly the
    destination register width.  In that case the backend will materialize the
    stored Int at that same symbolic width, so preserve the source-normalized
    signed value instead of freezing the cast at the default parameter value. -/
private def materializeDestinationWidthLiteral (target destinationWidth : DimExpr)
    (negative : Bool) (literal : SVLiteral) : Except String Int := do
  match target.toNat? with
  | some _ => materializeSizedLiteral target negative literal
  | none =>
      if target != destinationWidth then
        throw s!"symbolic literal reset cast width '{target}' is not exactly the destination width '{destinationWidth}'"
      let sourceValue ← materializeSourceLiteral literal
      pure (if negative then -(Int.ofNat sourceValue) else Int.ofNat sourceValue)

/-- Extract register assignments from if/else reset pattern:
    if (!rst_n) begin reg <= init; end
    else begin reg <= expr; end -/
def extractRegisters (resetBranch dataBranch : List SVStmt) : Except String (List RegInfo) := do
  let mut initMap : List (String × Int) := []
  for statement in resetBranch do
    match statement with
    | .nonblockAssign lhs rhs =>
      match exprToName lhs with
      | none => pure ()
      | some name =>
        let value? ← match rhs with
          | .lit (.decimal _ value) | .lit (.hex _ value) | .lit (.binary _ value) =>
              pure (some (Int.ofNat value))
          | .unary .neg (.lit (.decimal _ value)) =>
              pure (some (-(Int.ofNat value)))
          | .sizedCast target (.unary .neg (.lit literal)) =>
              pure (some (← materializeSizedNegativeLiteral target literal))
          | .unary .unsigned (.sizedCast target (.lit literal)) =>
              pure (some (← materializeSizedLiteral target false literal))
          | .unary .unsigned (.sizedCast target (.unary .neg (.lit literal))) =>
              pure (some (← materializeSizedLiteral target true literal))
          | _ => pure none
        for value in value?.toList do
          initMap := initMap ++ [(name, value)]
    | _ => pure ()
  let dataMap := dataBranch.filterMap fun s => match s with
    | .nonblockAssign lhs rhs => (exprToName lhs).map (·, lowerExpr rhs)
    | _ => none
  pure (initMap.filterMap fun (name, initVal) =>
    match dataMap.find? (·.1 == name) with
    | some (_, dataExpr) => some { name, initValue := initVal, dataExpr }
    | none => some { name, initValue := initVal, dataExpr := .ref name })

/-- Detect reset pattern in if/else:
    if (!rst_n) → active-low reset, returns (resetSignal, initBranch, dataBranch)
    if (rst)    → active-high reset -/
private def hasSubstr (s sub : String) : Bool := (s.splitOn sub).length > 1

def isResetName (name : String) : Bool :=
  name == "rst" || name == "reset" || name == "resetn" || name == "rst_n" ||
  name == "arst" || name == "arst_n" ||
  hasSubstr name "reset" || hasSubstr name "rst"

def detectReset (cond : SVExpr) (thenBranch elseBranch : List SVStmt)
    : Option (String × Bool × List SVStmt × List SVStmt) :=
  match cond with
  | .unary .logNot (.ident rst) =>
    -- if (!rst_n): active-low, then=init, else=data
    if isResetName rst then some (rst, false, thenBranch, elseBranch) else none
  | .unary .bitNot (.ident rst) =>
    if isResetName rst then some (rst, false, thenBranch, elseBranch) else none
  | .ident rst =>
    -- if (rst): active-high, then=init, else=data
    if isResetName rst then some (rst, true, thenBranch, elseBranch) else none
  | _ => none

-- ============================================================================
-- Imperative → Dataflow conversion (If-Conversion / Guarded Assignments)
--
-- Walk the statement tree tracking the current guard condition. Each
-- assignment produces (guard, target, value). Then chain them as a flat
-- priority mux: last-write-wins, matching Verilog semantics.
-- ============================================================================

/-- A guarded assignment: under `guard`, signal `target` gets `value`. -/
structure GuardedAssign where
  guard  : Expr
  target : String
  value  : Expr

/-- Conjunction helper: true & x = x, else AND -/
private def mkAnd (a b : Expr) : Expr :=
  match a with
  | .const 1 _ => b
  | _ => match b with
    | .const 1 _ => a
    | _ => .op .and [a, b]

/-- Is this a don't-care literal ('bx / 'hx)? -/
private def isDontCare : SVExpr → Bool
  | .lit (.binary none 0) => true
  | .lit (.hex none 0) => true
  | _ => false

/-- For a concat-LHS assignment like {a[31:20], a[10:1], a[11], a[19:12], a[0]} <= rhs,
    build the value expression that scatters RHS bits to the correct positions.
    Returns (targetName, scatteredExpr) or none if not applicable. -/
private def lowerConcatLhsAssign (lhs : SVExpr) (rhs : SVExpr) : Option (String × Expr) :=
  match lhs, concatLhsName lhs with
  | .concat elems, some name =>
    let fields : List (Nat × Nat) := elems.filterMap fun e => match e with
      | .slice (.ident _) hi lo => do
          let hi' ← hi.toNat?
          let lo' ← lo.toNat?
          some (hi', lo')
      | .index (.ident _) (.lit (.decimal _ idx)) => some (idx, idx)
      | .ident _ => some (31, 0)
      | _ => none
    if fields.length != elems.length then none
    else
      let rhsExpr := lowerExpr rhs
      let totalWidth := fields.foldl (fun acc (hi, lo) => acc + (hi - lo + 1)) 0
      let (terms, _) := fields.foldl (fun (acc, rhsOff) (hi, lo) =>
        let w := hi - lo + 1
        let rhsBit := totalWidth - rhsOff - w
        let extracted := Expr.slice rhsExpr (rhsBit + w - 1) rhsBit
        let shifted := if lo == 0 then extracted
                       else Expr.op .shl [extracted, Expr.const (Int.ofNat lo) 32]
        (acc ++ [shifted], rhsOff + w)
      ) ([], 0)
      let result := terms.foldl (fun acc t =>
        if acc == Expr.const 0 32 then t else Expr.op .or [acc, t]
      ) (Expr.const 0 32)
      some (name, result)
  | _, _ => none

/-- Decompose a multi-variable concat-LHS blocking assignment into per-variable assignments.
    `{a[hi1:lo1], b[base +: width], ...} = rhs` →
    [(a, rhs_slice_for_a), (b, rhs_slice_for_b), ...]
    Each target gets the corresponding bits from the RHS expression. -/
private def decomposeMultiConcatLhs (lhs : SVExpr) (rhs : SVExpr) : List (String × Expr) :=
  match lhs with
  | .concat elems =>
    -- Compute field widths and target names for each element
    let fields : List (String × Nat × Nat) := elems.filterMap fun e => match e with
      | .slice (.ident name) hi lo => do
          let hi' ← hi.toNat?
          let lo' ← lo.toNat?
          some (name, hi' - lo' + 1, lo')
      | .index (.ident name) idxExpr =>
        -- Evaluate index expression (may be constant expr like 0+4-1=3)
        match svExprToNat idxExpr with
        | some idx => some (name, 1, idx)
        | none => none
      | .partSelectPlus (.ident name) baseExpr widthExpr =>
        let base := match svExprToNat baseExpr with
          | some v => v | none => 0
        let width := svExprToNat widthExpr |>.getD 1
        some (name, width, base)
      | .ident name => some (name, 32, 0)
      | _ => none
    if fields.length != elems.length then []
    else
      let rhsExpr := lowerExpr rhs
      let totalWidth := fields.foldl (fun acc (_, w, _) => acc + w) 0
      -- Collect all (name, width, lo, rhsBit) with shifted RHS bits
      let (rawFields, _) := fields.foldl (fun (acc, rhsOff) (name, width, lo) =>
        let rhsBit := totalWidth - rhsOff - width
        (acc ++ [(name, width, lo, rhsBit)], rhsOff + width)
      ) ([], 0)
      -- Group by variable name: for each variable, produce a read-modify-write expression.
      -- Uses "__RMW_BASE__" as a placeholder for the old value, which is replaced by
      -- stmtsToMuxExprBlocking with the actual SSA base (previous iteration's output).
      let varNames := rawFields.map (·.1) |>.eraseDups
      varNames.flatMap fun varName =>
        let myFields := rawFields.filter (·.1 == varName)
        -- Compute combined mask for all fields
        let combinedMask := myFields.foldl (fun acc (_, width, lo, _) =>
          acc ||| (((1 <<< width) - 1) <<< lo)
        ) 0
        let invMask := combinedMask ^^^ 0xFFFFFFFFFFFFFFFF
        -- Build new bits: OR all shifted+masked fields
        let newBits := myFields.foldl (fun acc (_, width, lo, rhsBit) =>
          let extracted := Expr.slice rhsExpr (rhsBit + width - 1) rhsBit
          -- Force 64-bit promotion to avoid C++ UB on shifts >= 32
          let extracted64 := Expr.op .or [extracted, Expr.const 0 64]
          let shifted := if lo == 0 then extracted64
                         else Expr.op .shl [extracted64, Expr.const (Int.ofNat lo) 64]
          let maskVal := ((1 <<< width) - 1) <<< lo
          let masked := Expr.op .and [shifted, Expr.const (Int.ofNat maskVal) 64]
          if acc == Expr.const 0 64 then masked
          else Expr.op .or [acc, masked]
        ) (Expr.const 0 64)
        -- RMW: (varName & ~mask) | newBits
        -- Uses Expr.ref varName directly. For SSA variables, stmtsToMuxExprBlocking
        -- replaces self-references with the ssaBase (previous SSA iteration).
        -- This ensures topoSortBody's collectRefs sees the correct dependency.
        let cleared := Expr.op .and [Expr.ref varName, Expr.const (Int.ofNat invMask) 64]
        [(varName, Expr.op .or [cleared, newBits])]
  | _ => []

/-- Build a case arm condition from labels and selector.
    For case(1'b1), labels are direct conditions (priority encoding).
    For normal case, labels are compared against sel. -/
private def mkCaseCond (sel : SVExpr) (labels : List SVExpr) : Expr :=
  let isCase1b1 := match sel with
    | .lit (.binary (some 1) 1) => true
    | .lit (.decimal (some 1) 1) => true
    | _ => false
  labels.foldl (fun acc label =>
    let c := if isCase1b1 then lowerExpr label
             else Expr.op .eq [lowerExpr sel, lowerExpr label]
    if acc == Expr.const 0 1 then c else Expr.op .or [acc, c]
  ) (Expr.const 0 1)

/-- Process case arms: collect guarded assigns and track covered conditions.
    Verilog case semantics: first matching arm wins (no fall-through).
    Each arm's guard is AND-ed with !covered to exclude prior matches. -/
private def processCaseArms (sel : SVExpr) (arms : List (List SVExpr × List SVStmt))
    (guard : Expr) (collectFn : List SVStmt → Expr → List GuardedAssign)
    : List GuardedAssign × Expr :=
  arms.foldl (fun (result, covered) (labels, body) =>
    let armCond := mkCaseCond sel labels
    -- Guard this arm with !covered to enforce first-match-wins priority
    let activeGuard := if covered == .const 0 1 then mkAnd guard armCond
                       else mkAnd guard (mkAnd (.op .not [covered]) armCond)
    let armAssigns := collectFn body activeGuard
    let newCovered := if covered == .const 0 1 then armCond else .op .or [covered, armCond]
    (result ++ armAssigns, newCovered)
  ) ([], .const 0 1)

/-- Try to evaluate an IR expression as a compile-time constant.
    Returns some value if the expression is a constant (including
    constant comparisons like `eq(0, 0)` → 1). -/
private def tryEvalConst : Expr → Option Nat
  | .const v _ => some v.toNat
  | .op .eq [.const a _, .const b _] => some (if a == b then 1 else 0)
  | .op .not [e] => do let v ← tryEvalConst e; some (if v == 0 then 1 else 0)
  | _ => none

/-- Collect all guarded non-blocking assignments from statements.
    `guard` is the current path condition (true = Expr.const 1 1). -/
partial def collectGuardedNB (stmts : List SVStmt) (guard : Expr := .const 1 1)
    : List GuardedAssign :=
  stmts.flatMap fun s => match s with
    | .nonblockAssign lhs rhs =>
      if isDontCare rhs then []
      else match exprToName lhs with
        | some name => [{ guard, target := name, value := lowerExpr rhs }]
        | none =>
          -- Try concat-LHS (bit-scatter) assignment
          match lowerConcatLhsAssign lhs rhs with
          | some (name, value) => [{ guard, target := name, value }]
          | none => []
    | .ifElse cond thenB elseB =>
      let c := lowerExpr cond
      -- No constant folding for non-blocking assigns (posedge always blocks):
      -- tryEvalConst can change guard priority in the decoder's case statements,
      -- causing incorrect instruction decode when WITH_PCPI=1.
      collectGuardedNB thenB (mkAnd guard c) ++
      collectGuardedNB elseB (mkAnd guard (.op .not [c]))
    | .caseStmt sel arms default_ =>
      let (armAssigns, covered) := processCaseArms sel arms guard (fun s g => collectGuardedNB s g)
      let defAssigns := match default_ with
        | some d => collectGuardedNB d (mkAnd guard (.op .not [covered]))
        | none => []
      armAssigns ++ defAssigns
    | .forLoop _ _ _ body => collectGuardedNB body guard
    | _ => []

/-- Collect guarded assertions from statements.
    Each assertion becomes (guard, condition_expr). -/
partial def collectGuardedAsserts (stmts : List SVStmt) (guard : Expr := .const 1 1)
    : List (Expr × Expr) :=
  stmts.flatMap fun s => match s with
    | .assertStmt cond => [(guard, lowerExpr cond)]
    | .ifElse cond thenB elseB =>
      let c := lowerExpr cond
      collectGuardedAsserts thenB (mkAnd guard c) ++
      collectGuardedAsserts elseB (mkAnd guard (.op .not [c]))
    | .caseStmt sel arms default_ =>
      let (armAsserts, covered) := arms.foldl (fun (result, cov) (labels, body) =>
        let armCond := mkCaseCond sel labels
        let asserts := collectGuardedAsserts body (mkAnd guard armCond)
        let newCov := if cov == .const 0 1 then armCond else .op .or [cov, armCond]
        (result ++ asserts, newCov)
      ) ([], Expr.const 0 1)
      let defAsserts := match default_ with
        | some d => collectGuardedAsserts d (mkAnd guard (.op .not [covered]))
        | none => []
      armAsserts ++ defAsserts
    | _ => []

/-- Collect all guarded blocking assignments from statements. -/
partial def collectGuardedBlock (stmts : List SVStmt) (guard : Expr := .const 1 1)
    : List GuardedAssign :=
  stmts.flatMap fun s => match s with
    | .blockAssign lhs rhs =>
      if isDontCare rhs then []
      else match exprToName lhs with
        | some name => [{ guard, target := name, value := lowerExpr rhs }]
        | none =>
          -- Try single-variable concat-LHS
          match lowerConcatLhsAssign lhs rhs with
          | some (name, value) => [{ guard, target := name, value }]
          | none =>
            -- Multi-variable concat-LHS decomposition
            -- Group by variable name and OR-combine the shifted bit fields
            let assigns := decomposeMultiConcatLhs lhs rhs
            let names := assigns.map (·.1) |>.eraseDups
            names.flatMap fun name =>
              let fields := assigns.filter (·.1 == name) |>.map (·.2)
              match fields with
              | [] => []
              | [single] => [{ guard, target := name, value := single }]
              | first :: rest =>
                let combined := rest.foldl (fun acc f => Expr.op .or [acc, f]) first
                [{ guard, target := name, value := combined }]
    | .ifElse cond thenB elseB =>
      let c := lowerExpr cond
      -- No constant folding here — it corrupts decoder case priority in posedge blocks.
      -- Constant folding is only safe in emitBlockingStmtsSequential (always @*).
      collectGuardedBlock thenB (mkAnd guard c) ++
      collectGuardedBlock elseB (mkAnd guard (.op .not [c]))
    | .caseStmt sel arms default_ =>
      let (armAssigns, covered) := processCaseArms sel arms guard (fun s g => collectGuardedBlock s g)
      let defAssigns := match default_ with
        | some d => collectGuardedBlock d (mkAnd guard (.op .not [covered]))
        | none => []
      armAssigns ++ defAssigns
    | .forLoop _ _ _ body => collectGuardedBlock body guard
    | _ => []

/-- Collect all Expr.ref names used in an expression -/
partial def collectRefs : Expr → List String
  | .ref name => [name]
  | .op _ args => args.flatMap collectRefs
  | .concat args => args.flatMap collectRefs
  | .resize _ value => collectRefs value
  | .slice e _ _ => collectRefs e
  | .index a i => collectRefs a ++ collectRefs i
  | _ => []

/-- Chain guarded assignments into a flat priority mux (last-write-wins).
    `base` is the default when no guard is active (hold value for registers,
    first flat assign for blocking signals). -/
def guardedToMux (assigns : List GuardedAssign) (base : Expr) : Expr :=
  assigns.foldl (fun acc ga => .op .mux [ga.guard, ga.value, acc]) base

/-- Build mux expression for a non-blocking register from full always body. -/
def stmtsToMuxExpr (regName : String) (stmts : List SVStmt) : Expr :=
  let all := collectGuardedNB stmts
  let filtered := all.filter (·.target == regName)
  guardedToMux filtered (.ref regName)

/-- Build mux expression for a blocking combinational signal.
    Base is the first flat assignment (default value). -/
def stmtsToMuxExprBlocking (sigName : String) (stmts : List SVStmt) : Expr :=
  let initDefault := stmts.findSome? fun s => match s with
    | .blockAssign lhs rhs =>
      match exprToName lhs with
      | some n => if n == sigName then some (lowerExpr rhs) else none
      | none => none
    | _ => none
  -- For SSA variables (e.g., next_rd_ssa0_1), use the previous SSA version as base
  -- This avoids self-reference when no initDefault exists
  let ssaBase : Option Expr := do
    -- Extract the LAST _ssaD_N segment to handle nested SSA.
    -- "foo_ssa0_1_ssa1_2" → prefix="foo_ssa0_1", depth="1", idx=2 → base="foo_ssa0_1_ssa1_1"
    -- "foo_ssa0_0" → prefix="foo", depth="0", idx=0 → base="foo"
    let parts := sigName.splitOn "_ssa"
    if parts.length < 2 then none
    else
      -- Reconstruct: prefix = all parts except last, joined by "_ssa"
      let lastSuffix := parts[parts.length - 1]!  -- e.g., "1_2"
      let ssaPrefix := String.intercalate "_ssa" (parts.take (parts.length - 1))
      let suffParts := lastSuffix.splitOn "_"
      if suffParts.length < 2 then none
      else
        let depth := suffParts[0]!
        let idxStr := suffParts[1]!
        match idxStr.toNat? with
        | some 0 => some (.ref ssaPrefix)  -- ssa_0 reads from the prefix (original or outer SSA)
        | some n => some (.ref s!"{ssaPrefix}_ssa{depth}_{n - 1}")
        | none => none
  let base := initDefault.getD (ssaBase.getD (.ref sigName))
  let all := collectGuardedBlock stmts
  let filtered := all.filter (·.target == sigName)
  -- For SSA variables, replace self-references (Expr.ref sigName) in guarded assign
  -- values with the actual base (ssaBase = previous SSA iteration's output).
  -- This is needed for concat-LHS read-modify-write: (self & ~mask) | newBits
  -- where "self" should actually read from the previous SSA step.
  let resolved := if ssaBase.isSome then
      let rec substSelf (e : Expr) : Expr := match e with
        | .ref n => if n == sigName then base else .ref n
        | .op o args => .op o (args.map substSelf)
        | .concat args => .concat (args.map substSelf)
        | .resize width value => .resize width (substSelf value)
        | .slice inner hi lo => .slice (substSelf inner) hi lo
        | .index arr idx => .index (substSelf arr) (substSelf idx)
        | other => other
      filtered.map fun ga => { ga with value := substSelf ga.value }
    else filtered
  guardedToMux resolved base

/-- Collect all register names assigned (non-blocking) anywhere in statements -/
partial def collectAllRegNames (stmts : List SVStmt) : List String :=
  stmts.flatMap fun s => match s with
    | .nonblockAssign lhs _ =>
      match exprToName lhs with
      | some n => [n]
      | none => match concatLhsName lhs with | some n => [n] | none => []
    | .ifElse _ thenB elseB =>
      collectAllRegNames thenB ++ collectAllRegNames elseB
    | .caseStmt _ arms default_ =>
      let armNames := arms.flatMap fun (_, body) => collectAllRegNames body
      let defNames := match default_ with | some d => collectAllRegNames d | none => []
      armNames ++ defNames
    | .forLoop _ _ _ body => collectAllRegNames body
    | _ => []

/-- A byte-lane write: under `cond`, write `data[hi:lo]` to `arr[addr][hi:lo]` -/
structure ByteLaneWrite where
  addr : SVExpr
  data : SVExpr
  cond : SVExpr
  hi   : Nat
  lo   : Nat

/-- Collect array element writes: arr[idx] <= data, with optional condition.
    Also detects byte-strobe patterns: if (wstrb[n]) arr[idx][hi:lo] <= data[hi:lo] -/
partial def collectArrayWrites (arrName : String) (stmts : List SVStmt)
    : List (SVExpr × SVExpr × Option SVExpr) :=
  stmts.flatMap fun s => match s with
    | .nonblockAssign (.index (.ident name) idx) rhs =>
      if name == arrName then [(idx, rhs, none)] else []
    | .ifElse cond thenB elseB =>
      let thenWrites := (collectArrayWrites arrName thenB).map
        fun (i, d, _) => (i, d, some cond)
      let elseWrites := collectArrayWrites arrName elseB
      thenWrites ++ elseWrites
    | .caseStmt _ arms default_ =>
      let armWrites := arms.flatMap fun (_, body) => collectArrayWrites arrName body
      let defWrites := match default_ with | some body => collectArrayWrites arrName body | none => []
      armWrites ++ defWrites
    | _ => []

/-- Collect byte-lane writes: if (cond) arr[addr][hi:lo] <= data[hi:lo] -/
partial def collectByteLaneWrites (arrName : String) (stmts : List SVStmt)
    : List ByteLaneWrite :=
  stmts.flatMap fun s => match s with
    | .nonblockAssign (.slice (.index (.ident name) addr) hi lo) rhs =>
      if name == arrName then
        match hi.toNat?, lo.toNat? with
        | some hi', some lo' =>
          [{ addr, data := rhs, cond := .lit (.decimal none 1), hi := hi', lo := lo' }]
        | _, _ => []
      else []
    | .ifElse cond thenB elseB =>
      -- Recurse into both branches, propagating condition for then-branch
      let thenWrites := (collectByteLaneWrites arrName thenB).map
        fun w => { w with cond := if w.cond == .lit (.decimal none 1) then cond else w.cond }
      let elseWrites := collectByteLaneWrites arrName elseB
      thenWrites ++ elseWrites
    | .caseStmt _ arms default_ =>
      let armWrites := arms.flatMap fun (_, body) => collectByteLaneWrites arrName body
      let defWrites := match default_ with | some body => collectByteLaneWrites arrName body | none => []
      armWrites ++ defWrites
    | _ => []

/-- Build a read-modify-write expression for byte-lane writes.
    Combines multiple byte-strobe writes into: for each lane,
    if (cond) use new_byte else use old_byte. -/
def buildByteStrobeWrite (arrName : String) (addrExpr : Expr) (lanes : List ByteLaneWrite) : Expr :=
  -- Start with the old value: arr[addr]
  let oldVal := Expr.index (.ref arrName) addrExpr
  -- For each lane, apply a mux: cond ? (old & ~mask) | (new & mask) : old
  lanes.foldl (fun acc lane =>
    let condExpr := lowerExpr lane.cond
    let dataExpr := lowerExpr lane.data
    let width := lane.hi - lane.lo + 1
    let mask : Nat := ((1 <<< width) - 1) <<< lane.lo  -- e.g., 0xFF for [7:0], 0xFF00 for [15:8]
    let notMask : Nat := 0xFFFFFFFF ^^^ mask
    let maskConst := Expr.const (Int.ofNat mask) 32
    let notMaskConst := Expr.const (Int.ofNat notMask) 32
    -- Shift data to the correct bit position before masking
    -- dataExpr is already sliced (e.g., mem_wdata[15:8] → 8-bit value at bit 0)
    -- Need to shift it to lane.lo position before ANDing with mask
    let shiftedData := if lane.lo == 0 then dataExpr
      else Expr.op .shl [dataExpr, Expr.const (Int.ofNat lane.lo) 32]
    -- new_val = (old & ~mask) | (shifted_data & mask)
    let newVal := Expr.op .or [
      Expr.op .and [acc, notMaskConst],
      Expr.op .and [shiftedData, maskConst]
    ]
    Expr.op .mux [condExpr, newVal, acc]
  ) oldVal

/-- Collect all blocking-assigned signal names recursively -/
partial def collectBlockNamesTop (stmts : List SVStmt) : List String :=
  stmts.flatMap fun s => match s with
    | .blockAssign lhs _ =>
      match exprToName lhs with
      | some n => [n]
      | none =>
        -- Concat-LHS: extract all target variable names
        match lhs with
        | .concat elems => elems.filterMap fun e => match e with
          | .ident n => some n
          | .index (.ident n) _ => some n
          | .slice (.ident n) _ _ => some n
          | .partSelectPlus (.ident n) _ _ => some n
          | _ => none
        | _ => []
    | .ifElse _ t e => collectBlockNamesTop t ++ collectBlockNamesTop e
    | .caseStmt _ arms d =>
      (arms.flatMap fun (_, b) => collectBlockNamesTop b) ++
      (match d with | some b => collectBlockNamesTop b | none => [])
    | .forLoop _ _ _ body => collectBlockNamesTop body
    | _ => []

-- ============================================================================
-- Sequential SSA emitter for always @* blocks (MemorySSA approach)
-- ============================================================================

/-- Environment mapping variable names to their latest SSA wire name.
    Used by emitSequentialSSA to track the "current value" of each variable
    as statements are processed top-to-bottom. -/
abbrev SeqSSAEnv := List (String × String)

private def seqEnvLookup (env : SeqSSAEnv) (name : String) : String :=
  match env.find? (·.1 == name) with
  | some (_, latest) => latest
  | none => name

private def seqEnvUpdate (env : SeqSSAEnv) (name latest : String) : SeqSSAEnv :=
  if env.any (·.1 == name) then
    env.map fun (k, v) => if k == name then (k, latest) else (k, v)
  else
    env ++ [(name, latest)]

/-- Replace all Expr.ref names using the current SSA environment.
    Looks up each ref in env and substitutes with the latest SSA name. -/
private partial def substExprEnv (env : SeqSSAEnv) : Expr → Expr
  | .ref name => .ref (seqEnvLookup env name)
  | .op o args => .op o (args.map (substExprEnv env))
  | .concat args => .concat (args.map (substExprEnv env))
  | .resize width value => .resize width (substExprEnv env value)
  | .slice e hi lo => .slice (substExprEnv env e) hi lo
  | .index arr idx => .index (substExprEnv env arr) (substExprEnv env idx)
  | other => other

/-- Emit IR assigns for an always @* block by processing statements sequentially.
    Each variable write creates a new SSA wire; reads use the latest SSA name.
    This correctly handles "read-then-overwrite" patterns like:
      next_rdx = rdx;            // read initial
      for (...) use(next_rdx);   // reads initial value
      next_rdx = next_rdt << 1;  // overwrite with loop result
    which cannot be expressed as a single MUX without cyclic dependency.
    Returns (assigns, new_wires, final_env, step_counter). -/
partial def emitSequentialSSA (stmts : List SVStmt)
    (env : SeqSSAEnv) (stepCounter : Nat)
    : List Stmt × List Port × SeqSSAEnv × Nat :=
  stmts.foldl (fun (result, wires, curEnv, step) s =>
    match s with
    | .blockAssign lhs rhs =>
      if isDontCare rhs then (result, wires, curEnv, step)
      else
        match exprToName lhs with
        | some name =>
          let rhsExpr := substExprEnv curEnv (lowerExpr rhs)
          let wireName := s!"{name}_seq{step}"
          ( result ++ [.assign wireName rhsExpr]
          , wires ++ [{ name := wireName, ty := .bitVector 64 }]
          , seqEnvUpdate curEnv name wireName
          , step + 1 )
        | none =>
          -- Concat-LHS: decompose and create SSA wires for each target
          let assigns := decomposeMultiConcatLhs lhs rhs
          assigns.foldl (fun (r, w, e, st) (name, value) =>
            let substValue := substExprEnv e value
            let wireName := s!"{name}_seq{st}"
            ( r ++ [.assign wireName substValue]
            , w ++ [{ name := wireName, ty := .bitVector 64 }]
            , seqEnvUpdate e name wireName
            , st + 1 )
          ) (result, wires, curEnv, step)
    | .ifElse cond thenB elseB =>
      let condExpr := substExprEnv curEnv (lowerExpr cond)
      let (thenStmts, thenWires, thenEnv, thenStep) := emitSequentialSSA thenB curEnv step
      let (elseStmts, elseWires, elseEnv, elseStep) := emitSequentialSSA elseB curEnv thenStep
      -- Merge: MUX for each variable changed in either branch
      let allChanged := ((thenEnv ++ elseEnv).filter fun (k, v) =>
        seqEnvLookup curEnv k != v).map (·.1) |>.eraseDups
      let (muxStmts, muxWires, mergedEnv, muxStep) := allChanged.foldl
        (fun (r, w, e, st) name =>
          let preIfVal := seqEnvLookup curEnv name
          let thenLookup := seqEnvLookup thenEnv name
          let elseLookup := seqEnvLookup elseEnv name
          let thenChanged := thenLookup != preIfVal
          let elseChanged := elseLookup != preIfVal
          if thenChanged && elseChanged then
            -- Both branches modified: MUX between branch results
            let muxName := s!"{name}_seq{st}"
            ( r ++ [.assign muxName (.op .mux [condExpr, .ref thenLookup, .ref elseLookup])]
            , w ++ [{ name := muxName, ty := .bitVector 64 }]
            , seqEnvUpdate e name muxName
            , st + 1 )
          else if thenChanged then
            -- Only then-branch modified: MUX with pre-if value
            -- If preIfVal is the raw variable name (no _seq wire), it means the variable
            -- was never assigned before this if-else. Use the then-branch result directly
            -- guarded by condition, to avoid self-referencing the final output.
            let hasSeqWire := (preIfVal.splitOn "_seq").length > 1
            if hasSeqWire then
              let muxName := s!"{name}_seq{st}"
              ( r ++ [.assign muxName (.op .mux [condExpr, .ref thenLookup, .ref preIfVal])]
              , w ++ [{ name := muxName, ty := .bitVector 64 }]
              , seqEnvUpdate e name muxName
              , st + 1 )
            else
              -- No prior seq wire: just use the then-branch value (condition always true
              -- for constant-folded parameters, or the variable is don't-care otherwise)
              (r, w, seqEnvUpdate e name thenLookup, st)
          else
            -- Only else-branch modified
            let hasSeqWire := (preIfVal.splitOn "_seq").length > 1
            if hasSeqWire then
              let muxName := s!"{name}_seq{st}"
              ( r ++ [.assign muxName (.op .mux [condExpr, .ref preIfVal, .ref elseLookup])]
              , w ++ [{ name := muxName, ty := .bitVector 64 }]
              , seqEnvUpdate e name muxName
              , st + 1 )
            else
              (r, w, seqEnvUpdate e name elseLookup, st)
        ) ([], [], curEnv, elseStep)
      ( result ++ thenStmts ++ elseStmts ++ muxStmts
      , wires ++ thenWires ++ elseWires ++ muxWires
      , mergedEnv, muxStep )
    | .caseStmt sel arms default_ =>
      let selExpr := substExprEnv curEnv (lowerExpr sel)
      -- Process default first for base values
      let (defStmts, defWires, defEnv, defStep) := match default_ with
        | some d => emitSequentialSSA d curEnv step
        | none => ([], [], curEnv, step)
      -- Process arms
      let (armStmts, armWires, armEnvs, armStep) := arms.foldl
        (fun (r, w, envs, st) (labels, body) =>
          let (aStmts, aWires, aEnv, aStep) := emitSequentialSSA body curEnv st
          (r ++ aStmts, w ++ aWires, envs ++ [(labels, aEnv)], aStep)
        ) (defStmts, defWires, [], defStep)
      -- Merge with priority MUX
      let allChangedNames := (armEnvs.flatMap fun (_, aEnv) =>
        aEnv.filter (fun (k, v) => seqEnvLookup curEnv k != v) |>.map (·.1)
      ).eraseDups
      let (muxStmts, muxWires, mergedEnv, muxStep) := allChangedNames.foldl
        (fun (r, w, e, st) name =>
          let preVal := seqEnvLookup curEnv name
          let defLookup := seqEnvLookup defEnv name
          let hasSeqWire := (preVal.splitOn "_seq").length > 1
          -- If variable had no _seq wire before (e.g., initialized with 'bx / don't-care),
          -- and only some arms assign it, use the arm values directly without a default
          -- that would self-reference the final output.
          if !hasSeqWire && defLookup == preVal then
            -- No prior seq wire and default didn't change it: build MUX without default ref
            -- If only one arm changed it, just use that arm's value directly
            let armsThatChanged := armEnvs.filter fun (_, aEnv) =>
              seqEnvLookup aEnv name != preVal
            match armsThatChanged with
            | [(_, aEnv)] =>
              -- Single arm: just use its value
              let armVal := seqEnvLookup aEnv name
              (r, w, seqEnvUpdate e name armVal, st)
            | _ =>
              -- Multiple arms: build MUX chain, use const 0 as base (don't-care variable)
              let muxExpr := armEnvs.foldr (fun (labels, aEnv) acc =>
                let armLookup := seqEnvLookup aEnv name
                if armLookup == preVal then acc  -- arm didn't change: skip
                else
                  let cond := mkCaseCond sel labels
                  .op .mux [cond, .ref armLookup, acc]
              ) (.const 0 64)  -- don't-care base
              let muxName := s!"{name}_seq{st}"
              ( r ++ [.assign muxName muxExpr]
              , w ++ [{ name := muxName, ty := .bitVector 64 }]
              , seqEnvUpdate e name muxName
              , st + 1 )
          else
            -- Normal case: variable has a prior value
            let defVal := Expr.ref defLookup
            let muxExpr := armEnvs.foldr (fun (labels, aEnv) acc =>
              let armVal := Expr.ref (seqEnvLookup aEnv name)
              let cond := mkCaseCond sel labels
              .op .mux [cond, armVal, acc]
            ) defVal
            let muxName := s!"{name}_seq{st}"
            ( r ++ [.assign muxName muxExpr]
            , w ++ [{ name := muxName, ty := .bitVector 64 }]
            , seqEnvUpdate e name muxName
            , st + 1 )
        ) ([], [], curEnv, armStep)
      (result ++ armStmts ++ muxStmts, wires ++ armWires ++ muxWires, mergedEnv, muxStep)
    | .forLoop _ _ _ body =>
      let (innerStmts, innerWires, innerEnv, innerStep) := emitSequentialSSA body curEnv step
      (result ++ innerStmts, wires ++ innerWires, innerEnv, innerStep)
    | _ => (result, wires, curEnv, step)
  ) ([], [], env, stepCounter)

-- ============================================================================
-- Topological sort of IR statements
-- ============================================================================

def topoSortBody (body : List Stmt) : List Stmt := Id.run do
  let mut assigns : List (String × Expr) := []
  let mut registers : List Stmt := []
  let mut memories : List Stmt := []
  let mut others : List Stmt := []
  for s in body do
    match s with
    | .assign name rhs => assigns := assigns ++ [(name, rhs)]
    | .register _ _ _ _ _ => registers := registers ++ [s]
    | .memory _ _ _ _ _ _ _ _ _ _ => memories := memories ++ [s]
    | _ => others := others ++ [s]
  let assignNames := assigns.map (·.1)
  let mut sorted : List Stmt := []
  let mut emitted : List String := []
  let mut remaining := assigns
  -- Kahn's algorithm
  -- SSA prologues (name_ssa0_0 = original) should not depend on the
  -- epilogue assignment of 'original' — they read the initial value.
  -- Detect SSA prologues: "foo_ssaD_0" where the LAST segment after _ssa is "D_0"
  -- (not "D_10", "D_20", etc.)
  let isSsaPrologueName (name : String) : Bool :=
    let parts := name.splitOn "_ssa"
    if parts.length < 2 then false
    else
      let lastSeg := parts[parts.length - 1]!  -- e.g., "1_0" or "1_10"
      let segParts := lastSeg.splitOn "_"
      segParts.length >= 2 && segParts[segParts.length - 1]! == "0"
  let ssaPrologueBase (name : String) : Option String :=
    let parts := name.splitOn "_ssa"
    if parts.length < 2 then none
    else
      let lastSeg := parts[parts.length - 1]!
      let segParts := lastSeg.splitOn "_"
      if segParts.length >= 2 && segParts[segParts.length - 1]! == "0" then
        some (String.intercalate "_ssa" (parts.take (parts.length - 1)))
      else none
  let ssaPrologueOriginals := assigns.filterMap fun (name, _rhs) =>
    if isSsaPrologueName name then ssaPrologueBase name else none
  let mut changed := true
  while changed do
    changed := false
    let mut nextRemaining : List (String × Expr) := []
    for (name, rhs) in remaining do
      let deps := collectRefs rhs
      -- For SSA prologues, their reference to the original variable is NOT a dependency
      -- (they read the initial value, not the epilogue-updated value)
      let isSsaPrologue := isSsaPrologueName name
      let prologueBase := if isSsaPrologue then ssaPrologueBase name else none
      let depsReady := deps.all fun dep =>
        dep == name ||  -- Self-reference is not a dependency (resolved by stmtsToMuxExprBlocking)
        !(assignNames.any (· == dep)) || emitted.any (· == dep) ||
        (isSsaPrologue && prologueBase.any (· == dep))
      -- (trace removed)
      if depsReady then
        sorted := sorted ++ [.assign name rhs]
        emitted := emitted ++ [name]
        changed := true
      else
        nextRemaining := nextRemaining ++ [(name, rhs)]
    remaining := nextRemaining
  if !remaining.isEmpty then
    dbg_trace s!"[TOPO WARNING] {remaining.length} assigns have cyclic deps (of {assigns.length} total). Names: {remaining.map (·.1) |>.take 20}"
  for (name, rhs) in remaining do
    sorted := sorted ++ [.assign name rhs]
  return memories ++ sorted ++ registers ++ others

-- ============================================================================
-- Generate block evaluation
-- ============================================================================

/-- Try to evaluate an SVExpr to a constant Nat using parameter values.
    Returns `none` if the expression is too complex to evaluate statically. -/
partial def evalConstExpr (paramVals : List (String × Nat)) : SVExpr → Option Nat
  | .lit (.decimal width v) | .lit (.hex width v) | .lit (.binary width v) =>
      match width with
      | some concreteWidth => truncateNatToWidth concreteWidth v
      | none => some v
  | .ident name => paramVals.find? (·.1 == name) |>.map (·.2)
  | .binary .add a b => return (← evalConstExpr paramVals a) + (← evalConstExpr paramVals b)
  | .binary .sub a b => return (← evalConstExpr paramVals a) - (← evalConstExpr paramVals b)
  | .binary .mul a b => return (← evalConstExpr paramVals a) * (← evalConstExpr paramVals b)
  | .binary .pow a b => return (← evalConstExpr paramVals a) ^ (← evalConstExpr paramVals b)
  | .binary .eq a b => return if (← evalConstExpr paramVals a) == (← evalConstExpr paramVals b) then 1 else 0
  | .binary .neq a b => return if (← evalConstExpr paramVals a) != (← evalConstExpr paramVals b) then 1 else 0
  | .binary .lt a b => return if (← evalConstExpr paramVals a) < (← evalConstExpr paramVals b) then 1 else 0
  | .binary .le a b => return if (← evalConstExpr paramVals a) <= (← evalConstExpr paramVals b) then 1 else 0
  | .binary .gt a b => return if (← evalConstExpr paramVals a) > (← evalConstExpr paramVals b) then 1 else 0
  | .binary .ge a b => return if (← evalConstExpr paramVals a) >= (← evalConstExpr paramVals b) then 1 else 0
  | .binary .logOr a b => do
    let va ← evalConstExpr paramVals a
    let vb ← evalConstExpr paramVals b
    some (if va != 0 || vb != 0 then 1 else 0)
  | .binary .logAnd a b => do
    let va ← evalConstExpr paramVals a
    let vb ← evalConstExpr paramVals b
    some (if va != 0 && vb != 0 then 1 else 0)
  | .binary .bitOr a b => do
    let va ← evalConstExpr paramVals a
    let vb ← evalConstExpr paramVals b
    some (va ||| vb)
  | .unary .unsigned a => evalConstExpr paramVals a
  | .unary .logNot a => do
    let va ← evalConstExpr paramVals a
    some (if va == 0 then 1 else 0)
  | .sizedCast width value => do
      let concreteWidth ← width.eval? fun name =>
        paramVals.find? (·.1 == name) |>.map (·.2)
      truncateNatToWidth concreteWidth (← evalConstExpr paramVals value)
  | _ => none

/-- Extract parameter default values as (name, value) pairs -/
def extractParamDefaults (svMod : SVModule) : List (String × Nat) :=
  let declarations := svMod.params ++ svMod.items.filterMap fun item =>
    match item with | .paramDecl p => some p | _ => none
  declarations.foldl (fun values parameter =>
    match evalConstExpr values parameter.value with
    | some value => values ++ [(parameter.name, value)]
    | none => values) []

/-- Substitute parameter references with constant values in SV expressions -/
def substituteDimFromSV (params : List (String × SVExpr)) (dimension : DimExpr) : DimExpr :=
  dimension.substitute fun name => do
    let (_, value) ← params.find? (fun entry => entry.1 == name)
    Tools.SVParser.Parser.exprToDimExpr? value

partial def substParamExpr (params : List (String × SVExpr)) : SVExpr → SVExpr
  | .ident name => match params.find? fun (n, _) => n == name with
    | some (_, v) => v | none => .ident name
  | .unary op e => .unary op (substParamExpr params e)
  | .binary op a b => .binary op (substParamExpr params a) (substParamExpr params b)
  | .ternary c t e => .ternary (substParamExpr params c) (substParamExpr params t) (substParamExpr params e)
  | .index a i => .index (substParamExpr params a) (substParamExpr params i)
  | .slice e hi lo => .slice (substParamExpr params e)
      (substituteDimFromSV params hi) (substituteDimFromSV params lo)
  | .partSelectPlus e base w => .partSelectPlus (substParamExpr params e) (substParamExpr params base) (substParamExpr params w)
  | .concat es => .concat (es.map (substParamExpr params))
  | .repeat_ count value =>
    .repeat_ (substParamExpr params count) (substParamExpr params value)
  | .sizedCast width value =>
    .sizedCast (substituteDimFromSV params width) (substParamExpr params value)
  | e => e

partial def substParamStmt (params : List (String × SVExpr)) : SVStmt → SVStmt
  | .blockAssign lhs rhs => .blockAssign (substParamExpr params lhs) (substParamExpr params rhs)
  | .nonblockAssign lhs rhs => .nonblockAssign (substParamExpr params lhs) (substParamExpr params rhs)
  | .ifElse cond thenB elseB =>
    .ifElse (substParamExpr params cond)
      (thenB.map (substParamStmt params)) (elseB.map (substParamStmt params))
  | .caseStmt sel arms dflt =>
    .caseStmt (substParamExpr params sel)
      (arms.map fun (labels, body) => (labels.map (substParamExpr params), body.map (substParamStmt params)))
      (dflt.map fun d => d.map (substParamStmt params))
  | .forLoop init cond step body =>
    .forLoop (substParamStmt params init) (substParamExpr params cond) (substParamStmt params step)
      (body.map (substParamStmt params))
  | .assertStmt cond => .assertStmt (substParamExpr params cond)

/-- Collect all variable names read in expressions. -/
private partial def collectReadNamesExpr : SVExpr → List String
  | .ident n => [n]
  | .unary _ e => collectReadNamesExpr e
  | .binary _ a b => collectReadNamesExpr a ++ collectReadNamesExpr b
  | .ternary c t e => collectReadNamesExpr c ++ collectReadNamesExpr t ++ collectReadNamesExpr e
  | .index a i => collectReadNamesExpr a ++ collectReadNamesExpr i
  | .slice e hi lo => collectReadNamesExpr e ++ hi.parameters ++ lo.parameters
  | .partSelectPlus e base _ => collectReadNamesExpr e ++ collectReadNamesExpr base
  | .concat es => es.flatMap collectReadNamesExpr
  | .sizedCast width value => width.parameters ++ collectReadNamesExpr value
  | _ => []

private partial def collectReadNamesStmt : List SVStmt → List String
  | stmts => stmts.flatMap fun s => match s with
    | .blockAssign lhs rhs | .nonblockAssign lhs rhs =>
      collectReadNamesExpr lhs ++ collectReadNamesExpr rhs
    | .ifElse c t e => collectReadNamesExpr c ++ collectReadNamesStmt t ++ collectReadNamesStmt e
    | .caseStmt selector arms default_ =>
      collectReadNamesExpr selector ++ arms.flatMap (fun arm =>
        arm.1.flatMap collectReadNamesExpr ++ collectReadNamesStmt arm.2) ++
        (default_.map collectReadNamesStmt).getD []
    | .forLoop init condition step body =>
      collectReadNamesStmt [init, step] ++ collectReadNamesExpr condition ++ collectReadNamesStmt body
    | .assertStmt condition => collectReadNamesExpr condition

/-- True when an expression contains an explicit `$signed` conversion.  The
    unsigned Sparkle resize node cannot preserve the extension semantics of
    such an operand. -/
private partial def containsSignedConversion : SVExpr → Bool
  | .unary .signed _ => true
  | .unary _ argument => containsSignedConversion argument
  | .binary _ lhs rhs =>
      containsSignedConversion lhs || containsSignedConversion rhs
  | .ternary condition then_ else_ =>
      containsSignedConversion condition || containsSignedConversion then_ ||
        containsSignedConversion else_
  | .index array index =>
      containsSignedConversion array || containsSignedConversion index
  | .slice expression _ _ => containsSignedConversion expression
  | .partSelectPlus expression base width =>
      containsSignedConversion expression || containsSignedConversion base ||
        containsSignedConversion width
  | .concat expressions => expressions.any containsSignedConversion
  | .repeat_ count value =>
      containsSignedConversion count || containsSignedConversion value
  | .sizedCast _ value => containsSignedConversion value
  | .lit _ | .ident _ => false

/-- Conservative SystemVerilog signedness for expressions whose declarations
    are otherwise unsigned.  This is used only to reject signed operands at an
    unsigned IR resize boundary; it is not a replacement for a signed IR type.
    Unsized decimal literals are signed, unary arithmetic inherits its operand,
    binary arithmetic is signed only when both operands are signed, shifts
    inherit the left operand, and selects/concatenations are unsigned. -/
private partial def expressionIsSigned (signedIdentifiers : List String) : SVExpr → Bool
  | .lit (.decimal none _) => true
  | .lit _ => false
  | .ident name => signedIdentifiers.contains name
  | .unary .signed _ => true
  | .unary .unsigned _ | .unary .logNot _ | .unary .reductAnd _
  | .unary .reductOr _ => false
  | .unary .bitNot argument | .unary .neg argument =>
      expressionIsSigned signedIdentifiers argument
  | .binary .shl lhs _ | .binary .shr lhs _ | .binary .asr lhs _ =>
      expressionIsSigned signedIdentifiers lhs
  | .binary .add lhs rhs | .binary .sub lhs rhs | .binary .mul lhs rhs
  | .binary .pow lhs rhs | .binary .bitAnd lhs rhs | .binary .bitOr lhs rhs
  | .binary .bitXor lhs rhs =>
      expressionIsSigned signedIdentifiers lhs && expressionIsSigned signedIdentifiers rhs
  | .binary _ _ _ => false
  | .ternary _ then_ else_ =>
      expressionIsSigned signedIdentifiers then_ &&
        expressionIsSigned signedIdentifiers else_
  | .sizedCast _ value => expressionIsSigned signedIdentifiers value
  | .index _ _ | .slice _ _ _ | .partSelectPlus _ _ _ | .concat _ | .repeat_ _ _ => false

/-- Operations whose result bits depend on signedness that the unsigned
    Sparkle IR cannot currently retain.  This scan is intentionally recursive:
    a `$unsigned(...)` wrapper changes the wrapper's result signedness but does
    not change how a signed comparison inside it is evaluated. -/
private partial def containsUnsupportedSignedOperation
    (signedIdentifiers : List String) : SVExpr → Bool
  | .binary op lhs rhs =>
      let bothSigned :=
        expressionIsSigned signedIdentifiers lhs &&
          expressionIsSigned signedIdentifiers rhs
      let signedRelational :=
        (op == .lt || op == .le || op == .gt || op == .ge) && bothSigned
      -- Even equality and nominally unsigned-result arithmetic/bitwise nodes
      -- can first sign-extend two signed operands to a common width.  That
      -- extension is lost if an enclosing `$unsigned(size'(...))` is lowered
      -- directly to the unsigned IR.
      let signedWidthSensitive :=
        (op == .add || op == .sub || op == .mul || op == .pow ||
          op == .bitAnd || op == .bitOr || op == .bitXor ||
          op == .eq || op == .neq) && bothSigned
      let unsupportedShift := op == .asr
      signedRelational || signedWidthSensitive || unsupportedShift ||
        containsUnsupportedSignedOperation signedIdentifiers lhs ||
        containsUnsupportedSignedOperation signedIdentifiers rhs
  | .unary _ argument =>
      containsUnsupportedSignedOperation signedIdentifiers argument
  | .ternary condition then_ else_ =>
      (expressionIsSigned signedIdentifiers then_ &&
        expressionIsSigned signedIdentifiers else_) ||
        containsUnsupportedSignedOperation signedIdentifiers condition ||
        containsUnsupportedSignedOperation signedIdentifiers then_ ||
        containsUnsupportedSignedOperation signedIdentifiers else_
  | .index array index =>
      containsUnsupportedSignedOperation signedIdentifiers array ||
        containsUnsupportedSignedOperation signedIdentifiers index
  | .slice expression _ _ =>
      containsUnsupportedSignedOperation signedIdentifiers expression
  | .partSelectPlus expression base width =>
      containsUnsupportedSignedOperation signedIdentifiers expression ||
        containsUnsupportedSignedOperation signedIdentifiers base ||
        containsUnsupportedSignedOperation signedIdentifiers width
  | .concat expressions =>
      expressions.any (containsUnsupportedSignedOperation signedIdentifiers)
  | .repeat_ count value =>
      containsUnsupportedSignedOperation signedIdentifiers count ||
        containsUnsupportedSignedOperation signedIdentifiers value
  | .sizedCast _ value =>
      containsUnsupportedSignedOperation signedIdentifiers value
  | .lit _ | .ident _ => false

/-- Reject only `$signed` conversions that occur inside the operand of a sized
    cast.  A sized cast inherits its operand's signedness, which the unsigned
    Sparkle IR cannot retain.  The one safe materialization boundary is an
    immediate `$unsigned(size'(value))`: the inner cast first establishes its
    width and `$unsigned` then explicitly discards its signedness. -/
private partial def hasSignedSizedCastExpr (signedIdentifiers : List String) : SVExpr → Bool
  | .unary .unsigned (.sizedCast _ value) =>
      let exactlyMaterializedSignedOperand := match value with
        | .lit _ | .unary .neg (.lit _) => true
        | _ => false
      containsSignedConversion value ||
        containsUnsupportedSignedOperation signedIdentifiers value ||
        (expressionIsSigned signedIdentifiers value &&
          !exactlyMaterializedSignedOperand) ||
        hasSignedSizedCastExpr signedIdentifiers value
  | .sizedCast _ value =>
      containsSignedConversion value ||
        containsUnsupportedSignedOperation signedIdentifiers value ||
        expressionIsSigned signedIdentifiers value ||
        hasSignedSizedCastExpr signedIdentifiers value
  | .unary _ argument => hasSignedSizedCastExpr signedIdentifiers argument
  | .binary _ lhs rhs =>
      hasSignedSizedCastExpr signedIdentifiers lhs ||
        hasSignedSizedCastExpr signedIdentifiers rhs
  | .ternary condition then_ else_ =>
      hasSignedSizedCastExpr signedIdentifiers condition ||
        hasSignedSizedCastExpr signedIdentifiers then_ ||
        hasSignedSizedCastExpr signedIdentifiers else_
  | .index array index =>
      hasSignedSizedCastExpr signedIdentifiers array ||
        hasSignedSizedCastExpr signedIdentifiers index
  | .slice expression _ _ => hasSignedSizedCastExpr signedIdentifiers expression
  | .partSelectPlus expression base width =>
      hasSignedSizedCastExpr signedIdentifiers expression ||
        hasSignedSizedCastExpr signedIdentifiers base ||
        hasSignedSizedCastExpr signedIdentifiers width
  | .concat expressions =>
      expressions.any (hasSignedSizedCastExpr signedIdentifiers)
  | .repeat_ count value =>
      -- A replication multiplier is an elaboration-time Nat, not a packed
      -- data-path value.  When its complete expression is exactly evaluable,
      -- signed result metadata is irrelevant after conversion to that Nat.
      (if (svExprToNat count).isSome then false
       else hasSignedSizedCastExpr signedIdentifiers count) ||
        hasSignedSizedCastExpr signedIdentifiers value
  | .lit _ | .ident _ => false

private partial def hasSignedSizedCastStmt (signedIdentifiers : List String) : SVStmt → Bool
  | .blockAssign lhs rhs | .nonblockAssign lhs rhs =>
      hasSignedSizedCastExpr signedIdentifiers lhs ||
        hasSignedSizedCastExpr signedIdentifiers rhs
  | .ifElse condition then_ else_ =>
      hasSignedSizedCastExpr signedIdentifiers condition ||
        then_.any (hasSignedSizedCastStmt signedIdentifiers) ||
        else_.any (hasSignedSizedCastStmt signedIdentifiers)
  | .caseStmt selector arms default_ =>
      hasSignedSizedCastExpr signedIdentifiers selector ||
        arms.any (fun arm =>
          arm.1.any (hasSignedSizedCastExpr signedIdentifiers) ||
            arm.2.any (hasSignedSizedCastStmt signedIdentifiers)) ||
        default_.any (fun statements =>
          statements.any (hasSignedSizedCastStmt signedIdentifiers))
  | .forLoop init condition step body =>
      hasSignedSizedCastStmt signedIdentifiers init ||
        hasSignedSizedCastExpr signedIdentifiers condition ||
        hasSignedSizedCastStmt signedIdentifiers step ||
        body.any (hasSignedSizedCastStmt signedIdentifiers)
  | .assertStmt condition => hasSignedSizedCastExpr signedIdentifiers condition

private partial def hasSignedSizedCastItem (signedIdentifiers : List String) : SVModuleItem → Bool
  | .wireDecl _ _ init _ => init.any (hasSignedSizedCastExpr signedIdentifiers)
  | .paramDecl parameter => hasSignedSizedCastExpr signedIdentifiers parameter.value
  | .contAssign lhs rhs =>
      hasSignedSizedCastExpr signedIdentifiers lhs ||
        hasSignedSizedCastExpr signedIdentifiers rhs
  | .alwaysBlock _ statements =>
      statements.any (hasSignedSizedCastStmt signedIdentifiers)
  | .generateBlock condition body elseBody =>
      hasSignedSizedCastExpr signedIdentifiers condition ||
        body.any (hasSignedSizedCastItem signedIdentifiers) ||
        elseBody.any (hasSignedSizedCastItem signedIdentifiers)
  | .instantiation _ _ connections overrides =>
      connections.any (fun connection =>
        hasSignedSizedCastExpr signedIdentifiers connection.2) ||
        overrides.any (fun override =>
          hasSignedSizedCastExpr signedIdentifiers override.2)
  | .taskDecl _ statements =>
      statements.any (hasSignedSizedCastStmt signedIdentifiers)
  | .validationGuard condition => hasSignedSizedCastExpr signedIdentifiers condition
  | .regDecl .. | .integerDecl _ | .readmemh _ _ => false

private partial def collectItemParameters : List SVModuleItem → List SVParam
  | items => items.flatMap fun item => match item with
    | .paramDecl parameter => [parameter]
    | .generateBlock _ body elseBody =>
        collectItemParameters body ++ collectItemParameters elseBody
    | _ => []

private def inferredSignedParameterNames (module_ : SVModule) : List String :=
  (module_.params ++ collectItemParameters module_.items).foldl
    (fun signedNames parameter =>
      let inferredSigned := parameter.isSigned ||
        (parameter.width.isNone && expressionIsSigned signedNames parameter.value)
      if inferredSigned && !signedNames.contains parameter.name then
        signedNames ++ [parameter.name]
      else signedNames) []

private def hasSignedSizedCast (module_ : SVModule) : Bool :=
  let signedIdentifiers := inferredSignedParameterNames module_
  module_.params.any (fun parameter =>
      hasSignedSizedCastExpr signedIdentifiers parameter.value) ||
    module_.items.any (hasSignedSizedCastItem signedIdentifiers)

/-- Native symbolic lowering has no signed packed type.  Preserve the parser
    fact in the AST, then fail closed instead of silently changing extension,
    comparison, or shift semantics. -/
private partial def hasSignedDeclarationItem : SVModuleItem → Bool
  | .wireDecl _ _ _ isSigned | .regDecl _ _ _ isSigned => isSigned
  | .integerDecl _ => true
  | .paramDecl parameter => parameter.isSigned
  | .generateBlock _ body elseBody =>
      body.any hasSignedDeclarationItem || elseBody.any hasSignedDeclarationItem
  | _ => false

private def hasSignedDeclaration (module_ : SVModule) : Bool :=
  module_.ports.any (·.isSigned) || module_.params.any (·.isSigned) ||
    module_.items.any hasSignedDeclarationItem

/-- Detect any sized cast in a module.  Signed declaration types are not yet
    represented in Sparkle IR, so even a concrete cast can change from sign
    extension to zero extension if lowering discards that declaration fact. -/
private partial def containsSizedCastExpr : SVExpr → Bool
  | .sizedCast _ _ => true
  | .unary _ argument => containsSizedCastExpr argument
  | .binary _ lhs rhs => containsSizedCastExpr lhs || containsSizedCastExpr rhs
  | .ternary condition then_ else_ =>
      containsSizedCastExpr condition || containsSizedCastExpr then_ ||
        containsSizedCastExpr else_
  | .index array index => containsSizedCastExpr array || containsSizedCastExpr index
  | .slice expression _ _ => containsSizedCastExpr expression
  | .partSelectPlus expression base width =>
      containsSizedCastExpr expression || containsSizedCastExpr base ||
        containsSizedCastExpr width
  | .concat expressions => expressions.any containsSizedCastExpr
  | .repeat_ count value => containsSizedCastExpr count || containsSizedCastExpr value
  | .lit _ | .ident _ => false

private partial def containsSizedCastStmt : SVStmt → Bool
  | .blockAssign lhs rhs | .nonblockAssign lhs rhs =>
      containsSizedCastExpr lhs || containsSizedCastExpr rhs
  | .ifElse condition then_ else_ =>
      containsSizedCastExpr condition || then_.any containsSizedCastStmt ||
        else_.any containsSizedCastStmt
  | .caseStmt selector arms default_ =>
      containsSizedCastExpr selector ||
        arms.any (fun arm => arm.1.any containsSizedCastExpr ||
          arm.2.any containsSizedCastStmt) ||
        default_.any (fun statements => statements.any containsSizedCastStmt)
  | .forLoop init condition step body =>
      containsSizedCastStmt init || containsSizedCastExpr condition ||
        containsSizedCastStmt step || body.any containsSizedCastStmt
  | .assertStmt condition => containsSizedCastExpr condition

private partial def containsSizedCastItem : SVModuleItem → Bool
  | .wireDecl _ _ init _ => init.any containsSizedCastExpr
  | .paramDecl parameter => containsSizedCastExpr parameter.value
  | .contAssign lhs rhs => containsSizedCastExpr lhs || containsSizedCastExpr rhs
  | .alwaysBlock _ statements => statements.any containsSizedCastStmt
  | .generateBlock condition body elseBody =>
      containsSizedCastExpr condition || body.any containsSizedCastItem ||
        elseBody.any containsSizedCastItem
  | .instantiation _ _ connections overrides =>
      connections.any (fun connection => containsSizedCastExpr connection.2) ||
        overrides.any (fun override => containsSizedCastExpr override.2)
  | .taskDecl _ statements => statements.any containsSizedCastStmt
  | .validationGuard condition => containsSizedCastExpr condition
  | .regDecl .. | .integerDecl _ | .readmemh _ _ => false

private def hasSizedCast (module_ : SVModule) : Bool :=
  module_.params.any (fun parameter => containsSizedCastExpr parameter.value) ||
    module_.items.any containsSizedCastItem

/-- SystemVerilog sized casts are context-determined: their target width can
    flow into arithmetic, bitwise, shift, unary, and conditional operands
    before those operands are evaluated.  `Expr.resize`, by contrast, resizes
    an already evaluated unsigned IR value.  The two operations agree for
    self-determined operands such as identifiers, concatenations, selects,
    comparisons, nested casts, and an explicit `$unsigned` boundary, but not
    in general for the nodes listed here. -/
private def sizedCastOperandNeedsContext : SVExpr → Bool
  | .unary .neg (.lit _) => false
  | .unary .neg _ | .unary .bitNot _ => true
  | .binary .add _ _ | .binary .sub _ _ | .binary .mul _ _
  | .binary .pow _ _ | .binary .bitAnd _ _ | .binary .bitOr _ _
  | .binary .bitXor _ _ | .binary .shl _ _ | .binary .shr _ _
  | .binary .asr _ _ => true
  | .ternary _ _ _ => true
  | _ => false

/-- Find a context-dependent operand at any sized-cast boundary.  Traversal
    below a self-determined boundary is still required so nested sized casts
    receive their own independent check. -/
private partial def hasUnsupportedSizedCastContextExpr : SVExpr → Bool
  | .sizedCast _ value =>
      sizedCastOperandNeedsContext value ||
        hasUnsupportedSizedCastContextExpr value
  | .unary _ argument => hasUnsupportedSizedCastContextExpr argument
  | .binary _ lhs rhs =>
      hasUnsupportedSizedCastContextExpr lhs ||
        hasUnsupportedSizedCastContextExpr rhs
  | .ternary condition then_ else_ =>
      hasUnsupportedSizedCastContextExpr condition ||
        hasUnsupportedSizedCastContextExpr then_ ||
        hasUnsupportedSizedCastContextExpr else_
  | .index array index =>
      hasUnsupportedSizedCastContextExpr array ||
        hasUnsupportedSizedCastContextExpr index
  | .slice expression _ _ => hasUnsupportedSizedCastContextExpr expression
  | .partSelectPlus expression base width =>
      hasUnsupportedSizedCastContextExpr expression ||
        hasUnsupportedSizedCastContextExpr base ||
        hasUnsupportedSizedCastContextExpr width
  | .concat expressions => expressions.any hasUnsupportedSizedCastContextExpr
  | .repeat_ count value =>
      hasUnsupportedSizedCastContextExpr count ||
        hasUnsupportedSizedCastContextExpr value
  | .lit _ | .ident _ => false

private partial def hasUnsupportedSizedCastContextStmt : SVStmt → Bool
  | .blockAssign lhs rhs | .nonblockAssign lhs rhs =>
      hasUnsupportedSizedCastContextExpr lhs ||
        hasUnsupportedSizedCastContextExpr rhs
  | .ifElse condition then_ else_ =>
      hasUnsupportedSizedCastContextExpr condition ||
        then_.any hasUnsupportedSizedCastContextStmt ||
        else_.any hasUnsupportedSizedCastContextStmt
  | .caseStmt selector arms default_ =>
      hasUnsupportedSizedCastContextExpr selector ||
        arms.any (fun arm =>
          arm.1.any hasUnsupportedSizedCastContextExpr ||
            arm.2.any hasUnsupportedSizedCastContextStmt) ||
        default_.any (fun statements =>
          statements.any hasUnsupportedSizedCastContextStmt)
  | .forLoop init condition step body =>
      hasUnsupportedSizedCastContextStmt init ||
        hasUnsupportedSizedCastContextExpr condition ||
        hasUnsupportedSizedCastContextStmt step ||
        body.any hasUnsupportedSizedCastContextStmt
  | .assertStmt condition => hasUnsupportedSizedCastContextExpr condition

private partial def hasUnsupportedSizedCastContextItem : SVModuleItem → Bool
  | .wireDecl _ _ init _ =>
      init.any hasUnsupportedSizedCastContextExpr
  | .paramDecl parameter =>
      hasUnsupportedSizedCastContextExpr parameter.value
  | .contAssign lhs rhs =>
      hasUnsupportedSizedCastContextExpr lhs ||
        hasUnsupportedSizedCastContextExpr rhs
  | .alwaysBlock _ statements | .taskDecl _ statements =>
      statements.any hasUnsupportedSizedCastContextStmt
  | .generateBlock condition body elseBody =>
      hasUnsupportedSizedCastContextExpr condition ||
        body.any hasUnsupportedSizedCastContextItem ||
        elseBody.any hasUnsupportedSizedCastContextItem
  | .instantiation _ _ connections overrides =>
      connections.any (fun connection =>
        hasUnsupportedSizedCastContextExpr connection.2) ||
        overrides.any (fun override =>
          hasUnsupportedSizedCastContextExpr override.2)
  | .validationGuard condition =>
      hasUnsupportedSizedCastContextExpr condition
  | .regDecl .. | .integerDecl _ | .readmemh _ _ => false

private def hasUnsupportedSizedCastContext (module_ : SVModule) : Bool :=
  module_.params.any (fun parameter =>
      hasUnsupportedSizedCastContextExpr parameter.value) ||
    module_.items.any hasUnsupportedSizedCastContextItem

/-- `>>>` depends on the left operand's signedness.  Lowering currently has no
    signed packed type, and a sized-cast result can reach a later shift through
    a named wire.  A module containing both features must therefore fail closed
    until signedness is represented in the IR. -/
private partial def hasAsrConsumingSizedCastExpr : SVExpr → Bool
  | .binary .asr _ _ => true
  | .binary _ lhs rhs =>
      hasAsrConsumingSizedCastExpr lhs || hasAsrConsumingSizedCastExpr rhs
  | .unary _ argument => hasAsrConsumingSizedCastExpr argument
  | .ternary condition then_ else_ =>
      hasAsrConsumingSizedCastExpr condition ||
        hasAsrConsumingSizedCastExpr then_ ||
        hasAsrConsumingSizedCastExpr else_
  | .index array index =>
      hasAsrConsumingSizedCastExpr array || hasAsrConsumingSizedCastExpr index
  | .slice expression _ _ => hasAsrConsumingSizedCastExpr expression
  | .partSelectPlus expression base width =>
      hasAsrConsumingSizedCastExpr expression ||
        hasAsrConsumingSizedCastExpr base || hasAsrConsumingSizedCastExpr width
  | .concat expressions => expressions.any hasAsrConsumingSizedCastExpr
  | .repeat_ count value =>
      hasAsrConsumingSizedCastExpr count || hasAsrConsumingSizedCastExpr value
  | .sizedCast _ value => hasAsrConsumingSizedCastExpr value
  | .lit _ | .ident _ => false

private partial def hasAsrConsumingSizedCastStmt : SVStmt → Bool
  | .blockAssign lhs rhs | .nonblockAssign lhs rhs =>
      hasAsrConsumingSizedCastExpr lhs || hasAsrConsumingSizedCastExpr rhs
  | .ifElse condition then_ else_ =>
      hasAsrConsumingSizedCastExpr condition ||
        then_.any hasAsrConsumingSizedCastStmt ||
        else_.any hasAsrConsumingSizedCastStmt
  | .caseStmt selector arms default_ =>
      hasAsrConsumingSizedCastExpr selector ||
        arms.any (fun arm => arm.1.any hasAsrConsumingSizedCastExpr ||
          arm.2.any hasAsrConsumingSizedCastStmt) ||
        default_.any (fun statements => statements.any hasAsrConsumingSizedCastStmt)
  | .forLoop init condition step body =>
      hasAsrConsumingSizedCastStmt init ||
        hasAsrConsumingSizedCastExpr condition ||
        hasAsrConsumingSizedCastStmt step || body.any hasAsrConsumingSizedCastStmt
  | .assertStmt condition => hasAsrConsumingSizedCastExpr condition

private partial def hasAsrConsumingSizedCastItem : SVModuleItem → Bool
  | .wireDecl _ _ init _ => init.any hasAsrConsumingSizedCastExpr
  | .paramDecl parameter => hasAsrConsumingSizedCastExpr parameter.value
  | .contAssign lhs rhs =>
      hasAsrConsumingSizedCastExpr lhs || hasAsrConsumingSizedCastExpr rhs
  | .alwaysBlock _ statements | .taskDecl _ statements =>
      statements.any hasAsrConsumingSizedCastStmt
  | .generateBlock condition body elseBody =>
      hasAsrConsumingSizedCastExpr condition ||
        body.any hasAsrConsumingSizedCastItem ||
        elseBody.any hasAsrConsumingSizedCastItem
  | .instantiation _ _ connections overrides =>
      connections.any (fun connection =>
        hasAsrConsumingSizedCastExpr connection.2) ||
        overrides.any (fun override => hasAsrConsumingSizedCastExpr override.2)
  | .validationGuard condition => hasAsrConsumingSizedCastExpr condition
  | .regDecl .. | .integerDecl _ | .readmemh _ _ => false

private def hasAsrConsumingSizedCast (module_ : SVModule) : Bool :=
  hasSizedCast module_ &&
    (module_.params.any (fun parameter =>
        hasAsrConsumingSizedCastExpr parameter.value) ||
      module_.items.any hasAsrConsumingSizedCastItem)

/-- Does a procedural loop require elaboration using a retained module
    parameter?  Such a loop cannot be unrolled at the default while still
    advertising a working native override. -/
private partial def hasParameterizedFor (parameterNames : List String) : List SVStmt → Bool
  | statements => statements.any fun statement => match statement with
    | .forLoop init condition step body =>
      let references := collectReadNamesExpr condition ++ collectReadNamesStmt [init, step]
      references.any parameterNames.contains || hasParameterizedFor parameterNames body
    | .ifElse _ then_ else_ =>
      hasParameterizedFor parameterNames then_ || hasParameterizedFor parameterNames else_
    | .caseStmt _ arms default_ =>
      arms.any (fun arm => hasParameterizedFor parameterNames arm.2) ||
        default_.any (hasParameterizedFor parameterNames)
    | _ => false

private partial def hasParameterizedGenerate (parameterNames : List String)
    (items : List SVModuleItem) : Bool :=
  items.any fun item => match item with
  | .generateBlock condition body elseBody =>
    (collectReadNamesExpr condition).any parameterNames.contains ||
      hasParameterizedGenerate parameterNames body ||
      hasParameterizedGenerate parameterNames elseBody
  | .alwaysBlock _ statements => hasParameterizedFor parameterNames statements
  | _ => false

/-- Detect expression forms whose current lowering needs a concrete repeat,
    part-select, or sign-extension width.  Native parameters may still be used
    freely in declaration dimensions and ordinary arithmetic. -/
private partial def hasUnsupportedParameterizedExpr (parameterNames : List String) : SVExpr → Bool
  | .unary .signed argument =>
    -- `lowerExpr` has no signed-cast node.  In particular, dropping
    -- `$signed(x)` changes arithmetic-right-shift and comparison semantics
    -- when `x` has a retained width, even though the expression text itself
    -- does not mention the width parameter.
    !parameterNames.isEmpty || hasUnsupportedParameterizedExpr parameterNames argument
  | .unary _ argument => hasUnsupportedParameterizedExpr parameterNames argument
  | .binary _ lhs rhs =>
    hasUnsupportedParameterizedExpr parameterNames lhs ||
      hasUnsupportedParameterizedExpr parameterNames rhs
  | .ternary condition then_ else_ =>
    hasUnsupportedParameterizedExpr parameterNames condition ||
      hasUnsupportedParameterizedExpr parameterNames then_ ||
      hasUnsupportedParameterizedExpr parameterNames else_
  | .index array index =>
    hasUnsupportedParameterizedExpr parameterNames array ||
      hasUnsupportedParameterizedExpr parameterNames index
  | .slice expression hi lo =>
    (hi.parameters ++ lo.parameters).any parameterNames.contains ||
      hasUnsupportedParameterizedExpr parameterNames expression
  | .partSelectPlus expression base width =>
    (collectReadNamesExpr width).any parameterNames.contains ||
      hasUnsupportedParameterizedExpr parameterNames expression ||
      hasUnsupportedParameterizedExpr parameterNames base
  | .concat expressions => expressions.any (hasUnsupportedParameterizedExpr parameterNames)
  | .repeat_ count value =>
    (collectReadNamesExpr count).any parameterNames.contains ||
      hasUnsupportedParameterizedExpr parameterNames value
  | .sizedCast _width value =>
    -- Sized casts lower to the width-preserving IR `resize` node.  Continue
    -- checking the operand for independently unsupported constructs such as
    -- retained signed casts.
    hasUnsupportedParameterizedExpr parameterNames value
  | _ => false

private def hasUnsupportedNativeLhs (parameterNames : List String) (lhs : SVExpr) : Bool :=
  !parameterNames.isEmpty && !(match lhs with | .ident _ => true | _ => false)

private partial def hasBlockingAssignment : List SVStmt → Bool
  | statements => statements.any fun statement => match statement with
    | .blockAssign _ _ => true
    | .ifElse _ then_ else_ =>
      hasBlockingAssignment then_ || hasBlockingAssignment else_
    | .caseStmt _ arms default_ =>
      arms.any (fun arm => hasBlockingAssignment arm.2) ||
        default_.any hasBlockingAssignment
    | .forLoop init _ step body =>
      hasBlockingAssignment [init, step] || hasBlockingAssignment body
    | _ => false

private partial def hasUnsupportedParameterizedStmt (parameterNames : List String) : SVStmt → Bool
  | .blockAssign lhs rhs | .nonblockAssign lhs rhs =>
    hasUnsupportedNativeLhs parameterNames lhs ||
      hasUnsupportedParameterizedExpr parameterNames lhs ||
      hasUnsupportedParameterizedExpr parameterNames rhs
  | .ifElse condition then_ else_ =>
    hasUnsupportedParameterizedExpr parameterNames condition ||
      then_.any (hasUnsupportedParameterizedStmt parameterNames) ||
      else_.any (hasUnsupportedParameterizedStmt parameterNames)
  | .caseStmt selector arms default_ =>
    hasUnsupportedParameterizedExpr parameterNames selector ||
      arms.any (fun arm => arm.1.any (hasUnsupportedParameterizedExpr parameterNames) ||
        arm.2.any (hasUnsupportedParameterizedStmt parameterNames)) ||
      default_.any (fun statements => statements.any (hasUnsupportedParameterizedStmt parameterNames))
  | .forLoop init condition step body =>
    hasUnsupportedParameterizedStmt parameterNames init ||
      hasUnsupportedParameterizedExpr parameterNames condition ||
      hasUnsupportedParameterizedStmt parameterNames step ||
      body.any (hasUnsupportedParameterizedStmt parameterNames)
  | .assertStmt condition => hasUnsupportedParameterizedExpr parameterNames condition

private def hasUnsupportedParameterizedConstruct (parameterNames : List String)
    (items : List SVModuleItem) : Bool :=
  items.any fun item => match item with
  | .contAssign lhs rhs =>
    hasUnsupportedNativeLhs parameterNames lhs ||
      hasUnsupportedParameterizedExpr parameterNames lhs ||
      hasUnsupportedParameterizedExpr parameterNames rhs
  | .alwaysBlock .star _ =>
    -- The current SSA lowering gives temporary wires a concrete 64-bit type.
    -- Retaining a module parameter here would silently truncate W>64 values.
    !parameterNames.isEmpty
  | .alwaysBlock (.posedge _) statements =>
    (!parameterNames.isEmpty && hasBlockingAssignment statements) ||
      statements.any (hasUnsupportedParameterizedStmt parameterNames)
  | .alwaysBlock _ statements =>
    statements.any (hasUnsupportedParameterizedStmt parameterNames)
  | .wireDecl _ _ init _ => init.any (hasUnsupportedParameterizedExpr parameterNames)
  | .instantiation _ _ connections overrides =>
    connections.any (fun connection => hasUnsupportedParameterizedExpr parameterNames connection.2) ||
      overrides.any (fun override => hasUnsupportedParameterizedExpr parameterNames override.2)
  | _ => false

/-- Collect all variable names written in blocking assignments (including concat-LHS). -/
private partial def collectWriteNames : List SVStmt → List String
  | stmts => stmts.flatMap fun s => match s with
    | .blockAssign lhs _ => match lhs with
      | .ident name => [name]
      | .index (.ident name) _ => [name]
      | .slice (.ident name) _ _ => [name]
      | .partSelectPlus (.ident name) _ _ => [name]
      | .concat elems => elems.filterMap fun e => match e with
        | .ident n => some n | .index (.ident n) _ => some n
        | .slice (.ident n) _ _ => some n | .partSelectPlus (.ident n) _ _ => some n
        | _ => none
      | _ => []
    | .ifElse _ t e => collectWriteNames t ++ collectWriteNames e
    | .forLoop _ _ _ body => collectWriteNames body
    | _ => []

/-- Rename all occurrences of `oldName` to `newName` in an SVExpr. -/
private partial def renameExpr (oldName newName : String) : SVExpr → SVExpr
  | .ident n => if n == oldName then .ident newName else .ident n
  | .unary op e => .unary op (renameExpr oldName newName e)
  | .binary op a b => .binary op (renameExpr oldName newName a) (renameExpr oldName newName b)
  | .ternary c t e => .ternary (renameExpr oldName newName c) (renameExpr oldName newName t) (renameExpr oldName newName e)
  | .index a i => .index (renameExpr oldName newName a) (renameExpr oldName newName i)
  | .slice e hi lo => .slice (renameExpr oldName newName e) hi lo
  | .partSelectPlus e base w => .partSelectPlus (renameExpr oldName newName e) (renameExpr oldName newName base) (renameExpr oldName newName w)
  | .concat es => .concat (es.map (renameExpr oldName newName))
  | .sizedCast width value => .sizedCast width (renameExpr oldName newName value)
  | e => e

/-- Rename all occurrences of `oldName` to `newName` in an SVStmt. -/
private partial def renameStmt (oldName newName : String) : SVStmt → SVStmt
  | .blockAssign lhs rhs => .blockAssign (renameExpr oldName newName lhs) (renameExpr oldName newName rhs)
  | .nonblockAssign lhs rhs => .nonblockAssign (renameExpr oldName newName lhs) (renameExpr oldName newName rhs)
  | .ifElse c t e => .ifElse (renameExpr oldName newName c) (t.map (renameStmt oldName newName)) (e.map (renameStmt oldName newName))
  | .caseStmt sel arms d =>
    .caseStmt (renameExpr oldName newName sel)
      (arms.map fun (ls, b) => (ls.map (renameExpr oldName newName), b.map (renameStmt oldName newName)))
      (d.map fun ds => ds.map (renameStmt oldName newName))
  | .forLoop i c s b => .forLoop (renameStmt oldName newName i) (renameExpr oldName newName c) (renameStmt oldName newName s) (b.map (renameStmt oldName newName))
  | .assertStmt c => .assertStmt (renameExpr oldName newName c)

/-- Rename in LHS of blockAssign only, recursing into ifElse/forLoop/case. -/
private partial def renameLhsOnly (oldName newName : String) : SVStmt → SVStmt
  | .blockAssign lhs rhs => .blockAssign (renameExpr oldName newName lhs) rhs
  | .ifElse c t e => .ifElse c (t.map (renameLhsOnly oldName newName)) (e.map (renameLhsOnly oldName newName))
  | .forLoop i c s body => .forLoop i c s (body.map (renameLhsOnly oldName newName))
  | .caseStmt sel arms d =>
    .caseStmt sel (arms.map fun (ls, b) => (ls, b.map (renameLhsOnly oldName newName)))
      (d.map fun ds => ds.map (renameLhsOnly oldName newName))
  | other => other

/-- Unroll for loops with constant bounds in SV statements.
    Uses SSA-style renaming: variables written in the loop body get
    iteration-specific names (e.g., next_rd → next_rd_ssa0_0, next_rd_ssa0_1, ...)
    to correctly handle sequential blocking assignment dependencies.
    `depth` distinguishes nested loops (ssa0_, ssa1_, ...). -/
partial def unrollForLoops (paramVals : List (String × Nat)) (depth : Nat := 0) : List SVStmt → List SVStmt :=
  fun stmts => stmts.flatMap fun s => match s with
  | .forLoop (.blockAssign (.ident var) initExpr) condExpr (.blockAssign (.ident stepVar) stepExpr) body =>
    if var != stepVar then [s]
    else
      let initVal := evalConstExpr paramVals initExpr |>.getD 0
      let bound := match condExpr with
        | .binary .lt (.ident v) limitExpr =>
          if v == var then evalConstExpr paramVals limitExpr else none
        | _ => none
      let stepVal := match stepExpr with
        | .binary .add (.ident v) incExpr =>
          if v == var then evalConstExpr paramVals incExpr else none
        | _ => none
      match bound, stepVal with
      | some b, some inc =>
        if inc == 0 || b <= initVal then [s]
        else Id.run do
          let ssaTag := s!"_ssa{depth}_"
          -- SSA-rename ALL variables written in the loop.
          -- Even non-self-referential variables need SSA when updated via non-overlapping
          -- part-selects across iterations (e.g., next_rdt[j+3] = ...). Without SSA,
          -- MUX last-write-wins would discard previous iterations' bit fields.
          let writeNames := collectWriteNames body |>.eraseDups
          let readNames := collectReadNamesStmt body |>.eraseDups
          -- For nested SSA, unify read/write names that share the same base
          -- (e.g., write=foo_ssa0_1, read=foo_ssa0_0 → rename reads to write name)
          let stripSsa (n : String) : String :=
            let parts := n.splitOn "_ssa"
            if parts.length >= 2 then parts[0]! else n
          let mut unifiedBody := body
          for wn in writeNames do
            if stripSsa wn != wn then
              for rn in readNames do
                if rn != wn && stripSsa rn == stripSsa wn then
                  unifiedBody := unifiedBody.map (renameStmt rn wn)
          let selfRefNames := collectWriteNames unifiedBody |>.eraseDups
          let numIters := (b - initVal + inc - 1) / inc

          -- SSA in unrollForLoops is disabled: emitSequentialSSA handles ordering.
          if true then
            -- Simple unroll without SSA (sequential emitter handles variable tracking)
            let mut result : List SVStmt := []
            let mut j := initVal
            while j < b do
              let substituted := unifiedBody.map (substParamStmt [(var, .lit (.decimal (some 32) j))])
              let unrolled := unrollForLoops ((var, j) :: paramVals) (depth + 1) substituted
              result := result ++ unrolled
              j := j + inc
            result
          else
            -- SSA rename only self-referential variables
            let mut result : List SVStmt := []
            -- Prologue: capture initial values
            for name in selfRefNames do
              result := result ++ [.blockAssign (.ident s!"{name}{ssaTag}0") (.ident name)]

            let mut j := initVal
            let mut iterIdx : Nat := 0
            while j < b do
              let substituted := unifiedBody.map (substParamStmt [(var, .lit (.decimal (some 32) j))])
              -- SSA rename FIRST (before recursive unroll)
              let mut renamed := substituted
              for name in selfRefNames do
                renamed := renamed.map (renameStmt name s!"{name}{ssaTag}{iterIdx}")
              for name in selfRefNames do
                -- Rename LHS of ALL blockAssigns (including nested in ifElse/forLoop)
                let readName := s!"{name}{ssaTag}{iterIdx}"
                let writeName := s!"{name}{ssaTag}{iterIdx + 1}"
                renamed := renamed.map (renameLhsOnly readName writeName)
              -- THEN recursively unroll nested loops (they see renamed SSA names)
              let unrolled := unrollForLoops ((var, j) :: paramVals) (depth + 1) renamed
              result := result ++ unrolled
              j := j + inc
              iterIdx := iterIdx + 1

            -- Epilogue: write final SSA value back
            for name in selfRefNames do
              result := result ++ [.blockAssign (.ident name) (.ident s!"{name}{ssaTag}{numIters}")]
            result
      | _, _ => [s]
  | .ifElse cond thenB elseB =>
    [.ifElse cond (unrollForLoops paramVals depth thenB) (unrollForLoops paramVals depth elseB)]
  | .caseStmt sel arms dflt =>
    [.caseStmt sel
      (arms.map fun (labels, body) => (labels, unrollForLoops paramVals depth body))
      (dflt.map (unrollForLoops paramVals depth))]
  | other => [other]

def prepareParameterizedItem (paramVals : List (String × Nat)) : SVModuleItem → SVModuleItem
  | .alwaysBlock sens stmts =>
    let unrolled := unrollForLoops paramVals 0 stmts
    .alwaysBlock sens unrolled
  | item => item

def specializeItem (parameters : List (String × SVExpr))
    (parameterValues : List (String × Nat)) : SVModuleItem → SVModuleItem
  | .alwaysBlock sensitivity statements =>
    .alwaysBlock sensitivity <| unrollForLoops parameterValues 0
      (statements.map (substParamStmt parameters))
  | .contAssign lhs rhs =>
    .contAssign (substParamExpr parameters lhs) (substParamExpr parameters rhs)
  | .wireDecl name width init isSigned =>
    .wireDecl name width (init.map (substParamExpr parameters)) isSigned
  | .instantiation moduleName instanceName connections overrides =>
    .instantiation moduleName instanceName
      (connections.map fun connection => (connection.1, substParamExpr parameters connection.2))
      (overrides.map fun override => (override.1, substParamExpr parameters override.2))
  | item => item

/-- Expand generate blocks by evaluating conditions against parameter defaults.
    Returns the items from the selected branch (recursively for nested generates). -/
partial def expandGenerateBlocks (paramVals : List (String × Nat))
    (items : List SVModuleItem) : Except String (List SVModuleItem) := do
  let mut result : List SVModuleItem := []
  for item in items do
    match item with
    | .generateBlock cond ifItems elseItems =>
      let condVal ← match evalConstExpr paramVals cond with
        | some value => pure value
        | none => throw s!"unsupported generate condition; cannot safely choose a branch: {repr cond}"
      let selectedItems := if condVal != 0 then ifItems else elseItems
      result := result ++ (← expandGenerateBlocks paramVals selectedItems)
    | other => result := result ++ [other]
  pure result

/-- Validate replication multipliers after legacy parameter specialization.
    Replication is a constant elaboration context, so accepting an exactly
    evaluable sized cast here is sound even when its literal would be signed in
    a packed data path.  Conversely, lowering must never guess a multiplier. -/
private partial def validateRepeatCountsExpr : SVExpr → Except String Unit
  | .repeat_ count value => do
      match svExprToNat count with
      | some n =>
          if n == 0 then
            throw s!"replication count evaluates to zero: {repr count}"
      | none =>
          throw s!"replication count is not an exactly evaluable non-negative constant after parameter specialization: {repr count}"
      validateRepeatCountsExpr count
      validateRepeatCountsExpr value
  | .unary _ argument => validateRepeatCountsExpr argument
  | .binary _ lhs rhs => do
      validateRepeatCountsExpr lhs
      validateRepeatCountsExpr rhs
  | .ternary condition then_ else_ => do
      validateRepeatCountsExpr condition
      validateRepeatCountsExpr then_
      validateRepeatCountsExpr else_
  | .index array index => do
      validateRepeatCountsExpr array
      validateRepeatCountsExpr index
  | .slice expression _ _ => validateRepeatCountsExpr expression
  | .partSelectPlus expression base width => do
      validateRepeatCountsExpr expression
      validateRepeatCountsExpr base
      validateRepeatCountsExpr width
  | .concat expressions =>
      for expression in expressions do validateRepeatCountsExpr expression
  | .sizedCast _ value => validateRepeatCountsExpr value
  | .lit _ | .ident _ => pure ()

private partial def validateRepeatCountsStmt : SVStmt → Except String Unit
  | .blockAssign lhs rhs | .nonblockAssign lhs rhs => do
      validateRepeatCountsExpr lhs
      validateRepeatCountsExpr rhs
  | .ifElse condition then_ else_ => do
      validateRepeatCountsExpr condition
      for statement in then_ do validateRepeatCountsStmt statement
      for statement in else_ do validateRepeatCountsStmt statement
  | .caseStmt selector arms default_ => do
      validateRepeatCountsExpr selector
      for (labels, statements) in arms do
        for label in labels do validateRepeatCountsExpr label
        for statement in statements do validateRepeatCountsStmt statement
      for statements in default_.toList do
        for statement in statements do validateRepeatCountsStmt statement
  | .forLoop init condition step body => do
      validateRepeatCountsStmt init
      validateRepeatCountsExpr condition
      validateRepeatCountsStmt step
      for statement in body do validateRepeatCountsStmt statement
  | .assertStmt condition => validateRepeatCountsExpr condition

private partial def validateRepeatCountsItem : SVModuleItem → Except String Unit
  | .wireDecl _ _ init _ =>
      for expression in init.toList do validateRepeatCountsExpr expression
  | .paramDecl parameter => validateRepeatCountsExpr parameter.value
  | .contAssign lhs rhs => do
      validateRepeatCountsExpr lhs
      validateRepeatCountsExpr rhs
  | .alwaysBlock _ statements | .taskDecl _ statements =>
      for statement in statements do validateRepeatCountsStmt statement
  | .generateBlock condition body elseBody => do
      validateRepeatCountsExpr condition
      for item in body do validateRepeatCountsItem item
      for item in elseBody do validateRepeatCountsItem item
  | .instantiation _ _ connections overrides => do
      for (_, expression) in connections do validateRepeatCountsExpr expression
      for (_, expression) in overrides do validateRepeatCountsExpr expression
  | .validationGuard condition => validateRepeatCountsExpr condition
  | .regDecl .. | .integerDecl _ | .readmemh _ _ => pure ()

private def validateRepeatCounts (module_ : SVModule) : Except String Unit := do
  for parameter in module_.params do
    validateRepeatCountsExpr parameter.value
  for item in module_.items do
    validateRepeatCountsItem item

-- ============================================================================
-- Module lowering
-- ============================================================================

/-- Lower a single SVModule.  `retainParameters` selects the native symbolic
    path; the legacy SV simulation path specializes declared parameters at
    their defaults (or explicit overrides) before lowering. -/
def lowerModule (svMod : SVModule) (paramOverrides : List (String × Nat) := [])
    (retainParameters : Bool := false) : Except String Module := do
  if retainParameters && hasSignedDeclaration svMod then
    throw "native symbolic lowering does not support signed port, parameter, wire, register, or integer declarations; specialize the module through the legacy path"
  if hasSignedDeclaration svMod && hasSizedCast svMod then
    throw "a SystemVerilog module combines signed declarations with sized casts, but Sparkle IR resize is unsigned; signed sized-cast semantics are not supported"
  if hasSignedSizedCast svMod then
    throw "a SystemVerilog sized cast has signed operand/result semantics that Sparkle's unsigned IR resize cannot retain; wrap an intentionally materialized cast in $unsigned or use an explicitly unsigned operand"
  if hasUnsupportedSizedCastContext svMod then
    throw "a SystemVerilog sized cast applies context width to an arithmetic, bitwise, shift, unary, or conditional operand; Sparkle IR resize operates on an already evaluated value, so this cast must be rewritten with explicit operand widths before lowering"
  if hasAsrConsumingSizedCast svMod then
    throw "a module combines a sized cast with arithmetic right shift, but Sparkle IR does not retain enough signedness to prove their dataflow semantics; rewrite the shift with an explicitly supported signed or logical operation before lowering"
  -- Expand generate blocks using parameter defaults + overrides
  let paramDefaults := extractParamDefaults svMod
  -- Overrides take priority: replace defaults with overridden values
  let paramVals := paramDefaults.map fun (n, v) =>
    match paramOverrides.find? fun (on, _) => on == n with
    | some (_, ov) => (n, ov)
    | none => (n, v)
  let parameterDecls := svMod.params ++ svMod.items.filterMap fun item =>
    match item with
    | .paramDecl parameter => if parameter.isLocal then none else some parameter
    | _ => none
  let parameterNames := parameterDecls.map (·.name)
  if retainParameters then
    let mut priorNames : List String := []
    for parameter in parameterDecls do
      if (collectReadNamesExpr parameter.value).any priorNames.contains then
        throw s!"module parameter '{parameter.name}' has a default that depends on another parameter; native dependent defaults are not yet representable"
      priorNames := priorNames ++ [parameter.name]
  if retainParameters && hasParameterizedGenerate parameterNames svMod.items then
    throw "parameter-dependent generate/procedural-for elaboration cannot be retained as a native override; specialize the module explicitly"
  if retainParameters && hasUnsupportedParameterizedConstruct parameterNames svMod.items then
    throw "parameter-dependent slice/repeat/part-select/sign-extension, signed cast, complex assignment target, or procedural-combinational lowering is not supported for native overrides; specialize the module explicitly"
  let mut moduleParameters : List Sparkle.IR.AST.Parameter := []
  if retainParameters then
    for parameter in parameterDecls do
      let defaultValue ← match paramDefaults.find? (fun entry => entry.1 == parameter.name) with
        | some (_, value) => pure value
        | none => throw s!"module parameter '{parameter.name}' does not have a supported natural-number default"
      moduleParameters := moduleParameters ++ [{ name := parameter.name, defaultValue }]
  let expandedItems ← expandGenerateBlocks paramVals svMod.items
  -- Preserve parameter references in ordinary expressions.  Only loop/generate
  -- control is evaluated here; replacing data-path references with defaults
  -- would make a later SystemVerilog parameter override semantically inert.
  let expandedItems := if retainParameters then
      expandedItems.map (prepareParameterizedItem paramVals)
    else
      let paramLits : List (String × SVExpr) := paramVals.map fun (name, value) =>
        (name, .lit (.decimal (some 32) value))
      expandedItems.map (specializeItem paramLits paramVals)
  let svParams := if retainParameters then svMod.params else svMod.params.map fun parameter =>
    match paramVals.find? (fun entry => entry.1 == parameter.name) with
    | some (_, value) => { parameter with value := .lit (.decimal (some 32) value) }
    | none => parameter
  let svPorts := if retainParameters then svMod.ports else svMod.ports.map fun port =>
    { port with width := specializeWidth paramVals port.width }
  let expandedItems := if retainParameters then expandedItems else expandedItems.map fun item =>
    match item with
    | .wireDecl name width init isSigned =>
      .wireDecl name (specializeWidth paramVals width) init isSigned
    | .regDecl name width arraySize isSigned =>
      let arraySize := arraySize.map fun size => size.substitute fun parameter =>
        paramVals.find? (fun entry => entry.1 == parameter)
          |>.map fun entry => DimExpr.literal entry.2
      .regDecl name (specializeWidth paramVals width) arraySize isSigned
    | .paramDecl parameter =>
      .paramDecl { parameter with width := specializeWidth paramVals parameter.width }
    | other => other
  let svMod := { svMod with items := expandedItems, params := svParams, ports := svPorts }
  validateRepeatCounts svMod

  -- Build environment
  let mut env := LowerEnv.empty
  for p in svMod.ports do
    env := { env with portWidths := env.portWidths ++ [(p.name, p.width)] }
  for item in svMod.items do
    match item with
    | .wireDecl name width _ _ => env := { env with wireWidths := env.wireWidths ++ [(name, width)] }
    | .regDecl name width _ _ =>
      env := { env with wireWidths := env.wireWidths ++ [(name, width)],
                         regNames := env.regNames ++ [name] }
    | _ => pure ()

  -- Build ports
  let inputs := svMod.ports.filter (·.dir == .input) |>.map fun p =>
    { name := p.name, ty := widthToHWType p.width : Port }
  let outputs := svMod.ports.filter (·.dir == .output) |>.map fun p =>
    { name := p.name, ty := widthToHWType p.width : Port }
  let allPortNames := inputs.map (·.name) ++ outputs.map (·.name)

  -- Collect array register names (memory arrays, not scalar registers)
  let arrayRegNames := svMod.items.filterMap fun item => match item with
    | .regDecl name _ (some _) _ => some name
    | _ => none

  -- Helper: check if a wire name is already declared
  let wireExists := fun (wires : List Port) (name : String) =>
    wires.any (·.name == name) || allPortNames.any (· == name)

  -- Build wires list (from wire and reg declarations)
  let mut wires : List Port := []
  for item in svMod.items do
    match item with
    | .wireDecl name width _ _ => wires := wires ++ [{ name, ty := widthToHWType width }]
    | .regDecl name width arraySize _ =>
      match arraySize with
      | some _ => pure ()  -- Array regs handled by Stmt.memory (not wires)
      | none => wires := wires ++ [{ name, ty := widthToHWType width }]
    | .integerDecl name => wires := wires ++ [{ name, ty := .bitVector 32 }]
    | _ => pure ()

  -- Local parameters are implementation constants.  Module parameters live in
  -- `Module.parameters` and must not be redeclared as hardware wires.
  let mut paramNames : List String := []
  for item in svMod.items do
    match item with
    | .paramDecl param =>
      if param.isLocal then
        let ty := widthToHWType param.width
        if !(paramNames.any (· == param.name)) then
          wires := wires ++ [{ name := param.name, ty }]
          paramNames := paramNames ++ [param.name]
      else
        pure ()
    | _ => pure ()

  -- Build body statements
  let mut body : List Stmt := []
  -- All always @* blocks now use MUX mode (SSA handles loop dependencies)

  -- Emit parameter values as constant assigns (with overrides applied)
  let paramWidth (w : Option (DimExpr × DimExpr)) : DimExpr :=
    match w with | some (hi, lo) => hi - lo + 1 | none => 32
  for item in svMod.items do
    match item with
    | .paramDecl param =>
      if param.isLocal then
        let val := match paramVals.find? fun (n, _) => n == param.name with
          | some (_, v) => .const (Int.ofNat v) (paramWidth param.width)
          | none => lowerExpr param.value
        body := body ++ [.assign param.name val]
      else
        pure ()
    | _ => pure ()

  for item in svMod.items do
    match item with
    | .contAssign lhs rhs =>
      match exprToName lhs with
      | some name => body := body ++ [.assign name (lowerExpr rhs)]
      | none => throw "continuous assign LHS must be an identifier"
    | .alwaysBlock (.posedge clock) stmts =>
      -- Sequential: extract all register names, then build mux expression per register
      -- Detect reset pattern: find first if/else that looks like a reset check
      -- PicoRV32 has flat assigns before the reset check, so we scan for it
      let mut resetName := "rst"
      let mut initMap : List (String × Int) := []
      let resetCheck := stmts.findSome? fun s => match s with
        | .ifElse cond thenB elseB => detectReset cond thenB elseB
        | _ => none
      match resetCheck with
      | some (resetSig, isActiveHigh, initBranch, _dataBranch) =>
        resetName := if isActiveHigh then resetSig else s!"_rst_{resetSig}_inv"
        if !isActiveHigh then
          wires := wires ++ [{ name := resetName, ty := .bit }]
          body := body ++ [.assign resetName (.op .not [.ref resetSig])]
        for statement in initBranch do
          match statement with
          | .nonblockAssign lhs rhs =>
            match exprToName lhs with
            | none => pure ()
            | some name =>
              let initValue? ← match rhs with
                | .unary .neg (.lit (.decimal _ value)) =>
                    pure (some (-(Int.ofNat value)))
                | .sizedCast target (.unary .neg (.lit literal)) =>
                    pure (some (← materializeSizedNegativeLiteral target literal))
                | .unary .unsigned (.sizedCast target (.lit literal)) =>
                    pure (some (← materializeDestinationWidthLiteral target
                      (env.getHWType name).width false literal))
                | .unary .unsigned
                    (.sizedCast target (.unary .neg (.lit literal))) =>
                    pure (some (← materializeDestinationWidthLiteral target
                      (env.getHWType name).width true literal))
                | .unary .unsigned (.sizedCast target _) | .sizedCast target _ =>
                    if retainParameters && target.toNat?.isNone then
                      throw s!"symbolic reset cast for register '{name}' is not a supported literal form and cannot be evaluated at its default parameter value"
                    else
                      pure ((evalConstExpr paramVals rhs).map Int.ofNat)
                | _ => pure ((evalConstExpr paramVals rhs).map Int.ofNat)
              for initValue in initValue?.toList do
                initMap := initMap ++ [(name, initValue)]
          | _ => pure ()
      | none => pure ()

      -- Extract blocking assigns as combinational intermediates (from full always body)
      let blockingNames := (collectBlockNamesTop stmts).eraseDups
      for sigName in blockingNames do
        let expr := stmtsToMuxExprBlocking sigName stmts
        body := body ++ [.assign sigName expr]
        if !(wireExists wires sigName) then
          wires := wires ++ [{ name := sigName, ty := .bitVector 32 }]  -- default 32-bit

      -- Collect all register names (exclude array regs handled by Stmt.memory)
      let regNames := (collectAllRegNames stmts).eraseDups.filter
        fun n => !arrayRegNames.any (· == n)
      for regName in regNames do
        let hwTy := env.getHWType regName
        let initVal := match initMap.find? (·.1 == regName) with
          | some (_, v) => v
          | none => 0
        let dataExpr := stmtsToMuxExpr regName stmts
        body := body ++ [.register regName clock resetName dataExpr initVal]
        if !(wireExists wires regName) then
          wires := wires ++ [{ name := regName, ty := hwTy }]

    | .alwaysBlock .star stmts =>
      -- Sequential SSA: process statements top-to-bottom, creating SSA wires
      -- for each variable write. This correctly handles read-then-overwrite patterns.
      let (seqStmts, seqWires, finalEnv, _) := emitSequentialSSA stmts [] 0
      body := body ++ seqStmts
      wires := wires ++ seqWires
      -- Create final assigns: map original variable names to their latest SSA wire
      let sigNames := collectBlockNamesTop stmts |>.eraseDups
      for sigName in sigNames do
        let latestWire := seqEnvLookup finalEnv sigName
        if latestWire != sigName then
          body := body ++ [.assign sigName (.ref latestWire)]
          if !wireExists wires sigName then
            wires := wires ++ [{ name := sigName, ty := .bitVector 64 }]
    | .wireDecl name _ (some initExpr) _ =>
      -- wire x = expr; → assign
      body := body ++ [.assign name (lowerExpr initExpr)]
    | .regDecl name width (some arraySize) _ =>
      -- Array reg → Stmt.memory for JIT memory access
      -- Do NOT add to wires list — Stmt.memory creates the class member.
      let dataWidth ← match widthToBits width with
        | some value => pure value
        | none => throw s!"memory '{name}' has a symbolic data width; specialize it before concrete memory lowering"
      let arraySize ← match arraySize.toNat? with
        | some value => pure value
        | none => throw s!"memory '{name}' has a symbolic depth; specialize it before concrete memory lowering"
      let addrWidth := Nat.log2 arraySize + (if Nat.isPowerOfTwo arraySize then 0 else 1)
      -- Extract array writes from always blocks
      let mut writeAddr : Expr := .const 0 addrWidth
      let mut writeData : Expr := .const 0 dataWidth
      let mut writeEnable : Expr := .const 0 1
      for prevItem in svMod.items do
        match prevItem with
        | .alwaysBlock (.posedge _) stmts =>
          -- Try full-word writes first: arr[idx] <= data
          let arrayWrites := collectArrayWrites name stmts
          if !arrayWrites.isEmpty then
            for (idx, data, cond) in arrayWrites do
              writeAddr := lowerExpr idx
              writeData := lowerExpr data
              writeEnable := match cond with
                | some c => lowerExpr c
                | none => .const 1 1
          else
            -- Try byte-lane writes: if (wstrb[n]) arr[addr][hi:lo] <= data[hi:lo]
            let byteLanes := collectByteLaneWrites name stmts
            match byteLanes with
            | lane0 :: _ =>
              let addr := lowerExpr lane0.addr
              writeAddr := addr
              writeData := buildByteStrobeWrite name addr byteLanes
              -- Enable if any strobe bit is set
              let enableExpr := byteLanes.foldl (fun acc lane =>
                let c := lowerExpr lane.cond
                if acc == Expr.const 0 1 then c else Expr.op .or [acc, c]
              ) (Expr.const 0 1)
              writeEnable := enableExpr
            | [] => pure ()
        | _ => pure ()
      body := body ++ [.memory name addrWidth dataWidth "clk"
        writeAddr writeData writeEnable
        (.const 0 addrWidth) s!"{name}_rdata" true]
      wires := wires ++ [{ name := s!"{name}_rdata", ty := widthToHWType width }]
    | .instantiation modName instName conns paramOvr =>
      let irConns := conns.map fun (portName, expr) => (portName, lowerExpr expr)
      let mut irOverrides : List (String × DimExpr) := []
      for (name, value) in paramOvr do
        let dimension ← match Tools.SVParser.Parser.exprToDimExpr? value with
          | some dimension => pure dimension
          | none => throw s!"instance '{instName}' parameter override '{name}' is not a supported dimension expression"
        irOverrides := irOverrides ++ [(name, dimension)]
      body := body ++ [.inst modName instName irConns irOverrides]
    | _ => pure ()

  -- Deduplicate wires
  let mut dedupWires : List Port := []
  let mut seenWireNames : List String := []
  let portNames := inputs.map (·.name) ++ outputs.map (·.name)
  for w in wires do
    if !(seenWireNames.any (· == w.name)) && !(portNames.any (· == w.name)) then
      dedupWires := dedupWires ++ [w]
      seenWireNames := seenWireNames ++ [w.name]

  -- Deduplicate registers and handle output reg ports
  let mut dedupBody : List Stmt := []
  let mut seenRegNames : List String := []
  let outputNames := outputs.map (·.name)
  let exprDepthSimple := fun (e : Expr) =>
    let rec go : Expr → Nat
      | .op _ args => 1 + (args.map go).foldl max 0
      | .resize _ value => 1 + go value
      | .slice e _ _ => 1 + go e
      | .index a i => 1 + max (go a) (go i)
      | _ => 0
    go e

  -- For registers assigned in multiple always blocks, keep the one
  -- with deeper mux expression (more logic). This handles the PicoRV32
  -- pattern where the decode block sets a flag and the execution block clears it.
  let mut regDepthMap : List (String × Nat) := []
  for stmt in body do
    match stmt with
    | .register name _ _ input _ =>
      let depth := exprDepthSimple input
      regDepthMap := regDepthMap ++ [(name, depth)]
    | _ => pure ()
  let bestDepth (name : String) : Nat :=
    (regDepthMap.filter (·.1 == name)).foldl (fun acc (_, d) => max acc d) 0

  -- Process in FORWARD order — first occurrence wins.
  -- For PicoRV32, the decode block (always[9]) comes before the execution
  -- block (always[17]). The decode block sets flags; the execution block clears them.
  -- We keep the decode block's version which has the meaningful logic.
  for stmt in body do
    match stmt with
    | .register name clk rst input init =>
      if !(seenRegNames.any (· == name)) then
        -- For output reg: rename the register to _reg_name, add assign output = _reg_name
        if outputNames.any (· == name) then
          let regName := s!"_reg_{name}"
          dedupBody := [.assign name (.ref regName), .register regName clk rst input init] ++ dedupBody
          seenRegNames := seenRegNames ++ [name]
          -- Add the internal register wire
          if !(dedupWires.any (·.name == regName)) then
            let hwTy := env.getHWType name
            dedupWires := dedupWires ++ [{ name := regName, ty := hwTy }]
        else
          dedupBody := [stmt] ++ dedupBody
          seenRegNames := seenRegNames ++ [name]
    | _ => dedupBody := [stmt] ++ dedupBody

  -- Collect assertions from all always blocks
  -- Helper: collect blocking assigns from SV stmts for inlining
  let collectAssignsFromStmts := fun (stmts : List SVStmt) =>
    stmts.filterMap fun s => match s with
      | .blockAssign lhs rhs =>
        match exprToName lhs with
        | some n => some (n, lowerExpr rhs)
        | none => none
      | _ => none
  let mut assertions : List (String × Expr) := []
  let mut assertIdx : Nat := 0
  for item in svMod.items do
    match item with
    | .alwaysBlock _ stmts =>
      let guarded := collectGuardedAsserts stmts
      for (guard, cond) in guarded do
        let inlined := cond  -- assertions reference registers/inputs directly
        let guardedCond := if guard == .const 1 1 then inlined
          else .op .mux [guard, inlined, .const 1 1]
        assertions := assertions ++ [(s!"auto_assert_{assertIdx}", guardedCond)]
        assertIdx := assertIdx + 1
    | _ => pure ()

  let result : Module := {
    name := svMod.name
    parameters := moduleParameters
    inputs := inputs
    outputs := outputs
    wires := dedupWires
    body := topoSortBody dedupBody
    assertions := assertions
    isPrimitive := false
  }
  result.validateDimensions
  pure result

/-- Prefix all wire/register names in an expression -/
partial def prefixExprNames (pfx : String) (nameSet : List String) : Expr → Expr
  | .ref name => if nameSet.any (· == name) then .ref s!"{pfx}_{name}" else .ref name
  | .op o args => .op o (args.map (prefixExprNames pfx nameSet))
  | .concat args => .concat (args.map (prefixExprNames pfx nameSet))
  | .resize width value => .resize width (prefixExprNames pfx nameSet value)
  | .slice e hi lo => .slice (prefixExprNames pfx nameSet e) hi lo
  | .index arr idx => .index (prefixExprNames pfx nameSet arr) (prefixExprNames pfx nameSet idx)
  | e => e

/-- Flatten a design: inline all sub-module instantiations into a single module.
    The optional `svDesign` parameter provides access to the original SV AST
    for re-lowering sub-modules with parameter overrides (e.g., ENABLE_MUL=1). -/
def flattenDesign (design : Design) (svDesign : SVDesign := { modules := [] }) : Design := Id.run do
  let moduleMap := design.modules
  match design.modules.find? fun (m : Module) => m.name == design.topModule with
  | none => return design
  | some top =>
    let mut flatWires := top.wires
    let mut flatBody : List Stmt := []

    for stmt in top.body do
      match stmt with
      | .inst modName instName conns parameterOverrides =>
        -- Find the sub-module
        match moduleMap.find? fun (m : Module) => m.name == modName with
        | none =>
          -- Sub-module not found: emit warning wire and skip
          flatBody := flatBody ++ [.assign s!"_warn_missing_{modName}_{instName}" (.const 0 1)]
        | some subMod =>
          -- Find the SV AST for this instantiation to get parameter overrides
          -- Walk the SV top module items to find the matching instantiation
          let svTopMod? := svDesign.modules.find? fun m => m.name == design.topModule
          let astParamOvr : List (String × Nat) := match svTopMod? with
            | some svTop =>
              let expanded := match expandGenerateBlocks (extractParamDefaults svTop) svTop.items with
                | .ok items => items
                | .error _ => []
              match expanded.findSome? fun item =>
                match item with
                | .instantiation mn svInstName _ pOvr =>
                  if mn == modName && svInstName == instName then
                    some (pOvr.filterMap fun (name, expr) =>
                      match expr with
                      | .lit (.decimal _ v) => some (name, v)
                      | .lit (.hex _ v) => some (name, v)
                      | .lit (.binary _ v) => some (name, v)
                      | _ => none)
                  else none
                | _ => none
              with
              | some ovr => ovr
              | none => []
            | none => []
          let paramOvr : List (String × Nat) := if parameterOverrides.isEmpty then
              astParamOvr
            else
              parameterOverrides.filterMap fun (name, value) => value.toNat?.map (name, ·)

          -- Re-lower the sub-module with parameter overrides applied
          -- This ensures generate-if blocks are expanded with the correct values
          let svSubMod? := svDesign.modules.find? fun m => m.name == modName
          let effectiveSubMod ← match svSubMod? with
            | some svSub =>
              match lowerModule svSub paramOvr with
              | .ok m => pure m
              | .error _ => pure subMod
            | none => pure subMod

          -- Native Sparkle hierarchy stores parameter overrides directly in
          -- `Stmt.inst`.  Substitute them (or the child's declared defaults)
          -- into every child dimension before copying the child into the
          -- parent.  Overrides may themselves refer to parent parameters, so
          -- the resulting flattened dimensions can remain symbolic.
          let effectiveSubMod := effectiveSubMod.substituteDimensions fun name =>
            match parameterOverrides.find? (fun override => override.1 == name) with
            | some (_, value) => some value
            | none => effectiveSubMod.parameters.find? (fun parameter => parameter.name == name)
                |>.map fun parameter => .literal parameter.defaultValue

          -- Collect all internal names in sub-module (including memory names)
          let memNames := effectiveSubMod.body.filterMap fun s => match s with
            | .memory n _ _ _ _ _ _ _ _ _ => some n | _ => none
          let subNames := effectiveSubMod.wires.map (·.name) ++
                          effectiveSubMod.inputs.map (·.name) ++
                          effectiveSubMod.outputs.map (·.name) ++
                          memNames

          -- Add prefixed wires from sub-module
          for w in effectiveSubMod.wires do
            flatWires := flatWires ++ [{ name := s!"{instName}_{w.name}", ty := w.ty }]
          for p in effectiveSubMod.inputs do
            flatWires := flatWires ++ [{ name := s!"{instName}_{p.name}", ty := p.ty }]
          for p in effectiveSubMod.outputs do
            flatWires := flatWires ++ [{ name := s!"{instName}_{p.name}", ty := p.ty }]

          -- Wire port connections:
          -- Input ports: assign instName_portName = parentExpr
          -- Output ports: assign parentWire/expr = instName_portName
          let inputNames := effectiveSubMod.inputs.map (·.name)
          let outputNames := effectiveSubMod.outputs.map (·.name)
          for (portName, expr) in conns do
            if inputNames.any (· == portName) then
              -- Input: parent drives sub-module's port
              flatBody := flatBody ++ [.assign s!"{instName}_{portName}" expr]
            else if outputNames.any (· == portName) then
              -- Output: sub-module drives parent's wire
              match expr with
              | .ref parentWire =>
                flatBody := flatBody ++ [.assign parentWire (.ref s!"{instName}_{portName}")]
              | _ =>
                -- Complex output expression (array index, bit slice, concat, etc.)
                -- Create a temporary wire and assign the sub-module output to it.
                -- The parent can read from this wire.
                let tmpWire := s!"{instName}_{portName}_out"
                flatWires := flatWires ++ [{ name := tmpWire, ty := .bitVector 32 }]
                flatBody := flatBody ++ [.assign tmpWire (.ref s!"{instName}_{portName}")]

          -- Add prefixed body statements from sub-module
          for s in effectiveSubMod.body do
            let prefixed := match s with
              | .assign name rhs =>
                .assign s!"{instName}_{name}" (prefixExprNames instName subNames rhs)
              | .register name clk rst input init =>
                .register s!"{instName}_{name}" s!"{instName}_{clk}" s!"{instName}_{rst}"
                  (prefixExprNames instName subNames input) init
              | .inst subModName subInstName subConns subParameterOverrides =>
                -- Keep nested .inst with prefixed names — will be flattened in next iteration
                .inst subModName s!"{instName}_{subInstName}"
                  (subConns.map fun (pn, e) => (pn, prefixExprNames instName subNames e))
                  subParameterOverrides
              | .memory name aw dw clk wa wd we ra rd combo =>
                .memory s!"{instName}_{name}" aw dw s!"{instName}_{clk}"
                  (prefixExprNames instName subNames wa)
                  (prefixExprNames instName subNames wd)
                  (prefixExprNames instName subNames we)
                  (prefixExprNames instName subNames ra)
                  s!"{instName}_{rd}" combo
            flatBody := flatBody ++ [prefixed]
      | other => flatBody := flatBody ++ [other]

    -- Prefix internal wire names with _gen_ to prevent CppSim local shadowing.
    -- Exclude: input/output port names, register names, memory names.
    let portNames := top.inputs.map (·.name) ++ top.outputs.map (·.name)
    let regNames := flatBody.filterMap fun s => match s with
      | .register n _ _ _ _ => some n | _ => none
    let memNames := flatBody.filterMap fun s => match s with
      | .memory n _ _ _ _ _ _ _ _ _ => some n | _ => none
    let internalWireNames := flatWires.map (·.name) |>.filter fun n =>
      !(portNames.any (· == n)) && !(regNames.any (· == n)) && !(memNames.any (· == n))
    let addGen (n : String) : String :=
      if n.startsWith "_gen_" then n
      else if internalWireNames.any (· == n) then s!"_gen_{n}"
      else n
    let genWires := flatWires.map fun w => { w with name := addGen w.name }
    let genExpr := genExprRefs internalWireNames
    let genBody := flatBody.map fun s => match s with
      | .assign n rhs => .assign (addGen n) (genExpr rhs)
      | .register n clk rst input init => .register n clk rst (genExpr input) init
      | .inst mn in_ conns parameterOverrides =>
        .inst mn in_ (conns.map fun (p, e) => (p, genExpr e)) parameterOverrides
      | .memory n aw dw clk wa wd we ra rd combo =>
        .memory n aw dw clk (genExpr wa) (genExpr wd) (genExpr we) (genExpr ra) rd combo

    let flatModule : Module := {
      name := top.name
      parameters := top.parameters
      inputs := top.inputs
      outputs := top.outputs
      wires := genWires
      body := topoSortBody genBody
      assertions := top.assertions
      isPrimitive := false
    }
    return { topModule := design.topModule, modules := [flatModule] }
  where
    genExprRefs (wireNames : List String) : Expr → Expr
      | .ref n => if wireNames.any (· == n) && !n.startsWith "_gen_"
                  then .ref s!"_gen_{n}" else .ref n
      | .op o args => .op o (args.map (genExprRefs wireNames))
      | .concat args => .concat (args.map (genExprRefs wireNames))
      | .resize width value => .resize width (genExprRefs wireNames value)
      | .slice e hi lo => .slice (genExprRefs wireNames e) hi lo
      | .index a i => .index (genExprRefs wireNames a) (genExprRefs wireNames i)
      | e => e

/-- Lower a full SV design to Sparkle IR -/
def lowerDesign (svDesign : SVDesign) (retainParameters : Bool := false) : Except String Design := do
  let mut modules : List Module := []
  for m in svDesign.modules do
    let lowered ← lowerModule m [] retainParameters
    modules := modules ++ [lowered]
  let instantiatedNames := svDesign.modules.flatMap fun module_ =>
    module_.items.filterMap fun item => match item with
      | .instantiation moduleName _ _ _ => some moduleName
      | _ => none
  let roots := svDesign.modules.filter fun module_ => !instantiatedNames.contains module_.name
  let topName ← match roots with
    | [root] => pure root.name
    | [] => throw "cannot determine a top module: every parsed module is instantiated"
    | _ => throw s!"cannot determine a unique top module; candidates: {roots.map (·.name)}"
  pure { topModule := topName, modules }

-- ============================================================================
-- Public API: parse + lower
-- ============================================================================

/-- Memory initialization info from $readmemh -/
structure ReadMemHInfo where
  filename : String
  memName  : String
  deriving Repr

/-- Extract $readmemh info from a parsed SV design -/
def extractReadMemH (svDesign : SVDesign) : List ReadMemHInfo :=
  svDesign.modules.flatMap fun m =>
    m.items.filterMap fun item =>
      match item with
      | .readmemh filename memName => some { filename, memName }
      | _ => none

def parseAndLower (input : String) : Except String Design := do
  let svDesign ← Tools.SVParser.Parser.parse input
  lowerDesign svDesign

/-- Parse and retain supported SystemVerilog parameters as native symbolic IR
    dimensions. Unsupported parameter-dependent elaboration fails closed. -/
def parseAndLowerNative (input : String) : Except String Design := do
  let svDesign ← Tools.SVParser.Parser.parse input
  lowerDesign svDesign true

def parseAndLowerFlat (input : String) : Except String Design := do
  let svDesign ← Tools.SVParser.Parser.parse input
  let design ← lowerDesign svDesign
  -- Iteratively flatten until no .inst remains (handles nested sub-modules)
  let hasInst (d : Design) : Bool :=
    match d.modules.head? with
    | some m => m.body.any fun s => match s with | .inst .. => true | _ => false
    | none => false
  let mut result := flattenDesign design svDesign
  -- For nested hierarchies: re-flatten with all original modules available
  for _ in [:5] do
    if hasInst result then
      -- Re-add all original sub-modules so the flattener can find them
      let enriched := { result with modules := result.modules ++ design.modules }
      result := flattenDesign enriched svDesign
    else break
  pure result

def parseAndLowerWithMemInit (input : String) : Except String (Design × List ReadMemHInfo) := do
  let svDesign ← Tools.SVParser.Parser.parse input
  let design ← lowerDesign svDesign
  let memInits := extractReadMemH svDesign
  pure (design, memInits)

end Tools.SVParser.Lower
