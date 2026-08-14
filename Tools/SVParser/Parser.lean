/-
  SystemVerilog Parser — Recursive descent for synthesizable RTL

  Supports: module with #(parameter), input/output/output reg, wire/reg,
  assign, always @(posedge)/always @*, if/else, case/casez,
  localparam, integer, for loops, generate if, (* attributes *),
  `ifdef/`endif preprocessing, multiple modules.
-/

import Tools.SVParser.AST
import Tools.SVParser.Lexer

open Tools.SVParser.AST
open Tools.SVParser.Lexer

namespace Tools.SVParser.Parser

-- ============================================================================
-- Preprocessor: strip `ifdef/`endif blocks (take the default/else branch)
-- and remove `timescale, `define, `default_nettype, (* attributes *)
-- ============================================================================

/-- Simple preprocessor: remove ifdef blocks (keeping else branch),
    strip `timescale/`define/`default_nettype directives and (* ... *) attributes -/
def preprocess (input : String) : String := Id.run do
  let lines := input.splitOn "\n"
  let mut result : List String := []
  let mut ifdefDepth : Nat := 0
  let mut skipDepth : Nat := 0  -- depth at which we started skipping (0 = not skipping)
  for line in lines do
    let trimmed := line.trimLeft
    if trimmed.startsWith "`ifdef" then
      ifdefDepth := ifdefDepth + 1
      if skipDepth == 0 then
        -- `ifdef X: macros are not defined, skip this branch
        skipDepth := ifdefDepth
    else if trimmed.startsWith "`ifndef" then
      ifdefDepth := ifdefDepth + 1
      -- `ifndef X: macros are not defined, KEEP this branch (don't skip)
    else if trimmed.startsWith "`elsif" then
      if skipDepth == ifdefDepth then
        -- Was skipping this level: check elsif (treat as still skipping for simplicity)
        pure ()
      else if skipDepth == 0 then
        -- Was not skipping: now start skipping (elsif branch of a kept ifndef)
        skipDepth := ifdefDepth
    else if trimmed.startsWith "`else" then
      if skipDepth == ifdefDepth then
        skipDepth := 0  -- Was skipping: take the else branch
      else if skipDepth == 0 then
        skipDepth := ifdefDepth  -- Was keeping: skip the else branch
    else if trimmed.startsWith "`endif" then
      if skipDepth == ifdefDepth then
        skipDepth := 0
      if ifdefDepth > 0 then ifdefDepth := ifdefDepth - 1
    else if skipDepth > 0 then
      pure ()  -- skip this line
    else if trimmed.startsWith "`timescale" || trimmed.startsWith "`define" ||
            trimmed.startsWith "`default_nettype" ||
            trimmed.startsWith "`PICORV32" ||
            trimmed.startsWith "`assert" then
      pure ()  -- skip directive / macro invocation
    else if trimmed.startsWith "`debug" then
      -- Replace debug macro with empty statement (semicolon)
      result := result ++ [";"]
    else
      -- Remove (* ... *) attributes
      let cleaned := removeAttributes line
      result := result ++ [cleaned]
  "\n".intercalate result
where
  removeAttributes (s : String) : String := Id.run do
    let mut result := s
    -- Remove (* ... *) attributes
    let mut cont := true
    while cont do
      match result.splitOn "(*" with
      | [_] => cont := false
      | before :: rest =>
        let afterStar := "*".intercalate rest
        match afterStar.splitOn "*)" with
        | _ :: after => result := before ++ " ".intercalate after
        | [] => cont := false
      | [] => cont := false
    -- Remove inline `MACRONAME (backtick macros used as modifiers)
    result := result.replace "`FORMAL_KEEP " ""
    result := result.replace "`FORMAL_KEEP" ""
    result

-- ============================================================================
-- Expression parsing (all mutually recursive)
-- ============================================================================

private def literalToNaturalDim? : SVLiteral → Option Sparkle.IR.Type.DimExpr
  | .decimal width value | .hex width value | .binary width value =>
      match width with
      | some 0 => none
      | some bits => some (.literal (value % (2 ^ bits)))
      | none => some (.literal value)
  | .unknown _ => none

private inductive NatWrapperMode where
  | exactWorkWidth
  | metaUInt64

private def natWrapperWidthMatches (mode : NatWrapperMode)
    (width recovered : Sparkle.IR.Type.DimExpr) : Bool :=
  match mode with
  | .exactWorkWidth => width == recovered.natValueBitWidthBound
  | .metaUInt64 =>
      width == .literal 64 &&
        match recovered.natValueBitWidthBound.toNat? with
        | some bits => bits <= 64
        | none => false

/- Recover the two canonical wrapper layers emitted for a mathematical Nat.
   Value wrappers must exactly match the conservative work width.  Meta-width
   wrappers are fixed at 64 bits and accepted only when the recovered value is
   statically proven to fit; arbitrary narrowing casts are never erased. -/
mutual
private partial def recoverNatWrapped? (mode : NatWrapperMode) :
    SVExpr → Option Sparkle.IR.Type.DimExpr
  | .unary .unsigned (.sizedCast width value) => do
      let recovered ← recoverNatRawWith? mode value
      if natWrapperWidthMatches mode width recovered then some recovered else none
  | _ => none

private partial def recoverNatRawWith? (mode : NatWrapperMode) :
    SVExpr → Option Sparkle.IR.Type.DimExpr
  | .lit literal => literalToNaturalDim? literal
  | .ident name => some (.param name)
  | .binary .add lhs rhs =>
      return Sparkle.IR.Type.DimExpr.mkAdd
        (← recoverNatWrapped? mode lhs) (← recoverNatWrapped? mode rhs)
  | .binary .mul lhs rhs =>
      return Sparkle.IR.Type.DimExpr.mkMul
        (← recoverNatWrapped? mode lhs) (← recoverNatWrapped? mode rhs)
  | .binary .pow lhs rhs =>
      return Sparkle.IR.Type.DimExpr.mkPow
        (← recoverNatWrapped? mode lhs) (← recoverNatWrapped? mode rhs)
  | .binary .shl lhs rhs =>
      return Sparkle.IR.Type.DimExpr.mkShl
        (← recoverNatWrapped? mode lhs) (← recoverNatWrapped? mode rhs)
  | .binary .shr lhs rhs =>
      return Sparkle.IR.Type.DimExpr.mkShr
        (← recoverNatWrapped? mode lhs) (← recoverNatWrapped? mode rhs)
  | .binary .bitAnd lhs rhs =>
      return Sparkle.IR.Type.DimExpr.mkBitAnd
        (← recoverNatWrapped? mode lhs) (← recoverNatWrapped? mode rhs)
  | .binary .bitOr lhs rhs =>
      return Sparkle.IR.Type.DimExpr.mkBitOr
        (← recoverNatWrapped? mode lhs) (← recoverNatWrapped? mode rhs)
  | .binary .bitXor lhs rhs =>
      return Sparkle.IR.Type.DimExpr.mkBitXor
        (← recoverNatWrapped? mode lhs) (← recoverNatWrapped? mode rhs)
  -- Canonical totalization of Sparkle's Nat `clog2`: both zero and one map
  -- to zero, independent of downstream-tool behavior for `$clog2(0)`.
  | .ternary (.binary .le value (.lit (.decimal none 1)))
      (.lit (.decimal none 0)) (.unary .clog2 thenValue) => do
      if value == thenValue then
        return Sparkle.IR.Type.DimExpr.mkClog2 (← recoverNatWrapped? mode value)
      else none
  -- Canonical total Nat subtraction emitted by the backend.
  | .ternary (.binary .ge lhs rhs) (.binary .sub thenLhs thenRhs)
      (.lit (.decimal none 0)) => do
      if lhs == thenLhs && rhs == thenRhs then
        return Sparkle.IR.Type.DimExpr.mkSub
          (← recoverNatWrapped? mode lhs) (← recoverNatWrapped? mode rhs)
      else none
  -- `Nat.div a 0 = 0`.
  | .ternary (.binary .eq rhs (.lit (.decimal none 0)))
      (.lit (.decimal none 0)) (.binary .div lhs thenRhs) => do
      if rhs == thenRhs then
        return Sparkle.IR.Type.DimExpr.mkDiv
          (← recoverNatWrapped? mode lhs) (← recoverNatWrapped? mode rhs)
      else none
  -- `Nat.mod a 0 = a`.
  | .ternary (.binary .eq rhs (.lit (.decimal none 0))) lhsElse
      (.binary .mod lhs thenRhs) => do
      if rhs == thenRhs && lhsElse == lhs then
        return Sparkle.IR.Type.DimExpr.mkMod
          (← recoverNatWrapped? mode lhs) (← recoverNatWrapped? mode rhs)
      else none
  | .ternary (.binary .lt lhs rhs) then_ else_ => do
      if lhs == then_ && rhs == else_ then
        return Sparkle.IR.Type.DimExpr.mkMin
          (← recoverNatWrapped? mode lhs) (← recoverNatWrapped? mode rhs)
      else none
  | .ternary (.binary .gt lhs rhs) then_ else_ => do
      if lhs == then_ && rhs == else_ then
        return Sparkle.IR.Type.DimExpr.mkMax
          (← recoverNatWrapped? mode lhs) (← recoverNatWrapped? mode rhs)
      else none
  | _ => none
end

def recoverNatWorkExpr? (expression : SVExpr) : Option Sparkle.IR.Type.DimExpr :=
  recoverNatWrapped? .exactWorkWidth expression

private def recoverMetaNat64Expr? (expression : SVExpr) :
    Option Sparkle.IR.Type.DimExpr :=
  recoverNatWrapped? .metaUInt64 expression

private partial def provablyPositiveNatExpr : SVExpr → Bool
  | .lit literal =>
      match literalToNaturalDim? literal >>= (fun value => value.toNat?) with
      | some value => value > 0
      | none => false
  | .unary .unsigned value | .sizedCast _ value => provablyPositiveNatExpr value
  | .binary .add lhs rhs | .binary .bitOr lhs rhs =>
      provablyPositiveNatExpr lhs || provablyPositiveNatExpr rhs
  | .binary .mul lhs rhs =>
      provablyPositiveNatExpr lhs && provablyPositiveNatExpr rhs
  | .binary .shl lhs _ => provablyPositiveNatExpr lhs
  | .binary .pow base exponent =>
      provablyPositiveNatExpr base ||
        match exponent with
        | .lit literal =>
            match literalToNaturalDim? literal with
            | some value => value.toNat? == some 0
            | none => false
        | _ => false
  | .ternary _ then_ else_ =>
      provablyPositiveNatExpr then_ && provablyPositiveNatExpr else_
  | _ => false

private def provablyNonUnderflowingSub (lhs rhs : SVExpr) : Bool :=
  if lhs == rhs then true
  else
    match rhs with
    | .lit rhsLiteral =>
      match literalToNaturalDim? rhsLiteral with
      | some value =>
        match value.toNat? with
        | some 0 => true
        | some 1 => provablyPositiveNatExpr lhs
        | some rhsValue =>
            match lhs with
            | .lit lhsLiteral =>
                match literalToNaturalDim? lhsLiteral >>= (fun value => value.toNat?) with
                | some lhsValue => lhsValue >= rhsValue
                | none => false
            | _ => false
        | none => false
      | none => false
    | _ => false

private partial def exprToDimExprCore? (allowDeclarationClamp : Bool)
    (expression : SVExpr) : Option Sparkle.IR.Type.DimExpr :=
  if let some recovered := recoverNatWorkExpr? expression then
    some recovered
  else if let some recovered := recoverMetaNat64Expr? expression then
    some recovered
  else match expression with
  | .lit literal =>
      -- An explicitly sized SystemVerilog literal is truncated to that size
      -- before it participates in a constant expression.  Retaining the raw
      -- parser value would silently change, for example, `4'd31 + K` into
      -- `31 + K` instead of `(31 % 16) + K`.
      literalToNaturalDim? literal
  | .ident name => some (.param name)
  -- Canonical Sparkle declaration clamp.  The lower and upper checks keep an
  -- invalid unsigned parameter override from asking an SV frontend to build a
  -- zero- or multi-billion-bit range before the adjacent validation guard can
  -- report it.  This shape is recognized only in declaration/cast-width
  -- contexts; ordinary value ternaries retain their value semantics.
  | .ternary
      (.binary .logAnd
        (.binary .gt positive (.lit (.decimal none 0)))
        (.binary .le bounded (.lit (.decimal none 1048576))))
      then_ (.lit (.decimal none 1)) => do
      if allowDeclarationClamp && positive == bounded && positive == then_ then
        recoverNatWorkExpr? positive
      else none
  -- Sparkle's backend clamps a hardware dimension before placing it in a
  -- declaration range: `(d > 0 ? d : 1)`.  The guard emitted alongside the
  -- declaration rejects the invalid branch, so recover the native dimension
  -- here instead of baking the compatibility clamp into the IR.
  | .ternary (.binary .gt condition (.lit (.decimal none 0))) then_ (.lit (.decimal none 1)) => do
      if allowDeclarationClamp && condition == then_ then
        recoverNatWorkExpr? condition
      else none
  -- Sparkle caps the temporary width used to evaluate retained Nat values.
  -- Invalid overrides are paired with a canonical fatal generate guard; in a
  -- cast-width context recover the mathematical bound, never the fallback 1.
  | .ternary (.binary .le condition (.lit (.decimal none 1048576))) then_
      (.lit (.decimal none 1)) => do
      if allowDeclarationClamp && condition == then_ then
        recoverMetaNat64Expr? condition
      else none
  | .binary .add lhs rhs => return (Sparkle.IR.Type.DimExpr.mkAdd (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs))
  | .binary .sub lhs rhs => do
      unless allowDeclarationClamp || provablyNonUnderflowingSub lhs rhs do
        none
      return (Sparkle.IR.Type.DimExpr.mkSub (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs))
  | .binary .mul lhs rhs => return (Sparkle.IR.Type.DimExpr.mkMul (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs))
  | .binary .div lhs rhs => do
      unless allowDeclarationClamp || provablyPositiveNatExpr rhs do none
      return (Sparkle.IR.Type.DimExpr.mkDiv (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs))
  | .binary .mod lhs rhs => do
      unless allowDeclarationClamp || provablyPositiveNatExpr rhs do none
      return (Sparkle.IR.Type.DimExpr.mkMod (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs))
  | .binary .pow lhs rhs => return (Sparkle.IR.Type.DimExpr.mkPow (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs))
  | .binary .shl lhs rhs => return (Sparkle.IR.Type.DimExpr.mkShl (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs))
  | .binary .shr lhs rhs => return (Sparkle.IR.Type.DimExpr.mkShr (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs))
  | .binary .bitAnd lhs rhs => return (Sparkle.IR.Type.DimExpr.mkBitAnd (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs))
  | .binary .bitOr lhs rhs => return (Sparkle.IR.Type.DimExpr.mkBitOr (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs))
  | .binary .bitXor lhs rhs => return (Sparkle.IR.Type.DimExpr.mkBitXor (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs))
  | .unary .clog2 value => return Sparkle.IR.Type.DimExpr.mkClog2 (← exprToDimExprCore? allowDeclarationClamp value)
  -- SystemVerilog rendering of Nat subtraction: `(lhs >= rhs) ? lhs-rhs : 0`.
  | .ternary (.binary .ge lhs rhs) (.binary .sub thenLhs thenRhs)
      (.lit (.decimal none 0)) => do
      if lhs == thenLhs && rhs == thenRhs then
        return Sparkle.IR.Type.DimExpr.mkSub (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs)
      else none
  -- Canonical renderings produced by `emitDimExpr` for total Nat operations.
  | .ternary (.binary .eq rhs (.lit (.decimal none 0)))
      (.lit (.decimal none 0)) (.binary .div lhs thenRhs) => do
      if rhs == thenRhs then
        return Sparkle.IR.Type.DimExpr.mkDiv (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs)
      else none
  | .ternary (.binary .eq rhs (.lit (.decimal none 0))) lhsElse
      (.binary .mod lhs thenRhs) => do
      if rhs == thenRhs && lhsElse == lhs then
        return Sparkle.IR.Type.DimExpr.mkMod (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs)
      else none
  | .ternary (.binary .lt lhs rhs) then_ else_ => do
      if lhs == then_ && rhs == else_ then
        return Sparkle.IR.Type.DimExpr.mkMin (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs)
      else none
  | .ternary (.binary .gt lhs rhs) then_ else_ => do
      if lhs == then_ && rhs == else_ then
        return Sparkle.IR.Type.DimExpr.mkMax (← exprToDimExprCore? allowDeclarationClamp lhs) (← exprToDimExprCore? allowDeclarationClamp rhs)
      else none
  | _ => none

/-- Translate a packed-range/cast-width constant expression.  This entry point
    alone recognizes Sparkle's positive declaration clamp `(d > 0 ? d : 1)`. -/
private partial def containsIdentifierExpr : SVExpr → Bool
  | .ident _ => true
  | .unary _ value => containsIdentifierExpr value
  | .binary _ lhs rhs => containsIdentifierExpr lhs || containsIdentifierExpr rhs
  | .ternary condition then_ else_ =>
      containsIdentifierExpr condition || containsIdentifierExpr then_ ||
        containsIdentifierExpr else_
  | .index array index => containsIdentifierExpr array || containsIdentifierExpr index
  | .slice value _ _ => containsIdentifierExpr value
  | .partSelectPlus value base width =>
      containsIdentifierExpr value || containsIdentifierExpr base ||
        containsIdentifierExpr width
  | .concat values => values.any containsIdentifierExpr
  | .repeat_ count value => containsIdentifierExpr count || containsIdentifierExpr value
  | .sizedCast _ value => containsIdentifierExpr value
  | .lit _ => false

/-- Raw packed SV arithmetic is not automatically mathematical Nat arithmetic.
    In particular right shift/division/modulo observe any earlier fixed-width
    truncation, while a parameterized raw shift/power can overflow its 32-bit
    operands before a declaration sees the result.  Backend-emitted exact work
    wrappers are recognized first and remain fully supported. -/
private partial def hasUnsafeRawDimensionSizing (expression : SVExpr) : Bool :=
  if (recoverNatWorkExpr? expression).isSome ||
      (recoverMetaNat64Expr? expression).isSome then false
  else match expression with
  | .binary .shr _ _ | .binary .div _ _ | .binary .mod _ _ => true
  | .binary .shl lhs rhs | .binary .pow lhs rhs =>
      containsIdentifierExpr lhs || containsIdentifierExpr rhs ||
        hasUnsafeRawDimensionSizing lhs || hasUnsafeRawDimensionSizing rhs
  | .unary _ value => hasUnsafeRawDimensionSizing value
  | .binary _ lhs rhs =>
      hasUnsafeRawDimensionSizing lhs || hasUnsafeRawDimensionSizing rhs
  | .ternary condition then_ else_ =>
      hasUnsafeRawDimensionSizing condition || hasUnsafeRawDimensionSizing then_ ||
        hasUnsafeRawDimensionSizing else_
  | .index array index =>
      hasUnsafeRawDimensionSizing array || hasUnsafeRawDimensionSizing index
  | .slice value _ _ => hasUnsafeRawDimensionSizing value
  | .partSelectPlus value base width =>
      hasUnsafeRawDimensionSizing value || hasUnsafeRawDimensionSizing base ||
        hasUnsafeRawDimensionSizing width
  | .concat values => values.any hasUnsafeRawDimensionSizing
  | .repeat_ count value =>
      hasUnsafeRawDimensionSizing count || hasUnsafeRawDimensionSizing value
  | .sizedCast _ value => hasUnsafeRawDimensionSizing value
  | .lit _ | .ident _ => false

partial def exprToDimExpr? (expression : SVExpr) : Option Sparkle.IR.Type.DimExpr :=
  if hasUnsafeRawDimensionSizing expression then none
  else exprToDimExprCore? true expression

/-- Translate a mathematical Nat value expression.  Declaration clamps are
    deliberately not erased here: in a data path `(K > 0 ? K : 1)` really has
    value one when K is zero. -/
private partial def containsUnboundedRawNatOp (expression : SVExpr) : Bool :=
  if (recoverNatWorkExpr? expression).isSome ||
      (recoverMetaNat64Expr? expression).isSome then false
  else match expression with
  | .binary .add _ _ | .binary .sub _ _ | .binary .mul _ _
  | .binary .div _ _ | .binary .mod _ _ | .binary .pow _ _
  | .binary .shl _ _ | .binary .shr _ _
  | .binary .bitAnd _ _ | .binary .bitOr _ _ | .binary .bitXor _ _ => true
  | .unary _ value => containsUnboundedRawNatOp value
  | .binary _ lhs rhs =>
      containsUnboundedRawNatOp lhs || containsUnboundedRawNatOp rhs
  | .ternary condition then_ else_ =>
      containsUnboundedRawNatOp condition ||
        containsUnboundedRawNatOp then_ || containsUnboundedRawNatOp else_
  | .index array index =>
      containsUnboundedRawNatOp array || containsUnboundedRawNatOp index
  | .slice value _ _ => containsUnboundedRawNatOp value
  | .partSelectPlus value base width =>
      containsUnboundedRawNatOp value || containsUnboundedRawNatOp base ||
        containsUnboundedRawNatOp width
  | .concat values => values.any containsUnboundedRawNatOp
  | .repeat_ count value =>
      containsUnboundedRawNatOp count || containsUnboundedRawNatOp value
  | .sizedCast _ _ => false
  | .lit _ | .ident _ => false

partial def exprToNatExpr? (expression : SVExpr) : Option Sparkle.IR.Type.DimExpr :=
  if let some recovered := recoverNatWorkExpr? expression then some recovered
  else if let some recovered := recoverMetaNat64Expr? expression then some recovered
  else if containsUnboundedRawNatOp expression then none
  else exprToDimExprCore? false expression

/-- Parameter-only arithmetic inside an explicit sized cast is evaluated in
    that cast's packed context.  It may therefore use shifts/powers that are
    unsafe in a raw self-determined RHS or generate condition. -/
partial def exprToSizedNatExpr? (expression : SVExpr) : Option Sparkle.IR.Type.DimExpr :=
  exprToDimExprCore? false expression

mutual

partial def parseExpr : P SVExpr := parseTernary

partial def parseTernary : P SVExpr := do
  let e ← parseLogOr
  match ← attempt qmark with
  | some _ => let t ← parseExpr; colon; let el ← parseExpr; pure (SVExpr.ternary e t el)
  | none => pure e

partial def parseLogOr : P SVExpr := do
  let mut e ← parseLogAnd
  let mut cont := true
  while cont do
    match ← attempt (op2 "||") with
    | some _ => let rhs ← parseLogAnd; e := SVExpr.binary .logOr e rhs
    | none => cont := false
  pure e

partial def parseLogAnd : P SVExpr := do
  let mut e ← parseBitOr
  let mut cont := true
  while cont do
    match ← attempt (op2 "&&") with
    | some _ => let rhs ← parseBitOr; e := SVExpr.binary .logAnd e rhs
    | none => cont := false
  pure e

partial def parseBitOr : P SVExpr := do
  let mut e ← parseBitXor
  let mut cont := true
  while cont do
    match ← attempt (do
      let _ ← token (matchStr "|")
      let next ← peekChar
      if next == some '|' then fail "||"
      pure ()) with
    | some _ => let rhs ← parseBitXor; e := SVExpr.binary .bitOr e rhs
    | none => cont := false
  pure e

partial def parseBitXor : P SVExpr := do
  let mut e ← parseBitAnd
  let mut cont := true
  while cont do
    match ← attempt (token (matchStr "^")) with
    | some _ => let rhs ← parseBitAnd; e := SVExpr.binary .bitXor e rhs
    | none => cont := false
  pure e

partial def parseBitAnd : P SVExpr := do
  let mut e ← parseEquality
  let mut cont := true
  while cont do
    match ← attempt (do
      let _ ← token (matchStr "&")
      let next ← peekChar
      if next == some '&' then fail "&&"
      pure ()) with
    | some _ => let rhs ← parseEquality; e := SVExpr.binary .bitAnd e rhs
    | none => cont := false
  pure e

partial def parseEquality : P SVExpr := do
  let mut e ← parseRelational
  let mut cont := true
  while cont do
    match ← attempt (op2 "!=") with
    | some _ => let rhs ← parseRelational; e := SVExpr.binary .neq e rhs
    | none =>
      match ← attempt (op2 "==") with
      | some _ => let rhs ← parseRelational; e := SVExpr.binary .eq e rhs
      | none => cont := false
  pure e

partial def parseRelational : P SVExpr := do
  let mut e ← parseShift
  let mut cont := true
  while cont do
    match ← attempt (op2 "<=") with
    | some _ => let rhs ← parseShift; e := SVExpr.binary .le e rhs
    | none =>
      match ← attempt (op2 ">=") with
      | some _ => let rhs ← parseShift; e := SVExpr.binary .ge e rhs
      | none =>
        match ← attempt (do let _ ← token (matchStr "<"); let next ← peekChar
                            if next == some '<' then fail "<<"; pure ()) with
        | some _ => let rhs ← parseShift; e := SVExpr.binary .lt e rhs
        | none =>
          match ← attempt (do let _ ← token (matchStr ">"); let next ← peekChar
                              if next == some '>' then fail ">>"; pure ()) with
          | some _ => let rhs ← parseShift; e := SVExpr.binary .gt e rhs
          | none => cont := false
  pure e

partial def parseShift : P SVExpr := do
  let mut e ← parseAdd
  let mut cont := true
  while cont do
    match ← attempt (op2 ">>>") with
    | some _ => let rhs ← parseAdd; e := SVExpr.binary .asr e rhs
    | none =>
      match ← attempt (op2 "<<") with
      | some _ => let rhs ← parseAdd; e := SVExpr.binary .shl e rhs
      | none =>
        match ← attempt (op2 ">>") with
        | some _ => let rhs ← parseAdd; e := SVExpr.binary .shr e rhs
        | none => cont := false
  pure e

partial def parseAdd : P SVExpr := do
  let mut e ← parseMul
  let mut cont := true
  while cont do
    match ← attempt (token (matchStr "+")) with
    | some _ => let rhs ← parseMul; e := SVExpr.binary .add e rhs
    | none =>
      match ← attempt (do let _ ← token (matchStr "-"); parseMul) with
      | some rhs => e := SVExpr.binary .sub e rhs
      | none => cont := false
  pure e

partial def parseMul : P SVExpr := do
  let mut e ← parsePower
  let mut cont := true
  while cont do
    -- A single `*` is multiplication; do not consume the first character of
    -- the higher-precedence `**` token.
    match ← attempt (do
      let _ ← token (matchStr "*")
      if (← peekChar) == some '*' then fail "exponentiation"
      pure ()) with
    | some _ => let rhs ← parsePower; e := SVExpr.binary .mul e rhs
    | none =>
      match ← attempt (token (matchStr "/")) with
      | some _ => let rhs ← parsePower; e := SVExpr.binary .div e rhs
      | none =>
        match ← attempt (token (matchStr "%")) with
        | some _ => let rhs ← parsePower; e := SVExpr.binary .mod e rhs
        | none => cont := false
  pure e

/-- Exponentiation binds more tightly than multiplication/division/modulo.
    SystemVerilog tools evaluate chained powers left-to-right, so retain that
    associativity as well.  Keeping it in `parseMul` made `2 * 3 ** K` parse as
    `(2 * 3) ** K`, which silently changed parameter constants. -/
partial def parsePower : P SVExpr := do
  let mut expression ← parseUnary
  let mut continuing := true
  while continuing do
    match ← attempt (op2 "**") with
    | some _ => expression := .binary .pow expression (← parseUnary)
    | none => continuing := false
  return expression

partial def parseUnary : P SVExpr := do
  let c ← peekChar
  match c with
  | some '!' => let _ ← token (matchStr "!"); let e ← parseUnary; pure (SVExpr.unary .logNot e)
  | some '~' => let _ ← token (matchStr "~"); let e ← parseUnary; pure (SVExpr.unary .bitNot e)
  | some '-' =>
    -- Unary minus: -expr (two's complement negation)
    match ← attempt (do let _ ← token (matchStr "-"); let e ← parseUnary; pure e) with
    | some e => pure (SVExpr.unary .neg e)
    | none => parsePrimary
  | some '&' =>
    -- Check for reduction AND (unary &) vs binary &
    match ← attempt (do
      let _ ← token (matchStr "&")
      let next ← peekChar
      if next == some '&' then fail "&&"
      let e ← parseUnary; pure e) with
    | some e => pure (SVExpr.unary .reductAnd e)
    | none => parsePrimaryPost
  | some '|' =>
    match ← attempt (do
      let _ ← token (matchStr "|")
      let next ← peekChar
      if next == some '|' then fail "||"
      let e ← parseUnary; pure e) with
    | some e => pure (SVExpr.unary .reductOr e)
    | none => parsePrimaryPost
  | _ => parsePrimaryPost

partial def parsePrimaryPost : P SVExpr := do
  let e ← parsePrimary
  parsePostfix e

partial def parsePostfix (e : SVExpr) : P SVExpr := do
  match ← attempt lbracket with
  | some _ =>
    -- Try [base +: width] part-select first
    -- Use parsePrimary (not parseExpr) for base to avoid consuming + as addition
    match ← attempt (do
      let base ← parsePrimary
      let _ ← token (matchStr "+:")
      let widthExpr ← parsePrimary
      rbracket
      pure (base, widthExpr)
    ) with
    | some (base, widthExpr) => parsePostfix (SVExpr.partSelectPlus e base widthExpr)
    | none =>
    -- Normal: [idx], [hi:lo]
    let idx ← parseExpr
    match ← attempt colon with
    | some _ =>
      let lo ← parseExpr
      rbracket
      let hiDim ← match exprToDimExpr? idx with
        | some dim => pure dim
        | none => fail "unsupported symbolic high bound in part-select"
      let loDim ← match exprToDimExpr? lo with
        | some dim => pure dim
        | none => fail "unsupported symbolic low bound in part-select"
      parsePostfix (SVExpr.slice e hiDim loDim)
    | none =>
      rbracket
      parsePostfix (SVExpr.index e idx)
  | none => pure e

partial def parsePrimary : P SVExpr := do
  let c ← peekChar
  match c with
  | some '{' =>
    lbrace
    let first ← parseExpr
    -- Check for replication: {n{expr}}
    match ← attempt lbrace with
    | some _ =>
      let inner ← parseExpr; rbrace; rbrace
      pure (SVExpr.repeat_ first inner)
    | none =>
      let mut args := [first]
      let mut cont := true
      while cont do
        match ← attempt comma with
        | some _ => let e ← parseExpr; args := args ++ [e]
        | none => cont := false
      rbrace; pure (SVExpr.concat args)
  | some '(' =>
    lparen
    let e ← parseExpr
    rparen
    -- A parenthesized constant expression followed by `'(value)` is a
    -- parameter-sized SystemVerilog cast (the form emitted by Sparkle).
    match ← attempt (token (matchStr "'")) with
    | some _ =>
      lparen
      let value ← parseExpr
      rparen
      let width ← match exprToDimExpr? e with
        | some dim => pure dim
        | none => fail "unsupported symbolic width in sized cast"
      pure (.sizedCast width value)
    | none => pure e
  | some '"' =>
    -- String literal: "text" → treat as constant 0 (debug strings not synthesizable)
    let _ ← nextChar  -- consume opening "
    let mut running := true
    while running do
      let ch ← nextChar
      if ch == '"' then running := false
    ws
    pure (SVExpr.lit (.decimal none 0))
  | some '$' =>
    -- System functions like $signed, $unsigned
    let _ ← nextChar  -- consume $
    let name ← identifier
    lparen; let arg ← parseExpr; rparen
    if name == "signed" then
      -- Preserve the cast in the AST even when its argument is an identifier.
      -- Assuming every identifier is 32 bits is unsound once a declaration
      -- width is a retained module parameter; the native lowering path can
      -- now diagnose this unsupported case instead of losing signedness.
      pure (SVExpr.unary .signed arg)
    else if name == "unsigned" then
      -- Sparkle IR packed expressions are intrinsically unsigned.  Retain the
      -- wrapper in the parser AST long enough for sized-cast lowering to avoid
      -- accidentally treating `$unsigned(-1)` as a sign-extending literal.
      pure (SVExpr.unary .unsigned arg)
    else if name == "clog2" then
      pure (SVExpr.unary .clog2 arg)
    else
      -- Unknown system functions are not transparent wrappers.  For example,
      -- `$bits(K)` evaluates to a type width, not to K.  Reject syntax outside
      -- the modeled subset instead of silently erasing the function call.
      fail s!"unsupported system function '${name}' in expression"
  | some '\'' =>
    -- Unsized literal: 'b0, 'bx, 'h0, etc.
    let _ ← token (matchStr "'")
    let base ← nextChar
    match base with
    | 'b' | 'B' =>
      skipUnderscoresAndSpaces
      let bd ← binDigitsStr
      if hasUnknownDigit bd then pure (SVExpr.lit (.unknown none))
      else pure (SVExpr.lit (.binary none (binToNat bd)))
    | 'h' | 'H' =>
      skipUnderscoresAndSpaces
      let hd ← hexDigitsWithUnderscore
      if hasUnknownDigit hd then pure (SVExpr.lit (.unknown none))
      else pure (SVExpr.lit (.hex none (hexToNat hd)))
    | 'd' | 'D' =>
      skipUnderscoresAndSpaces
      let dd ← digits
      pure (SVExpr.lit (.decimal none dd.toNat!))
    | _ => fail s!"unexpected base '{base}' in unsized literal"
  | some c' =>
    if isDigit c' then let lit ← numericLiteral; pure (SVExpr.lit lit)
    else if isAlpha c' then let name ← identifier; pure (SVExpr.ident name)
    else fail s!"unexpected char in expression: '{c'}'"
  | none => fail "unexpected end of input in expression"

-- Statement parsing
partial def parseStmtList : P (List SVStmt) := do
  match ← attempt (keyword "begin") with
  | some _ =>
    let _ ← attempt (do colon; let _ ← identifier; pure ())
    let stmts ← many parseStmt
    keyword "end"; pure stmts.toList
  | none =>
    -- Single statement or empty (;)
    match ← attempt semi with
    | some _ => pure []  -- empty statement
    | none => let s ← parseStmt; pure [s]

partial def parseStmt : P SVStmt := do
  -- Empty statement (standalone ;)
  match ← attempt semi with
  | some _ => return SVStmt.blockAssign (.lit (.decimal none 0)) (.lit (.decimal none 0))
  | none => pure ()
  match ← attempt (keyword "if") with
  | some _ =>
    lparen; let cond ← parseExpr; rparen
    let thenB ← parseStmtList
    let elseB ← match ← attempt (keyword "else") with
      | some _ => parseStmtList | none => pure []
    pure (SVStmt.ifElse cond thenB elseB)
  | none =>
    match ← attempt (keyword "case") with
    | some _ => parseCaseBody
    | none =>
      match ← attempt (keyword "casez") with
      | some _ => parseCaseBody
      | none =>
        match ← attempt (keyword "for") with
        | some _ =>
          lparen
          let init ← parseAssignStmt
          let cond ← parseExpr; semi
          let step ← parseAssignStmtNoSemi
          rparen
          let body ← parseStmtList
          pure (SVStmt.forLoop init cond step body)
        | none =>
          match ← attempt (keyword "assert") with
          | some _ =>
            lparen; let cond ← parseExpr; rparen; semi
            pure (SVStmt.assertStmt cond)
          | none => parseAssignStmt

partial def parseCaseBody : P SVStmt := do
  lparen; let expr ← parseExpr; rparen
  let mut arms : List (List SVExpr × List SVStmt) := []
  let mut default_ : Option (List SVStmt) := none
  let mut cont := true
  while cont do
    match ← attempt (keyword "endcase") with
    | some _ => cont := false
    | none =>
      match ← attempt (keyword "default") with
      | some _ =>
        colon; let stmts ← parseStmtList; default_ := some stmts
      | none =>
        -- Parse one or more comma-separated labels
        let first ← parseExpr
        let mut labels := [first]
        let mut moreLabels := true
        while moreLabels do
          match ← attempt comma with
          | some _ =>
            -- Check it's not a new case label (followed by :)
            match ← attempt (do let e ← parseExpr; pure e) with
            | some e => labels := labels ++ [e]
            | none => moreLabels := false
          | none => moreLabels := false
        colon
        let stmts ← parseStmtList
        arms := arms ++ [(labels, stmts)]
  pure (SVStmt.caseStmt expr arms default_)

partial def parseAssignStmt : P SVStmt := do
  -- Try non-blocking first: lhs <= expr ;
  match ← attempt (do
    let lhs ← parsePrimaryPost
    op2 "<="; let rhs ← parseExpr; semi
    pure (SVStmt.nonblockAssign lhs rhs)) with
  | some s => pure s
  | none =>
    let lhs ← parsePrimaryPost
    eqSign; let rhs ← parseExpr; semi
    pure (SVStmt.blockAssign lhs rhs)

partial def parseAssignStmtNoSemi : P SVStmt := do
  let lhs ← parsePrimaryPost
  eqSign; let rhs ← parseExpr
  pure (SVStmt.blockAssign lhs rhs)

end -- mutual

-- ============================================================================
-- Module-level parsing (not mutually recursive with expressions)
-- ============================================================================

def parsePortDir : P SVPortDir := do
  match ← attempt (keyword "input") with
  | some _ => pure .input
  | none => match ← attempt (keyword "output") with
    | some _ => pure .output
    | none => keyword "inout"; pure .inout

def parseOptWidth : P (Option (Sparkle.IR.Type.DimExpr × Sparkle.IR.Type.DimExpr)) := do
  match ← attempt lbracket with
  | some _ =>
    let hi ← parseExpr
    colon
    let lo ← parseExpr
    rbracket
    let hiDim ← match exprToDimExpr? hi with
      | some dim => pure dim
      | none => fail "unsupported symbolic high bound in packed range"
    let loDim ← match exprToDimExpr? lo with
      | some dim => pure dim
      | none => fail "unsupported symbolic low bound in packed range"
    let descending : Bool := match hiDim.toNat?, loDim.toNat? with
      | some hiValue, some loValue => decide (hiValue >= loValue)
      | _, _ => match hiDim, loDim with
        | .sub _ (.literal 1), .literal 0 => true
        | .param _, .literal 0 => true
        | _, _ => hiDim == loDim
    unless descending do
      fail "ascending or direction-ambiguous packed ranges are not supported; use a descending [high:low] range"
    pure (some (hiDim, loDim))
  | none => pure none

/-- Parse a port: direction [reg] [width] name -/
def parsePortInList : P SVPort := do
  let dir ← parsePortDir
  let isReg ← match ← attempt (keyword "reg") with | some _ => pure true | none => pure false
  let _ ← attempt (keyword "logic")
  let _ ← attempt (keyword "wire")
  let isSigned ← match ← attempt (keyword "signed") with
    | some _ => pure true
    | none => pure false
  let width ← parseOptWidth
  let name ← identifier
  pure { dir, isReg, isSigned, width, name }

/-- Parse port list with direction carry-over.
    In Verilog, `input clk, resetn` means both are inputs.
    Direction/reg/width persist until a new direction keyword appears. -/
def parsePortList : P (List SVPort) := do
  lparen
  let first ← parsePortInList
  let mut ports := [first]
  let mut lastDir := first.dir
  let mut lastIsReg := first.isReg
  let mut lastIsSigned := first.isSigned
  let mut lastWidth := first.width
  let mut cont := true
  while cont do
    match ← attempt comma with
    | some _ =>
      -- Check if next token is a direction keyword
      match ← attempt parsePortDir with
      | some dir =>
        lastDir := dir
        lastIsReg := match ← attempt (keyword "reg") with | some _ => true | none => false
        let _ ← attempt (keyword "logic")
        let _ ← attempt (keyword "wire")
        lastIsSigned := match ← attempt (keyword "signed") with
          | some _ => true
          | none => false
        lastWidth ← parseOptWidth
        let name ← identifier
        let port : SVPort := { dir := lastDir, isReg := lastIsReg, isSigned := lastIsSigned, width := lastWidth, name := name }
        ports := ports ++ [port]
      | none =>
        -- No direction keyword — carry over from previous
        if (← attempt (keyword "signed")).isSome then
          lastIsSigned := true
        -- Check for new width override
        let width ← parseOptWidth
        let w := if width.isSome then width else lastWidth
        let name ← identifier
        let port : SVPort := { dir := lastDir, isReg := lastIsReg, isSigned := lastIsSigned, width := w, name := name }
        ports := ports ++ [port]
    | none => cont := false
  rparen; pure ports

/-- Parse a single parameter declaration: parameter [width] name = value -/
def parseParamDecl (isLocal : Bool) : P SVParam := do
  let isInteger := (← attempt (keyword "integer")).isSome
  let hasSignedKeyword := (← attempt (keyword "signed")).isSome
  let width ← parseOptWidth
  let name ← identifier
  eqSign; let value ← parseExpr
  pure { name, width, value, isLocal, isSigned := isInteger || hasSignedKeyword }

/-- Parse parameter list in #(...) -/
def parseParamList : P (List SVParam) := do
  token (matchStr "#"); lparen
  let mut params : List SVParam := []
  let mut cont := true
  while cont do
    keyword "parameter"
    let p ← parseParamDecl false
    params := params ++ [p]
    match ← attempt comma with
    | some _ => pure ()
    | none => cont := false
  rparen
  pure params

def parseSensitivity : P SVSensitivity := do
  match ← attempt (keyword "posedge") with
  | some _ => let s ← identifier; pure (SVSensitivity.posedge s)
  | none => match ← attempt (keyword "negedge") with
    | some _ => let s ← identifier; pure (SVSensitivity.negedge s)
    | none => let _ ← token (matchStr "*"); pure SVSensitivity.star

partial def parseAlwaysBlock : P SVModuleItem := do
  let flavor ← match ← attempt (keyword "always_ff") with
    | some _ => pure "ff"
    | none => match ← attempt (keyword "always_comb") with
      | some _ => pure "comb"
      | none => keyword "always"; pure "plain"
  match ← attempt at_ with
  | some _ =>
    -- Sensitivity list: @(posedge clk or negedge rst) or @*
    match ← attempt (do let _ ← token (matchStr "*"); pure ()) with
    | some _ =>
      -- always @* — try begin/end or single statement
      let body ← parseAlwaysBody
      pure (SVModuleItem.alwaysBlock .star body)
    | none =>
      lparen; let sens ← parseSensitivity
      let _ ← many (do keyword "or"; let _ ← parseSensitivity; pure ())
      rparen
      let body ← parseAlwaysBody
      pure (SVModuleItem.alwaysBlock sens body)
  | none =>
    -- always_comb has an implicit complete combinational sensitivity list.
    if flavor == "ff" then fail "always_ff requires an explicit event control"
    let body ← parseAlwaysBody
    pure (SVModuleItem.alwaysBlock .star body)
where
  parseAlwaysBody : P (List SVStmt) := do
    match ← attempt (keyword "begin") with
    | some _ =>
      let _ ← attempt (do colon; let _ ← identifier; pure ())
      -- Parse statements, tracking begin/end depth
      let mut stmts : List SVStmt := []
      let mut depth : Nat := 1  -- we already consumed one "begin"
      while depth > 0 do
        -- Try to parse a statement
        match ← attempt parseStmt with
        | some s =>
          stmts := stmts ++ [s]
          -- Count begin/end depth changes from the statement
          -- (parseStmt already consumed matching begin/end internally)
        | none =>
          -- Check for end keyword
          match ← attempt (keyword "end") with
          | some _ => depth := depth - 1
          | none =>
            -- Check for begin keyword (nested block we couldn't parse)
            match ← attempt (keyword "begin") with
            | some _ => depth := depth + 1
            | none =>
              -- Skip one token (error recovery)
              let _ ← nextChar
      pure stmts
    | none =>
      let s ← parseStmt; pure [s]

/-- Parse multiple comma-separated names: `reg [w] a, b, c;` → 3 items -/
def parseMultiNames (mkItem : String → SVModuleItem) : P (List SVModuleItem) := do
  let first ← identifier
  let mut items := [mkItem first]
  let mut cont := true
  while cont do
    match ← attempt comma with
    | some _ => let n ← identifier; items := items ++ [mkItem n]
    | none => cont := false
  semi; pure items

mutual

/-- Parse the items inside one branch of a generate if/else block.
    Collects items until we see `end` at depth 0. -/
partial def parseGenerateBranchItems : P (List SVModuleItem) := do
  keyword "begin"
  let mut items : List SVModuleItem := []
  let mut done := false
  while !done do
    match ← attempt (keyword "end") with
    | some _ => done := true
    | none =>
      match ← attempt parseModuleItems with
      | some itemGroup => items := items ++ itemGroup
      | none =>
        -- Skip unrecognized token
        let _ ← nextChar; pure ()
  pure items

/-- Parse a generate if/else if/else/endgenerate block.
    Returns generateBlock with condition, if-body, and else-body.
    Chained `else if` is represented by nesting: else-body contains another generateBlock. -/
partial def parseGenerateBlock : P (List SVModuleItem) := do
  -- Already consumed "generate" keyword
  -- Expect: if (COND) begin ... end [else [if (COND) begin ... end]* [begin ... end]] endgenerate
  keyword "if"
  lparen; let cond ← parseExpr; rparen
  let ifItems ← parseGenerateBranchItems
  -- Check for else / else if
  let elseItems ← match ← attempt (keyword "else") with
    | some _ =>
      match ← attempt (keyword "if") with
      | some _ =>
        -- else if: parse condition and branches, wrap as nested generateBlock
        lparen; let cond2 ← parseExpr; rparen
        let ifItems2 ← parseGenerateBranchItems
        let elseItems2 ← match ← attempt (keyword "else") with
          | some _ => parseGenerateBranchItems
          | none => pure []
        pure [SVModuleItem.generateBlock cond2 ifItems2 elseItems2]
      | none =>
        -- plain else
        parseGenerateBranchItems
    | none => pure []
  keyword "endgenerate"
  pure [SVModuleItem.generateBlock cond ifItems elseItems]

/-- Parse the validation-generate shape emitted by Sparkle.  Its body contains
    only an `initial $fatal` diagnostic and is regenerated from IR dimensions,
    so it is safe to omit during lowering. -/
partial def parseValidationGenerate : P (List SVModuleItem) := do
  keyword "if"
  lparen
  let cond ← parseExpr
  rparen
  keyword "begin"
  colon
  let label ← identifier
  unless label.startsWith "sparkle_invalid_nat_parameter_" ||
      label.startsWith "sparkle_invalid_dimension_" ||
      label.startsWith "sparkle_invalid_nat_work_width_" do
    fail "not a canonical Sparkle validation-generate label"
  keyword "initial"
  let _ ← token (matchStr "$fatal")
  lparen
  let mut depth : Nat := 1
  while depth > 0 do
    let ch ← nextChar
    if ch == '(' then depth := depth + 1
    else if ch == ')' then depth := depth - 1
  ws
  semi
  keyword "end"
  keyword "endgenerate"
  pure [.validationGuard cond]

/-- Core memory IR retains an exact depth and zero-based numeric indexes, but
    not arbitrary unpacked-array bases.  Both `[0:N-1]` and `[N-1:0]` denote
    the same supported index set; nonzero-base ranges fail closed. -/
private partial def zeroBasedUnpackedDepth? (left right : Sparkle.IR.Type.DimExpr) :
    Option Sparkle.IR.Type.DimExpr :=
  match left, right with
  | .literal 0, .sub base (.literal 1)
  | .sub base (.literal 1), .literal 0 => some base
  | .literal 0, .literal high | .literal high, .literal 0 =>
      some (.literal (high + 1))
  | _, _ => none

partial def parseModuleItems : P (List SVModuleItem) := do
  match ← attempt (keyword "assign") with
  | some _ =>
    let lhs ← parseExpr; eqSign; let rhs ← parseExpr; semi
    pure [SVModuleItem.contAssign lhs rhs]
  | none => match ← attempt (keyword "wire") with
    | some _ =>
      let isSigned := (← attempt (keyword "signed")).isSome
      let w ← parseOptWidth
      let n ← identifier
      match ← attempt eqSign with
      | some _ => let e ← parseExpr; semi; pure [SVModuleItem.wireDecl n w (some e) isSigned]
      | none =>
        -- Check for additional comma-separated names
        let mut items := [SVModuleItem.wireDecl n w none isSigned]
        let mut cont := true
        while cont do
          match ← attempt comma with
          | some _ => let n2 ← identifier; items := items ++ [SVModuleItem.wireDecl n2 w none isSigned]
          | none => cont := false
        semi; pure items
    | none => match ← attempt (keyword "logic") with
      | some _ =>
        let isSigned := (← attempt (keyword "signed")).isSome
        let w ← parseOptWidth
        let n ← identifier
        match ← attempt lbracket with
        | some _ =>
          let lo ← parseExpr
          colon
          let hi ← parseExpr
          rbracket
          let loDim ← match exprToDimExpr? lo with
            | some dim => pure dim
            | none => fail "unsupported symbolic low bound in unpacked array range"
          let hiDim ← match exprToDimExpr? hi with
            | some dim => pure dim
            | none => fail "unsupported symbolic high bound in unpacked array range"
          let arrSize ← match zeroBasedUnpackedDepth? loDim hiDim with
            | some depth => pure depth
            | none => fail "unpacked arrays require a zero-based [0:high] or [high:0] range"
          semi
          pure [SVModuleItem.regDecl n w (some arrSize) isSigned]
        | none =>
          match ← attempt eqSign with
          | some _ => let e ← parseExpr; semi; pure [SVModuleItem.wireDecl n w (some e) isSigned]
          | none =>
            let mut items := [SVModuleItem.wireDecl n w none isSigned]
            let mut cont := true
            while cont do
              match ← attempt comma with
              | some _ => let n2 ← identifier; items := items ++ [SVModuleItem.wireDecl n2 w none isSigned]
              | none => cont := false
            semi; pure items
      | none => match ← attempt (keyword "reg") with
      | some _ =>
        let isSigned := (← attempt (keyword "signed")).isSome
        let w ← parseOptWidth; let n ← identifier
        match ← attempt lbracket with
        | some _ =>
          let lo ← parseExpr
          colon
          let hi ← parseExpr
          rbracket
          let loDim ← match exprToDimExpr? lo with
            | some dim => pure dim
            | none => fail "unsupported symbolic low bound in unpacked array range"
          let hiDim ← match exprToDimExpr? hi with
            | some dim => pure dim
            | none => fail "unsupported symbolic high bound in unpacked array range"
          let arrSize ← match zeroBasedUnpackedDepth? loDim hiDim with
            | some depth => pure depth
            | none => fail "unpacked arrays require a zero-based [0:high] or [high:0] range"
          semi
          pure [SVModuleItem.regDecl n w (some arrSize) isSigned]
        | none =>
          let mut items := [SVModuleItem.regDecl n w none isSigned]
          let mut cont := true
          while cont do
            match ← attempt comma with
            | some _ => let n2 ← identifier; items := items ++ [SVModuleItem.regDecl n2 w none isSigned]
            | none => cont := false
          semi; pure items
      | none => match ← attempt (keyword "integer") with
        | some _ =>
          let items ← parseMultiNames (SVModuleItem.integerDecl ·)
          pure items
        | none => match ← attempt (keyword "localparam") with
          | some _ =>
            let p ← parseParamDecl true; semi; pure [SVModuleItem.paramDecl p]
          | none => match ← attempt (keyword "parameter") with
            | some _ =>
              let p ← parseParamDecl false; semi; pure [SVModuleItem.paramDecl p]
            | none => match ← attempt (keyword "generate") with
              | some _ =>
                match ← attempt parseValidationGenerate with
                | some guard => pure guard
                | none => parseGenerateBlock
              | none => match ← attempt (keyword "initial") with
                | some _ =>
                  -- Parse initial block — extract $readmemh if present
                  keyword "begin"
                  let mut items : List SVModuleItem := []
                  let mut d : Nat := 1
                  while d > 0 do
                    -- Check for $readmemh("file", mem);
                    match ← attempt (do
                      let _ ← token (matchStr "$readmemh")
                      lparen
                      -- Parse filename string: "filename"
                      let _ ← token (matchStr "\"")
                      let mut filename : List Char := []
                      let mut readingName := true
                      while readingName do
                        let c ← nextChar
                        if c == '"' then readingName := false
                        else filename := filename ++ [c]
                      ws; comma
                      let memName ← identifier
                      rparen; semi
                      pure (String.ofList filename, memName)) with
                    | some (filename, memName) =>
                      items := items ++ [SVModuleItem.readmemh filename memName]
                    | none =>
                      let hitBegin ← attempt (keyword "begin")
                      if hitBegin.isSome then d := d + 1
                      else
                        let hitEnd ← attempt (keyword "end")
                        if hitEnd.isSome then d := d - 1
                        else let _ ← nextChar; pure ()
                  pure items
                | none => match ← attempt (keyword "task") with
                | some _ =>
                  let n ← identifier; semi
                  -- Skip task body until endtask (tasks are not synthesizable)
                  let mut depth : Nat := 1
                  while depth > 0 do
                    match ← attempt (keyword "endtask") with
                    | some _ => depth := depth - 1
                    | none => let _ ← nextChar; pure ()
                  pure [SVModuleItem.taskDecl n []]
                | none =>
                  -- Try module instantiation: moduleName instName ( .port(expr), ... );
                  match ← attempt (do
                    let modName ← identifier
                    -- Parse optional #(.param(val), ...) parameter overrides
                    let mut paramOvr : List (String × SVExpr) := []
                    match ← attempt (token (matchStr "#")) with
                    | some _ =>
                      lparen
                      let mut pcont := true
                      while pcont do
                        match ← attempt (do dot; let pn ← identifier; lparen; let pe ← parseExpr; rparen; pure (pn, pe)) with
                        | some (pn, pe) =>
                          paramOvr := paramOvr ++ [(pn, pe)]
                          match ← attempt comma with | some _ => pure () | none => pcont := false
                        | none => pcont := false
                      rparen
                    | none => pure ()
                    let instName ← identifier
                    lparen
                    let mut conns : List (String × SVExpr) := []
                    let mut cont := true
                    while cont do
                      dot; let pName ← identifier; lparen; let pExpr ← parseExpr; rparen
                      conns := conns ++ [(pName, pExpr)]
                      match ← attempt comma with | some _ => pure () | none => cont := false
                    rparen; semi
                    pure (modName, instName, conns, paramOvr)
                  ) with
                  | some (modName, instName, conns, paramOvr) =>
                    pure [SVModuleItem.instantiation modName instName conns paramOvr]
                  | none =>
                  -- Try always block; on failure, skip balanced begin/end
                  match ← attempt parseAlwaysBlock with
                  | some item => pure [item]
                  | none =>
                    -- Skip past the always block by matching begin/end balance
                    match ← attempt (keyword "always_ff") with
                    | some _ => pure ()
                    | none => match ← attempt (keyword "always_comb") with
                      | some _ => pure ()
                      | none => keyword "always"
                    let _ ← attempt at_
                    -- Skip sensitivity list
                    match ← attempt lparen with
                    | some _ =>
                      let mut parenDepth : Nat := 1
                      while parenDepth > 0 do
                        match ← attempt rparen with
                        | some _ => parenDepth := parenDepth - 1
                        | none => let _ ← nextChar; pure ()
                    | none =>
                      let _ ← attempt (token (matchStr "*"))
                    -- Skip body by matching begin/end
                    keyword "begin"
                    let mut depth : Nat := 1
                    while depth > 0 do
                      match ← attempt (keyword "begin") with
                      | some _ => depth := depth + 1
                      | none =>
                        match ← attempt (keyword "end") with
                        | some _ => depth := depth - 1
                        | none => let _ ← nextChar; pure ()
                    pure []

end  -- mutual

-- ============================================================================
-- Top-level module parsing
-- ============================================================================

partial def parseModule : P SVModule := do
  keyword "module"
  let name ← identifier
  -- Optional parameter list: #(parameter ...)
  let params ← match ← attempt (token (matchStr "#")) with
    | some _ =>
      lparen
      let mut ps : List SVParam := []
      let mut cont := true
      while cont do
        keyword "parameter"
        let p ← parseParamDecl false
        ps := ps ++ [p]
        match ← attempt comma with
        | some _ => pure ()
        | none => cont := false
      rparen; pure ps
    | none => pure []
  let ports ← parsePortList
  semi
  let itemGroups ← many parseModuleItems
  let items := itemGroups.toList.flatMap id
  keyword "endmodule"
  pure { name, params, ports, items }

-- ============================================================================
-- Public API
-- ============================================================================

def parse (input : String) : Except String SVDesign :=
  let preprocessed := preprocess input
  Lexer.run (do
    ws
    let modules ← many1 parseModule
    ws
    match ← peekChar with
    | none => pure { modules := modules.toList }
    | some ch => fail s!"unexpected trailing input starting with '{ch}'") preprocessed

def parseModuleFromString (input : String) : Except String SVModule :=
  let preprocessed := preprocess input
  Lexer.run (do
    ws
    let module_ ← parseModule
    ws
    match ← peekChar with
    | none => pure module_
    | some ch => fail s!"unexpected trailing input starting with '{ch}'") preprocessed

end Tools.SVParser.Parser
