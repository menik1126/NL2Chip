/-
  Verilog IR → Lean Semantic Model Generator

  Extracts a pure state-machine model from Sparkle IR (Module),
  generating Lean source code with State/Input/nextState definitions
  suitable for formal verification with `simp`, `omega`, `bv_decide`.

  Pipeline:
    Verilog → [SVParser] → IR Module → [extractModel] → SemanticModel → [generateLean] → .lean source
-/

import Sparkle.IR.AST
import Sparkle.IR.Type

open Sparkle.IR.AST
open Sparkle.IR.Type

namespace Tools.SVParser.Verify

-- ============================================================================
-- Semantic Model types
-- ============================================================================

/-- A register extracted from the IR -/
structure RegField where
  name : String
  width : Nat
  initValue : Int
  nextExpr : Expr
  deriving Repr

/-- An input port -/
structure InputField where
  name : String
  width : Nat
  deriving Repr

/-- Extracted semantic model of a hardware module -/
structure SemanticModel where
  moduleName : String
  registers : List RegField
  inputs : List InputField
  assertions : List (String × Expr) := []
  deriving Repr

-- ============================================================================
-- Model extraction from IR
-- ============================================================================

/-- Require a concrete width for verification-model generation. -/
private def concreteWidth (role : String) (ty : HWType) : Except String Nat := do
  let width ← ty.requireBitWidth role
  if width == 0 then throw s!"{role} has zero width"
  return width

/-- Extract a semantic model only after all native parameters have been
    specialized.  Returning `Except` prevents symbolic widths from becoming a
    guessed `0` or `32` in proof artifacts. -/
def extractModelChecked (m : Module) : Except String SemanticModel := do
  m.validateDimensions
  unless m.parameters.isEmpty do
    throw s!"module '{m.name}' is parameterized; specialize it before generating a concrete Lean verification model"
  for dimension in m.dimensionExpressions do
    unless dimension.isConcrete do
      throw s!"module '{m.name}' contains symbolic dimension '{dimension}'; specialize it before generating a concrete Lean verification model"
  let mut regs := []
  for stmt in m.body do
    match stmt with
    | .register name _clk _rst input initVal =>
      let port ← match m.wires.find? (fun w => w.name == name) with
        | some p => pure p
        | none => match m.outputs.find? (fun w => w.name == name) with
          | some p => pure p
          | none => throw s!"register '{name}' has no declared output type"
      let width ← concreteWidth s!"register '{name}'" port.ty
      regs := regs ++ [{ name, width, initValue := initVal, nextExpr := input }]
    | _ => pure ()
  let mut inputs := []
  for port in m.inputs.filter (fun p => p.name != "clk") do
    let width ← concreteWidth s!"input '{port.name}'" port.ty
    inputs := inputs ++ [{ name := port.name, width := width }]
  return {
    moduleName := m.name
    registers := regs
    inputs := inputs
    assertions := m.assertions
  }

/-- Public checked model-extraction API. -/
def extractModel (m : Module) : Except String SemanticModel :=
  extractModelChecked m

-- ============================================================================
-- Wire inlining (substitute assign references)
-- ============================================================================

/-- Build a map of wire name → expression from Stmt.assign -/
def collectAssigns (body : List Stmt) : List (String × Expr) :=
  body.filterMap fun stmt => match stmt with
    | .assign name rhs => some (name, rhs)
    | _ => none

/-- Recursively inline wire references with their definitions -/
partial def inlineAssigns (assigns : List (String × Expr)) : Expr → Expr
  | .ref name =>
    match assigns.find? (·.1 == name) with
    | some (_, rhs) => inlineAssigns assigns rhs
    | none => .ref name
  | .op operator args => .op operator (args.map (inlineAssigns assigns))
  | .concat args => .concat (args.map (inlineAssigns assigns))
  | .slice e hi lo => .slice (inlineAssigns assigns e) hi lo
  | .index a i => .index (inlineAssigns assigns a) (inlineAssigns assigns i)
  | e => e  -- const passes through

-- ============================================================================
-- Width inference
-- ============================================================================

/-- Infer the BitVec width of an IR expression.  Unknown references and malformed
    operator arities are errors rather than guessed 32-bit values. -/
partial def inferWidthChecked (regWidths inputWidths : List (String × Nat)) : Expr → Except String Nat
  | .const _ w => w.requireNat "verification expression constant width"
  | .ref name =>
    match regWidths.find? (·.1 == name) with
    | some (_, w) => pure w
    | none => match inputWidths.find? (·.1 == name) with
      | some (_, w) => pure w
      | none => throw s!"verification width is unknown for reference '{name}'"
  | .op .eq _ | .op .lt_u _ | .op .lt_s _ | .op .le_u _ | .op .le_s _
  | .op .gt_u _ | .op .gt_s _ | .op .ge_u _ | .op .ge_s _ => pure 1
  | .op .mux args => match args with
    | [_, t, _] => inferWidthChecked regWidths inputWidths t
    | _ => throw "verification mux width inference requires exactly three operands"
  | .op _ args => match args with
    | a :: _ => inferWidthChecked regWidths inputWidths a
    | _ => throw "verification operator width inference requires at least one operand"
  | .slice _ hi lo => (hi - lo + 1).requireNat "verification slice width"
  | .concat args => args.foldlM (fun acc a => return acc + (← inferWidthChecked regWidths inputWidths a)) 0
  | .index _ _ => throw "verification model generation does not support array-index expressions"

/-- Width inference used by rewriting helpers after checked extraction.  A zero
    result is an internal sentinel only; public source generation rechecks via
    `inferWidthChecked` and returns an error instead of emitting Lean code. -/
def inferWidth (regWidths inputWidths : List (String × Nat)) (expr : Expr) : Nat :=
  match inferWidthChecked regWidths inputWidths expr with
  | .ok width => width
  | .error _ => 0

-- ============================================================================
-- IR Expr → Lean source string
-- ============================================================================

/-- Sanitize a name for Lean (replace special chars) -/
def leanName (s : String) : String :=
  s.map fun c => if c == '$' || c == '.' then '_' else c

/-- Fix constant widths to match the target register width.
    Verilog unsized constants default to 32-bit in the IR, but the register
    may be 8-bit. Replace `Expr.const v 32` with `Expr.const v targetWidth`
    when the constant is used in a context where targetWidth is known. -/
partial def fixConstWidths (expr : Expr) (targetWidth : Nat)
    (widthEnv : List (String × Nat)) : Expr :=
  match expr with
  | .const v w => if w.toNat? == some 32 && targetWidth != 32 then .const v targetWidth else expr
  | .op .mux [c, t, e] =>
    .op .mux [c, fixConstWidths t targetWidth widthEnv, fixConstWidths e targetWidth widthEnv]
  | .op op args => .op op (args.map (fixConstWidths · targetWidth widthEnv))
  | .concat args => .concat (args.map (fixConstWidths · targetWidth widthEnv))
  | .slice e hi lo =>
    match (hi - lo + 1).toNat? with
    | some width => .slice (fixConstWidths e width widthEnv) hi lo
    | none => .slice e hi lo
  | _ => expr

/-- Fix constant widths by inferring the correct width from context.
    For binary ops, constants adopt the width of the other operand.
    For mux, constants adopt the width of the then-branch. -/
partial def fixConstWidthsSmart (expr : Expr) (widthEnv : List (String × Nat)) : Expr :=
  match expr with
  | .op .mux [c, t, e] =>
    let tc := fixConstWidthsSmart c widthEnv
    let tt := fixConstWidthsSmart t widthEnv
    let te := fixConstWidthsSmart e widthEnv
    let tw := inferWidth widthEnv widthEnv tt
    -- Fix else-branch constants to match then-branch width
    let te := match te with
      | .const v (.literal 32) => if tw != 32 then .const v tw else te
      | _ => te
    .op .mux [tc, tt, te]
  | .op .eq [a, b] =>
    let a := fixConstWidthsSmart a widthEnv
    let b := fixConstWidthsSmart b widthEnv
    let wa := inferWidth widthEnv widthEnv a
    let wb := inferWidth widthEnv widthEnv b
    let a := if wa == 32 && wb != 32 then match a with | .const v _ => .const v wb | _ => a else a
    let b := if wb == 32 && wa != 32 then match b with | .const v _ => .const v wa | _ => b else b
    .op .eq [a, b]
  | .op op [a, b] =>
    let a := fixConstWidthsSmart a widthEnv
    let b := fixConstWidthsSmart b widthEnv
    let wa := inferWidth widthEnv widthEnv a
    let wb := inferWidth widthEnv widthEnv b
    let a := if wa == 32 && wb != 32 then match a with | .const v _ => .const v wb | _ => a else a
    let b := if wb == 32 && wa != 32 then match b with | .const v _ => .const v wa | _ => b else b
    .op op [a, b]
  | .op op args => .op op (args.map (fixConstWidthsSmart · widthEnv))
  | _ => expr

/-- Convert IR Expr to a Lean BitVec expression string.
    `regNames`/`inputNames` control `s.` vs `i.` prefix.
    `widthEnv` is used for width inference (may include extra wires). -/
partial def irExprToLeanChecked (expr : Expr) (regNames inputNames : List (String × Nat))
    (widthEnv : List (String × Nat)) (stateVar inputVar : String) : Except String String := do
  let go (e : Expr) := irExprToLeanChecked e regNames inputNames widthEnv stateVar inputVar
  let width ← inferWidthChecked widthEnv widthEnv expr
  match expr with
  | .const v w =>
    match w.toNat? with
    | some concreteWidth =>
      if v < 0 then pure s!"(BitVec.ofInt {concreteWidth} ({v}))"
      else pure s!"({v}#{concreteWidth})"
    | none => throw s!"symbolic constant width '{w}' reached concrete verification source generation"
  | .ref name =>
    if regNames.any (·.1 == name) then pure s!"{stateVar}.{leanName name}"
    else if inputNames.any (·.1 == name) then pure s!"{inputVar}.{leanName name}"
    else throw s!"unresolved reference '{name}' reached verification source generation"
  | .op .mux [cond, thenVal, elseVal] =>
    let condW ← inferWidthChecked widthEnv widthEnv cond
    return s!"(if {← go cond} != (0 : BitVec {condW}) then {← go thenVal} else {← go elseVal})"
  | .op .add [a, b] => return s!"({← go a} + {← go b})"
  | .op .sub [a, b] => return s!"({← go a} - {← go b})"
  | .op .mul [a, b] => return s!"({← go a} * {← go b})"
  | .op .and [a, b] => return s!"({← go a} &&& {← go b})"
  | .op .or [a, b] => return s!"({← go a} ||| {← go b})"
  | .op .xor [a, b] => return s!"({← go a} ^^^ {← go b})"
  | .op .not [a] =>
    if width <= 1 then return s!"(if {← go a} == (0 : BitVec {width}) then (1 : BitVec {width}) else (0 : BitVec {width}))"
    else return s!"(~~~ {← go a})"
  | .op .eq [a, b] =>
    return s!"(if {← go a} == {← go b} then (1 : BitVec 1) else (0 : BitVec 1))"
  | .op .lt_u [a, b] => return s!"(if {← go a} < {← go b} then (1 : BitVec 1) else (0 : BitVec 1))"
  | .op .shl [a, b] => return s!"({← go a} <<< {← go b})"
  | .op .shr [a, b] => return s!"({← go a} >>> {← go b})"
  | .op .asr [a, b] =>
    return s!"(BitVec.sshiftRight {← go a} {← go b}.toNat)"
  | .op .neg [a] => return s!"(- {← go a})"
  | .slice e hi lo =>
    match hi.toNat?, lo.toNat? with
    | some concreteHi, some concreteLo =>
      return s!"(BitVec.extractLsb' {concreteLo} {concreteHi - concreteLo + 1} {← go e})"
    | _, _ => throw "symbolic slice reached concrete verification source generation"
  | .concat args =>
    match args with
    | [] => pure "(0 : BitVec 0)"
    | [a] => go a
    | a :: rest =>
      let aStr ← go a
      let restStr ← go (Expr.concat rest)
      pure s!"({aStr} ++ {restStr})"
  | _ => throw s!"unsupported verification expression: {repr expr}"

/-- Compatibility wrapper retained for callers that already validated their
    model.  It never produces a compilable placeholder on failure. -/
def irExprToLean (expr : Expr) (regNames inputNames : List (String × Nat))
    (widthEnv : List (String × Nat)) (stateVar inputVar : String) : String :=
  match irExprToLeanChecked expr regNames inputNames widthEnv stateVar inputVar with
  | .ok source => source
  | .error message => s!"/* ERROR: {message} */"

-- ============================================================================
-- Lean source generation
-- ============================================================================

/-- Generate complete Lean source file from a semantic model -/
def generateLeanChecked (model : SemanticModel)
    (extraWidths : List (String × Nat) := []) : Except String String := do
  let ns := leanName model.moduleName
  let regWidths := model.registers.map fun r => (r.name, r.width)
  let inputWidths := model.inputs.map fun i => (i.name, i.width)
  -- Extra widths only for width inference, not for name resolution
  let allWidths := regWidths ++ inputWidths ++ extraWidths

  -- State structure
  let stateFields := model.registers.map fun r =>
    s!"  {leanName r.name} : BitVec {r.width}"
  let stateStruct := s!"structure State where\n" ++
    String.intercalate "\n" stateFields ++
    "\n  deriving DecidableEq, Repr, BEq, Inhabited\n"

  -- Input structure
  let inputFields := model.inputs.map fun i =>
    s!"  {leanName i.name} : BitVec {i.width}"
  let inputStruct := s!"structure Input where\n" ++
    String.intercalate "\n" inputFields ++
    "\n  deriving DecidableEq, Repr, BEq, Inhabited\n"

  -- nextState function — use register width to fix constant widths
  let mut regAssigns : List String := []
  for r in model.registers do
    let fixedExpr := fixConstWidths r.nextExpr r.width allWidths
    regAssigns := regAssigns ++
      [s!"    {leanName r.name} := {← irExprToLeanChecked fixedExpr regWidths inputWidths allWidths "s" "i"}"]
  let nextStateFn := "def nextState (s : State) (i : Input) : State :=\n  {\n" ++
    String.intercalate "\n" regAssigns ++
    "\n  }\n"

  -- Initial state
  let initFields := model.registers.map fun r =>
    s!"    {leanName r.name} := ({r.initValue}#{ r.width})"
  let initState := "def initState : State :=\n  {\n" ++
    String.intercalate "\n" initFields ++
    "\n  }\n"

  -- Assemble
  return s!"/-\n  Auto-generated semantic model from Verilog module: {model.moduleName}\n  Generated by Sparkle SVParser Verify\n-/\n\n" ++
  s!"namespace {ns}.Verify\n\n" ++
  stateStruct ++ "\n" ++
  inputStruct ++ "\n" ++
  nextStateFn ++ "\n" ++
  initState ++ "\n" ++
  s!"end {ns}.Verify\n"

def generateLean (model : SemanticModel) (extraWidths : List (String × Nat) := []) : String :=
  match generateLeanChecked model extraWidths with
  | .ok source => source
  | .error message => s!"/* ERROR: {message} */"

-- ============================================================================
-- Combined pipeline: Module → Lean source
-- ============================================================================

/-- Extract model from IR Module and generate Lean verification source -/
def moduleToLeanChecked (m : Module) : Except String String := do
  let model ← extractModelChecked m
  let assigns := collectAssigns m.body
  -- Inline wire references in all register next-expressions
  let model := { model with
    registers := model.registers.map fun r =>
      { r with nextExpr := inlineAssigns assigns r.nextExpr }
  }
  -- Collect all wire widths for accurate width inference
  let mut wireWidths := []
  for wire in m.wires do
    wireWidths := wireWidths ++ [(wire.name,
      ← concreteWidth s!"wire '{wire.name}'" wire.ty)]
  let mut portWidths := []
  for port in m.inputs do
    portWidths := portWidths ++ [(port.name,
      ← concreteWidth s!"input '{port.name}'" port.ty)]
  let allWidths := wireWidths ++ portWidths
  generateLeanChecked model allWidths

/-- Public checked source-generation API. -/
def moduleToLean (m : Module) : Except String String :=
  moduleToLeanChecked m

end Tools.SVParser.Verify
