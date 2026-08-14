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
  | .clog2     => .not  -- unreachable: parameter expression or rejected

def lowerBinOp : SVBinOp → Operator
  | .add    => .add
  | .sub    => .sub
  | .mul    => .mul
  | .div    => .mul  -- unreachable: parameter expression or rejected below
  | .mod    => .mul  -- unreachable: parameter expression or rejected below
  | .pow    => .mul  -- unreachable: parameter expression or rejected below
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
  | .unknown width => width.getD 1

def literalToConst : SVLiteral → Expr
  | .decimal (some w) v => .const (Int.ofNat v) w
  | literal@(.decimal none v) => .const (Int.ofNat v) (naturalLiteralWidth literal)
  | .hex (some w) v     => .const (Int.ofNat v) w
  | literal@(.hex none v) => .const (Int.ofNat v) (naturalLiteralWidth literal)
  | .binary (some w) v  => .const (Int.ofNat v) w
  | literal@(.binary none v) => .const (Int.ofNat v) (naturalLiteralWidth literal)
  | .unknown _ => .const 0 0

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
  | .binary .div a b => do let va ← svExprToNat a; let vb ← svExprToNat b; some (va / vb)
  | .binary .mod a b => do let va ← svExprToNat a; let vb ← svExprToNat b; some (va % vb)
  | .binary .pow a b => do let va ← svExprToNat a; let vb ← svExprToNat b; some (va ^ vb)
  | .binary .shl a b => do let va ← svExprToNat a; let vb ← svExprToNat b; some (va <<< vb)
  | .binary .shr a b => do let va ← svExprToNat a; let vb ← svExprToNat b; some (va >>> vb)
  | .binary .bitAnd a b => do let va ← svExprToNat a; let vb ← svExprToNat b; some (va &&& vb)
  | .binary .bitOr a b => do let va ← svExprToNat a; let vb ← svExprToNat b; some (va ||| vb)
  | .binary .bitXor a b => do let va ← svExprToNat a; let vb ← svExprToNat b; some (va ^^^ vb)
  | .unary .clog2 a => DimExpr.clog2Nat <$> svExprToNat a
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

/- Promoted localparam aliases are substituted as an explicit totalized
   packed modulo.  This exact internal shape is safe even though ordinary raw
   arithmetic must remain rejected by the context-free Nat parser. -/
private partial def retainedPackedAliasOperandSafe : SVExpr → Bool
  | .lit _ | .ident _ => true
  | .unary .unsigned value => retainedPackedAliasOperandSafe value
  | .ternary (.binary .ge lhs rhs) (.binary .sub thenLhs thenRhs)
      (.lit (.decimal _ 0)) =>
      lhs == thenLhs && rhs == thenRhs &&
        retainedPackedAliasOperandSafe lhs && retainedPackedAliasOperandSafe rhs
  | .ternary (.binary .eq rhs (.lit (.decimal _ 0))) lhsElse
      (.binary .mod lhs thenRhs) =>
      rhs == thenRhs && lhsElse == lhs &&
        retainedPackedAliasOperandSafe lhs && retainedPackedAliasOperandSafe rhs
  | .binary operator lhs rhs =>
      let supported := match operator with
        | .add | .sub | .mul | .pow | .shl | .bitAnd | .bitOr | .bitXor => true
        | _ => false
      supported && retainedPackedAliasOperandSafe lhs &&
        retainedPackedAliasOperandSafe rhs
  | .concat values => values.all retainedPackedAliasOperandSafe
  | .sizedCast _ value => retainedPackedAliasOperandSafe value
  | _ => false

private def isRetainedPackedAliasValue : SVExpr → Bool
  | .ternary (.binary .eq rhs (.lit (.decimal _ 0))) lhsElse
      (.binary .mod lhs thenRhs) =>
      rhs == thenRhs && lhsElse == lhs &&
        retainedPackedAliasOperandSafe lhs && retainedPackedAliasOperandSafe rhs
  | _ => false

private def retainedNatValueExpr? (expression : SVExpr) : Option DimExpr :=
  match Tools.SVParser.Parser.exprToNatExpr? expression with
  | some value => some value
  | none =>
      if isRetainedPackedAliasValue expression then
        Tools.SVParser.Parser.exprToSizedNatExpr? expression
      else none

private def retainedParameterExpr? (parameterNames : List String)
    (expression : SVExpr) : Option DimExpr := do
  let value ← retainedNatValueExpr? expression
  let references := value.parameters
  if !references.isEmpty && references.all parameterNames.contains then some value else none

private partial def provablyPositiveParameterNat : SVExpr → Bool
  | .lit literal => (svExprToNat (.lit literal)).any (· > 0)
  | .unary .unsigned value => provablyPositiveParameterNat value
  | .binary .add lhs rhs | .binary .bitOr lhs rhs =>
      provablyPositiveParameterNat lhs || provablyPositiveParameterNat rhs
  | .binary .mul lhs rhs =>
      provablyPositiveParameterNat lhs && provablyPositiveParameterNat rhs
  | .binary .shl lhs _ => provablyPositiveParameterNat lhs
  | .binary .pow base exponent =>
      provablyPositiveParameterNat base || svExprToNat exponent == some 0
  | _ => false

private def provablyNonUnderflowingParameterSub (lhs rhs : SVExpr) : Bool :=
  if lhs == rhs then true
  else match svExprToNat rhs with
  | some 0 => true
  | some 1 => provablyPositiveParameterNat lhs
  | some rhsValue => match svExprToNat lhs with
    | some lhsValue => lhsValue >= rhsValue
    | none => false
  | none => false

/-- Operations for which evaluating a mathematical Nat and truncating once at
    the enclosing packed cast is equivalent to SystemVerilog's packed modular
    evaluation.  Non-congruence-preserving operations (right shift, division,
    modulo, comparisons and muxes) are deliberately excluded. -/
private partial def parameterOuterModuloSafe : SVExpr → Bool
  | expression =>
      if (Tools.SVParser.Parser.recoverNatWorkExpr? expression).isSome then true
      else match expression with
      | .lit _ | .ident _ => true
      | .unary .unsigned value => parameterOuterModuloSafe value
      | .binary .sub lhs rhs =>
          provablyNonUnderflowingParameterSub lhs rhs &&
            parameterOuterModuloSafe lhs && parameterOuterModuloSafe rhs
      | .binary operator lhs rhs =>
          let supported := match operator with
            | .add | .mul | .pow | .shl | .bitAnd | .bitOr | .bitXor => true
            | _ => false
          supported && parameterOuterModuloSafe lhs && parameterOuterModuloSafe rhs
      | .concat values => values.all parameterOuterModuloSafe
      | .sizedCast _ value => parameterOuterModuloSafe value
      | _ => false

private def retainedSizedParameterExpr? (parameterNames : List String)
    (expression : SVExpr) : Option DimExpr := do
  guard (parameterOuterModuloSafe expression)
  let value ← Tools.SVParser.Parser.exprToSizedNatExpr? expression
  let references := value.parameters
  if !references.isEmpty && references.all parameterNames.contains then some value else none

/-- Replace accidental data-wire references to retained natural-number module
    parameters.  This final safety pass covers expressions built by procedural
    helpers that predate native parameter lowering. -/
partial def materializeParameterRefs (parameterNames : List String) : Expr → Expr
  | .ref name =>
      if parameterNames.contains name then .paramConst (.param name) 32 else .ref name
  | .const value width => .const value width
  | .paramConst value width => .paramConst value width
  | .op operator args => .op operator (args.map (materializeParameterRefs parameterNames))
  | .concat args => .concat (args.map (materializeParameterRefs parameterNames))
  | .resize width value => .resize width (materializeParameterRefs parameterNames value)
  | .slice value hi lo => .slice (materializeParameterRefs parameterNames value) hi lo
  | .index array index =>
      .index (materializeParameterRefs parameterNames array)
        (materializeParameterRefs parameterNames index)

partial def lowerExpr (e : SVExpr) (parameterNames : List String := []) : Expr :=
  let lowerExpr := fun value => lowerExpr value parameterNames
  if let some value := retainedParameterExpr? parameterNames e then
    .paramConst value 32
  else match e with
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
  | .unary .clog2 arg =>
    match svExprToNat arg with
    | some value => .const (Int.ofNat (DimExpr.clog2Nat value)) 32
    | none => .const 0 0
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
  | .binary .pow lhs rhs
  | .binary .div lhs rhs
  | .binary .mod lhs rhs =>
    match svExprToNat e with
    | some value => .const (Int.ofNat value) 32
    | none => .const 0 0
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
    match retainedSizedParameterExpr? parameterNames value with
    | some parameterValue => .paramConst parameterValue width
    | none => match value with
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

private partial def expressionReferencesAny
    (names : List String) : SVExpr → Bool
  | .ident name => names.contains name
  | .unary _ value => expressionReferencesAny names value
  | .binary _ lhs rhs =>
      expressionReferencesAny names lhs || expressionReferencesAny names rhs
  | .ternary condition then_ else_ =>
      expressionReferencesAny names condition || expressionReferencesAny names then_ ||
        expressionReferencesAny names else_
  | .index array index =>
      expressionReferencesAny names array || expressionReferencesAny names index
  | .slice value _ _ => expressionReferencesAny names value
  | .partSelectPlus value base width =>
      expressionReferencesAny names value || expressionReferencesAny names base ||
        expressionReferencesAny names width
  | .concat values => values.any (expressionReferencesAny names)
  | .repeat_ count value =>
      expressionReferencesAny names count || expressionReferencesAny names value
  | .sizedCast _ value => expressionReferencesAny names value
  | .lit _ => false

/-- Lower an expression in a known packed assignment context.  A complete,
    modularly safe parameter expression is materialized directly at the
    destination width.  If a parameter-dependent raw expression cannot be
    represented this way, generic lowering would discard SV's destination
    context; fail closed instead of silently narrowing it to 32 bits. -/
def lowerExprAtWidth (e : SVExpr) (parameterNames : List String)
    (width : DimExpr) : Except String Expr :=
  match retainedParameterExpr? parameterNames e with
  | some value => pure (.paramConst value width)
  | none =>
      if expressionReferencesAny parameterNames e then
        match retainedSizedParameterExpr? parameterNames e with
        | some value => pure (.paramConst value width)
        | none => match e with
          | .sizedCast _ value
          | .unary .unsigned (.sizedCast _ value) =>
              if (retainedSizedParameterExpr? parameterNames value).isSome then
                -- Canonical backend output already carries an explicit result
                -- boundary and recursively isolated work widths.  Preserve
                -- that IR cast instead of trying to reinterpret the whole
                -- wrapper as a raw destination-context expression.
                pure (lowerExpr e parameterNames)
              else
                throw s!"parameter-dependent packed expression cannot preserve its SystemVerilog destination-width semantics at width {width}; add explicit supported operand sizing or specialize the module"
          | _ =>
              throw s!"parameter-dependent packed expression cannot preserve its SystemVerilog destination-width semantics at width {width}; add explicit supported operand sizing or specialize the module"
      else pure (lowerExpr e parameterNames)

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
    | .unknown _ =>
        throw "four-state x/z literals cannot be materialized in Sparkle's two-state IR"
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
  | .lit (.unknown none) => true
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

/-- Native 1R1W lowering retains the exact guard on its single write site.
    This is deliberately stricter than the legacy collector above: case/loop
    writes and partial-word writes must not be approximated as an unconditional
    full-word update. -/
private def combineWriteGuard (outer inner : Option SVExpr) : Option SVExpr :=
  match outer, inner with
  | none, guard | guard, none => guard
  | some lhs, some rhs => some (.binary .logAnd lhs rhs)

private partial def expressionTargetsArray (arrName : String) : SVExpr → Bool
  | .index (.ident name) _ => name == arrName
  | .slice value _ _ | .partSelectPlus value _ _ =>
      expressionTargetsArray arrName value
  | _ => false

private partial def statementWritesArray (arrName : String) : SVStmt → Bool
  | .nonblockAssign lhs _ | .blockAssign lhs _ =>
      expressionTargetsArray arrName lhs
  | .ifElse _ then_ else_ =>
      then_.any (statementWritesArray arrName) ||
        else_.any (statementWritesArray arrName)
  | .caseStmt _ arms default_ =>
      arms.any (fun arm => arm.2.any (statementWritesArray arrName)) ||
        default_.any (fun body => body.any (statementWritesArray arrName))
  | .forLoop init _ step body =>
      statementWritesArray arrName init || statementWritesArray arrName step ||
        body.any (statementWritesArray arrName)
  | .assertStmt _ => false

private partial def collectNativeArrayWrites (arrName : String)
    (outerGuard : Option SVExpr) (stmts : List SVStmt) :
    Except String (List (SVExpr × SVExpr × Option SVExpr)) := do
  let mut writes := []
  for statement in stmts do
    match statement with
    | .nonblockAssign (.index (.ident name) index) data =>
        if name == arrName then
          writes := writes ++ [(index, data, outerGuard)]
    | .nonblockAssign lhs _ | .blockAssign lhs _ =>
        if statementWritesArray arrName statement then
          throw s!"native memory '{arrName}' contains a partial, blocking, or otherwise unsupported write target: {repr lhs}"
    | .ifElse condition then_ else_ =>
        let thenGuard := combineWriteGuard outerGuard (some condition)
        let elseGuard := combineWriteGuard outerGuard
          (some (.unary .logNot condition))
        writes := writes ++ (← collectNativeArrayWrites arrName thenGuard then_)
        writes := writes ++ (← collectNativeArrayWrites arrName elseGuard else_)
    | .caseStmt _ _ _ =>
        if statementWritesArray arrName statement then
          throw s!"native memory '{arrName}' uses a case-controlled write; canonical 1R1W lowering does not approximate this control flow"
    | .forLoop _ _ _ _ =>
        if statementWritesArray arrName statement then
          throw s!"native memory '{arrName}' uses a loop-controlled write; canonical 1R1W lowering does not approximate this control flow"
    | .assertStmt _ => pure ()
  pure writes

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
    | .memory _ _ _ _ _ _ _ _ _ _ _ => memories := memories ++ [s]
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
  | .binary .div a b => return (← evalConstExpr paramVals a) / (← evalConstExpr paramVals b)
  | .binary .mod a b => return (← evalConstExpr paramVals a) % (← evalConstExpr paramVals b)
  | .binary .pow a b => return (← evalConstExpr paramVals a) ^ (← evalConstExpr paramVals b)
  | .binary .shl a b => return (← evalConstExpr paramVals a) <<< (← evalConstExpr paramVals b)
  | .binary .shr a b => return (← evalConstExpr paramVals a) >>> (← evalConstExpr paramVals b)
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
  | .binary .bitAnd a b => do
    let va ← evalConstExpr paramVals a
    let vb ← evalConstExpr paramVals b
    some (va &&& vb)
  | .binary .bitXor a b => do
    let va ← evalConstExpr paramVals a
    let vb ← evalConstExpr paramVals b
    some (va ^^^ vb)
  | .unary .clog2 a => DimExpr.clog2Nat <$> evalConstExpr paramVals a
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
    match Tools.SVParser.Parser.exprToDimExpr? value with
    | some dimension => some dimension
    | none =>
        -- Promoted localparam aliases are generated internally as an exact
        -- totalized packed modulo.  The public dimension parser intentionally
        -- rejects this raw-looking shape; accept only this tagged structural
        -- form during trusted alias substitution.
        if isRetainedPackedAliasValue value then
          Tools.SVParser.Parser.exprToSizedNatExpr? value
        else none

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
  | .unary .unsigned _ | .unary .clog2 _ | .unary .logNot _ | .unary .reductAnd _
  | .unary .reductOr _ => false
  | .unary .bitNot argument | .unary .neg argument =>
      expressionIsSigned signedIdentifiers argument
  | .binary .shl lhs _ | .binary .shr lhs _ | .binary .asr lhs _ =>
      expressionIsSigned signedIdentifiers lhs
  | .binary .add lhs rhs | .binary .sub lhs rhs | .binary .mul lhs rhs
  | .binary .div lhs rhs | .binary .mod lhs rhs | .binary .pow lhs rhs
  | .binary .bitAnd lhs rhs | .binary .bitOr lhs rhs
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
        (op == .add || op == .sub || op == .mul || op == .div ||
          op == .mod || op == .pow ||
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
private partial def hasSignedSizedCastExpr (signedIdentifiers parameterNames : List String) : SVExpr → Bool
  | .unary .unsigned (.sizedCast _ value) =>
      if (retainedSizedParameterExpr? parameterNames value).isSome then false else
      let exactlyMaterializedSignedOperand := match value with
        | .lit _ | .unary .neg (.lit _) => true
        | _ => false
      containsSignedConversion value ||
        containsUnsupportedSignedOperation signedIdentifiers value ||
        (expressionIsSigned signedIdentifiers value &&
          !exactlyMaterializedSignedOperand) ||
        hasSignedSizedCastExpr signedIdentifiers parameterNames value
  | .sizedCast _ value =>
      containsSignedConversion value ||
        containsUnsupportedSignedOperation signedIdentifiers value ||
        expressionIsSigned signedIdentifiers value ||
        hasSignedSizedCastExpr signedIdentifiers parameterNames value
  | .unary _ argument => hasSignedSizedCastExpr signedIdentifiers parameterNames argument
  | .binary _ lhs rhs =>
      hasSignedSizedCastExpr signedIdentifiers parameterNames lhs ||
        hasSignedSizedCastExpr signedIdentifiers parameterNames rhs
  | .ternary condition then_ else_ =>
      hasSignedSizedCastExpr signedIdentifiers parameterNames condition ||
        hasSignedSizedCastExpr signedIdentifiers parameterNames then_ ||
        hasSignedSizedCastExpr signedIdentifiers parameterNames else_
  | .index array index =>
      hasSignedSizedCastExpr signedIdentifiers parameterNames array ||
        hasSignedSizedCastExpr signedIdentifiers parameterNames index
  | .slice expression _ _ => hasSignedSizedCastExpr signedIdentifiers parameterNames expression
  | .partSelectPlus expression base width =>
      hasSignedSizedCastExpr signedIdentifiers parameterNames expression ||
        hasSignedSizedCastExpr signedIdentifiers parameterNames base ||
        hasSignedSizedCastExpr signedIdentifiers parameterNames width
  | .concat expressions =>
      expressions.any (hasSignedSizedCastExpr signedIdentifiers parameterNames)
  | .repeat_ count value =>
      -- A replication multiplier is an elaboration-time Nat, not a packed
      -- data-path value.  When its complete expression is exactly evaluable,
      -- signed result metadata is irrelevant after conversion to that Nat.
      (if (svExprToNat count).isSome then false
       else hasSignedSizedCastExpr signedIdentifiers parameterNames count) ||
        hasSignedSizedCastExpr signedIdentifiers parameterNames value
  | .lit _ | .ident _ => false

private partial def hasSignedSizedCastStmt (signedIdentifiers parameterNames : List String) : SVStmt → Bool
  | .blockAssign lhs rhs | .nonblockAssign lhs rhs =>
      hasSignedSizedCastExpr signedIdentifiers parameterNames lhs ||
        hasSignedSizedCastExpr signedIdentifiers parameterNames rhs
  | .ifElse condition then_ else_ =>
      hasSignedSizedCastExpr signedIdentifiers parameterNames condition ||
        then_.any (hasSignedSizedCastStmt signedIdentifiers parameterNames) ||
        else_.any (hasSignedSizedCastStmt signedIdentifiers parameterNames)
  | .caseStmt selector arms default_ =>
      hasSignedSizedCastExpr signedIdentifiers parameterNames selector ||
        arms.any (fun arm =>
          arm.1.any (hasSignedSizedCastExpr signedIdentifiers parameterNames) ||
            arm.2.any (hasSignedSizedCastStmt signedIdentifiers parameterNames)) ||
        default_.any (fun statements =>
          statements.any (hasSignedSizedCastStmt signedIdentifiers parameterNames))
  | .forLoop init condition step body =>
      hasSignedSizedCastStmt signedIdentifiers parameterNames init ||
        hasSignedSizedCastExpr signedIdentifiers parameterNames condition ||
        hasSignedSizedCastStmt signedIdentifiers parameterNames step ||
        body.any (hasSignedSizedCastStmt signedIdentifiers parameterNames)
  | .assertStmt condition => hasSignedSizedCastExpr signedIdentifiers parameterNames condition

private partial def hasSignedSizedCastItem (signedIdentifiers parameterNames : List String) : SVModuleItem → Bool
  | .wireDecl _ _ init _ => init.any (hasSignedSizedCastExpr signedIdentifiers parameterNames)
  | .paramDecl parameter => hasSignedSizedCastExpr signedIdentifiers parameterNames parameter.value
  | .contAssign lhs rhs =>
      hasSignedSizedCastExpr signedIdentifiers parameterNames lhs ||
        hasSignedSizedCastExpr signedIdentifiers parameterNames rhs
  | .alwaysBlock _ statements =>
      statements.any (hasSignedSizedCastStmt signedIdentifiers parameterNames)
  | .generateBlock condition body elseBody =>
      hasSignedSizedCastExpr signedIdentifiers parameterNames condition ||
        body.any (hasSignedSizedCastItem signedIdentifiers parameterNames) ||
        elseBody.any (hasSignedSizedCastItem signedIdentifiers parameterNames)
  | .instantiation _ _ connections overrides =>
      connections.any (fun connection =>
        hasSignedSizedCastExpr signedIdentifiers parameterNames connection.2) ||
        overrides.any (fun override =>
          hasSignedSizedCastExpr signedIdentifiers parameterNames override.2)
  | .taskDecl _ statements =>
      statements.any (hasSignedSizedCastStmt signedIdentifiers parameterNames)
  | .validationGuard condition => hasSignedSizedCastExpr signedIdentifiers parameterNames condition
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
  let parameterNames := (module_.params ++ collectItemParameters module_.items).map (·.name)
  module_.params.any (fun parameter =>
      hasSignedSizedCastExpr signedIdentifiers parameterNames parameter.value) ||
    module_.items.any (hasSignedSizedCastItem signedIdentifiers parameterNames)

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
  | .binary .div _ _ | .binary .mod _ _
  | .binary .pow _ _ | .binary .bitAnd _ _ | .binary .bitOr _ _
  | .binary .bitXor _ _ | .binary .shl _ _ | .binary .shr _ _
  | .binary .asr _ _ => true
  | .ternary _ _ _ => true
  | _ => false

/-- Find a context-dependent operand at any sized-cast boundary.  Traversal
    below a self-determined boundary is still required so nested sized casts
    receive their own independent check. -/
private partial def hasUnsupportedSizedCastContextExpr (parameterNames : List String) : SVExpr → Bool
  | whole@(.unary .unsigned (.sizedCast _ value)) =>
      -- The Verilog backend wraps every retained Nat subexpression in this
      -- exact unsigned sized-cast form.  Check the complete wrapper before
      -- descending into its context-determined body; inspecting the body in
      -- isolation would incorrectly reject canonical totalized clog2/sub/div.
      if (Tools.SVParser.Parser.exprToNatExpr? whole).isSome then false
      else if (retainedSizedParameterExpr? parameterNames value).isSome then false
      else sizedCastOperandNeedsContext value ||
        hasUnsupportedSizedCastContextExpr parameterNames value
  | .sizedCast _ value =>
      if (retainedSizedParameterExpr? parameterNames value).isSome then false
      else sizedCastOperandNeedsContext value ||
        hasUnsupportedSizedCastContextExpr parameterNames value
  | .unary _ argument => hasUnsupportedSizedCastContextExpr parameterNames argument
  | .binary _ lhs rhs =>
      hasUnsupportedSizedCastContextExpr parameterNames lhs ||
        hasUnsupportedSizedCastContextExpr parameterNames rhs
  | .ternary condition then_ else_ =>
      hasUnsupportedSizedCastContextExpr parameterNames condition ||
        hasUnsupportedSizedCastContextExpr parameterNames then_ ||
        hasUnsupportedSizedCastContextExpr parameterNames else_
  | .index array index =>
      hasUnsupportedSizedCastContextExpr parameterNames array ||
        hasUnsupportedSizedCastContextExpr parameterNames index
  | .slice expression _ _ => hasUnsupportedSizedCastContextExpr parameterNames expression
  | .partSelectPlus expression base width =>
      hasUnsupportedSizedCastContextExpr parameterNames expression ||
        hasUnsupportedSizedCastContextExpr parameterNames base ||
        hasUnsupportedSizedCastContextExpr parameterNames width
  | .concat expressions => expressions.any (hasUnsupportedSizedCastContextExpr parameterNames)
  | .repeat_ count value =>
      hasUnsupportedSizedCastContextExpr parameterNames count ||
        hasUnsupportedSizedCastContextExpr parameterNames value
  | .lit _ | .ident _ => false

private partial def hasUnsupportedSizedCastContextStmt (parameterNames : List String) : SVStmt → Bool
  | .blockAssign lhs rhs | .nonblockAssign lhs rhs =>
      hasUnsupportedSizedCastContextExpr parameterNames lhs ||
        hasUnsupportedSizedCastContextExpr parameterNames rhs
  | .ifElse condition then_ else_ =>
      hasUnsupportedSizedCastContextExpr parameterNames condition ||
        then_.any (hasUnsupportedSizedCastContextStmt parameterNames) ||
        else_.any (hasUnsupportedSizedCastContextStmt parameterNames)
  | .caseStmt selector arms default_ =>
      hasUnsupportedSizedCastContextExpr parameterNames selector ||
        arms.any (fun arm =>
          arm.1.any (hasUnsupportedSizedCastContextExpr parameterNames) ||
            arm.2.any (hasUnsupportedSizedCastContextStmt parameterNames)) ||
        default_.any (fun statements =>
          statements.any (hasUnsupportedSizedCastContextStmt parameterNames))
  | .forLoop init condition step body =>
      hasUnsupportedSizedCastContextStmt parameterNames init ||
        hasUnsupportedSizedCastContextExpr parameterNames condition ||
        hasUnsupportedSizedCastContextStmt parameterNames step ||
        body.any (hasUnsupportedSizedCastContextStmt parameterNames)
  | .assertStmt condition => hasUnsupportedSizedCastContextExpr parameterNames condition

private partial def hasUnsupportedSizedCastContextItem (parameterNames : List String) : SVModuleItem → Bool
  | .wireDecl _ _ init _ =>
      init.any (hasUnsupportedSizedCastContextExpr parameterNames)
  | .paramDecl parameter =>
      hasUnsupportedSizedCastContextExpr parameterNames parameter.value
  | .contAssign lhs rhs =>
      hasUnsupportedSizedCastContextExpr parameterNames lhs ||
        hasUnsupportedSizedCastContextExpr parameterNames rhs
  | .alwaysBlock _ statements | .taskDecl _ statements =>
      statements.any (hasUnsupportedSizedCastContextStmt parameterNames)
  | .generateBlock condition body elseBody =>
      hasUnsupportedSizedCastContextExpr parameterNames condition ||
        body.any (hasUnsupportedSizedCastContextItem parameterNames) ||
        elseBody.any (hasUnsupportedSizedCastContextItem parameterNames)
  | .instantiation _ _ connections overrides =>
      connections.any (fun connection =>
        hasUnsupportedSizedCastContextExpr parameterNames connection.2) ||
        overrides.any (fun override =>
          hasUnsupportedSizedCastContextExpr parameterNames override.2)
  | .validationGuard _ =>
      -- `Parser.parseValidationGenerate` admits only Sparkle's exact labeled
      -- guards.  Their meta-width casts are compiler diagnostics, not user
      -- data-path casts.
      false
  | .regDecl .. | .integerDecl _ | .readmemh _ _ => false

private def hasUnsupportedSizedCastContext (module_ : SVModule) : Bool :=
  let parameterNames := (module_.params ++ collectItemParameters module_.items).map (·.name)
  module_.params.any (fun parameter =>
      hasUnsupportedSizedCastContextExpr parameterNames parameter.value) ||
    module_.items.any (hasUnsupportedSizedCastContextItem parameterNames)

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

private def hasUnsupportedNativeLhs (parameterNames arrayRegNames : List String)
    (lhs : SVExpr) : Bool :=
  !parameterNames.isEmpty && !(match lhs with
    | .ident _ => true
    | .index (.ident name) _ => arrayRegNames.contains name
    | _ => false)

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

private partial def hasUnsupportedParameterizedStmt
    (parameterNames arrayRegNames : List String) : SVStmt → Bool
  | .blockAssign lhs rhs | .nonblockAssign lhs rhs =>
    hasUnsupportedNativeLhs parameterNames arrayRegNames lhs ||
      hasUnsupportedParameterizedExpr parameterNames lhs ||
      hasUnsupportedParameterizedExpr parameterNames rhs
  | .ifElse condition then_ else_ =>
    hasUnsupportedParameterizedExpr parameterNames condition ||
      then_.any (hasUnsupportedParameterizedStmt parameterNames arrayRegNames) ||
      else_.any (hasUnsupportedParameterizedStmt parameterNames arrayRegNames)
  | .caseStmt selector arms default_ =>
    hasUnsupportedParameterizedExpr parameterNames selector ||
      arms.any (fun arm => arm.1.any (hasUnsupportedParameterizedExpr parameterNames) ||
        arm.2.any (hasUnsupportedParameterizedStmt parameterNames arrayRegNames)) ||
      default_.any (fun statements =>
        statements.any (hasUnsupportedParameterizedStmt parameterNames arrayRegNames))
  | .forLoop init condition step body =>
    hasUnsupportedParameterizedStmt parameterNames arrayRegNames init ||
      hasUnsupportedParameterizedExpr parameterNames condition ||
      hasUnsupportedParameterizedStmt parameterNames arrayRegNames step ||
      body.any (hasUnsupportedParameterizedStmt parameterNames arrayRegNames)
  | .assertStmt condition => hasUnsupportedParameterizedExpr parameterNames condition

private def hasUnsupportedParameterizedConstruct
    (parameterNames arrayRegNames : List String)
    (items : List SVModuleItem) : Bool :=
  items.any fun item => match item with
  | .contAssign lhs rhs =>
    hasUnsupportedNativeLhs parameterNames arrayRegNames lhs ||
      hasUnsupportedParameterizedExpr parameterNames lhs ||
      hasUnsupportedParameterizedExpr parameterNames rhs
  | .alwaysBlock .star _ =>
    -- The current SSA lowering gives temporary wires a concrete 64-bit type.
    -- Retaining a module parameter here would silently truncate W>64 values.
    !parameterNames.isEmpty
  | .alwaysBlock (.posedge _) statements =>
    (!parameterNames.isEmpty && hasBlockingAssignment statements) ||
      statements.any (hasUnsupportedParameterizedStmt parameterNames arrayRegNames)
  | .alwaysBlock _ statements =>
    statements.any (hasUnsupportedParameterizedStmt parameterNames arrayRegNames)
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
        if inc == 0 then [s]
        else if b <= initVal then []
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
-- SystemVerilog-native generate/procedural lowering
-- ============================================================================

/-- Native procedural loops retain their SystemVerilog `integer` induction
    variables.  Other signed packed declarations still fail closed. -/
private partial def hasNativeUnsupportedSignedItem : SVModuleItem → Bool
  | .wireDecl _ _ _ isSigned | .regDecl _ _ _ isSigned => isSigned
  | .integerDecl _ => false
  | .paramDecl parameter => parameter.isSigned
  | .generateBlock _ thenItems elseItems =>
      thenItems.any hasNativeUnsupportedSignedItem ||
        elseItems.any hasNativeUnsupportedSignedItem
  | _ => false

private def hasNativeUnsupportedSignedDeclaration (module_ : SVModule) : Bool :=
  module_.ports.any (·.isSigned) || module_.params.any (·.isSigned) ||
    module_.items.any hasNativeUnsupportedSignedItem

private partial def collectForVariables : List SVStmt → List String
  | statements => statements.flatMap fun statement => match statement with
    | .forLoop (.blockAssign (.ident name) _) _ _ body =>
        name :: collectForVariables body
    | .forLoop _ _ _ body => collectForVariables body
    | .ifElse _ then_ else_ =>
        collectForVariables then_ ++ collectForVariables else_
    | .caseStmt _ arms default_ =>
        arms.flatMap (fun arm => collectForVariables arm.2) ++
          (default_.map collectForVariables).getD []
    | _ => []

private partial def collectNativeLoopVariables (parameterNames : List String)
    (items : List SVModuleItem) : List String :=
  items.flatMap fun item => match item with
  | .alwaysBlock .star statements =>
      if hasParameterizedFor parameterNames statements then
        collectForVariables statements
      else []
  | .generateBlock _ thenItems elseItems =>
      collectNativeLoopVariables parameterNames thenItems ++
        collectNativeLoopVariables parameterNames elseItems
  | _ => []

private partial def collectIntegerDeclarations (items : List SVModuleItem) : List String :=
  items.flatMap fun item => match item with
  | .integerDecl name => [name]
  | .generateBlock _ thenItems elseItems =>
      collectIntegerDeclarations thenItems ++ collectIntegerDeclarations elseItems
  | _ => []

private partial def containsForStmt : SVStmt → Bool
  | .forLoop .. => true
  | .ifElse _ then_ else_ => then_.any containsForStmt || else_.any containsForStmt
  | .caseStmt _ arms default_ =>
      arms.any (fun arm => arm.2.any containsForStmt) ||
        default_.any (fun statements => statements.any containsForStmt)
  | _ => false

private def containsForItem : SVModuleItem → Bool
  | .alwaysBlock _ statements => statements.any containsForStmt
  | _ => false

/- A raw SystemVerilog subtraction wraps at its expression width, whereas a
   `DimExpr.sub` is subtraction on natural numbers and therefore saturates at
   zero.  The backend's canonical totalized ternary/wrapper is safe to recover;
   arbitrary source subtraction is not safe at this boundary. -/
private partial def hasPotentiallyWrappingRawSub (expression : SVExpr) : Bool :=
  if Tools.SVParser.Parser.recoverNatWorkExpr? expression |>.isSome then false
  else match expression with
  | .ternary (.binary .ge lhs rhs) (.binary .sub thenLhs thenRhs)
      (.lit (.decimal _ 0)) =>
      if lhs == thenLhs && rhs == thenRhs then
        hasPotentiallyWrappingRawSub lhs || hasPotentiallyWrappingRawSub rhs
      else true
  | subtraction@(.binary .sub _ _) =>
      Tools.SVParser.Parser.exprToNatExpr? subtraction |>.isNone
  | .unary _ argument => hasPotentiallyWrappingRawSub argument
  | .binary _ lhs rhs =>
      hasPotentiallyWrappingRawSub lhs || hasPotentiallyWrappingRawSub rhs
  | .ternary condition then_ else_ =>
      hasPotentiallyWrappingRawSub condition ||
        hasPotentiallyWrappingRawSub then_ || hasPotentiallyWrappingRawSub else_
  | .index array index =>
      hasPotentiallyWrappingRawSub array || hasPotentiallyWrappingRawSub index
  | .slice value _ _ => hasPotentiallyWrappingRawSub value
  | .partSelectPlus value base width =>
      hasPotentiallyWrappingRawSub value || hasPotentiallyWrappingRawSub base ||
        hasPotentiallyWrappingRawSub width
  | .concat values => values.any hasPotentiallyWrappingRawSub
  | .repeat_ count value =>
      hasPotentiallyWrappingRawSub count || hasPotentiallyWrappingRawSub value
  | .sizedCast _ value => hasPotentiallyWrappingRawSub value
  | .lit _ | .ident _ => false

private def parameterDimExpr (parameterNames : List String) (role : String)
    (expression : SVExpr) : Except String DimExpr := do
  if hasPotentiallyWrappingRawSub expression then
    throw s!"{role} contains raw SystemVerilog subtraction whose fixed-width wraparound cannot be represented as natural-number subtraction; use the canonical totalized form or rewrite the expression"
  let value ← match retainedNatValueExpr? expression with
    | some value => pure value
    | none => throw s!"{role} is not a supported natural-number parameter expression: {repr expression}"
  for name in value.parameters do
    unless parameterNames.contains name do
      throw s!"{role} references '{name}', which is not a declared module parameter"
  pure value

private partial def lowerNativeCondition (parameterNames : List String)
    (condition : SVExpr) : Except String NativeCondition := do
  match condition with
  | .unary .logNot inner => return .not (← lowerNativeCondition parameterNames inner)
  | .binary .logAnd lhs rhs =>
      return .and (← lowerNativeCondition parameterNames lhs)
        (← lowerNativeCondition parameterNames rhs)
  | .binary .logOr lhs rhs =>
      return .or (← lowerNativeCondition parameterNames lhs)
        (← lowerNativeCondition parameterNames rhs)
  | .binary operator lhs rhs =>
      let lhs ← parameterDimExpr parameterNames "generate condition left operand" lhs
      let rhs ← parameterDimExpr parameterNames "generate condition right operand" rhs
      match operator with
      | .eq => pure (.eq lhs rhs)
      | .neq => pure (.ne lhs rhs)
      | .lt => pure (.lt lhs rhs)
      | .le => pure (.le lhs rhs)
      | .gt => pure (.gt lhs rhs)
      | .ge => pure (.ge lhs rhs)
      | _ => throw "native generate conditions support only ==, !=, <, <=, >, >=, &&, ||, and !"
  | other => return .nonzero (← parameterDimExpr parameterNames "generate condition" other)

/-- The legacy/default-specialization path must not evaluate a packed SV
    generate expression with the unbounded-Nat evaluator.  Reuse the native
    condition checker for parameter-dependent branches before selecting one;
    safe comparisons such as `W > 1` remain supported, while expressions such
    as `(1 << W) != 0` fail closed instead of changing at W >= 32. -/
private partial def validateParameterizedGenerateConditions
    (parameterNames : List String) (items : List SVModuleItem) : Except String Unit := do
  for item in items do
    match item with
    | .generateBlock condition thenItems elseItems =>
        if (collectReadNamesExpr condition).any parameterNames.contains then
          let _ ← lowerNativeCondition parameterNames condition
        validateParameterizedGenerateConditions parameterNames thenItems
        validateParameterizedGenerateConditions parameterNames elseItems
    | _ => pure ()

/-- Preserve a procedural assignment target.  This must not use `lowerExpr`:
    its dynamic packed-index lowering is a read expression (shift-and-mask),
    not an assignable SystemVerilog lvalue. -/
private def lowerNativeLhs (parameterNames arrayNames : List String)
    (lhs : SVExpr) : Except String Expr := do
  match lhs with
  | .ident name => pure (.ref name)
  | .index (.ident name) index =>
      if arrayNames.contains name then
        throw s!"native procedural assignment to memory element '{name}[...]' is outside the supported packed-loop subset"
      pure (.index (.ref name) (lowerExpr index parameterNames))
  | _ =>
      throw s!"native procedural assignment target is not a supported identifier or packed bit-select: {repr lhs}"

private partial def lowerNativeProcStmt (parameterNames integerNames arrayNames : List String)
    (statement : SVStmt) : Except String ProcStmt := do
  match statement with
  | .blockAssign lhs rhs =>
      return .blocking (← lowerNativeLhs parameterNames arrayNames lhs)
        (lowerExpr rhs parameterNames)
  | .ifElse condition then_ else_ =>
      return .ifElse (lowerExpr condition parameterNames)
        (← then_.mapM (lowerNativeProcStmt parameterNames integerNames arrayNames))
        (← else_.mapM (lowerNativeProcStmt parameterNames integerNames arrayNames))
  | .forLoop (.blockAssign (.ident loopVar) initExpr) condition
      (.blockAssign (.ident stepVariable) stepExpr) body =>
      unless loopVar == stepVariable do
        throw s!"native loop initializes '{loopVar}' but updates '{stepVariable}'"
      unless integerNames.contains loopVar do
        throw s!"native loop variable '{loopVar}' must have an `integer` declaration"
      let init ← match svExprToNat initExpr with
        | some value => pure value
        | none => throw s!"native loop '{loopVar}' has a non-constant or negative initializer"
      let (inclusive, boundExpr) ← match condition with
        | .binary .lt (.ident conditionVariable) bound =>
            if conditionVariable == loopVar then pure (false, bound)
            else throw s!"native loop condition uses '{conditionVariable}' instead of '{loopVar}'"
        | .binary .le (.ident conditionVariable) bound =>
            if conditionVariable == loopVar then pure (true, bound)
            else throw s!"native loop condition uses '{conditionVariable}' instead of '{loopVar}'"
        | _ => throw s!"native loop '{loopVar}' must use a canonical `<` or `<=` upper bound"
      let bound ← parameterDimExpr parameterNames s!"native loop '{loopVar}' bound" boundExpr
      let step ← match stepExpr with
        | .binary .add (.ident stepSource) increment =>
            unless stepSource == loopVar do
              throw s!"native loop step for '{loopVar}' reads '{stepSource}'"
            match svExprToNat increment with
            | some 0 => throw s!"native loop '{loopVar}' increment/step must be positive, not zero"
            | some value => pure value
            | none => throw s!"native loop '{loopVar}' increment must be a concrete positive constant"
        | _ => throw s!"native loop '{loopVar}' must use the canonical update `{loopVar} = {loopVar} + step`"
      if (collectWriteNames body).contains loopVar then
        throw s!"native loop body writes induction variable '{loopVar}'"
      return .forLoop loopVar init bound step inclusive
        (← body.mapM (lowerNativeProcStmt parameterNames integerNames arrayNames))
  | .forLoop .. =>
      throw "native procedural loop does not match the supported canonical increasing-loop form"
  | .nonblockAssign .. =>
      throw "native always_comb subset does not support nonblocking assignments"
  | .caseStmt .. => throw "native always_comb subset does not yet support case statements"
  | .assertStmt .. => throw "native always_comb subset does not yet support procedural assertions"

private partial def lowerNativeItems (parameterNames integerNames arrayNames : List String)
    (items : List SVModuleItem) : Except String (List NativeItem) := do
  let mut result : List NativeItem := []
  for item in items do
    match item with
    | .wireDecl name width init false =>
        result := result ++ [.wireDecl name (widthToHWType width)]
        for value in init.toList do
          result := result ++ [.contAssign (.ref name) (lowerExpr value parameterNames)]
    | .wireDecl _ _ _ true => throw "signed native generate wire declarations are not supported"
    | .regDecl name width none false =>
        result := result ++ [.wireDecl name (widthToHWType width)]
    | .regDecl name _ (some _) _ =>
        throw s!"native generate array declaration '{name}' is outside the supported subset"
    | .regDecl _ _ _ true => throw "signed native generate register declarations are not supported"
    | .integerDecl name =>
        unless integerNames.contains name do
          throw s!"native integer '{name}' is supported only as a canonical loop induction variable"
        result := result ++ [.integerDecl name]
    | .contAssign lhs rhs =>
        result := result ++ [.contAssign
          (← lowerNativeLhs parameterNames arrayNames lhs) (lowerExpr rhs parameterNames)]
    | .alwaysBlock .star statements =>
        result := result ++ [.process .comb
          (← statements.mapM (lowerNativeProcStmt parameterNames integerNames arrayNames))]
    | .alwaysBlock _ _ =>
        throw "native generate/procedural retention currently supports only always_comb/always @* blocks"
    | .generateBlock condition thenItems elseItems =>
        if thenItems.isEmpty && elseItems.isEmpty then
          throw "native generate has two empty/unsupported branches; refusing to silently discard source constructs"
        result := result ++ [.generateIf (← lowerNativeCondition parameterNames condition)
          (← lowerNativeItems parameterNames integerNames arrayNames thenItems)
          (← lowerNativeItems parameterNames integerNames arrayNames elseItems)]
    | .instantiation moduleName instanceName connections overrides =>
        let mut loweredOverrides : List (String × DimExpr) := []
        for (name, value) in overrides do
          loweredOverrides := loweredOverrides ++
            [(name, ← parameterDimExpr parameterNames
              s!"native instance '{instanceName}' override '{name}'" value)]
        result := result ++ [.inst moduleName instanceName
          (connections.map fun (name, value) => (name, lowerExpr value parameterNames))
          loweredOverrides]
    | .validationGuard _ => pure ()
    | .paramDecl parameter =>
        throw s!"native generate-local parameter '{parameter.name}' is outside the supported subset"
    | .taskDecl name _ => throw s!"native generate task '{name}' is not supported"
    | .readmemh _ name => throw s!"native generate readmemh for '{name}' is not supported"
  pure result

/-- Packed width of a local parameter materialized in the core IR.  Untyped
    SV localparams normally infer their width from the RHS.  Preserve the
    common/canonical sized-cast form instead of silently forcing it to 32 bits;
    other untyped forms retain the legacy 32-bit subset. -/
private def localParamPackedWidth (parameter : SVParam) : DimExpr :=
  match parameter.width with
  | some (hi, lo) => rangeWidth hi lo
  | none => match parameter.value with
    | .sizedCast width _
    | .unary .unsigned (.sizedCast width _) => width
    | _ => 32

-- ============================================================================
-- Native dependent local-parameter aliases
-- ============================================================================

/-- Render a natural-number dimension back into the parser's expression AST.
    This is used only as an internal substitution vehicle; the resulting value
    is parsed back into `DimExpr` before it reaches the IR. -/
private def dimExprToSVExpr : DimExpr → SVExpr
  | .literal value => .lit (.decimal none value)
  | .param name => .ident name
  | .add lhs rhs => .binary .add (dimExprToSVExpr lhs) (dimExprToSVExpr rhs)
  | .sub lhs rhs =>
      let lhs := dimExprToSVExpr lhs
      let rhs := dimExprToSVExpr rhs
      .ternary (.binary .ge lhs rhs) (.binary .sub lhs rhs)
        (.lit (.decimal none 0))
  | .mul lhs rhs => .binary .mul (dimExprToSVExpr lhs) (dimExprToSVExpr rhs)
  | .div lhs rhs =>
      let lhs := dimExprToSVExpr lhs
      let rhs := dimExprToSVExpr rhs
      .ternary (.binary .eq rhs (.lit (.decimal none 0)))
        (.lit (.decimal none 0)) (.binary .div lhs rhs)
  | .mod lhs rhs =>
      let lhs := dimExprToSVExpr lhs
      let rhs := dimExprToSVExpr rhs
      .ternary (.binary .eq rhs (.lit (.decimal none 0))) lhs
        (.binary .mod lhs rhs)
  | .pow lhs rhs => .binary .pow (dimExprToSVExpr lhs) (dimExprToSVExpr rhs)
  | .shl lhs rhs => .binary .shl (dimExprToSVExpr lhs) (dimExprToSVExpr rhs)
  | .shr lhs rhs => .binary .shr (dimExprToSVExpr lhs) (dimExprToSVExpr rhs)
  | .bitAnd lhs rhs => .binary .bitAnd (dimExprToSVExpr lhs) (dimExprToSVExpr rhs)
  | .bitOr lhs rhs => .binary .bitOr (dimExprToSVExpr lhs) (dimExprToSVExpr rhs)
  | .bitXor lhs rhs => .binary .bitXor (dimExprToSVExpr lhs) (dimExprToSVExpr rhs)
  | .clog2 value =>
      let value := dimExprToSVExpr value
      .ternary (.binary .le value (.lit (.decimal none 1)))
        (.lit (.decimal none 0)) (.unary .clog2 value)
  | .min lhs rhs =>
      let lhs := dimExprToSVExpr lhs
      let rhs := dimExprToSVExpr rhs
      .ternary (.binary .lt lhs rhs) lhs rhs
  | .max lhs rhs =>
      let lhs := dimExprToSVExpr lhs
      let rhs := dimExprToSVExpr rhs
      .ternary (.binary .gt lhs rhs) lhs rhs

private def dimensionNames : Option (DimExpr × DimExpr) → List String
  | none => []
  | some (hi, lo) => hi.parameters ++ lo.parameters

private partial def allExpressionNames : SVExpr → List String
  | .lit _ => []
  | .ident name => [name]
  | .unary _ value => allExpressionNames value
  | .binary _ lhs rhs => allExpressionNames lhs ++ allExpressionNames rhs
  | .ternary condition then_ else_ =>
      allExpressionNames condition ++ allExpressionNames then_ ++
        allExpressionNames else_
  | .index value index => allExpressionNames value ++ allExpressionNames index
  | .slice value hi lo =>
      allExpressionNames value ++ hi.parameters ++ lo.parameters
  | .partSelectPlus value base width =>
      allExpressionNames value ++ allExpressionNames base ++
        allExpressionNames width
  | .concat values => values.flatMap allExpressionNames
  | .repeat_ count value => allExpressionNames count ++ allExpressionNames value
  | .sizedCast width value => width.parameters ++ allExpressionNames value

/-- Names used specifically in elaboration contexts nested inside a packed
    expression.  Plain data identifiers do not cause an otherwise ordinary
    localparam to be converted into a native alias. -/
private partial def expressionElaborationNames : SVExpr → List String
  | .lit _ | .ident _ => []
  | .unary _ value => expressionElaborationNames value
  | .binary _ lhs rhs =>
      expressionElaborationNames lhs ++ expressionElaborationNames rhs
  | .ternary condition then_ else_ =>
      expressionElaborationNames condition ++ expressionElaborationNames then_ ++
        expressionElaborationNames else_
  | .index value index =>
      expressionElaborationNames value ++ expressionElaborationNames index
  | .slice value hi lo =>
      hi.parameters ++ lo.parameters ++ expressionElaborationNames value
  | .partSelectPlus value base width =>
      allExpressionNames width ++ expressionElaborationNames value ++
        expressionElaborationNames base
  | .concat values => values.flatMap expressionElaborationNames
  | .repeat_ count value =>
      allExpressionNames count ++ expressionElaborationNames value
  | .sizedCast width value =>
      width.parameters ++ expressionElaborationNames value

private partial def statementElaborationNames : SVStmt → List String
  | .blockAssign lhs rhs | .nonblockAssign lhs rhs =>
      expressionElaborationNames lhs ++ expressionElaborationNames rhs
  | .ifElse condition then_ else_ =>
      expressionElaborationNames condition ++
        then_.flatMap statementElaborationNames ++
        else_.flatMap statementElaborationNames
  | .caseStmt selector arms default_ =>
      expressionElaborationNames selector ++
        arms.flatMap (fun arm =>
          arm.1.flatMap expressionElaborationNames ++
            arm.2.flatMap statementElaborationNames) ++
        (default_.map (fun body => body.flatMap statementElaborationNames)).getD []
  | .forLoop init condition step body =>
      allExpressionNames condition ++ statementElaborationNames init ++
        statementElaborationNames step ++ body.flatMap statementElaborationNames
  | .assertStmt condition => expressionElaborationNames condition

private partial def itemElaborationNames : SVModuleItem → List String
  | .wireDecl _ width init _ =>
      dimensionNames width ++
        (init.map expressionElaborationNames).getD []
  | .regDecl _ width depth _ =>
      dimensionNames width ++ (depth.map (fun value => value.parameters)).getD []
  | .integerDecl _ | .paramDecl _ | .readmemh _ _ => []
  | .contAssign lhs rhs =>
      expressionElaborationNames lhs ++ expressionElaborationNames rhs
  | .alwaysBlock _ statements => statements.flatMap statementElaborationNames
  | .generateBlock condition thenItems elseItems =>
      allExpressionNames condition ++ thenItems.flatMap itemElaborationNames ++
        elseItems.flatMap itemElaborationNames
  | .instantiation _ _ connections overrides =>
      connections.flatMap (fun connection => expressionElaborationNames connection.2) ++
        overrides.flatMap (fun override => allExpressionNames override.2)
  | .taskDecl _ statements => statements.flatMap statementElaborationNames
  | .validationGuard condition => allExpressionNames condition

private def localParameterDependencies (localNames : List String)
    (parameter : SVParam) : List String :=
  (allExpressionNames parameter.value ++ dimensionNames parameter.width).filter
      localNames.contains
    |>.eraseDups

private def promotedLocalParameterNames (module_ : SVModule)
    (localParameters : List SVParam) : List String := Id.run do
  let localNames := localParameters.map (·.name)
  let mut promoted :=
    (module_.ports.flatMap (fun port => dimensionNames port.width) ++
      module_.items.flatMap itemElaborationNames).filter localNames.contains
      |>.eraseDups
  let mut changed := true
  while changed do
    changed := false
    for parameter in localParameters do
      if promoted.contains parameter.name then
        for dependency in localParameterDependencies localNames parameter do
          unless promoted.contains dependency do
            promoted := promoted ++ [dependency]
            changed := true
  return promoted

private def substituteAliasDimension (aliases : List (String × DimExpr))
    (dimension : DimExpr) : DimExpr :=
  dimension.substitute fun name =>
    aliases.find? (fun alias => alias.1 == name) |>.map (·.2)

private def substituteAliasWidth (aliases : List (String × DimExpr))
    (width : Option (DimExpr × DimExpr)) : Option (DimExpr × DimExpr) :=
  width.map fun (hi, lo) =>
    (substituteAliasDimension aliases hi, substituteAliasDimension aliases lo)

/-- Expressions for which reducing the final mathematical result modulo the
    inferred packed width is equivalent to packed unsigned evaluation at each
    intermediate node.  Division, remainder, right shift, clog2 and conditionals
    are intentionally absent because they do not preserve congruence. -/
private partial def localParamOuterModuloSafe : SVExpr → Bool
  | .lit _ | .ident _ => true
  | .unary .unsigned value => localParamOuterModuloSafe value
  | .ternary (.binary .ge lhs rhs) (.binary .sub thenLhs thenRhs)
      (.lit (.decimal _ 0)) =>
      lhs == thenLhs && rhs == thenRhs &&
        localParamOuterModuloSafe lhs && localParamOuterModuloSafe rhs
  | .ternary (.binary .eq rhs (.lit (.decimal _ 0))) lhsElse
      (.binary .mod lhs thenRhs) =>
      rhs == thenRhs && lhsElse == lhs &&
        localParamOuterModuloSafe lhs && localParamOuterModuloSafe rhs
  | .binary operator lhs rhs =>
      let supported := match operator with
        | .add | .sub | .mul | .pow | .shl | .bitAnd | .bitOr | .bitXor => true
        | _ => false
      supported && localParamOuterModuloSafe lhs && localParamOuterModuloSafe rhs
  | .concat values => values.all localParamOuterModuloSafe
  | .repeat_ count value =>
      (Tools.SVParser.Parser.exprToNatExpr? count).isSome &&
        localParamOuterModuloSafe value
  | .sizedCast _ value => localParamOuterModuloSafe value
  | _ => false

/-- Infer the packed result width for the deliberately small, unambiguous
    subset of untyped localparam expressions used by native aliases.  In
    particular, unsized shifts and powers are rejected: real SV tools can give
    them a wider constant-expression result than the apparent 32-bit lhs, so a
    blanket `% 2^32` would silently change generate selection. -/
private partial def inferNativeUntypedLocalParamWidth?
    (moduleParameterNames : List String)
    (aliasWidths : List (String × DimExpr)) : SVExpr → Option DimExpr
  | .lit literal => some (.literal (naturalLiteralWidth literal))
  | .ident name =>
      if moduleParameterNames.contains name then some 32
      else aliasWidths.find? (fun alias => alias.1 == name) |>.map (·.2)
  | .unary .unsigned value | .unary .bitNot value | .unary .neg value =>
      inferNativeUntypedLocalParamWidth? moduleParameterNames aliasWidths value
  | .unary .clog2 _ => some 32
  | .unary .logNot _ | .unary .reductAnd _ | .unary .reductOr _ => some 1
  | .unary .signed _ => none
  | .binary operator lhs rhs =>
      match operator with
      | .eq | .neq | .lt | .le | .gt | .ge | .logAnd | .logOr => some 1
      | .shl | .shr | .asr | .pow => none
      | .add | .sub | .mul | .div | .mod | .bitAnd | .bitOr | .bitXor => do
          let lhsWidth ← inferNativeUntypedLocalParamWidth?
            moduleParameterNames aliasWidths lhs
          let rhsWidth ← inferNativeUntypedLocalParamWidth?
            moduleParameterNames aliasWidths rhs
          some (DimExpr.mkMax lhsWidth rhsWidth)
  | .ternary _ then_ else_ => do
      let thenWidth ← inferNativeUntypedLocalParamWidth?
        moduleParameterNames aliasWidths then_
      let elseWidth ← inferNativeUntypedLocalParamWidth?
        moduleParameterNames aliasWidths else_
      some (DimExpr.mkMax thenWidth elseWidth)
  | .index _ _ => some 1
  | .slice _ hi lo => some (rangeWidth hi lo)
  | .partSelectPlus _ _ width =>
      Tools.SVParser.Parser.exprToNatExpr? width
  | .concat values => do
      let widths ← values.mapM
        (inferNativeUntypedLocalParamWidth? moduleParameterNames aliasWidths)
      some (widths.foldl DimExpr.mkAdd 0)
  | .repeat_ count value => do
      let count ← Tools.SVParser.Parser.exprToNatExpr? count
      let valueWidth ← inferNativeUntypedLocalParamWidth?
        moduleParameterNames aliasWidths value
      some (DimExpr.mkMul count valueWidth)
  | .sizedCast width _ => some width

private partial def substituteAliasItem (aliases : List (String × DimExpr))
    (aliasExpressions : List (String × SVExpr)) : SVModuleItem → SVModuleItem
  | .wireDecl name width init isSigned =>
      .wireDecl name (substituteAliasWidth aliases width)
        (init.map (substParamExpr aliasExpressions)) isSigned
  | .regDecl name width depth isSigned =>
      .regDecl name (substituteAliasWidth aliases width)
        (depth.map (substituteAliasDimension aliases)) isSigned
  | .integerDecl name => .integerDecl name
  | .paramDecl parameter => .paramDecl {
      parameter with
      width := substituteAliasWidth aliases parameter.width
      value := substParamExpr aliasExpressions parameter.value }
  | .contAssign lhs rhs =>
      .contAssign (substParamExpr aliasExpressions lhs)
        (substParamExpr aliasExpressions rhs)
  | .alwaysBlock sensitivity statements =>
      .alwaysBlock sensitivity (statements.map (substParamStmt aliasExpressions))
  | .generateBlock condition thenItems elseItems =>
      .generateBlock (substParamExpr aliasExpressions condition)
        (thenItems.map (substituteAliasItem aliases aliasExpressions))
        (elseItems.map (substituteAliasItem aliases aliasExpressions))
  | .instantiation moduleName instanceName connections overrides =>
      .instantiation moduleName instanceName
        (connections.map fun connection =>
          (connection.1, substParamExpr aliasExpressions connection.2))
        (overrides.map fun override =>
          (override.1, substParamExpr aliasExpressions override.2))
  | .taskDecl name statements =>
      .taskDecl name (statements.map (substParamStmt aliasExpressions))
  | .readmemh filename memory => .readmemh filename memory
  | .validationGuard condition =>
      .validationGuard (substParamExpr aliasExpressions condition)

/-- Convert only localparams that participate in elaboration into symbolic
    aliases.  The conversion is declaration ordered and the aliases are fully
    expanded to module parameters, so they never become undeclared IR
    parameters or fixed-width hardware wires. -/
private def resolveNativeLocalParameterAliases (moduleParameterNames : List String)
    (module_ : SVModule) : Except String SVModule := do
  let localParameters := module_.items.filterMap fun item => match item with
    | .paramDecl parameter => if parameter.isLocal then some parameter else none
    | _ => none
  let localNames := localParameters.map (·.name)
  if localNames.eraseDups.length != localNames.length then
    throw "native symbolic lowering does not support duplicate top-level localparam declarations"
  for name in localNames do
    if moduleParameterNames.contains name then
      throw s!"localparam '{name}' collides with a module parameter"
  let promoted := promotedLocalParameterNames module_ localParameters
  if promoted.isEmpty then return module_

  let mut seenLocalNames : List String := []
  let mut aliases : List (String × DimExpr) := []
  let mut aliasWidths : List (String × DimExpr) := []
  let mut aliasExpressions : List (String × SVExpr) := []
  let mut retainedItems : List SVModuleItem := []
  for item in module_.items do
    match item with
    | .paramDecl parameter =>
        if parameter.isLocal then
          if promoted.contains parameter.name then
            for dependency in localParameterDependencies localNames parameter do
              unless seenLocalNames.contains dependency do
                throw s!"native localparam alias '{parameter.name}' has a recursive or forward reference to '{dependency}'"
            let substitutedValue := substParamExpr aliasExpressions parameter.value
            unless localParamOuterModuloSafe substitutedValue do
              throw s!"native localparam alias '{parameter.name}' uses an operation whose packed intermediate semantics cannot be represented by a final width reduction; add explicit supported casts or simplify the expression"
            let value ← match substitutedValue with
              | .sizedCast width inner
              | .unary .unsigned (.sizedCast width inner) =>
                  match Tools.SVParser.Parser.exprToSizedNatExpr? inner with
                  | some innerValue =>
                      pure (DimExpr.mkMod innerValue (DimExpr.mkShl 1 width))
                  | none =>
                      throw s!"native localparam alias '{parameter.name}' has an unsupported sized-cast value"
              | other => match Tools.SVParser.Parser.exprToSizedNatExpr? other with
                  | some value => pure value
                  | none =>
                      throw s!"native localparam alias '{parameter.name}' is not a supported natural-number expression"
            for reference in value.parameters do
              unless moduleParameterNames.contains reference do
                throw s!"native localparam alias '{parameter.name}' references '{reference}', which is not a module parameter or an earlier localparam alias"
            -- A localparam is still a packed SystemVerilog value before it is
            -- used as an elaboration constant.  Infer its result width only
            -- for syntax whose SV sizing is represented exactly; ambiguous
            -- unsized shifts/powers fail closed and require an explicit cast.
            let packedWidth ← match substituteAliasWidth aliases parameter.width with
              | some (hi, lo) => pure (rangeWidth hi lo)
              | none => match (inferNativeUntypedLocalParamWidth?
                  moduleParameterNames aliasWidths parameter.value) with
                | some width => pure (substituteAliasDimension aliases width)
                | none =>
                    throw s!"native localparam alias '{parameter.name}' has an untyped expression whose packed width cannot be preserved safely; add an explicit packed width or sized cast"
            let value := DimExpr.mkMod value (DimExpr.mkShl 1 packedWidth)
            aliases := aliases ++ [(parameter.name, value)]
            aliasWidths := aliasWidths ++ [(parameter.name, packedWidth)]
            aliasExpressions := aliasExpressions ++
              [(parameter.name, dimExprToSVExpr value)]
          else
            retainedItems := retainedItems ++ [item]
          seenLocalNames := seenLocalNames ++ [parameter.name]
        else
          retainedItems := retainedItems ++ [item]
    | other => retainedItems := retainedItems ++ [other]

  let ports := module_.ports.map fun port =>
    { port with width := substituteAliasWidth aliases port.width }
  let items := retainedItems.map (substituteAliasItem aliases aliasExpressions)
  pure { module_ with ports, items }

-- ============================================================================
-- Module lowering
-- ============================================================================

/-- Retained parameters implement Sparkle's unsigned-Nat contract, not the
    implicit packed-width arithmetic of arbitrary SystemVerilog defaults.
    Accept only expressions parsed by the strict Nat-value entry point and
    closed under module parameters; raw shifts/arithmetic must be made explicit
    with a supported canonical wrapper instead of being evaluated as unbounded
    Nats here. -/
private def extractNativeParameterDefaults
    (parameters : List SVParam) : Except String (List (String × Nat)) := do
  let mut values : List (String × Nat) := []
  for parameter in parameters do
    let value ← match Tools.SVParser.Parser.exprToNatExpr? parameter.value with
      | some valueExpr =>
          unless valueExpr.parameters.isEmpty do
            throw s!"native module parameter '{parameter.name}' has a dependent or unresolved default; spell the dependency directly at each use or specialize the source"
          match valueExpr.eval? (fun _ => none) with
          | some value => pure value
          | none =>
              throw s!"native module parameter '{parameter.name}' default could not be evaluated as a closed natural number"
      | none =>
          -- A direct literal under a concrete sized cast has exact, local SV
          -- semantics (for example `(4)'(8'd16) == 0`) and is a useful
          -- canonical parameter default.  Do not generalize this escape hatch
          -- to arithmetic under a cast: cast context propagates into those
          -- operands and the Nat evaluator would again be unsound.
          let isDirectSizedLiteral := match parameter.value with
            | .sizedCast _ (.lit _)
            | .sizedCast _ (.unary .neg (.lit _))
            | .unary .unsigned (.sizedCast _ (.lit _))
            | .unary .unsigned (.sizedCast _ (.unary .neg (.lit _))) => true
            | _ => false
          if isDirectSizedLiteral then
            match evalConstExpr [] parameter.value with
            | some value => pure value
            | none =>
                throw s!"native module parameter '{parameter.name}' has a sized-literal default that could not be evaluated"
          else
            throw s!"native module parameter '{parameter.name}' default uses packed SystemVerilog arithmetic whose sizing cannot be represented by the retained natural-number contract"
    values := values ++ [(parameter.name, value)]
  pure values

/-- Check parameter-dependent expressions before the legacy path substitutes
    defaults into the source AST.  Once substituted, a raw packed expression
    such as `(1 << K) >> K` is indistinguishable from an ordinary concrete
    expression, even though SystemVerilog evaluates it in the destination
    context while the old generic lowerer evaluates its children at 32 bits.

    Continuous assignments and declaration initializers have a known packed
    destination, so the exact `paramConst` subset accepted by
    `lowerExprAtWidth` remains supported.  The older procedural collectors do
    not carry target widths; reject parameter-valued procedural expressions
    until they are routed through the same checked representation rather than
    silently executing them with the wrong width. -/
private def validateKnownWidthParameterExpr (parameterNames : List String)
    (role : String) (expression : SVExpr) : Except String Unit := do
  if expressionReferencesAny parameterNames expression then
    let exactAtDestination :=
      (retainedParameterExpr? parameterNames expression).isSome ||
      (retainedSizedParameterExpr? parameterNames expression).isSome ||
      match expression with
      | .sizedCast _ value
      | .unary .unsigned (.sizedCast _ value) =>
          (retainedSizedParameterExpr? parameterNames value).isSome
      | _ => false
    unless exactAtDestination do
      throw s!"{role} contains a parameter-dependent packed expression whose SystemVerilog destination-width semantics cannot be represented exactly; add explicit supported operand sizing or specialize the source"

private structure LegacyProceduralContext where
  parameterValues : List (String × Nat)
  canonicalParameterNames : List String
  packedWidths : List (String × Nat)

private def specializeDimensionValue?
    (parameterValues : List (String × Nat)) (dimension : DimExpr) : Option Nat :=
  dimension.substitute (fun name =>
    parameterValues.find? (fun entry => entry.1 == name)
      |>.map fun entry => DimExpr.literal entry.2) |>.toNat?

/-- Width inferred by the legacy expression lowerer after parameters have been
    specialized.  This mirrors the packed widths represented by the resulting
    IR closely enough to decide whether an assignment context would widen a
    parameter-dependent intermediate. -/
private partial def legacyExprWidth?
    (context : LegacyProceduralContext) : SVExpr → Option Nat
  | .lit (.unknown _) => none
  | .lit literal => some (naturalLiteralWidth literal)
  | .ident name =>
      context.packedWidths.find? (fun entry => entry.1 == name)
        |>.map fun entry => entry.2
  | .unary operator value => match operator with
      | .logNot | .reductAnd | .reductOr => some 1
      | .clog2 => some 32
      | .bitNot | .neg | .signed | .unsigned => legacyExprWidth? context value
  | .binary operator lhs rhs => match operator with
      | .eq | .neq | .lt | .le | .gt | .ge | .logAnd | .logOr => some 1
      | .shl | .shr | .asr => legacyExprWidth? context lhs
      | .add | .sub | .mul | .div | .mod | .pow
      | .bitAnd | .bitOr | .bitXor => do
          let lhsWidth ← legacyExprWidth? context lhs
          let rhsWidth ← legacyExprWidth? context rhs
          pure (max lhsWidth rhsWidth)
  | .ternary _ then_ else_ => do
      let thenWidth ← legacyExprWidth? context then_
      let elseWidth ← legacyExprWidth? context else_
      pure (max thenWidth elseWidth)
  | .index _ _ => some 1
  | .slice _ hi lo => do
      let hi ← specializeDimensionValue? context.parameterValues hi
      let lo ← specializeDimensionValue? context.parameterValues lo
      if hi < lo then none else pure (hi - lo + 1)
  | .partSelectPlus _ _ width => do
      let width ← evalConstExpr context.parameterValues width
      if width == 0 then none else pure width
  | .concat values => do
      let widths ← values.mapM (legacyExprWidth? context)
      pure (widths.foldl (.+.) 0)
  | .repeat_ count value => do
      let count ← evalConstExpr context.parameterValues count
      let valueWidth ← legacyExprWidth? context value
      if count == 0 then none else pure (count * valueWidth)
  | .sizedCast width _ => specializeDimensionValue? context.parameterValues width

private def expressionUsesOnlyCanonicalLegacyParameters
    (parameterNames : List String) (context : LegacyProceduralContext)
    (expression : SVExpr) : Bool :=
  parameterNames.all fun parameterName =>
    !expressionReferencesAny [parameterName] expression ||
      context.canonicalParameterNames.contains parameterName

/-- Check that legacy default specialization cannot lose an assignment width.

    Packed arithmetic is context-determined in SystemVerilog.  The legacy IR
    collectors do not carry that context into child expressions, so a child
    mentioning a parameter is safe only when its own specialized width is at
    least the context it would receive.  Self-determined constructs (concat,
    selects, comparisons, and shift amounts) deliberately start a fresh width
    context.  Division/modulo/power/clog2 and arithmetic right shift remain
    fail-closed because the generic lowerer cannot preserve all of their packed
    intermediate or signed semantics. -/
private partial def legacyProceduralParameterExprSafe
    (parameterNames : List String) (context : LegacyProceduralContext)
    (requiredWidth : Nat) (expression : SVExpr) : Bool :=
  if !expressionReferencesAny parameterNames expression then true
  else if !expressionUsesOnlyCanonicalLegacyParameters parameterNames context expression then false
  else
    let recurse := legacyProceduralParameterExprSafe parameterNames context
    match expression with
    | .lit _ => true
    | .ident _ => (legacyExprWidth? context expression).any (requiredWidth ≤ ·)
    | .unary operator value => match operator with
        | .clog2 => false
        | .logNot | .reductAnd | .reductOr =>
            (legacyExprWidth? context value).any fun valueWidth =>
              recurse valueWidth value
        | .bitNot | .neg | .signed | .unsigned =>
            (legacyExprWidth? context expression).any fun selfWidth =>
              requiredWidth ≤ selfWidth && recurse selfWidth value
    | .binary operator lhs rhs => match operator with
        | .div | .mod | .pow | .asr => false
        | .eq | .neq | .lt | .le | .gt | .ge | .logAnd | .logOr =>
            match legacyExprWidth? context lhs, legacyExprWidth? context rhs with
            | some lhsWidth, some rhsWidth =>
                let operandWidth := max lhsWidth rhsWidth
                recurse operandWidth lhs && recurse operandWidth rhs
            | _, _ => false
        | .shl | .shr =>
            match legacyExprWidth? context lhs, legacyExprWidth? context rhs with
            | some lhsWidth, some rhsWidth =>
                requiredWidth ≤ lhsWidth &&
                  recurse lhsWidth lhs && recurse rhsWidth rhs
            | _, _ => false
        | .add | .sub | .mul | .bitAnd | .bitOr | .bitXor =>
            match legacyExprWidth? context lhs, legacyExprWidth? context rhs with
            | some lhsWidth, some rhsWidth =>
                let selfWidth := max lhsWidth rhsWidth
                requiredWidth ≤ selfWidth &&
                  recurse selfWidth lhs && recurse selfWidth rhs
            | _, _ => false
    | .ternary condition then_ else_ =>
        match legacyExprWidth? context condition,
            legacyExprWidth? context then_, legacyExprWidth? context else_ with
        | some conditionWidth, some thenWidth, some elseWidth =>
            let valueWidth := max thenWidth elseWidth
            requiredWidth ≤ valueWidth && recurse conditionWidth condition &&
              recurse valueWidth then_ && recurse valueWidth else_
        | _, _, _ => false
    | .index array index =>
        match legacyExprWidth? context array, legacyExprWidth? context index with
        | some arrayWidth, some indexWidth =>
            recurse arrayWidth array && recurse indexWidth index
        | _, _ => false
    | .slice value _ _ =>
        (legacyExprWidth? context value).any fun valueWidth => recurse valueWidth value
    | .partSelectPlus value base width =>
        match legacyExprWidth? context value, legacyExprWidth? context base,
            legacyExprWidth? context width with
        | some valueWidth, some baseWidth, some widthWidth =>
            recurse valueWidth value && recurse baseWidth base && recurse widthWidth width
        | _, _, _ => false
    | .concat values => values.all fun value =>
        (legacyExprWidth? context value).any fun valueWidth => recurse valueWidth value
    | .repeat_ count value =>
        match legacyExprWidth? context count, legacyExprWidth? context value with
        | some countWidth, some valueWidth =>
            recurse countWidth count && recurse valueWidth value
        | _, _ => false
    | .sizedCast width value =>
        match specializeDimensionValue? context.parameterValues width with
        | some castWidth => recurse castWidth value
        | none => false

private partial def legacyAssignmentWidth?
    (context : LegacyProceduralContext) : SVExpr → Option Nat
  | .ident name =>
      context.packedWidths.find? (fun entry => entry.1 == name)
        |>.map fun entry => entry.2
  | .index _ _ => some 1
  | .slice _ hi lo => do
      let hi ← specializeDimensionValue? context.parameterValues hi
      let lo ← specializeDimensionValue? context.parameterValues lo
      if hi < lo then none else pure (hi - lo + 1)
  | .partSelectPlus _ _ width => do
      let width ← evalConstExpr context.parameterValues width
      if width == 0 then none else pure width
  | .concat values => do
      let widths ← values.mapM (legacyAssignmentWidth? context)
      pure (widths.foldl (.+.) 0)
  | _ => none

private partial def validateProceduralParameterExprs
    (parameterNames : List String) (legacyContext : Option LegacyProceduralContext)
    (statements : List SVStmt) : Except String Unit := do
  for statement in statements do
    match statement with
    | .blockAssign lhs rhs | .nonblockAssign lhs rhs =>
        if expressionReferencesAny parameterNames rhs then
          match legacyContext with
          | some context =>
              let targetWidth ← match legacyAssignmentWidth? context lhs with
                | some width => pure width
                | none => throw "parameter-dependent procedural assignment has a destination width that legacy specialization cannot determine"
              unless legacyProceduralParameterExprSafe parameterNames context targetWidth rhs do
                throw "parameter-dependent procedural assignment would lose packed destination-width semantics during legacy specialization; add explicit supported operand sizing"
          | none =>
              throw "parameter-dependent procedural assignment cannot yet preserve its destination-width semantics in normalized IR; emit native SystemVerilog or specialize/rewrite the expression explicitly"
    | .ifElse condition then_ else_ =>
        if expressionReferencesAny parameterNames condition then
          throw "parameter-dependent procedural condition cannot yet preserve packed SystemVerilog sizing in normalized IR"
        validateProceduralParameterExprs parameterNames legacyContext then_
        validateProceduralParameterExprs parameterNames legacyContext else_
    | .caseStmt selector arms default_ =>
        if expressionReferencesAny parameterNames selector then
          throw "parameter-dependent procedural case selector cannot yet preserve packed SystemVerilog sizing in normalized IR"
        for (labels, body) in arms do
          if labels.any (expressionReferencesAny parameterNames) then
            throw "parameter-dependent procedural case label cannot yet preserve packed SystemVerilog sizing in normalized IR"
          validateProceduralParameterExprs parameterNames legacyContext body
        for body in default_.toList do
          validateProceduralParameterExprs parameterNames legacyContext body
    | .forLoop _ condition _ body =>
        -- Parameter-dependent canonical loops are either retained as native
        -- procedural IR or specialized/unrolled before the flat collectors.
        -- Their body must therefore not be rejected by this core-only check.
        if !(expressionReferencesAny parameterNames condition) then
          validateProceduralParameterExprs parameterNames legacyContext body
    | .assertStmt condition =>
        if expressionReferencesAny parameterNames condition then
          throw "parameter-dependent procedural assertion cannot yet preserve packed SystemVerilog sizing in normalized IR"

private partial def validateCoreParameterExpressions
    (parameterNames : List String) (legacyContext : Option LegacyProceduralContext)
    (items : List SVModuleItem) : Except String Unit := do
  for item in items do
    match item with
    | .wireDecl name _ init _ =>
        for expression in init.toList do
          validateKnownWidthParameterExpr parameterNames
            s!"initializer for '{name}'" expression
    | .paramDecl parameter =>
        if parameter.isLocal then
          validateKnownWidthParameterExpr parameterNames
            s!"localparam '{parameter.name}'" parameter.value
    | .contAssign lhs rhs =>
        validateKnownWidthParameterExpr parameterNames
          s!"continuous assignment to '{repr lhs}'" rhs
    | .alwaysBlock _ statements | .taskDecl _ statements =>
        validateProceduralParameterExprs parameterNames legacyContext statements
    | .generateBlock _ thenItems elseItems =>
        validateCoreParameterExpressions parameterNames legacyContext thenItems
        validateCoreParameterExpressions parameterNames legacyContext elseItems
    | .instantiation _ instanceName connections _ =>
        for (portName, expression) in connections do
          if expressionReferencesAny parameterNames expression &&
              (retainedParameterExpr? parameterNames expression).isNone then
            throw s!"connection '{instanceName}.{portName}' contains a parameter-dependent packed expression without an explicit result width"
    | .regDecl .. | .integerDecl _ | .readmemh _ _ | .validationGuard _ =>
        pure ()

private def legacyParameterPackedWidth?
    (parameterValues : List (String × Nat)) (parameter : SVParam) : Option Nat :=
  match parameter.width with
  | some width => widthToBits (specializeWidth parameterValues (some width))
  | none => match parameter.value with
      | .lit (.unknown _) => none
      | .lit literal => some (naturalLiteralWidth literal)
      | .unary .neg (.lit literal)
      | .unary .unsigned (.lit literal) =>
          match literal with
          | .unknown _ => none
          | _ => some (naturalLiteralWidth literal)
      | .sizedCast width _
      | .unary .unsigned (.sizedCast width _) =>
          specializeDimensionValue? parameterValues width
      | _ => none

private partial def collectLegacyItemPackedWidths
    (parameterValues : List (String × Nat)) :
    List SVModuleItem → List (String × Nat)
  | items => items.flatMap fun item => match item with
      | .wireDecl name width _ _ | .regDecl name width _ _ =>
          (widthToBits (specializeWidth parameterValues width)).toList.map (name, ·)
      | .integerDecl name => [(name, 32)]
      | .paramDecl parameter =>
          (legacyParameterPackedWidth? parameterValues parameter).toList.map
            (parameter.name, ·)
      | .generateBlock _ thenItems elseItems =>
          collectLegacyItemPackedWidths parameterValues thenItems ++
            collectLegacyItemPackedWidths parameterValues elseItems
      | _ => []

private def makeLegacyProceduralContext (svMod : SVModule)
    (parameters : List SVParam) (parameterValues : List (String × Nat)) :
    LegacyProceduralContext :=
  let parameterWidths := parameters.filterMap fun parameter =>
    (legacyParameterPackedWidth? parameterValues parameter).map
      (parameter.name, ·)
  let portWidths := svMod.ports.filterMap fun port =>
    (widthToBits (specializeWidth parameterValues port.width)).map (port.name, ·)
  { parameterValues
    canonicalParameterNames := parameterWidths.filterMap fun entry =>
      if entry.2 == 32 then some entry.1 else none
    packedWidths := parameterWidths ++ portWidths ++
      collectLegacyItemPackedWidths parameterValues svMod.items }

/-- Lower a single SVModule.  `retainParameters` selects the native symbolic
    path; the legacy SV simulation path specializes declared parameters at
    their defaults (or explicit overrides) before lowering. -/
def lowerModule (svMod : SVModule) (paramOverrides : List (String × Nat) := [])
    (retainParameters : Bool := false) : Except String Module := do
  if retainParameters && hasNativeUnsupportedSignedDeclaration svMod then
    throw "native symbolic lowering does not support signed ports, parameters, wires, or packed registers; specialize the module through the legacy path"
  if hasSignedDeclaration svMod && hasSizedCast svMod then
    throw "a SystemVerilog module combines signed declarations with sized casts, but Sparkle IR resize is unsigned; signed sized-cast semantics are not supported"
  if hasSignedSizedCast svMod then
    throw "a SystemVerilog sized cast has signed operand/result semantics that Sparkle's unsigned IR resize cannot retain; wrap an intentionally materialized cast in $unsigned or use an explicitly unsigned operand"
  if hasUnsupportedSizedCastContext svMod then
    throw "a SystemVerilog sized cast applies context width to an arithmetic, bitwise, shift, unary, or conditional operand; Sparkle IR resize operates on an already evaluated value, so this cast must be rewritten with explicit operand widths before lowering"
  if hasAsrConsumingSizedCast svMod then
    throw "a module combines a sized cast with arithmetic right shift, but Sparkle IR does not retain enough signedness to prove their dataflow semantics; rewrite the shift with an explicitly supported signed or logical operation before lowering"
  let parameterDecls := svMod.params ++ svMod.items.filterMap fun item =>
    match item with
    | .paramDecl parameter => if parameter.isLocal then none else some parameter
    | _ => none
  let parameterNames := parameterDecls.map (·.name)
  -- Expand generate blocks using parameter defaults + overrides.  The native
  -- path validates defaults with the strict Nat contract before they can be
  -- copied into the IR; legacy specialization retains its existing evaluator.
  let paramDefaults ← if retainParameters then
      extractNativeParameterDefaults parameterDecls
    else pure (extractParamDefaults svMod)
  let paramVals := paramDefaults.map fun (n, v) =>
    match paramOverrides.find? fun (on, _) => on == n with
    | some (_, ov) => (n, ov)
    | none => (n, v)
  if retainParameters then
    for parameter in parameterDecls do
      match parameter.width with
      | none => pure ()
      | some (hi, lo) =>
          unless hi.toNat? == some 31 && lo.toNat? == some 0 do
            throw s!"native module parameter '{parameter.name}' has packed width [{hi}:{lo}]; retained natural-number parameters must be untyped or canonical unsigned [31:0]"
      let defaultValue ← match paramDefaults.find? (fun entry => entry.1 == parameter.name) with
        | some (_, value) => pure value
        | none =>
            throw s!"module parameter '{parameter.name}' does not have a supported natural-number default"
      if defaultValue > 0xffffffff then
        throw s!"native module parameter '{parameter.name}' default {defaultValue} exceeds the unsigned 32-bit natural-number contract"
    for (name, value) in paramOverrides do
      if value > 0xffffffff then
        throw s!"native module parameter override '{name}'={value} exceeds the unsigned 32-bit natural-number contract"
  let svMod ← if retainParameters then
      resolveNativeLocalParameterAliases parameterNames svMod
    else pure svMod
  if retainParameters then
    for item in svMod.items do
      match item with
      | .paramDecl parameter =>
          if parameter.isLocal && parameter.width.isNone &&
              (collectReadNamesExpr parameter.value).any parameterNames.contains &&
              (inferNativeUntypedLocalParamWidth?
                parameterNames [] parameter.value).isNone then
            throw s!"native data localparam '{parameter.name}' has a parameter-dependent untyped packed width that cannot be inferred without changing SystemVerilog sizing; add an explicit packed range or sized cast"
      | _ => pure ()
  -- Run this on the unspecialized source for both APIs.  In particular, the
  -- legacy path must not erase the evidence that a concrete-looking shift or
  -- division originally depended on a packed module parameter.  Its narrow
  -- compatibility subset is checked against the concrete default-specialized
  -- widths before the parameter references disappear.
  let legacyProceduralContext := if retainParameters then none else
    some (makeLegacyProceduralContext svMod parameterDecls paramVals)
  validateCoreParameterExpressions parameterNames legacyProceduralContext svMod.items
  if !retainParameters then
    let localParameterNames := svMod.items.filterMap fun item => match item with
      | .paramDecl parameter => if parameter.isLocal then some parameter.name else none
      | _ => none
    validateParameterizedGenerateConditions
      (parameterNames ++ localParameterNames) svMod.items
  let declaredArrayNames := svMod.items.filterMap fun item => match item with
    | .regDecl name _ (some _) _ => some name
    | _ => none
  if retainParameters then
    let mut priorNames : List String := []
    for parameter in parameterDecls do
      if (collectReadNamesExpr parameter.value).any priorNames.contains then
        throw s!"module parameter '{parameter.name}' has a default that depends on another parameter; native dependent defaults are not yet representable"
      priorNames := priorNames ++ [parameter.name]
  let nativeLoopVariables :=
    (collectNativeLoopVariables parameterNames svMod.items).eraseDups
  if retainParameters then
    for name in collectIntegerDeclarations svMod.items do
      unless nativeLoopVariables.contains name do
        throw s!"native integer '{name}' is supported only as an induction variable of a parameter-dependent canonical always_comb loop"
    for item in svMod.items do
      match item with
      | .alwaysBlock sensitivity statements =>
          if hasParameterizedFor parameterNames statements then
            match sensitivity with
            | .star => pure ()
            | _ => throw "parameter-dependent procedural loops are retained only inside always_comb/always @*"
      | _ => pure ()
  let isNativeSourceItem := fun item => match item with
    | .generateBlock .. => true
    | .alwaysBlock .star statements =>
        retainParameters && hasParameterizedFor parameterNames statements
    | .integerDecl name => retainParameters && nativeLoopVariables.contains name
    | _ => false
  let nativeSourceItems := if retainParameters then
      svMod.items.filter isNativeSourceItem
    else []
  let coreSourceItems := if retainParameters then
      svMod.items.filter fun item => !isNativeSourceItem item
    else svMod.items
  if retainParameters &&
      hasUnsupportedParameterizedConstruct parameterNames declaredArrayNames coreSourceItems then
    throw "parameter-dependent slice/repeat/part-select/sign-extension, signed cast, complex assignment target, or procedural-combinational lowering is not supported for native overrides; specialize the module explicitly"
  let mut moduleParameters : List Sparkle.IR.AST.Parameter := []
  if retainParameters then
    for parameter in parameterDecls do
      let defaultValue ← match paramDefaults.find? (fun entry => entry.1 == parameter.name) with
        | some (_, value) => pure value
        | none => throw s!"module parameter '{parameter.name}' does not have a supported natural-number default"
      moduleParameters := moduleParameters ++ [{ name := parameter.name, defaultValue }]
  let nativeItems ← if retainParameters then
      lowerNativeItems parameterNames nativeLoopVariables declaredArrayNames nativeSourceItems
    else pure []
  let expandedItems ← if retainParameters then pure coreSourceItems
    else expandGenerateBlocks paramVals coreSourceItems
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
  if svMod.items.any containsForItem then
    throw "a procedural for-loop remained after safe unrolling/native retention; refusing to execute its body once"
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
        let ty := .bitVector (localParamPackedWidth param)
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
  for item in svMod.items do
    match item with
    | .paramDecl param =>
      if param.isLocal then
        let width := localParamPackedWidth param
        let val ← if retainParameters then
            -- A localparam declaration supplies a real packed destination just
            -- like an assignment.  Route both raw exact-Nat forms and the
            -- backend's explicit `$unsigned(width'(...))` wrapper through the
            -- same checked lowering instead of imposing a second, narrower
            -- recognition rule here.
            lowerExprAtWidth param.value parameterNames width
          else pure (match paramVals.find? fun (n, _) => n == param.name with
            | some (_, v) => .const (Int.ofNat v) width
            | none => lowerExpr param.value)
        body := body ++ [.assign param.name val]
      else
        pure ()
    | _ => pure ()

  for item in svMod.items do
    match item with
    | .contAssign lhs rhs =>
      let isCanonicalMemoryRead := retainParameters && match rhs with
        | .index (.ident arrayName) _ => arrayRegNames.contains arrayName
        | _ => false
      if isCanonicalMemoryRead then
        -- The corresponding Stmt.memory owns this read port.  Emitting a
        -- second ordinary assignment would create duplicate drivers in SV.
        pure ()
      else
        match exprToName lhs with
        | some name =>
            let rhs ← lowerExprAtWidth rhs parameterNames (env.getHWType name).width
            body := body ++ [.assign name rhs]
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
              if retainParameters &&
                  (collectReadNamesExpr rhs).any parameterNames.contains then
                match rhs with
                | .unary .unsigned (.sizedCast _ (.lit _))
                | .unary .unsigned (.sizedCast _ (.unary .neg (.lit _))) =>
                    pure ()
                | _ =>
                    throw s!"parameter-dependent reset value for register '{name}' cannot be represented by the concrete IR register initializer; use a canonical width-cast literal (including zero/all-ones) or specialize the module"
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
      let initExpr ← lowerExprAtWidth initExpr parameterNames (env.getHWType name).width
      body := body ++ [.assign name initExpr]
    | .regDecl name width (some arraySize) _ =>
      -- Array reg → Stmt.memory for JIT memory access
      -- Do NOT add to wires list — Stmt.memory creates the class member.
      if retainParameters then
        let dataWidth := match width with
          | none => (1 : DimExpr)
          | some (hi, lo) => rangeWidth hi lo
        let mut writes : List (String × SVExpr × SVExpr × Option SVExpr) := []
        for prevItem in svMod.items do
          match prevItem with
          | .alwaysBlock (.posedge clock) stmts =>
            let arrayWrites ← collectNativeArrayWrites name none stmts
            for (idx, data, condition) in arrayWrites do
              writes := writes ++ [(clock, idx, data, condition)]
          | _ => pure ()
        let mut reads : List (String × SVExpr) := []
        for prevItem in svMod.items do
          match prevItem with
          | .contAssign (.ident readData) (.index (.ident arrayName) readAddr) =>
            if arrayName == name then
              reads := reads ++ [(readData, readAddr)]
          | _ => pure ()
        let (clock, writeAddrSV, writeDataSV, writeCondition) ← match writes with
          | [write] => pure write
          | [] =>
            throw s!"native memory '{name}' requires exactly one full-word write in a posedge block; found none"
          | _ =>
            throw s!"native memory '{name}' has multiple writes or write sites; canonical 1R1W lowering requires exactly one"
        let (readData, readAddrSV) ← match reads with
          | [read] => pure read
          | [] =>
            throw s!"native memory '{name}' requires exactly one combinational read assignment; found none"
          | _ =>
            throw s!"native memory '{name}' has multiple combinational reads; canonical 1R1W lowering requires exactly one"
        let expressionWidth? := fun expression => match expression with
          | .ident signal =>
            let declared := env.portWidths.any (·.1 == signal) ||
              env.wireWidths.any (·.1 == signal)
            if declared then some (env.getHWType signal).width else none
          | .slice _ hi lo => some (rangeWidth hi lo)
          | .sizedCast target _ => some target
          | .lit (.decimal (some bits) _)
          | .lit (.hex (some bits) _)
          | .lit (.binary (some bits) _) => some (.literal bits)
          | _ => none
        let writeAddrWidth ← match expressionWidth? writeAddrSV with
          | some value => pure value
          | none => throw s!"cannot determine the write-address width of native memory '{name}'"
        let readAddrWidth ← match expressionWidth? readAddrSV with
          | some value => pure value
          | none => throw s!"cannot determine the read-address width of native memory '{name}'"
        if writeAddrWidth != readAddrWidth then
          throw s!"native memory '{name}' has different read/write address widths ({readAddrWidth} vs {writeAddrWidth})"
        let writeEnable := match writeCondition with
          | some condition => lowerExpr condition parameterNames
          | none => .const 1 1
        let writeAddr ← lowerExprAtWidth writeAddrSV parameterNames writeAddrWidth
        let writeData ← lowerExprAtWidth writeDataSV parameterNames dataWidth
        let readAddr ← lowerExprAtWidth readAddrSV parameterNames readAddrWidth
        body := body ++ [.memory name writeAddrWidth dataWidth arraySize clock
          writeAddr writeData writeEnable readAddr
          readData true]
      else
        let dataWidth ← match widthToBits width with
          | some value => pure value
          | none => throw s!"memory '{name}' has a symbolic data width; specialize it before concrete memory lowering"
        let concreteDepth ← match arraySize.toNat? with
          | some value => pure value
          | none => throw s!"memory '{name}' has a symbolic depth; specialize it before concrete memory lowering"
        let addrWidth := Nat.log2 concreteDepth +
          (if Nat.isPowerOfTwo concreteDepth then 0 else 1)
        -- Legacy concrete lowering keeps its byte-lane compatibility path.
        let mut writeAddr : Expr := .const 0 addrWidth
        let mut writeData : Expr := .const 0 dataWidth
        let mut writeEnable : Expr := .const 0 1
        for prevItem in svMod.items do
          match prevItem with
          | .alwaysBlock (.posedge _) stmts =>
            let arrayWrites := collectArrayWrites name stmts
            if !arrayWrites.isEmpty then
              for (idx, data, cond) in arrayWrites do
                writeAddr := lowerExpr idx
                writeData := lowerExpr data
                writeEnable := match cond with
                  | some c => lowerExpr c
                  | none => .const 1 1
            else
              let byteLanes := collectByteLaneWrites name stmts
              match byteLanes with
              | lane0 :: _ =>
                let addr := lowerExpr lane0.addr
                writeAddr := addr
                writeData := buildByteStrobeWrite name addr byteLanes
                let enableExpr := byteLanes.foldl (fun acc lane =>
                  let c := lowerExpr lane.cond
                  if acc == Expr.const 0 1 then c else Expr.op .or [acc, c]
                ) (Expr.const 0 1)
                writeEnable := enableExpr
              | [] => pure ()
          | _ => pure ()
        body := body ++ [.memory name addrWidth dataWidth concreteDepth "clk"
          writeAddr writeData writeEnable
          (.const 0 addrWidth) s!"{name}_rdata" true]
        wires := wires ++ [{ name := s!"{name}_rdata", ty := widthToHWType width }]
    | .instantiation modName instName conns paramOvr =>
      let irConns := conns.map fun (portName, expr) =>
        (portName, lowerExpr expr parameterNames)
      let mut irOverrides : List (String × DimExpr) := []
      for (name, value) in paramOvr do
        let dimension ← match retainedNatValueExpr? value with
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

  let materialize := materializeParameterRefs parameterNames
  let result : Module := {
    name := svMod.name
    parameters := moduleParameters
    inputs := inputs
    outputs := outputs
    wires := dedupWires
    body := (topoSortBody dedupBody).map fun statement => match statement with
      | .assign lhs rhs => .assign lhs (materialize rhs)
      | .register output clock reset input initValue =>
          .register output clock reset (materialize input) initValue
      | .memory name addrWidth dataWidth depth clock writeAddr writeData writeEnable
          readAddr readData comboRead =>
          .memory name addrWidth dataWidth depth clock (materialize writeAddr)
            (materialize writeData) (materialize writeEnable) (materialize readAddr)
            readData comboRead
      | .inst moduleName instanceName connections overrides =>
          .inst moduleName instanceName
            (connections.map fun (portName, expression) =>
              (portName, materialize expression)) overrides
    nativeItems := nativeItems
    assertions := assertions.map fun (name, expression) => (name, materialize expression)
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
  -- The legacy flattener only understands normalized `Stmt` hierarchy.  Keep
  -- native generate/procedural designs intact rather than silently discarding
  -- branch-local declarations or processes.
  if design.modules.any (fun module_ => !module_.nativeItems.isEmpty) then
    return design
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
            | .memory n _ _ _ _ _ _ _ _ _ _ => some n | _ => none
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
              | .memory name aw dw depth clk wa wd we ra rd combo =>
                .memory s!"{instName}_{name}" aw dw depth s!"{instName}_{clk}"
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
      | .memory n _ _ _ _ _ _ _ _ _ _ => some n | _ => none
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
      | .memory n aw dw depth clk wa wd we ra rd combo =>
        .memory n aw dw depth clk (genExpr wa) (genExpr wd) (genExpr we) (genExpr ra) rd combo

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

private partial def collectInstantiatedModuleNames
    (items : List SVModuleItem) : List String :=
  items.flatMap fun item => match item with
  | .instantiation moduleName _ _ _ => [moduleName]
  | .generateBlock _ thenItems elseItems =>
      collectInstantiatedModuleNames thenItems ++
        collectInstantiatedModuleNames elseItems
  | _ => []

/-- Lower a full SV design to Sparkle IR -/
def lowerDesign (svDesign : SVDesign) (retainParameters : Bool := false) : Except String Design := do
  let mut modules : List Module := []
  for m in svDesign.modules do
    let lowered ← lowerModule m [] retainParameters
    modules := modules ++ [lowered]
  let instantiatedNames := svDesign.modules.flatMap fun module_ =>
    collectInstantiatedModuleNames module_.items
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
