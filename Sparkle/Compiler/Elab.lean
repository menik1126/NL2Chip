/-
  Elaborator & Compiler

  Translates Lean expressions into hardware netlists using metaprogramming.
  This bridges the gap between high-level Signal code and low-level IR.
-/

import Lean
import Sparkle.IR.Builder
import Sparkle.IR.AST
import Sparkle.IR.Type
import Sparkle.Data.BitPack
import Sparkle.Backend.Verilog
import Sparkle.Backend.CppSim
import Sparkle.IR.Optimize
import Sparkle.IR.Specialize
import Sparkle.Compiler.DRC
import Sparkle.Core.Signal
import Sparkle.Core.Vector

namespace Sparkle.Compiler.Elab

open Lean Lean.Elab Lean.Elab.Command Lean.Meta
open Sparkle.IR.Builder
open Sparkle.IR.AST (Operator Port Module Expr Stmt)
open Sparkle.IR.Type
open Sparkle.Backend.Verilog

initialize registerTraceClass `sparkle.compiler

instance : Inhabited Sparkle.IR.AST.Port := ⟨{ name := "default", ty := .bit }⟩


/-- Compiler state tracking variable mappings and context -/
structure CompilerState where
  varMap : List (FVarId × String) := []  -- Map Lean variables to wire names
  dimMap : List (FVarId × DimExpr) := [] -- Top-level Nat binders retained as SV parameters
  parameterDefaults : List (String × Nat) := []
  clockWire : Option String := none       -- Name of clock wire (if any)
  resetWire : Option String := none       -- Name of reset wire (if any)

/-- Compiler monad: combines CircuitM builder with MetaM -/
abbrev CompilerM := ReaderT CompilerState (StateT CircuitState MetaM)

namespace CompilerM

/-- Get the current compiler state (from ReaderT) -/
def getCompilerState : CompilerM CompilerState :=
  read

/-- Lookup a variable mapping -/
def lookupVar (fvarId : FVarId) : CompilerM (Option String) := do
  let s ← getCompilerState
  return s.varMap.lookup fvarId

/-- Look up the symbolic hardware dimension associated with a Lean Nat binder. -/
def lookupDim (fvarId : FVarId) : CompilerM (Option DimExpr) := do
  let s ← getCompilerState
  return s.dimMap.lookup fvarId

/-- Execute an action with an additional variable mapping in scope -/
def withVarMapping {α : Type} (fvarId : FVarId) (wireName : String) (k : CompilerM α) : CompilerM α := do
  let oldState ← getCompilerState
  let newState := { oldState with varMap := (fvarId, wireName) :: oldState.varMap }
  withReader (fun _ => newState) k

/-- Execute an action with an additional symbolic dimension mapping in scope. -/
def withDimMapping {α : Type} (fvarId : FVarId) (dim : DimExpr) (k : CompilerM α) : CompilerM α := do
  let oldState ← getCompilerState
  let newState := { oldState with dimMap := (fvarId, dim) :: oldState.dimMap }
  withReader (fun _ => newState) k

/-- Execute an action with a new local declaration in MetaM scope -/
def withLocalDecl {α : Type} (name : Name) (type : Lean.Expr) (k : Lean.Expr → CompilerM α) : CompilerM α := do
  let ctx ← read
  let s ← get
  let (res, newS) ← liftMetaM <| withLocalDeclD name type fun fvar => do
    (k fvar ctx).run s
  set newS
  return res

/-- Execute an action with a new let declaration in MetaM scope (for logic values) -/
def withLetDecl {α : Type} (name : Name) (type : Lean.Expr) (value : Lean.Expr) (k : Lean.Expr → CompilerM α) : CompilerM α := do
  let ctx ← read
  let s ← get
  let (res, newS) ← liftMetaM <| Lean.Meta.withLetDecl name type value fun fvar => do
    (k fvar ctx).run s
  set newS
  return res

/-- Lift MetaM into CompilerM -/
def liftMetaM {α : Type} (m : MetaM α) : CompilerM α :=
  liftM m

/-- Lift CircuitM operations by modifying the circuit state -/
def makeWire (hint : String) (ty : HWType) (named : Bool := false) : CompilerM String := do
  let cs ← get
  let (name, cs') := CircuitM.makeWire hint ty named cs
  set cs'
  return name

def emitAssign (lhs : String) (rhs : Sparkle.IR.AST.Expr) : CompilerM Unit := do
  let cs ← get
  let ((), cs') := CircuitM.emitAssign lhs rhs cs
  set cs'

def addInput (name : String) (ty : HWType) : CompilerM Unit := do
  let cs ← get
  let ((), cs') := CircuitM.addInput name ty cs
  set cs'

/-- Add a module input exactly once.  Hierarchical sequential instances share
    the parent's clock/reset ports, so multiple children must not duplicate
    those declarations. -/
def ensureInput (name : String) (ty : HWType) : CompilerM Unit := do
  let cs ← get
  match cs.module.inputs.find? (fun port => port.name == name) with
  | some port =>
      unless port.ty == ty do
        liftMetaM $ throwError m!"Conflicting types for propagated input '{name}' in module '{cs.module.name}'."
  | none => addInput name ty


def addOutput (name : String) (ty : HWType) : CompilerM Unit := do
  let cs ← get
  let ((), cs') := CircuitM.addOutput name ty cs
  set cs'

/-- Look up the hardware type of a value without materializing the builder's
    pending wire list.  This keeps width-heavy lowering linear while ensuring
    a wire is visible immediately after `makeWire`/register/memory emission. -/
def getWireType (wireName : String) : CompilerM HWType := do
  let cs ← get
  match cs.findPort? wireName with
  | some p => return p.ty
  | none => CompilerM.liftMetaM $ throwError
      s!"Internal Sparkle compiler error: wire '{wireName}' has no declared hardware type; refusing to assume an 8-bit width."

/-- Look up the HW width of a wire by name (from pending/materialized wires,
    inputs, or outputs). -/
def getWireWidth (wireName : String) : CompilerM DimExpr := do
  return (← getWireType wireName).width

def emitRegister (hint : String) (clk : String) (rst : String) (input : Sparkle.IR.AST.Expr) (initVal : Int) (ty : HWType) (named : Bool := false) : CompilerM String := do
  let cs ← get
  let (name, cs') := CircuitM.emitRegister hint clk rst input initVal ty named cs
  set cs'
  return name

def emitMemory (hint : String) (addrWidth dataWidth : DimExpr) (clk : String)
    (writeAddr writeData writeEnable readAddr : Sparkle.IR.AST.Expr) (named : Bool := false) : CompilerM String := do
  let cs ← get
  let (name, cs') := CircuitM.emitMemory hint addrWidth dataWidth clk writeAddr writeData writeEnable readAddr named cs
  set cs'
  return name

def emitMemoryComboRead (hint : String) (addrWidth dataWidth : DimExpr) (clk : String)
    (writeAddr writeData writeEnable readAddr : Sparkle.IR.AST.Expr) (named : Bool := false) : CompilerM String := do
  let cs ← get
  let (name, cs') := CircuitM.emitMemoryComboRead hint addrWidth dataWidth clk writeAddr writeData writeEnable readAddr named cs
  set cs'
  return name

def emitInstance (moduleName : String) (instName : String)
    (connections : List (String × Sparkle.IR.AST.Expr))
    (parameterOverrides : List (String × DimExpr) := []) : CompilerM Unit := do
  let cs ← get
  let ((), cs') := CircuitM.emitInstance moduleName instName connections parameterOverrides cs
  set cs'

def freshInstanceName (moduleName : String) : CompilerM String := do
  let cs ← get
  let (name, cs') := CircuitM.freshName s!"inst_{moduleName}" false cs
  set cs'
  return name

def addParameter (name : String) (defaultValue : Nat) : CompilerM Unit := do
  let cs ← get
  let m := cs.module
  if m.parameters.any (fun p => p.name == name) then
    return
  let m := m.addParameter { name := name, defaultValue := defaultValue }
  set { cs with module := m }

private def sameParameterizedModuleShape
    (lhs rhs : Sparkle.IR.AST.Module) : Bool :=
  let eraseDefaults (m : Sparkle.IR.AST.Module) :=
    { m with parameters := m.parameters.map fun (parameter : Sparkle.IR.AST.Parameter) =>
        { parameter with defaultValue := 0 } }
  eraseDefaults lhs == eraseDefaults rhs

def addModuleToDesign (m : Sparkle.IR.AST.Module) : CompilerM Unit := do
  let cs ← get
  match cs.design.modules.find? (fun existing => existing.name == m.name) with
  | some existing =>
      unless sameParameterizedModuleShape existing m do
        CompilerM.liftMetaM $ throwError m!"Conflicting hardware modules named '{m.name}' were produced while lowering hierarchy. Parameterized child modules instantiated at different values must retain one symbolic module shape."
  | none =>
      let ((), cs') := CircuitM.addModuleToDesign m cs
      set cs'

end CompilerM

/--
  Primitive Registry: Maps Lean function names to IR operators
-/
def primitiveRegistry : List (Name × Sparkle.IR.AST.Operator) :=
  [
    -- Logical operations
    (``BitVec.and, .and),
    (``HAnd.hAnd, .and),
    (``BitVec.or, .or),
    (``HOr.hOr, .or),
    (``BitVec.xor, .xor),
    (``HXor.hXor, .xor),
    -- Arithmetic operations
    (``BitVec.add, .add),
    (``HAdd.hAdd, .add),
    (``BitVec.sub, .sub),
    (``HSub.hSub, .sub),
    (``BitVec.mul, .mul),
    (``HMul.hMul, .mul),
    -- Comparison operations (unsigned)
    (``BitVec.ult, .lt_u),
    (``BitVec.ule, .le_u),
    (``LT.lt, .lt_u),
    (``LE.le, .le_u),
    (``BEq.beq, .eq),
    -- Comparison operations (signed)
    (``BitVec.slt, .lt_s),
    (``BitVec.sle, .le_s),
    -- Shift operations (BitVec × BitVec via typeclass operators <<<, >>>)
    (``HShiftLeft.hShiftLeft, .shl),
    (``ShiftLeft.shiftLeft, .shl),
    (``HShiftRight.hShiftRight, .shr),
    (``ShiftRight.shiftRight, .shr),
    -- Negation (unary: -x)
    (``Neg.neg, .neg),
    (``BitVec.neg, .neg),
    -- Bitwise NOT (unary: ~~~x)
    (``Complement.complement, .not),
    (``BitVec.not, .not),
    -- Arithmetic shift right (BitVec × BitVec wrapper for sshiftRight)
    (``Sparkle.Core.Signal.ashr, .asr),
    -- Boolean operations (for Signal dom Bool combinators)
    (``Bool.not, .not),
    (``not, .not),
    (``Bool.and, .and),
    (``Bool.or, .or),
    (``Bool.xor, .xor)
  ]

def isPrimitive (name : Name) : Bool :=
  primitiveRegistry.any (fun (n, _) => n == name)

def getOperator (name : Name) : Option Operator :=
  primitiveRegistry.lookup name

/-- Require a concrete natural number for a non-dimensional compiler value. -/
partial def requireConcreteNat (role : String) (expr : Lean.Expr) : MetaM Nat := do
  let expr ← instantiateMVars expr
  let normalized ← whnf expr
  match normalized with
  | .lit (.natVal n) => return n
  | _ =>
    let fn := normalized.getAppFn
    let args := normalized.getAppArgs
    if fn.isConstOf ``OfNat.ofNat && args.size >= 2 then
      requireConcreteNat role args[1]!
    else if fn.isConstOf ``Fin.mk && args.size >= 2 then
      requireConcreteNat role args[1]!
    else
      let rendered ← ppExpr expr
      throwError m!"Cannot synthesize unresolved {role} {rendered}; this value must be compile-time concrete."

/--
Require a positive compile-time hardware dimension. This check is deliberately
separate from `requireConcreteNat`: zero is a valid literal value and slice
offset, but it cannot be represented as a zero-width packed value or zero-length
array by the current SystemVerilog backend (`[0:0]` denotes one bit).
-/
def requirePositiveConcreteNat (role : String) (expr : Lean.Expr) : MetaM Nat := do
  let n ← requireConcreteNat role expr
  if n == 0 then
    throwError m!"Cannot synthesize hardware with zero {role}.\n\n\
      Sparkle's current SystemVerilog backend cannot represent zero-width values \
      or zero-length arrays; a range such as [0:0] denotes one bit. Use a \
      positive compile-time dimension instead."
  return n

/-- Read Lean's `(W : Nat := n)` defaults from a declaration type.  Command
    defaults may override these values; ordinary `{W : Nat}` binders remain
    intentionally default-free and must be listed by the synthesis command. -/
partial def collectDeclaredNatDefaults (type : Lean.Expr)
    : MetaM (List (String × Nat)) := do
  match type with
  | .forallE binderName binderType body _ =>
    let fn := binderType.getAppFn
    let args := binderType.getAppArgs
    let rest ← collectDeclaredNatDefaults body
    if fn.isConstOf ``optParam && args.size >= 2 then
      let valueType ← whnf args[args.size - 2]!
      if valueType.isConstOf ``Nat then
        if args.back!.hasLooseBVars then
          throwError m!"Dependent default for Nat parameter '{binderName}' is not supported by native SystemVerilog parameter emission.\n\n\
            Express derived hardware dimensions directly from earlier parameters \
            (for example, `BitVec (W + 1)`), or supply an independent concrete \
            default. Sparkle refuses to freeze a dependent default at one \
            elaboration value because a later parameter override would change its semantics."
        let defaultValue ← requireConcreteNat
          s!"default value of Nat parameter '{binderName}'" args.back!
        return (binderName.toString, defaultValue) :: rest
    return rest
  | _ => return []

private def mergeParameterDefaults
    (declared supplied : List (String × Nat)) : List (String × Nat) :=
  let withOverrides := declared.map fun (name, value) =>
    (name, supplied.lookup name |>.getD value)
  supplied.foldl (fun defaults entry =>
    if defaults.any (fun existing => existing.1 == entry.1) then defaults
    else defaults ++ [entry]) withOverrides

/-- Lower a Lean Nat expression into the symbolic dimension language of the IR. -/
partial def lowerDimExpr (role : String) (expr : Lean.Expr) : CompilerM DimExpr := do
  let expr ← CompilerM.liftMetaM (instantiateMVars expr)
  if let .fvar fvarId := expr then
    if let some dim ← CompilerM.lookupDim fvarId then
      return dim
    let value? ← CompilerM.liftMetaM do
      return (← getLCtx).find? fvarId |>.bind (·.value?)
    if let some value := value? then
      return ← lowerDimExpr role value
    let rendered ← CompilerM.liftMetaM (ppExpr expr)
    CompilerM.liftMetaM $ throwError m!"Unresolved {role} {rendered} is not a declared module parameter.\n\n\
      Give the top-level Nat binder a SystemVerilog default in the synthesis \
      command, for example: #synthesizeVerilog circuit parameters [W := 8]. \
      Parameter defaults may be zero, but every derived hardware width and \
      array length must be positive."

  match expr with
  | .lit (.natVal n) => return .literal n
  | .letE _ _ value body _ => return ← lowerDimExpr role (body.instantiate1 value)
  | _ =>
    -- Inspect surface arithmetic before reduction.  `whnf` can expand Nat
    -- subtraction/min/max into recursors that no longer expose a stable
    -- constant-expression shape.
    let fn := expr.getAppFn
    let args := expr.getAppArgs
    let lowerBinary (ctor : DimExpr → DimExpr → DimExpr) : CompilerM DimExpr := do
      if args.size < 2 then
        CompilerM.liftMetaM $ throwError m!"Malformed {role}: expected two operands"
      return ctor
        (← lowerDimExpr role args[args.size - 2]!)
        (← lowerDimExpr role args[args.size - 1]!)
    if fn.isConstOf ``OfNat.ofNat && args.size >= 2 then
      return ← lowerDimExpr role args[1]!
    if fn.isConstOf ``Fin.mk && args.size >= 2 then
      return ← lowerDimExpr role args[1]!
    if fn.isConstOf ``Nat.succ && args.size >= 1 then
      return (← lowerDimExpr role args.back!) + 1
    if fn.isConstOf ``HAdd.hAdd || fn.isConstOf ``Nat.add then
      return ← lowerBinary DimExpr.mkAdd
    if fn.isConstOf ``HSub.hSub || fn.isConstOf ``Nat.sub then
      return ← lowerBinary DimExpr.mkSub
    if fn.isConstOf ``HMul.hMul || fn.isConstOf ``Nat.mul then
      return ← lowerBinary DimExpr.mkMul
    if fn.isConstOf ``HDiv.hDiv || fn.isConstOf ``Nat.div then
      return ← lowerBinary DimExpr.mkDiv
    if fn.isConstOf ``HMod.hMod || fn.isConstOf ``Nat.mod then
      return ← lowerBinary DimExpr.mkMod
    if fn.isConstOf ``HPow.hPow || fn.isConstOf ``Nat.pow then
      return ← lowerBinary DimExpr.mkPow
    if fn.isConstOf ``HShiftLeft.hShiftLeft || fn.isConstOf ``ShiftLeft.shiftLeft then
      return ← lowerBinary DimExpr.mkShl
    if fn.isConstOf ``HShiftRight.hShiftRight || fn.isConstOf ``ShiftRight.shiftRight then
      return ← lowerBinary DimExpr.mkShr
    if fn.isConstOf ``HAnd.hAnd then
      return ← lowerBinary DimExpr.mkBitAnd
    if fn.isConstOf ``HOr.hOr then
      return ← lowerBinary DimExpr.mkBitOr
    if fn.isConstOf ``HXor.hXor then
      return ← lowerBinary DimExpr.mkBitXor
    if fn.isConstOf ``Sparkle.IR.Type.DimExpr.clog2Nat && args.size >= 1 then
      return DimExpr.mkClog2 (← lowerDimExpr role args.back!)
    if fn.isConstOf ``Min.min || fn.isConstOf ``Nat.min then
      return ← lowerBinary DimExpr.mkMin
    if fn.isConstOf ``Max.max || fn.isConstOf ``Nat.max then
      return ← lowerBinary DimExpr.mkMax

    let normalized ← CompilerM.liftMetaM do
      withTransparency .reducible (whnf expr)
    if normalized != expr then
      return ← lowerDimExpr role normalized

    let rendered ← CompilerM.liftMetaM (ppExpr expr)
    CompilerM.liftMetaM $ throwError m!"Cannot lower {role} {rendered} to a SystemVerilog constant expression.\n\n\
      Supported symbolic dimension operations are addition, natural subtraction, \
      multiplication, division, remainder, power, shifts, bitwise and/or/xor, \
      ceiling-log2, min, and max."

/-- Lower a hardware dimension and reject a statically known zero. -/
def lowerPositiveDimExpr (role : String) (expr : Lean.Expr) : CompilerM DimExpr := do
  let dim ← lowerDimExpr role expr
  if dim.toNat? == some 0 then
    CompilerM.liftMetaM $ throwError m!"Cannot synthesize hardware with zero {role}.\n\n\
      Packed hardware widths and array lengths must be positive."
  return dim

def hwTypeFromDim (width : DimExpr) : HWType :=
  match width with
  | .literal 1 => .bit
  | _ => .bitVector width

partial def inferHWType (type : Lean.Expr) : CompilerM (Option HWType) := do
  let type ← CompilerM.liftMetaM (whnf type)
  match type with
  | .app (.const ``BitVec _) width =>
    let w ← lowerPositiveDimExpr "BitVec width" width
    return some (hwTypeFromDim w)
  | .const ``Bool _ =>
    return some .bit
  | .app (.app (.const ``Prod _) ty1) ty2 =>
    -- Product type: concatenate the two types
    match ← inferHWType ty1, ← inferHWType ty2 with
    | some hwType1, some hwType2 =>
      return some (hwTypeFromDim (hwType1.width + hwType2.width))
    | _, _ => return none
  | .app (.app (.const ``Sparkle.Core.Vector.HWVector _) elemType) size =>
    -- HWVector α n: extract element type and size
    let n ← lowerPositiveDimExpr "HWVector size" size
    match ← inferHWType elemType with
    | some hwElemType => return some (.array n hwElemType)
    | none => return none
  | _ =>
    return none


def inferHWTypeFromSignal? (signalType : Lean.Expr) : CompilerM (Option HWType) := do
  let signalType ← CompilerM.liftMetaM (whnf signalType)
  match signalType with
  | .app (.app signalConstr _dom) innerType =>
    match signalConstr with
    | .const name _ =>
      if name.toString.endsWith "Signal" then
        inferHWType innerType
      else
        inferHWType signalType
    | _ => inferHWType signalType
  | _ => inferHWType signalType

def inferHWTypeFromSignal (signalType : Lean.Expr) : CompilerM HWType := do
  match ← inferHWTypeFromSignal? signalType with
  | some hwType => return hwType
  | none => CompilerM.liftMetaM $ throwError s!"Cannot infer hardware type from {signalType}"

/-- Helper to extract a Nat literal or OfNat.ofNat wrap. -/
partial def extractNat (e : Lean.Expr) : CompilerM Nat := do
  CompilerM.liftMetaM (requireConcreteNat "compile-time Nat" e)

/-- Extract a concrete, positive dimension used to construct hardware. -/
def extractPositiveNat (role : String) (e : Lean.Expr) : CompilerM Nat := do
  CompilerM.liftMetaM (requirePositiveConcreteNat role e)

/-- Extract a positive concrete or parameterized hardware dimension. -/
def extractPositiveDim (role : String) (e : Lean.Expr) : CompilerM DimExpr :=
  lowerPositiveDimExpr role e

/-- Evaluate a retained hardware dimension using the module's declared
    SystemVerilog parameter defaults.  A parameterized module must have a
    concrete, valid default elaboration even though downstream users may
    override those defaults. -/
def evalDefaultDim (m : Sparkle.IR.AST.Module) (role : String) (dim : DimExpr) : MetaM Nat := do
  let lookup (name : String) : Option Nat :=
    (m.parameters.find? (fun parameter => parameter.name == name)).map (·.defaultValue)
  match dim.natValueBitWidthBound.evalUpperBoundCapped?
      lookup DimExpr.maxNatWorkWidth with
  | some value =>
      if value > DimExpr.maxNatWorkWidth then
        throwError m!"Cannot evaluate {role} '{dim}' in module '{m.name}': the natural-number working width exceeds {DimExpr.maxNatWorkWidth} bits."
  | none =>
      throwError m!"Cannot evaluate {role} '{dim}' in module '{m.name}': its natural-number working-width bound is unresolved."
  match dim.eval? lookup with
  | some value => return value
  | none => throwError m!"Cannot evaluate {role} '{dim}' in module '{m.name}' under its parameter defaults."

def validatePositiveDefaultDim (m : Sparkle.IR.AST.Module) (role : String) (dim : DimExpr) : MetaM Unit := do
  let value ← evalDefaultDim m role dim
  if value == 0 then
    throwError m!"The default parameter configuration of module '{m.name}' gives zero {role} ('{dim}').\n\n\
      Packed hardware widths and array lengths must be positive. Choose defaults \
      whose derived hardware dimensions are all greater than zero."

partial def validateHWTypeDefaults (m : Sparkle.IR.AST.Module) (role : String) : HWType → MetaM Unit
  | .bit => pure ()
  | .bitVector width => validatePositiveDefaultDim m s!"{role} width" width
  | .array size elemType => do
      validatePositiveDefaultDim m s!"{role} array length" size
      validateHWTypeDefaults m s!"{role} element" elemType

partial def validateExprDefaults (m : Sparkle.IR.AST.Module) (role : String) : Sparkle.IR.AST.Expr → MetaM Unit
  | .const _ width => validatePositiveDefaultDim m s!"{role} constant width" width
  | .paramConst value width => do
      let _ ← evalDefaultDim m s!"{role} parameter constant value" value
      validatePositiveDefaultDim m s!"{role} parameter constant width" width
  | .ref _ => pure ()
  | .op _ args | .concat args =>
      args.forM (validateExprDefaults m role)
  | .resize width value => do
      validatePositiveDefaultDim m s!"{role} resize width" width
      validateExprDefaults m role value
  | .slice expr hi lo => do
      validateExprDefaults m role expr
      let hiValue ← evalDefaultDim m s!"{role} slice high index" hi
      let loValue ← evalDefaultDim m s!"{role} slice low index" lo
      if hiValue < loValue then
        throwError m!"The default parameter configuration of module '{m.name}' gives an empty or reversed slice {hi}:{lo} in {role}."
  | .index array index => do
      validateExprDefaults m role array
      validateExprDefaults m role index

def validateStmtDefaults (m : Sparkle.IR.AST.Module) : Stmt → MetaM Unit
  | .assign lhs rhs => validateExprDefaults m s!"assignment to '{lhs}'" rhs
  | .register output _ _ input _ => validateExprDefaults m s!"register '{output}' input" input
  | .memory name addrWidth dataWidth depth _ writeAddr writeData writeEnable readAddr _ _ => do
      validatePositiveDefaultDim m s!"memory '{name}' address width" addrWidth
      validatePositiveDefaultDim m s!"memory '{name}' data width" dataWidth
      validatePositiveDefaultDim m s!"memory '{name}' depth" depth
      [writeAddr, writeData, writeEnable, readAddr].forM
        (validateExprDefaults m s!"memory '{name}' expression")
  | .inst _ instName connections _ =>
      connections.forM fun (_, expr) => validateExprDefaults m s!"instance '{instName}' connection" expr

private def systemVerilogKeywords : List String :=
  ["accept_on", "alias", "always", "always_comb", "always_ff", "always_latch",
   "and", "assert", "assign", "assume", "automatic", "before", "begin", "bind",
   "bins", "binsof", "bit", "break", "buf", "bufif0", "bufif1", "byte", "case",
   "casex", "casez", "cell", "chandle", "checker", "class", "clocking", "cmos",
   "config", "const", "constraint", "context", "continue", "cover", "covergroup",
   "coverpoint", "cross", "deassign", "default", "defparam", "design", "disable",
   "dist", "do", "edge", "else", "end", "endcase", "endchecker", "endclass",
   "endclocking", "endconfig", "endfunction", "endgenerate", "endgroup", "endinterface",
   "endmodule", "endpackage", "endprimitive", "endprogram", "endproperty", "endspecify",
   "endsequence", "endtable", "endtask", "enum", "event", "eventually", "expect",
   "export", "extends", "extern", "final", "first_match", "for", "force", "foreach",
   "forever", "fork", "forkjoin", "function", "generate", "genvar", "global", "highz0",
   "highz1", "if", "iff", "ifnone", "ignore_bins", "illegal_bins", "implements",
   "implies", "import", "incdir", "include", "initial", "inout", "input", "inside",
   "instance", "int", "integer", "interconnect", "interface", "intersect", "join",
   "join_any", "join_none", "large", "let", "liblist", "library", "local", "localparam",
   "logic", "longint", "macromodule", "matches", "medium", "modport", "module", "nand",
   "negedge", "nettype", "new", "nexttime", "nmos", "nor", "noshowcancelled", "not",
   "notif0", "notif1", "null", "or", "output", "package", "packed", "parameter",
   "pmos", "posedge", "primitive", "priority", "program", "property", "protected",
   "pull0", "pull1", "pulldown", "pullup", "pulsestyle_ondetect", "pulsestyle_onevent",
   "pure", "rand", "randc", "randcase", "randsequence", "rcmos", "real", "realtime",
   "ref", "reg", "reject_on", "release", "repeat", "restrict", "return", "rnmos",
   "rpmos", "rtran", "rtranif0", "rtranif1", "s_always", "s_eventually", "s_nexttime",
   "s_until", "s_until_with", "scalared", "sequence", "shortint", "shortreal", "showcancelled",
   "signed", "small", "solve", "specify", "specparam", "static", "string", "strong",
   "strong0", "strong1", "struct", "super", "supply0", "supply1", "sync_accept_on",
   "sync_reject_on", "table", "tagged", "task", "this", "throughout", "time",
   "timeprecision", "timeunit", "tran", "tranif0", "tranif1", "tri", "tri0", "tri1",
   "triand", "trior", "trireg", "type", "typedef", "union", "unique", "unique0",
   "unsigned", "until", "until_with", "untyped", "use", "uwire", "var", "vectored",
   "virtual", "void", "wait", "wait_order", "wand", "weak", "weak0", "weak1",
   "while", "wildcard", "wire", "with", "within", "wor", "xnor", "xor"]

private def isLegalSystemVerilogIdentifier (name : String) : Bool :=
  match name.toList with
  | [] => false
  | first :: rest =>
      (first.isAlpha || first == '_' || first == '$') &&
        rest.all (fun c => c.isAlphanum || c == '_' || c == '$') &&
        !systemVerilogKeywords.contains name

/-- Validate facts that SystemVerilog itself cannot express in a parameter
    declaration: emitted-name uniqueness and a legal default elaboration. -/
def validateParameterizedModule (m : Sparkle.IR.AST.Module) : MetaM Unit := do
  -- Run the shared capped work-width preflight before any of the detailed
  -- default checks below call `DimExpr.eval?`.  This prevents a hostile but
  -- syntactically valid default such as `K = 0xffffffff` in `1 << K` from
  -- allocating an enormous Nat during compiler-side validation.
  match m.validateDimensions with
  | .ok () => pure ()
  | .error message => throwError m!"{message}"
  for parameter in m.parameters do
    let emittedName := Sparkle.Backend.Verilog.sanitizeName parameter.name
    unless isLegalSystemVerilogIdentifier emittedName do
      throwError m!"Lean parameter '{parameter.name}' emits as invalid or reserved SystemVerilog identifier '{emittedName}' in module '{m.name}'. Rename the binder."
    let conflicts := m.parameters.filter fun (other : Sparkle.IR.AST.Parameter) =>
      other.name != parameter.name &&
        Sparkle.Backend.Verilog.sanitizeName other.name == emittedName
    if let conflict :: _ := conflicts then
      throwError m!"SystemVerilog parameter names '{parameter.name}' and '{conflict.name}' both emit as '{emittedName}' in module '{m.name}'. Rename one binder."
    let hardwareNames := (m.inputs ++ m.outputs ++ m.wires).map (fun port =>
      (port.name, Sparkle.Backend.Verilog.sanitizeName port.name))
    if let some (sourceName, _) := hardwareNames.find? (fun entry => entry.2 == emittedName) then
      throwError m!"SystemVerilog parameter '{parameter.name}' and hardware name '{sourceName}' both emit as '{emittedName}' in module '{m.name}'. Rename the parameter binder."
  for input in m.inputs do
    validateHWTypeDefaults m s!"input '{input.name}'" input.ty
  for output in m.outputs do
    validateHWTypeDefaults m s!"output '{output.name}'" output.ty
  for wire in m.wires do
    validateHWTypeDefaults m s!"wire '{wire.name}'" wire.ty
  for stmt in m.body do
    validateStmtDefaults m stmt

def validateDesignForEmission (design : Sparkle.IR.AST.Design) : MetaM Unit := do
  for module in design.modules do
    let emittedName := Sparkle.Backend.Verilog.sanitizeName module.name
    if let some conflict := design.modules.find? fun other =>
        other.name != module.name &&
          Sparkle.Backend.Verilog.sanitizeName other.name == emittedName then
      throwError m!"Module names '{module.name}' and '{conflict.name}' both emit as '{emittedName}' in SystemVerilog. Rename one definition or namespace."
  for module in design.modules do
    validateParameterizedModule module
  for module in design.modules do
    for stmt in module.body do
      match stmt with
      | .inst childName instName _ parameterOverrides =>
          let child ← match design.findModule childName with
            | some child => pure child
            | none => throwError m!"Instance '{instName}' in module '{module.name}' refers to missing child module '{childName}'."
          for (parameterName, _) in parameterOverrides do
            unless child.parameters.any (fun parameter => parameter.name == parameterName) do
              throwError m!"Instance '{instName}' overrides undeclared parameter '{parameterName}' of child module '{childName}'."
      | _ => pure ()

/-- Pair the natural-number binders of a called definition with the fully
    elaborated arguments at that call site.  These pairs are used only when a
    definition cannot be inlined and must remain a parameterized submodule. -/
partial def collectNatCallArguments (type : Lean.Expr) (args : Array Lean.Expr)
    (index : Nat := 0) : MetaM (List (String × Lean.Expr)) := do
  if h : index < args.size then
    let type ← whnf type
    match type with
    | .forallE binderName binderType body _ =>
      let arg := args[index]
      let rest ← collectNatCallArguments (body.instantiate1 arg) args (index + 1)
      let binderType ← whnf binderType
      if binderType.isConstOf ``Nat then
        return (binderName.toString, arg) :: rest
      return rest
    | _ => return []
  else
    return []

def extractBitVecLiteral? (expr : Lean.Expr) : CompilerM (Option (Nat × DimExpr)) := do
  let inspect (candidate : Lean.Expr) : CompilerM (Option (Nat × DimExpr)) := do
    let fn := candidate.getAppFn
    let args := candidate.getAppArgs
    match fn with
    | .const name _ =>
      if name == ``OfNat.ofNat && args.size >= 3 then
        let literalType ← CompilerM.liftMetaM (whnf args[0]!)
        match literalType with
        | .app (.const ``BitVec _) widthExpr =>
          let w ← extractPositiveDim "BitVec literal width" widthExpr
          let v ← extractNat args[1]!
          return some (v, w)
        | _ => return none
      else if name == ``BitVec.ofNat && args.size >= 2 then
        let w ← extractPositiveDim "BitVec literal width" args[args.size - 2]!
        let v ← extractNat args[args.size - 1]!
        return some (v, w)
      else if name == ``BitVec.ofFin && args.size >= 2 then
        let w ← extractPositiveDim "BitVec literal width" args[0]!
        let v ← extractNat args[1]!
        return some (v, w)
      else if name == ``Bool.false then
        return some (0, 1)
      else if name == ``Bool.true then
        return some (1, 1)
      else
        return none
    | _ => return none

  -- Preserve `BitVec.ofNat W value` before reduction: whnf turns it into a
  -- modulo expression whose value depends on W even when `value` is concrete.
  if let some literal ← inspect expr then
    return some literal
  let reduced ← CompilerM.liftMetaM (whnf expr)
  if reduced == expr then return none
  inspect reduced

def extractBitVecLiteral (expr : Lean.Expr) : CompilerM (Nat × DimExpr) := do
  match ← extractBitVecLiteral? expr with
  | some literal => return literal
  | none => CompilerM.liftMetaM $ throwError s!"Expected BitVec literal, got: {expr}"

/-- Recognize an all-ones `BitVec` whose value depends on its retained width.

`BitVec.ofNat W (2 ^ W - 1)` cannot be converted to a compile-time `Int` when
`W` is a native SystemVerilog parameter.  Its hardware meaning is nevertheless
width-polymorphic and exact: it is the bitwise complement of the `W`-bit zero
value.  Keep that meaning in the ordinary expression IR instead of evaluating
the source Nat at Lean elaboration time.

The bounded unfolding handles small transparent helpers such as
`Sparkle.Library.RTL.onesBV` without allowing arbitrary value-level Nat
programs to leak into the hardware constant-expression subset. -/
partial def extractSymbolicAllOnes? (expr : Lean.Expr) (fuel : Nat := 4)
    : CompilerM (Option DimExpr) := do
  let expr ← CompilerM.liftMetaM (instantiateMVars expr)
  let fn := expr.getAppFn
  let args := expr.getAppArgs
  if fn.isConstOf ``BitVec.allOnes && args.size >= 1 then
    try
      return some (← extractPositiveDim "BitVec all-ones width" args.back!)
    catch _ =>
      -- This recognizer must not replace the established literal diagnostic
      -- when the width itself is invalid or was not declared as a parameter.
      return none
  if fn.isConstOf ``BitVec.ofNat && args.size >= 2 then
    try
      let width ← extractPositiveDim "BitVec all-ones width" args[args.size - 2]!
      let valueDim ← lowerDimExpr "BitVec symbolic constant value" args.back!
      let expected := DimExpr.mkSub (DimExpr.mkPow 2 width) 1
      return if valueDim == expected then some width else none
    catch _ =>
      return none
  if fuel == 0 then return none
  let unfolded? ← CompilerM.liftMetaM (Lean.Meta.unfoldDefinition? expr)
  match unfolded? with
  | some unfolded => extractSymbolicAllOnes? unfolded (fuel - 1)
  | none => return none

/-- Recognize a `BitVec.ofNat` whose natural-number value belongs to the
    retained parameter-expression subset.  Bounded unfolding admits small
    transparent mask helpers while still refusing arbitrary value programs. -/
partial def extractParameterizedBitVecConstant? (expr : Lean.Expr) (fuel : Nat := 4)
    : CompilerM (Option (Sparkle.IR.AST.Expr × DimExpr)) := do
  let expr ← CompilerM.liftMetaM (instantiateMVars expr)
  let fn := expr.getAppFn
  let args := expr.getAppArgs
  if fn.isConstOf ``BitVec.ofNat && args.size >= 2 then
    let width ← extractPositiveDim "BitVec parameter constant width"
      args[args.size - 2]!
    let value ← lowerDimExpr "BitVec parameter constant value" args.back!
    return some <| match value.toNat? with
      | some concreteValue => (.const (Int.ofNat concreteValue) width, width)
      | none => (.paramConst value width, width)
  if fuel == 0 then return none
  let unfolded? ← CompilerM.liftMetaM (Lean.Meta.unfoldDefinition? expr)
  match unfolded? with
  | some unfolded => extractParameterizedBitVecConstant? unfolded (fuel - 1)
  | none => return none

/-- Extract a constant BitVec as hardware IR.  Concrete literals retain the
existing `.const` representation; parameter-only Nat values use `.paramConst`,
while the common all-ones idiom keeps its compact width-polymorphic `~0` form. -/
def extractBitVecConstant (expr : Lean.Expr)
    : CompilerM (Sparkle.IR.AST.Expr × DimExpr) := do
  if let some width ← extractSymbolicAllOnes? expr then
    return (.op .not [.const 0 width], width)
  if let some constant ← extractParameterizedBitVecConstant? expr then
    return constant
  let (value, width) ← extractBitVecLiteral expr
  return (.const (Int.ofNat value) width, width)

/-- Register reset values are stored as an `Int` in the current IR.  `-1` is
the exact width-independent encoding of an all-ones reset because the backend
casts it to the register's retained packed width. -/
def extractBitVecResetValue (expr : Lean.Expr) : CompilerM (Int × DimExpr) := do
  if let some width ← extractSymbolicAllOnes? expr then
    return (-1, width)
  let (value, width) ← extractBitVecLiteral expr
  return (Int.ofNat value, width)

/-- Extract a Nat literal from an expression -/
def extractNatLiteral (expr : Lean.Expr) : CompilerM (Nat × Unit) := do
  let n ← extractNat expr
  return (n, ())

/-- Extract values from a List (BitVec n) expression into an array of (value, width) pairs -/
partial def extractBitVecList (expr : Lean.Expr) : CompilerM (Array (Nat × DimExpr)) := do
  let expr ← CompilerM.liftMetaM (whnf expr)
  let fn := expr.getAppFn
  let args := expr.getAppArgs
  match fn with
  | .const name _ =>
    if name == ``List.cons && args.size >= 3 then
      let head := args[1]!
      let tail := args[2]!
      let (val, width) ← extractBitVecLiteral head
      let rest ← extractBitVecList tail
      return #[(val, width)] ++ rest
    else if name == ``List.nil then
      return #[]
    else
      CompilerM.liftMetaM $ throwError s!"Expected List.cons or List.nil, got: {name}"
  | _ =>
    CompilerM.liftMetaM $ throwError s!"Expected List expression, got: {expr}"

/-- Extract values from an Array (BitVec n) expression -/
def extractBitVecArray (expr : Lean.Expr) : CompilerM (Array (Nat × DimExpr)) := do
  let expr ← CompilerM.liftMetaM (Lean.Meta.reduce expr (skipTypes := true) (skipProofs := true))
  let fn := expr.getAppFn
  let args := expr.getAppArgs
  match fn with
  | .const name _ =>
    if name == ``Array.mk && args.size >= 2 then
      extractBitVecList args[1]!
    else if name == ``List.toArray && args.size >= 2 then
      extractBitVecList args[1]!
    else
      CompilerM.liftMetaM $ throwError s!"Expected Array.mk, got: {name} with {args.size} args"
  | _ =>
    CompilerM.liftMetaM $ throwError s!"Expected Array expression, got: {expr}"

mutual
  partial def translateExprToWire (e : Lean.Expr) (hint : String := "wire") (isTopLevel : Bool := false) (isNamed : Bool := false) : CompilerM String := do
    trace[sparkle.compiler] "translateExprToWire hint={hint} isTopLevel={isTopLevel}"
    -- 0. Handle free variables first (before any whnf)
    if let .fvar fvarId := e then
      match ← CompilerM.lookupVar fvarId with
      | some wireName => return wireName
      | none =>
        -- Check if this is a non-HW fvar (typeclass instance, config, etc.)
        -- with a value in the local context that we can inline (zeta-reduce)
        let inlinedVal ← CompilerM.liftMetaM do
          let lctx ← getLCtx
          match lctx.find? fvarId with
          | some decl => return decl.value?
          | none => return none
        match inlinedVal with
        | some val => return ← translateExprToWire val hint isTopLevel isNamed
        | none =>
          -- Try full reduction for type-level fvars (Nat widths, erased params)
          let reduced ← CompilerM.liftMetaM (try Lean.Meta.reduce e catch _ => pure e)
          if reduced != e then
            return ← translateExprToWire reduced hint isTopLevel isNamed
          let ty ← CompilerM.liftMetaM (try Lean.Meta.inferType e catch _ => pure (.const `unknown []))
          let tyPP ← CompilerM.liftMetaM (try ppExpr ty catch _ => pure s!"{ty}")
          let userName ← CompilerM.liftMetaM do
            let lctx ← getLCtx
            match lctx.find? fvarId with
            | some decl => return s!"{decl.userName}"
            | none => return "not_in_lctx"
          let st ← CompilerM.getCompilerState
          let known := st.varMap.map (fun (k,_) => k.name)
          CompilerM.liftMetaM $ throwError s!"Unbound variable: {fvarId.name} (userName={userName})\n  type: {tyPP}\n  hint: {hint}\n  known: {known}"

    let fn := e.getAppFn
    let args := e.getAppArgs


    -- 0. Early interception for Signal operators (before WHNF)
    -- When HAdd/HSub/HMul/HAnd/HOr/HXor/HShiftLeft/HShiftRight/HAppend instances
    -- are applied to Signals (or mixed Signal/BitVec), intercept before WHNF
    -- to avoid OfNat.ofNat expansion failures and domain metavariable stalls.
    if let .const instName _ := fn then
      -- General binary operator interception
      let binOp? : Option Operator := match instName with
        | ``HAdd.hAdd => some .add
        | ``HSub.hSub => some .sub
        | ``HMul.hMul => some .mul
        | ``HAnd.hAnd => some .and
        | ``HOr.hOr   => some .or
        | ``HXor.hXor => some .xor
        | ``HShiftLeft.hShiftLeft => some .shl
        | ``HShiftRight.hShiftRight => some .shr
        | _ => none
      if let some op := binOp? then
        if args.size >= 2 then
          let arg1 := args[args.size - 2]!
          let arg2 := args[args.size - 1]!
          let type1 ← CompilerM.liftMetaM (Lean.Meta.inferType arg1)
          let type2 ← CompilerM.liftMetaM (Lean.Meta.inferType arg2)
          let isSignal1 := type1.isAppOf ``Sparkle.Core.Signal.Signal
          let isSignal2 := type2.isAppOf ``Sparkle.Core.Signal.Signal
          if isSignal1 || isSignal2 then
            let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
            let hwType ← inferHWTypeFromSignal exprType
            let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
            -- Mixed Signal/BitVec operands may contain a retained-width
            -- constant such as `(2 ^ width - 1)#width`.
            let wireA ← if isSignal1 then
              translateExprToWire arg1 "op_a" (isTopLevel := false)
            else
              let (constant, cWidth) ← extractBitVecConstant arg1
              let constWire ← CompilerM.makeWire "op_const" (.bitVector cWidth)
              CompilerM.emitAssign constWire constant
              pure constWire
            let wireB ← if isSignal2 then
              translateExprToWire arg2 "op_b" (isTopLevel := false)
            else
              let (constant, cWidth) ← extractBitVecConstant arg2
              let constWire ← CompilerM.makeWire "op_const" (.bitVector cWidth)
              CompilerM.emitAssign constWire constant
              pure constWire
            CompilerM.emitAssign resWire (.op op [.ref wireA, .ref wireB])
            return resWire

      -- HAppend (concat) — separate because it uses .concat not .op
      if instName == ``HAppend.hAppend && args.size >= 2 then
        let arg1 := args[args.size - 2]!
        let arg2 := args[args.size - 1]!
        let type1 ← CompilerM.liftMetaM (Lean.Meta.inferType arg1)
        let type2 ← CompilerM.liftMetaM (Lean.Meta.inferType arg2)
        let isSignal1 := type1.isAppOf ``Sparkle.Core.Signal.Signal
        let isSignal2 := type2.isAppOf ``Sparkle.Core.Signal.Signal
        -- Both Signal case: translate directly to concat
        if isSignal1 && isSignal2 then
          let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
          let hwType ← inferHWTypeFromSignal exprType
          let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
          let wireA ← translateExprToWire arg1 "concat_hi" (isTopLevel := false)
          let wireB ← translateExprToWire arg2 "concat_lo" (isTopLevel := false)
          CompilerM.emitAssign resWire (.concat [.ref wireA, .ref wireB])
          return resWire
        -- Mixed case: one is Signal, one is BitVec constant
        if isSignal1 != isSignal2 then
          let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
          let hwType ← inferHWTypeFromSignal exprType
          let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
          if isSignal1 then
            -- Signal ++ BitVec: arg1 is signal, arg2 is constant
            let wireA ← translateExprToWire arg1 "concat_hi" (isTopLevel := false)
            let (constant, cWidth) ← extractBitVecConstant arg2
            let constWire ← CompilerM.makeWire "concat_const" (.bitVector cWidth)
            CompilerM.emitAssign constWire constant
            CompilerM.emitAssign resWire (.concat [.ref wireA, .ref constWire])
          else
            -- BitVec ++ Signal: arg1 is constant, arg2 is signal
            let (constant, cWidth) ← extractBitVecConstant arg1
            let constWire ← CompilerM.makeWire "concat_const" (.bitVector cWidth)
            CompilerM.emitAssign constWire constant
            let wireB ← translateExprToWire arg2 "concat_lo" (isTopLevel := false)
            CompilerM.emitAssign resWire (.concat [.ref constWire, .ref wireB])
          return resWire

    -- 1. High-priority Signal Recognition (Avoid premature unfolding)
    if let .const name _ := fn then
        -- OfNat.ofNat: numeric literal (e.g., 0#4, 0xFFFFF#20, 35)
        -- Must be checked BEFORE `.endsWith ".ofNat"` which would take args.back! (the instance)
        if name == ``OfNat.ofNat && args.size >= 3 then
          let type ← CompilerM.liftMetaM (whnf args[0]!)
          if let .app (.const ``BitVec _) widthExpr := type then
            let w ← extractPositiveDim "BitVec literal width" widthExpr
            let v ← extractNat args[1]!
            let resWire ← CompilerM.makeWire hint (hwTypeFromDim w) (named := isNamed)
            CompilerM.emitAssign resWire (.const v w)
            return resWire

        -- Bool constants
        if name == ``Bool.true then
          let resWire ← CompilerM.makeWire hint .bit (named := isNamed)
          CompilerM.emitAssign resWire (.const 1 1)
          return resWire
        if name == ``Bool.false then
          let resWire ← CompilerM.makeWire hint .bit (named := isNamed)
          CompilerM.emitAssign resWire (.const 0 1)
          return resWire

        -- OfNat.mk: unwrap the constructor to its value
        if name == ``OfNat.mk && args.size >= 1 then
          return ← translateExprToWire args.back! hint (isNamed := isNamed)

        -- Signal wrappers & identity casts
        -- Note: exclude OfNat.ofNat from .endsWith ".ofNat" (already handled above)
        if name == ``Sparkle.Core.Signal.Signal.mk || name == ``Sparkle.Core.Signal.Signal.val ||
           name == ``BitVec.ofFin || name == ``Fin.mk || name == ``BitVec.ofNat || name == ``BitVec.toNat ||
           name.toString.endsWith ".ofFin" ||
           (name.toString.endsWith ".ofNat" && name != ``OfNat.ofNat) ||
           name.toString.endsWith ".toNat" then
          if args.size >= 1 then
            let payload := if name == ``Fin.mk && args.size >= 2 then args[args.size-2]! else args.back!
            return ← translateExprToWire payload hint (isNamed := isNamed)

        -- Signal.clock: expose the implicit clock as a data signal (compiles to 'clk' wire reference)
        if name == ``Sparkle.Core.Signal.Signal.clock then
          -- Create a wire that references the 'clk' input directly
          let resWire ← CompilerM.makeWire hint .bit (named := isNamed)
          CompilerM.emitAssign resWire (.ref "clk")
          return resWire

        -- Signal.pure / Signal.lit (constant signals)
        if (name == ``Sparkle.Core.Signal.Signal.pure || name == ``Sparkle.Core.Signal.Signal.lit) && args.size >= 1 then
           let constValue := args[args.size-1]!
           -- Check for Bool constants first
           let constReduced ← CompilerM.liftMetaM (whnf constValue)
           if let .const boolName _ := constReduced then
             if boolName == ``Bool.true then
               let resWire ← CompilerM.makeWire hint .bit (named := isNamed)
               CompilerM.emitAssign resWire (.const 1 1)
               return resWire
             if boolName == ``Bool.false then
               let resWire ← CompilerM.makeWire hint .bit (named := isNamed)
               CompilerM.emitAssign resWire (.const 0 1)
               return resWire
           -- Check if argument is an fvar with wire mapping (let-bound constant)
           if let .fvar fvarId := constValue then
             match ← CompilerM.lookupVar fvarId with
             | some wireName => return wireName
             | none => pure ()
           -- Retain the common width-dependent all-ones mask as `~0` rather
           -- than trying to evaluate `2 ^ width - 1` in Lean.
           if let some width ← extractSymbolicAllOnes? constValue then
             let resWire ← CompilerM.makeWire hint (.bitVector width) (named := isNamed)
             CompilerM.emitAssign resWire (.op .not [.const 0 width])
             return resWire
           if let some (constant, width) ←
               extractParameterizedBitVecConstant? constValue then
             let resWire ← CompilerM.makeWire hint (.bitVector width) (named := isNamed)
             CompilerM.emitAssign resWire constant
             return resWire
           -- Shape mismatches can fall through to general expression lowering,
           -- but errors in a recognized literal (such as a symbolic width) must
           -- propagate instead of being swallowed by a catch-all fallback.
           let literal? ← extractBitVecLiteral? constValue
           let literal? ← match literal? with
             | some literal => pure (some literal)
             | none => do
               let reduced ← CompilerM.liftMetaM (reduce constValue)
               extractBitVecLiteral? reduced
           let (value, width) ← match literal? with
             | some literal => pure literal
             | none => return ← translateExprToWire constValue hint (isNamed := isNamed)
           let resWire ← CompilerM.makeWire hint (.bitVector width) (named := isNamed)
           CompilerM.emitAssign resWire (.const value width)
           return resWire

        -- bundle2
        if name == ``Sparkle.Core.Signal.bundle2 && args.size >= 2 then
           let wireA ← translateExprToWire args[args.size-2]! "a"
           let wireB ← translateExprToWire args[args.size-1]! "b"
           let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
           let hwType ← inferHWTypeFromSignal exprType
           let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
           CompilerM.emitAssign resWire (.concat [.ref wireA, .ref wireB])
           return resWire

        -- map Prod.fst/snd
        if name == ``Sparkle.Core.Signal.Signal.map && args.size >= 2 then
           let f := args[args.size-2]!
           let s := args[args.size-1]!
           let fFn := f.getAppFn
           if fFn.isConstOf ``Prod.fst then
               let wireS ← translateExprToWire s "s" (isTopLevel := false)
               let totalWidth ← CompilerM.getWireWidth wireS
               let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
               let hwType ← inferHWTypeFromSignal exprType
               let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
               let width := hwType.width
               CompilerM.emitAssign resWire (.slice (.ref wireS) (totalWidth - 1) (totalWidth - width))
               return resWire
           if fFn.isConstOf ``Prod.snd then
               let wireS ← translateExprToWire s "s" (isTopLevel := false)
               let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
               let hwType ← inferHWTypeFromSignal exprType
               let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
               let width := hwType.width
               CompilerM.emitAssign resWire (.slice (.ref wireS) (width - 1) 0)
               return resWire

           -- Handle lambda functions in Signal.map (extractLsb', unary primitives)
           if let .lam _ _ body _ := f then
             let bodyFn := body.getAppFn
             if let .const opName _ := bodyFn then
               -- BitVec.extractLsb' → slice
               if opName == ``BitVec.extractLsb' then
                 let bodyArgs := body.getAppArgs
                 if bodyArgs.size >= 4 then
                   let start ← lowerDimExpr "BitVec slice offset" bodyArgs[bodyArgs.size - 3]!
                   let len ← extractPositiveDim "BitVec slice width" bodyArgs[bodyArgs.size - 2]!
                   let wireS ← translateExprToWire s "s" (isTopLevel := false)
                   let resWire ← CompilerM.makeWire hint (.bitVector len) (named := isNamed)
                   CompilerM.emitAssign resWire (.slice (.ref wireS) (start + len - 1) start)
                   return resWire
               -- Unary primitives (neg, not) — binary ops fall through to generic fallback
               if let some op := getOperator opName then
                if op == .not || op == .neg then
                 let wireS ← translateExprToWire s "s" (isTopLevel := false)
                 let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
                 let hwType ← inferHWTypeFromSignal exprType
                 let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
                 CompilerM.emitAssign resWire (.op op [.ref wireS])
                 return resWire

           -- Generic lambda fallback: translate arbitrary lambda body as combinational logic
           if let .lam binderName binderType body _ := f then
             trace[sparkle.compiler] "→ Signal.map generic lambda fallback"
             let wireS ← translateExprToWire s "map_in" (isTopLevel := false)
             let resWire ← CompilerM.withLocalDecl binderName binderType fun fvar => do
               let fvarId := fvar.fvarId!
               CompilerM.withVarMapping fvarId wireS do
                 let bodyInst := body.instantiate1 fvar
                 translateExprToWire bodyInst hint (isNamed := isNamed)
             return resWire

        -- Detect if-then-else and match expressions that cannot be synthesized
        if name == ``ite || name == ``dite then
          let exprStr ← CompilerM.liftMetaM (ppExpr e)
          CompilerM.liftMetaM $ throwError
            "if-then-else expressions cannot be synthesized to hardware.\n\n\
            Expression: {exprStr}\n\n\
            Use Signal.mux instead:\n\
            ❌ WRONG: if cond then a else b\n\
            ✓ RIGHT:  Signal.mux cond a b\n\n\
            See Tests/TestConditionals.lean for examples."

        if name == ``Decidable.rec || name == ``Decidable.casesOn then
          CompilerM.liftMetaM $ throwError
            "Decidable.rec (from if-then-else) cannot be synthesized.\n\n\
            Use Signal.mux for hardware multiplexers:\n\
            ✓ Signal.mux (cond : Signal d Bool) (ifTrue ifFalse : Signal d α) : Signal d α\n\n\
            See Tests/TestConditionals.lean for examples."

        -- Note: unbundle pattern matching detection removed (see comment in translateExprToWireApp)

        -- Handle recursors by forcing reduction (use reduce for full beta reduction)
        if name == ``Prod.rec || name == ``Prod.casesOn then
          let e' ← CompilerM.liftMetaM (withTransparency TransparencyMode.all $ reduce e)

          -- Check if the result is: fvar proj1 proj2 (tuple destructuring continuation pattern)
          let handled ← match e' with
          | .app (.app cont arg1) arg2 =>
            if arg1.isProj && arg2.isProj then do
              -- Pattern: continuation applied to two projections
              -- Extract the base of the projections and the continuation
              let baseExpr := match arg1 with
                | .proj _ _ base => base
                | _ => arg1

              -- Translate the base expression to get the tuple wire
              let tupleWire ← translateExprToWire baseExpr "tuple" (isTopLevel := false)

              -- Infer component types from the continuation lambda types
              let (ty1, ty2) ← match cont with
                | .lam _ t1 (.lam _ t2 _ _) _ => pure (t1, t2)
                | .lam _ _ _ _ =>
                  CompilerM.liftMetaM $ throwError
                    "Cannot infer the second component type while lowering Prod.rec; refusing to guess a tuple width."
                | _ => CompilerM.liftMetaM $ throwError "Expected lambda in Prod.rec continuation"

              let hwType1 ← inferHWTypeFromSignal ty1
              let hwType2 ← inferHWTypeFromSignal ty2
              let width1 := hwType1.width
              let width2 := hwType2.width

              -- Extract the two components
              let wire1 ← CompilerM.makeWire (hint ++ "_fst") hwType1
              let wire2 ← CompilerM.makeWire (hint ++ "_snd") hwType2
              CompilerM.emitAssign wire1 (.slice (.ref tupleWire) (width1 + width2 - 1) width2)
              CompilerM.emitAssign wire2 (.slice (.ref tupleWire) (width2 - 1) 0)

              -- Now we need to apply the continuation with these wires
              -- The continuation should be a lambda (or nested lambdas)
              let result ← match cont with
              | .lam n1 ty1 body1 _ =>
                -- Single lambda - check if body is another lambda
                match body1 with
                | .lam n2 ty2 body2 _ =>
                  -- Nested lambdas: substitute both parameters
                  CompilerM.withLocalDecl n1 ty1 fun fvar1 => do
                    CompilerM.withVarMapping fvar1.fvarId! wire1 do
                      let body1Inst := body2.instantiate1 fvar1
                      CompilerM.withLocalDecl n2 ty2 fun fvar2 => do
                        CompilerM.withVarMapping fvar2.fvarId! wire2 do
                          let body2Inst := body1Inst.instantiate1 fvar2
                          translateExprToWire body2Inst hint isTopLevel isNamed
                | _ =>
                  -- Single lambda body - substitute just the first parameter
                  CompilerM.withLocalDecl n1 ty1 fun fvar1 => do
                    CompilerM.withVarMapping fvar1.fvarId! wire1 do
                      let bodyInst := body1.instantiate1 fvar1
                      translateExprToWire bodyInst hint isTopLevel isNamed
              | .fvar contId =>
                -- The continuation is an fvar - check if it has a value in the local context
                let contValue? ← CompilerM.liftMetaM do
                  let lctx ← getLCtx
                  match lctx.find? contId with
                  | some decl => return decl.value?
                  | none => return none

                match contValue? with
                | some contExpr =>
                  -- The fvar has a value - it should be a lambda
                  match contExpr with
                  | .lam n1 ty1 (.lam n2 ty2 body _) _ =>
                    CompilerM.withLocalDecl n1 ty1 fun fvar1 => do
                      CompilerM.withVarMapping fvar1.fvarId! wire1 do
                        let body1 := body.instantiate1 fvar1
                        CompilerM.withLocalDecl n2 ty2 fun fvar2 => do
                          CompilerM.withVarMapping fvar2.fvarId! wire2 do
                            let body2 := body1.instantiate1 fvar2
                            translateExprToWire body2 hint isTopLevel isNamed
                  | _ =>
                    CompilerM.liftMetaM $ throwError s!"Expected nested lambda in continuation, got: {contExpr}"
                | none =>
                  CompilerM.liftMetaM $ throwError s!"Continuation fvar {contId.name} has no value in context"
              | _ =>
                CompilerM.liftMetaM $ throwError s!"Unexpected continuation type: {cont}"
              pure (some result)
            else if e' != e then do
              let result ← translateExprToWire e' hint (isTopLevel := false) (isNamed := isNamed)
              pure (some result)
            else
              pure none
          | _ =>
            if e' != e then do
              let result ← translateExprToWire e' hint (isTopLevel := false) (isNamed := isNamed)
              pure (some result)
            else
              pure none

          -- If we successfully handled it, return the result
          match handled with
          | some wire => return wire
          | none => pure ()

        -- Handle Seq.seq and Functor.map which might appear if Signal.ap reduces
        if name == ``Seq.seq && args.size >= 2 then
            let sf := args[args.size-2]!
            let b := args[args.size-1]!
            let sfFn := sf.getAppFn
            if sfFn.isConstOf ``Functor.map && sf.getAppArgs.size >= 2 then
                let fmapArgs := sf.getAppArgs
                let f := fmapArgs[fmapArgs.size-2]!
                let a := fmapArgs[fmapArgs.size-1]!
                let wireA ← translateExprToWire a "a" (isTopLevel := false)
                let wireB ← translateExprToWire b "b" (isTopLevel := false)
                -- Get op name from lambda body
                let opName ← getPrimitiveNameFromLambda f
                match getOperator opName with
                | some op =>
                   -- Infer result type from the expression type
                   let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
                   let hwType ← inferHWTypeFromSignal exprType
                   let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
                   CompilerM.emitAssign resWire (.op op [.ref wireA, .ref wireB])
                   return resWire
                | none =>
                   -- Special: BitVec.append / HAppend → concat
                   if opName == ``HAppend.hAppend || opName == ``BitVec.append then
                     let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
                     let hwType ← inferHWTypeFromSignal exprType
                     let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
                     CompilerM.emitAssign resWire (.concat [.ref wireA, .ref wireB])
                     return resWire
                   -- Special: BitVec.sshiftRight → asr
                   if opName == ``BitVec.sshiftRight then
                     let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
                     let hwType ← inferHWTypeFromSignal exprType
                     let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
                     CompilerM.emitAssign resWire (.op .asr [.ref wireA, .ref wireB])
                     return resWire
                   pure ()

        if name == ``Functor.map && args.size >= 2 then
             let f := args[args.size-2]!
             let a := args[args.size-1]!

             -- Try to extract lambda body for partial application detection
             match f with
             | .lam _ _ body _ =>
               let bodyApp := body
               let bodyFn := bodyApp.getAppFn

               -- Check if it's a primitive operation
               if let .const opName _ := bodyFn then
                 -- Special: BitVec.extractLsb' → slice (unary on signal, start/len are constants)
                 if opName == ``BitVec.extractLsb' then
                   let bodyArgs := bodyApp.getAppArgs
                   if bodyArgs.size >= 4 then
                     let start ← lowerDimExpr "BitVec slice offset" bodyArgs[bodyArgs.size - 3]!
                     let len ← extractPositiveDim "BitVec slice width" bodyArgs[bodyArgs.size - 2]!
                     let wireA ← translateExprToWire a "a" (isTopLevel := false)
                     let resWire ← CompilerM.makeWire hint (.bitVector len) (named := isNamed)
                     CompilerM.emitAssign resWire (.slice (.ref wireA) (start + len - 1) start)
                     return resWire

                 -- Simple unary map: NOT, NEG (may have extra typeclass/type args)
                 if let some op := getOperator opName then
                   if op == .not || op == .neg then
                     let wireA ← translateExprToWire a "a" (isTopLevel := false)
                     let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
                     let hwType ← inferHWTypeFromSignal exprType
                     let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
                     CompilerM.emitAssign resWire (.op op [.ref wireA])
                     return resWire

                 -- Binary operation in lambda with one constant and one bvar:
                 -- e.g., (fun d => (0#24 ++ d)) <$> sig  or  (fun x => x + 1#8) <$> sig
                 let bodyArgs := bodyApp.getAppArgs
                 if bodyArgs.size >= 2 then
                   let arg1 := bodyArgs[bodyArgs.size - 2]!
                   let arg2 := bodyArgs[bodyArgs.size - 1]!
                   let arg1HasBVar := arg1.hasLooseBVars
                   let arg2HasBVar := arg2.hasLooseBVars
                   -- Exactly one argument should reference the lambda parameter
                   if arg1HasBVar != arg2HasBVar then
                     let wireA ← translateExprToWire a "a" (isTopLevel := false)
                     -- Check for concat (HAppend.hAppend / BitVec.append)
                     if opName == ``HAppend.hAppend || opName == ``BitVec.append then
                       let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
                       let hwType ← inferHWTypeFromSignal exprType
                       if arg1HasBVar then
                         -- (fun d => d ++ const) — signal is high bits
                         let (constant, cWidth) ← extractBitVecConstant arg2
                         let constWire ← CompilerM.makeWire "lambda_const" (.bitVector cWidth)
                         CompilerM.emitAssign constWire constant
                         let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
                         CompilerM.emitAssign resWire (.concat [.ref wireA, .ref constWire])
                         return resWire
                       else
                         -- (fun d => const ++ d) — signal is low bits
                         let (constant, cWidth) ← extractBitVecConstant arg1
                         let constWire ← CompilerM.makeWire "lambda_const" (.bitVector cWidth)
                         CompilerM.emitAssign constWire constant
                         let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
                         CompilerM.emitAssign resWire (.concat [.ref constWire, .ref wireA])
                         return resWire
                     -- Other binary primitives (add, sub, and, or, xor, etc.)
                     if let some op := getOperator opName then
                       let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
                       let hwType ← inferHWTypeFromSignal exprType
                       if arg1HasBVar then
                         -- (fun x => x + const)
                         let (constant, cWidth) ← extractBitVecConstant arg2
                         let constWire ← CompilerM.makeWire "lambda_const" (.bitVector cWidth)
                         CompilerM.emitAssign constWire constant
                         let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
                         CompilerM.emitAssign resWire (.op op [.ref wireA, .ref constWire])
                         return resWire
                       else
                         -- (fun x => const + x)
                         let (constant, cWidth) ← extractBitVecConstant arg1
                         let constWire ← CompilerM.makeWire "lambda_const" (.bitVector cWidth)
                         CompilerM.emitAssign constWire constant
                         let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
                         CompilerM.emitAssign resWire (.op op [.ref constWire, .ref wireA])
                         return resWire

                 -- Remaining unary primitives (non-NOT/NEG) handled here
                 if let some op := getOperator opName then
                   let bodyArgs := bodyApp.getAppArgs
                   -- Only if the body has exactly 1 loose-bvar arg (the lambda param)
                   let numBVarArgs := bodyArgs.toList.filter (·.hasLooseBVars) |>.length
                   if numBVarArgs ≤ 1 then
                     let wireA ← translateExprToWire a "a" (isTopLevel := false)
                     let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
                     let hwType ← inferHWTypeFromSignal exprType
                     let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
                     CompilerM.emitAssign resWire (.op op [.ref wireA])
                     return resWire
             | _ => pure ()


    -- Check if expression contains any of our mapped fvars (skip whnf if so)
    let varMap ← CompilerM.getCompilerState
    let hasMappedFvar := e.find? (fun sub =>
      match sub with
      | .fvar fid => varMap.varMap.any (fun (vid, _) => vid == fid)
      | _ => false
    ) |>.isSome

    -- 2. Fallback to normal reduction (only if no mapped fvars)
    --    Exception: lambda applications (beta-redexes) are always reduced with
    --    reducible transparency, which beta-reduces without unfolding Signal
    --    primitives (mux, register, memory). This handles local function inlining
    --    (e.g., `let f := fun x => ... Signal.mux ...; f arg`).
    let isBetaRedex := e.isApp && e.getAppFn.isLambda
    let e ← if !hasMappedFvar || isBetaRedex then
              CompilerM.liftMetaM (withTransparency TransparencyMode.reducible $ whnf e)
            else pure e
    let fn := e.getAppFn


    match e with
    | .app .. =>
      if let .const _ _ := fn then
         translateExprToWireApp e hint isNamed
      else
         -- Manual Zeta Reduction: Check if head is a local definition (let-bound)
         let zetaE ← if let .fvar fvarId := fn then
             CompilerM.liftMetaM do
                let lctx ← getLCtx
                match lctx.find? fvarId with
                | some decl =>
                   match decl.value? with
                   | some val =>
                      return some (e.replaceFVarId fvarId val)
                   | none =>
                      return none
                | none => return none
           else pure none

         match zetaE with
         | some e' => translateExprToWire e' hint (isTopLevel := isTopLevel) (isNamed := isNamed)
         | none =>
            -- Fallback to general reduction (use default transparency to preserve
            -- Signal.pure and mixed operator instance structure)
            let e' ← CompilerM.liftMetaM (withTransparency TransparencyMode.default $ whnf e)
            if e' != e then translateExprToWire e' hint (isTopLevel := isTopLevel) (isNamed := isNamed)
            else translateExprToWireApp e hint isNamed

    | .proj _ idx eStruct => do
      let wireS ← translateExprToWire eStruct "s"
      -- Infer result type from the expression type
      let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
      let hwType ← inferHWTypeFromSignal exprType
      let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
      let width := hwType.width
      let lo := (1 - idx) * width
      let hi := lo + width - 1
      CompilerM.emitAssign resWire (.slice (.ref wireS) hi lo)
      return resWire

    | .lit (.natVal n) => do
      -- Infer result type from the expression type
      let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
      let hwType ← inferHWTypeFromSignal exprType
      let width := hwType.width
      let wire ← CompilerM.makeWire hint hwType (named := isNamed)
      CompilerM.emitAssign wire (.const (Int.ofNat n) width)
      return wire

    | .fvar fvarId => do
      match ← CompilerM.lookupVar fvarId with
      | some wireName => return wireName
      | none =>
        let st ← CompilerM.getCompilerState
        let known := st.varMap.map (fun (k,_) => k.name)
        CompilerM.liftMetaM $ throwError s!"Unbound variable: {fvarId.name}. Known: {known}"

    | .letE name type value body _ => do
      -- For any let binding, just use normal let handling
      if let some _ ← inferHWTypeFromSignal? type then
        -- Hardware let: translate value to wire
        let valueWire ← translateExprToWire value name.toString (isTopLevel := false) (isNamed := true)
        CompilerM.withLocalDecl name type fun fvar => do
          let fvarId := fvar.fvarId!
          CompilerM.withVarMapping fvarId valueWire do
            let bodyInst := body.instantiate1 fvar
            translateExprToWire bodyInst hint isTopLevel isNamed
      else
        -- Logic let: add to context for reduction (zeta)
        -- This allows let-bound values to be inlined when referenced
        CompilerM.withLetDecl name type value fun fvar => do
          let bodyInst := body.instantiate1 fvar
          translateExprToWire bodyInst hint isTopLevel isNamed

    | .lam binderName binderType body _ => do
      match ← inferHWTypeFromSignal? binderType with
      | some hwType =>
          let paramWire ← CompilerM.makeWire binderName.toString hwType (named := true)
          -- Only add as input if this is a top-level function parameter
          if isTopLevel then
            CompilerM.addInput paramWire hwType

          -- Process the lambda body within a proper local context
          CompilerM.withLocalDecl binderName binderType fun fvar => do
            let fvarId := fvar.fvarId!
            CompilerM.withVarMapping fvarId paramWire do
              let bodyInst := body.instantiate1 fvar
              -- Nested lambdas are also top-level if they're part of the function signature
              translateExprToWire bodyInst hint isTopLevel isNamed
      | none =>
          let binderTypeWhnf ← CompilerM.liftMetaM (whnf binderType)
          let parameterName := binderName.toString
          let compilerState ← CompilerM.getCompilerState
          if isTopLevel && binderTypeWhnf.isConstOf ``Nat then
            match compilerState.parameterDefaults.lookup parameterName with
            | some defaultValue =>
              CompilerM.addParameter parameterName defaultValue
              CompilerM.withLocalDecl binderName binderType fun fvar => do
                let bodyInst := body.instantiate1 fvar
                CompilerM.withDimMapping fvar.fvarId! (.param parameterName) do
                  translateExprToWire bodyInst hint isTopLevel isNamed
            | none =>
              -- Preserve old behavior for unused logic parameters.  If this
              -- binder reaches a hardware dimension, lowerDimExpr reports the
              -- missing explicit SystemVerilog default at that use site.
              CompilerM.withLocalDecl binderName binderType fun fvar => do
                let bodyInst := body.instantiate1 fvar
                translateExprToWire bodyInst hint isTopLevel isNamed
          else
            -- Logic argument (e.g. clock-domain config): no wire/input.
            CompilerM.withLocalDecl binderName binderType fun fvar => do
              let bodyInst := body.instantiate1 fvar
              translateExprToWire bodyInst hint isTopLevel isNamed


    | _ =>
      translateExprToWireApp e hint isNamed

  -- ===========================================================================
  -- Handler functions: each handles a category of expressions in translateExprToWireApp.
  -- Returns `some wireName` if handled, `none` if not applicable.
  -- ===========================================================================

  /-- Detect unsynthesizable patterns (if-then-else, Decidable) and throw errors -/
  partial def handleErrorPatterns (_e : Lean.Expr) (name : Name) (_args : Array Lean.Expr) (_hint : String) (_isNamed : Bool) : CompilerM Unit := do
    if name == ``ite || name == ``dite then
      let exprStr ← CompilerM.liftMetaM (ppExpr _e)
      CompilerM.liftMetaM $ throwError
        "if-then-else expressions cannot be synthesized to hardware.\n\n\
        Expression: {exprStr}\n\n\
        Use Signal.mux instead:\n\
        ❌ WRONG: if cond then a else b\n\
        ✓ RIGHT:  Signal.mux cond a b\n\n\
        See Tests/TestConditionals.lean for examples."
    if name == ``Decidable.rec || name == ``Decidable.casesOn then
      CompilerM.liftMetaM $ throwError
        "Decidable.rec (from if-then-else) cannot be synthesized.\n\n\
        Use Signal.mux for hardware multiplexers:\n\
        ✓ Signal.mux (cond : Signal d Bool) (ifTrue ifFalse : Signal d α) : Signal d α\n\n\
        See Tests/TestConditionals.lean for examples."

  /-- Handle Signal.fst, Signal.snd, Signal.map Prod.fst/Prod.snd -/
  partial def handleTupleProjections (e : Lean.Expr) (name : Name) (args : Array Lean.Expr) (hint : String) (isNamed : Bool) : CompilerM (Option String) := do
    -- Signal.fst (new readable syntax)
    if name == ``Sparkle.Core.Signal.Signal.fst && args.size >= 1 then
      trace[sparkle.compiler] "→ tuple projection (fst)"
      let s := args[args.size-1]!
      let wireS ← translateExprToWire s "s" (isTopLevel := false)
      let totalWidth ← CompilerM.getWireWidth wireS
      let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
      let hwType ← inferHWTypeFromSignal exprType
      let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
      let width := hwType.width
      CompilerM.emitAssign resWire (.slice (.ref wireS) (totalWidth - 1) (totalWidth - width))
      return some resWire

    -- Signal.snd (new readable syntax)
    if name == ``Sparkle.Core.Signal.Signal.snd && args.size >= 1 then
      trace[sparkle.compiler] "→ tuple projection (snd)"
      let s := args[args.size-1]!
      let wireS ← translateExprToWire s "s" (isTopLevel := false)
      let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
      let hwType ← inferHWTypeFromSignal exprType
      let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
      let width := hwType.width
      CompilerM.emitAssign resWire (.slice (.ref wireS) (width - 1) 0)
      return some resWire

    -- Signal.map f s: apply pure function f combinationally to signal s
    if name == ``Sparkle.Core.Signal.Signal.map && args.size >= 2 then
      let f := args[args.size-2]!
      let s := args[args.size-1]!
      trace[sparkle.compiler] "→ Signal.map handler: f.ctorName={f.ctorName}"

      -- Fast path: Prod.fst/snd projections
      if f.isConstOf ``Prod.fst then
        trace[sparkle.compiler] "→ tuple projection (map fst)"
        let wireS ← translateExprToWire s "s" (isTopLevel := false)
        let totalWidth ← CompilerM.getWireWidth wireS
        let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
        let hwType ← inferHWTypeFromSignal exprType
        let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
        let width := hwType.width
        CompilerM.emitAssign resWire (.slice (.ref wireS) (totalWidth - 1) (totalWidth - width))
        return some resWire
      if f.isConstOf ``Prod.snd then
        trace[sparkle.compiler] "→ tuple projection (map snd)"
        let wireS ← translateExprToWire s "s" (isTopLevel := false)
        let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
        let hwType ← inferHWTypeFromSignal exprType
        let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
        let width := hwType.width
        CompilerM.emitAssign resWire (.slice (.ref wireS) (width - 1) 0)
        return some resWire

      -- Generic fallback: translate f applied to the signal's wire value
      -- Works for both lambdas (fun b => ...) and partial applications (BitVec.extractLsb' 0 1)
      trace[sparkle.compiler] "→ Signal.map generic fallback"
      let wireS ← translateExprToWire s "map_in" (isTopLevel := false)
      -- Infer the inner type of the signal (the pure type that f operates on)
      let sType ← CompilerM.liftMetaM (Lean.Meta.inferType s)
      let innerType ← CompilerM.liftMetaM do
        let sType ← whnf sType
        match sType with
        | .app (.app _ _dom) inner => return inner
        | _ => throwError s!"Signal.map: cannot infer inner type from {sType}"
      let resWire ← CompilerM.withLocalDecl `map_arg innerType fun fvar => do
        let fvarId := fvar.fvarId!
        CompilerM.withVarMapping fvarId wireS do
          -- Apply f to the fvar: this handles both lambdas and partial applications
          let applied := Lean.mkApp f fvar
          -- Beta-reduce if f is a lambda (use reducible to avoid over-unfolding)
          let applied ← CompilerM.liftMetaM (Lean.Meta.withTransparency .reducible $ Lean.Meta.whnf applied)
          translateExprToWire applied hint (isNamed := isNamed)
      return some resWire

    return none

  /-- Handle Signal.ap — binary op lifting, concat/sshiftRight special cases -/
  partial def handleApplicative (e : Lean.Expr) (name : Name) (args : Array Lean.Expr) (hint : String) (isNamed : Bool) : CompilerM (Option String) := do
    if name == ``Sparkle.Core.Signal.Signal.ap && args.size >= 2 then
      let sf := args[args.size-2]!
      let b := args[args.size-1]!
      let sfFn := sf.getAppFn
      let sfArgs := sf.getAppArgs
      if sfFn.isConstOf ``Sparkle.Core.Signal.Signal.map && sfArgs.size >= 2 then
        trace[sparkle.compiler] "→ applicative (Signal.ap)"
        let f := sfArgs[sfArgs.size-2]!
        let a := sfArgs[sfArgs.size-1]!
        let wireA ← translateExprToWire a "a"
        let wireB ← translateExprToWire b "b"
        let opName ← getPrimitiveNameFromLambda f
        match getOperator opName with
        | some op =>
          let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
          let hwType ← inferHWTypeFromSignal exprType
          let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
          CompilerM.emitAssign resWire (.op op [.ref wireA, .ref wireB])
          return some resWire
        | none =>
          -- Special: BitVec.append / HAppend → concat
          if opName == ``HAppend.hAppend || opName == ``BitVec.append then
            let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
            let hwType ← inferHWTypeFromSignal exprType
            let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
            CompilerM.emitAssign resWire (.concat [.ref wireA, .ref wireB])
            return some resWire
          -- Special: BitVec.sshiftRight → asr (Nat arg handled via signal wire)
          if opName == ``BitVec.sshiftRight then
            let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
            let hwType ← inferHWTypeFromSignal exprType
            let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
            CompilerM.emitAssign resWire (.op .asr [.ref wireA, .ref wireB])
            return some resWire
          CompilerM.liftMetaM $ throwError s!"Complex lift of {opName} not yet supported: operator not found"
    return none

  /-- Handle BitVec.extractLsb', shifts, concat, isPrimitive dispatch -/
  partial def handleBitVecOps (e : Lean.Expr) (name : Name) (args : Array Lean.Expr) (hint : String) (isNamed : Bool) : CompilerM (Option String) := do
    -- BitVec.extractLsb': bit slice extraction
    if name == ``BitVec.extractLsb' && args.size >= 4 then
      trace[sparkle.compiler] "→ extractLsb'"
      let start ← lowerDimExpr "BitVec slice offset" args[args.size - 3]!
      let len ← extractPositiveDim "BitVec slice width" args[args.size - 2]!
      let bvWire ← translateExprToWire args[args.size - 1]! "slice_src"
      let resWire ← CompilerM.makeWire hint (.bitVector len) (named := isNamed)
      CompilerM.emitAssign resWire (.slice (.ref bvWire) (start + len - 1) start)
      return some resWire

    -- BitVec.getLsb: single bit extraction → slice of width 1
    -- getLsb x i  ≡  extractLsb' i 1 x  (returns Bool, we emit a 1-bit slice)
    if name == ``BitVec.getLsb && args.size >= 3 then
      trace[sparkle.compiler] "→ getLsb"
      let idx ← lowerDimExpr "BitVec bit index" args[args.size - 1]!
      let bvWire ← translateExprToWire args[args.size - 2]! "getlsb_src"
      let resWire ← CompilerM.makeWire hint .bit (named := isNamed)
      CompilerM.emitAssign resWire (.slice (.ref bvWire) idx idx)
      return some resWire

    -- BitVec.shiftLeft / BitVec.ushiftRight / BitVec.sshiftRight
    if (name == ``BitVec.shiftLeft || name == ``BitVec.ushiftRight || name == ``BitVec.sshiftRight)
        && args.size >= 3 then
      trace[sparkle.compiler] "→ shift op {name}"
      let bvExpr := args[args.size - 2]!
      let natExpr := args[args.size - 1]!
      let wire1 ← translateExprToWire bvExpr "shift_a"
      let amountExpr ← translateShiftAmount natExpr "shift_b"
      let op := if name == ``BitVec.shiftLeft then Operator.shl
                else if name == ``BitVec.ushiftRight then Operator.shr
                else Operator.asr
      let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
      let hwType ← inferHWTypeFromSignal exprType
      let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
      CompilerM.emitAssign resWire (.op op [.ref wire1, amountExpr])
      return some resWire

    -- BitVec.append / HAppend.hAppend: concatenation
    if (name == ``HAppend.hAppend || name == ``BitVec.append) && args.size >= 2 then
      trace[sparkle.compiler] "→ concat"
      let hiWire ← translateExprToWire args[args.size - 2]! "concat_hi"
      let loWire ← translateExprToWire args[args.size - 1]! "concat_lo"
      let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
      let hwType ← inferHWTypeFromSignal exprType
      let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
      CompilerM.emitAssign resWire (.concat [.ref hiWire, .ref loWire])
      return some resWire

    -- BitVec.zeroExtend / BitVec.setWidth: unsigned resize in either direction
    if (name == ``BitVec.zeroExtend || name == ``BitVec.setWidth) && args.size >= 2 then
      trace[sparkle.compiler] "→ setWidth/unsigned resize"
      let targetWidth ← extractPositiveDim "BitVec target width" args[args.size - 2]!
      let srcWire ← translateExprToWire args[args.size - 1]! "zext_src"
      let resWire ← CompilerM.makeWire hint (.bitVector targetWidth) (named := isNamed)
      -- Preserve the conversion as an explicit IR operation.  This matters
      -- when the value is later inlined or embedded in a concat: assignment
      -- context alone is not a stable representation of the resize boundary.
      CompilerM.emitAssign resWire (.resize targetWidth (.ref srcWire))
      return some resWire

    -- isPrimitive dispatch
    if isPrimitive name then
      trace[sparkle.compiler] "→ primitive {name}"
      match getOperator name with
      | some op =>
        -- Unary operators: NOT, NEG, Complement.complement
        -- These may have extra typeclass/type args before the actual signal arg
        let isUnary := op == .not || op == .neg
        if isUnary && args.size >= 1 then
           let wire1 ← translateExprToWire args[args.size-1]! "arg1"
           let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
           let hwType ← inferHWTypeFromSignal exprType
           let resultWire ← CompilerM.makeWire hint hwType (named := isNamed)
           CompilerM.emitAssign resultWire (.op op [.ref wire1])
           return some resultWire
        else if args.size >= 2 then
          let wire1 ← translateExprToWire args[args.size-2]! "arg1"
          let wire2 ← translateExprToWire args[args.size-1]! "arg2"
          let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
          let hwType ← inferHWTypeFromSignal exprType
          let resultWire ← CompilerM.makeWire hint hwType (named := isNamed)
          CompilerM.emitAssign resultWire (.op op [.ref wire1, .ref wire2])
          return some resultWire
      | none =>
        CompilerM.liftMetaM $ throwError s!"Internal error: {name} is marked as primitive but has no operator"

    return none

  /-- Handle Signal.register, Signal.registerNeg, Signal.registerWithEnable -/
  partial def handleRegister (e : Lean.Expr) (name : Name) (args : Array Lean.Expr) (hint : String) (isNamed : Bool) : CompilerM (Option String) := do
    if name.toString.endsWith ".register" && args.size >= 2 then
      trace[sparkle.compiler] "→ register"
      let init := args[args.size-2]!
      let input := args[args.size-1]!
      let (initVal, _) ← extractBitVecResetValue init
      let inputWire ← translateExprToWire input "reg_input"
      let exprType ← CompilerM.liftMetaM (inferType e)
      let hwType ← inferHWTypeFromSignal exprType
      let w ← CompilerM.emitRegister hint "clk" "rst" (.ref inputWire) initVal hwType (named := isNamed)
      return some w

    -- Signal.registerNeg: negedge-triggered register
    if name.toString.endsWith ".registerNeg" && args.size >= 2 then
      trace[sparkle.compiler] "→ registerNeg"
      let init := args[args.size-2]!
      let input := args[args.size-1]!
      let (initVal, _) ← extractBitVecResetValue init
      let inputWire ← translateExprToWire input "reg_input"
      let exprType ← CompilerM.liftMetaM (inferType e)
      let hwType ← inferHWTypeFromSignal exprType
      -- Use "clk__neg" as clock name; backend detects suffix and emits @(negedge clk)
      let w ← CompilerM.emitRegister hint "clk__neg" "rst" (.ref inputWire) initVal hwType (named := isNamed)
      return some w

    -- Signal.registerNoReset: posedge-triggered register without reset port
    if name.toString.endsWith ".registerNoReset" && args.size >= 2 then
      trace[sparkle.compiler] "→ registerNoReset"
      let init := args[args.size-2]!
      let input := args[args.size-1]!
      let (initVal, _) ← extractBitVecResetValue init
      let inputWire ← translateExprToWire input "reg_input"
      let exprType ← CompilerM.liftMetaM (inferType e)
      let hwType ← inferHWTypeFromSignal exprType
      -- Use "clk__norst" as clock name; backend detects suffix and emits @(posedge clk) without reset
      let w ← CompilerM.emitRegister hint "clk__norst" "rst" (.ref inputWire) initVal hwType (named := isNamed)
      return some w

    -- Signal.registerWithEnable: register with conditional update
    if name.toString.endsWith ".registerWithEnable" && args.size >= 3 then
      trace[sparkle.compiler] "→ registerWithEnable"
      let init := args[args.size-3]!
      let en := args[args.size-2]!
      let input := args[args.size-1]!
      let (initVal, _) ← extractBitVecResetValue init
      let enWire ← translateExprToWire en "reg_en"
      let inputWire ← translateExprToWire input "reg_input"
      let exprType ← CompilerM.liftMetaM (inferType e)
      let hwType ← inferHWTypeFromSignal exprType
      let muxWire ← CompilerM.makeWire (hint ++ "_mux") hwType
      let regWire ← CompilerM.emitRegister hint "clk" "rst" (.ref muxWire) initVal hwType (named := isNamed)
      CompilerM.emitAssign muxWire (.op .mux [.ref enWire, .ref inputWire, .ref regWire])
      return some regWire

    return none

  /-- Handle Signal.mux, lutMuxTree -/
  partial def handleMux (e : Lean.Expr) (name : Name) (args : Array Lean.Expr) (hint : String) (isNamed : Bool) : CompilerM (Option String) := do
    -- lutMuxTree: generate mux chain from concrete lookup table
    if name.toString.endsWith ".lutMuxTree" && args.size >= 5 then
      trace[sparkle.compiler] "→ lutMuxTree"
      let tableArg := args[args.size-2]!
      let indexArg := args[args.size-1]!
      let tableValues ← extractBitVecArray tableArg
      if tableValues.size > 0 then
        let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
        let hwType ← inferHWTypeFromSignal exprType
        let (_, dataWidth) := tableValues[0]!
        let indexType ← CompilerM.liftMetaM (Lean.Meta.inferType indexArg)
        let indexHwType ← inferHWTypeFromSignal indexType
        let indexWidth := indexHwType.width
        let indexWire ← translateExprToWire indexArg "lut_idx"
        let mut resultWire ← CompilerM.makeWire (hint ++ "_d") hwType
        CompilerM.emitAssign resultWire (.const tableValues[0]!.1 dataWidth)
        for i in [:tableValues.size] do
          let (val, _) := tableValues[i]!
          let eqWire ← CompilerM.makeWire s!"{hint}_eq{i}" (.bitVector 1)
          CompilerM.emitAssign eqWire (.op .eq [.ref indexWire, .const i indexWidth])
          let muxWire ← CompilerM.makeWire s!"{hint}_m{i}" hwType
          CompilerM.emitAssign muxWire (.op .mux [.ref eqWire, .const val dataWidth, .ref resultWire])
          resultWire := muxWire
        return some resultWire

    -- Signal.mux
    if name.toString.endsWith ".mux" && args.size >= 3 then
      trace[sparkle.compiler] "→ mux"
      let cond := args[args.size-3]!
      let thenSig := args[args.size-2]!
      let elseSig := args[args.size-1]!
      let cW ← translateExprToWire cond "mux_cond"
      let tW ← translateExprToWire thenSig "mux_then"
      let eW ← translateExprToWire elseSig "mux_else"
      let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
      let hwType ← inferHWTypeFromSignal exprType
      let rW ← CompilerM.makeWire hint hwType (named := isNamed)
      CompilerM.emitAssign rW (.op .mux [.ref cW, .ref tW, .ref eW])
      return some rW

    return none

  /-- Handle Signal.memory, Signal.memoryComboRead -/
  partial def handleMemory (_e : Lean.Expr) (name : Name) (args : Array Lean.Expr) (hint : String) (isNamed : Bool) : CompilerM (Option String) := do
    -- Signal.memory: synchronous RAM/BRAM
    if name.toString.endsWith ".memory" && !name.toString.endsWith ".memoryComboRead" && args.size >= 4 then
      trace[sparkle.compiler] "→ memory (sync)"
      let addrWidthArg := args[args.size-6]!
      let dataWidthArg := args[args.size-5]!
      let addrWidth ← extractPositiveDim "memory address width" addrWidthArg
      let dataWidth ← extractPositiveDim "memory data width" dataWidthArg
      let writeAddr := args[args.size-4]!
      let writeData := args[args.size-3]!
      let writeEnable := args[args.size-2]!
      let readAddr := args[args.size-1]!
      let waW ← translateExprToWire writeAddr "mem_waddr"
      let wdW ← translateExprToWire writeData "mem_wdata"
      let weW ← translateExprToWire writeEnable "mem_we"
      let raW ← translateExprToWire readAddr "mem_raddr"
      let w ← CompilerM.emitMemory hint addrWidth dataWidth "clk"
        (.ref waW) (.ref wdW) (.ref weW) (.ref raW) (named := isNamed)
      return some w

    -- Signal.memoryComboRead: memory with combinational (same-cycle) read
    if name.toString.endsWith ".memoryComboRead" && args.size >= 4 then
      trace[sparkle.compiler] "→ memory (combo read)"
      let addrWidthArg := args[args.size-6]!
      let dataWidthArg := args[args.size-5]!
      let addrWidth ← extractPositiveDim "memory address width" addrWidthArg
      let dataWidth ← extractPositiveDim "memory data width" dataWidthArg
      let writeAddr := args[args.size-4]!
      let writeData := args[args.size-3]!
      let writeEnable := args[args.size-2]!
      let readAddr := args[args.size-1]!
      let waW ← translateExprToWire writeAddr "mem_waddr"
      let wdW ← translateExprToWire writeData "mem_wdata"
      let weW ← translateExprToWire writeEnable "mem_we"
      let raW ← translateExprToWire readAddr "mem_raddr"
      let w ← CompilerM.emitMemoryComboRead hint addrWidth dataWidth "clk"
        (.ref waW) (.ref wdW) (.ref weW) (.ref raW) (named := isNamed)
      return some w

    return none

  /-- Handle Signal.loop, HWVector.get -/
  partial def handleLoop (e : Lean.Expr) (name : Name) (args : Array Lean.Expr) (hint : String) (isNamed : Bool) : CompilerM (Option String) := do
    -- HWVector.get: array indexing
    if name == ``Sparkle.Core.Vector.HWVector.get && args.size >= 2 then
      trace[sparkle.compiler] "→ HWVector.get"
      let vec := args[args.size-2]!
      let idx := args[args.size-1]!
      let vecWire ← translateExprToWire vec "vec"
      let idxWire ← translateExprToWire idx "idx"
      let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
      let hwType ← inferHWTypeFromSignal exprType
      let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
      CompilerM.emitAssign resWire (.index (.ref vecWire) (.ref idxWire))
      return some resWire

    -- Signal.loop
    if name.toString.endsWith ".loop" && args.size >= 1 then
      trace[sparkle.compiler] "→ loop"
      let f := args.back!
      let fReduced ← match f with
        | .lam .. => pure f
        | _ => CompilerM.liftMetaM (Lean.Meta.whnf f)
      match fReduced with
      | .lam binderName binderType body _ =>
        let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
        let hwType ← inferHWTypeFromSignal exprType
        let loopWire ← CompilerM.makeWire "loop" hwType
        let resultWire ← CompilerM.withLocalDecl binderName binderType fun fvar => do
          let fvarId := fvar.fvarId!
          CompilerM.withVarMapping fvarId loopWire do
            let bodyInst := body.instantiate1 fvar
            translateExprToWire bodyInst "loop_body"
        CompilerM.emitAssign loopWire (.ref resultWire)
        return some resultWire
      | _ => CompilerM.liftMetaM $ throwError "Signal.loop argument must be a lambda"

    return none

  /-- Handle definition unfolding (inline) or sub-module synthesis (fallback) -/
  partial def handleDefinitionUnfold (e : Lean.Expr) (name : Name) (args : Array Lean.Expr) (hint : String) (isNamed : Bool) : CompilerM (Option String) := do
    let isValidDef ← CompilerM.liftMetaM do
      try
        let constInfo ← getConstInfo name
        match constInfo with
        | .defnInfo _ => return true
        | _ => return false
      catch _ => return false

    if !isValidDef then return none

    trace[sparkle.compiler] "→ definition unfold {name}"

    -- First try to reduce the function call and translate inline.
    -- Use reducible transparency to avoid expanding HAdd/HAppend mixed instances
    let eReduced ← CompilerM.liftMetaM do
      match ← Lean.Meta.unfoldDefinition? e with
        | some e' => return e'
        | none => return e
    if eReduced != e then
      try
        let w ← translateExprToWire eReduced hint (isNamed := isNamed)
        return some w
      catch _ex1 =>
        -- Inline expansion failed (often due to mixed Signal/BitVec operators
        -- inside the expanded body). Retry with reducible transparency to
        -- prevent over-expansion of Signal.pure and OfNat instances.
        try
          let eReduced2 ← CompilerM.liftMetaM do
            Lean.Meta.withTransparency .reducible do
              match ← Lean.Meta.unfoldDefinition? e with
              | some e' => return e'
              | none => return e
          if eReduced2 != e then
            let w ← translateExprToWire eReduced2 hint (isNamed := isNamed)
            return some w
        catch ex2 =>
          let msg := ex2.toMessageData
          CompilerM.liftMetaM $ throwError m!"Inline expansion failed for {name}:\n{msg}"

    -- Fallback: sub-module synthesis
    trace[sparkle.compiler] "→ sub-module synthesis {name}"
    let declarationType ← CompilerM.liftMetaM do
      return (← getConstInfo name).type
    let natCallArguments ← CompilerM.liftMetaM $
      collectNatCallArguments declarationType args
    let parentState ← CompilerM.getCompilerState
    let parentDefault (parameterName : String) : Option Nat :=
      parentState.parameterDefaults.lookup parameterName
    let mut subParameterDefaults : List (String × Nat) := []
    let mut parameterOverrides : List (String × DimExpr) := []
    for (parameterName, argument) in natCallArguments do
      let override ← lowerDimExpr s!"instance parameter '{parameterName}'" argument
      let defaultValue ← match override.eval? parentDefault with
        | some value => pure value
        | none => CompilerM.liftMetaM $ throwError m!"Cannot determine a concrete default for parameter '{parameterName}' of submodule '{name}'.\n\n\
            Parameterized hierarchy requires every child default to be evaluable \
            under the parent module's declared defaults."
      subParameterDefaults := subParameterDefaults ++ [(parameterName, defaultValue)]
      parameterOverrides := parameterOverrides ++ [(parameterName, override)]
    let (subModule, subDesign) ← CompilerM.liftMetaM $
      synthesizeCombinational name subParameterDefaults
    for m in subDesign.modules do CompilerM.addModuleToDesign m
    CompilerM.addModuleToDesign subModule

    let mut connections := []
    let inputPorts := subModule.inputs.filter (fun p => p.name != "clk" && p.name != "rst")
    if args.size < inputPorts.length then
       CompilerM.liftMetaM $ throwError s!"Sub-module {name} requires {inputPorts.length} args, but got {args.size}"

    for i in [:inputPorts.length] do
       let argExpr := args[args.size - inputPorts.length + i]!
       let argWire ← translateExprToWire argExpr s!"arg{i}"
       connections := (inputPorts[i]!.name, Sparkle.IR.AST.Expr.ref argWire) :: connections

    -- A parameterized child may remain as a real module instance rather than
    -- being inlined.  Propagate its implicit sequential interface through the
    -- parent and connect every instance to the shared clock/reset nets.
    if subModule.inputs.any (fun port => port.name == "clk") then
      CompilerM.ensureInput "clk" .bit
      connections := ("clk", .ref "clk") :: connections
    if subModule.inputs.any (fun port => port.name == "rst") then
      CompilerM.ensureInput "rst" .bit
      connections := ("rst", .ref "rst") :: connections

    let exprType ← CompilerM.liftMetaM (Lean.Meta.inferType e)
    let hwType ← inferHWTypeFromSignal exprType
    let resWire ← CompilerM.makeWire hint hwType (named := isNamed)
    connections := ("out", Sparkle.IR.AST.Expr.ref resWire) :: connections

    let instanceName ← CompilerM.freshInstanceName subModule.name
    CompilerM.emitInstance subModule.name instanceName connections.reverse parameterOverrides
    return some resWire

  -- ===========================================================================
  -- Main dispatcher: routes expressions to the appropriate handler
  -- ===========================================================================

  partial def translateExprToWireApp (e : Lean.Expr) (hint : String) (isNamed : Bool := false) : CompilerM String := do
    let fn := e.getAppFn
    let args := e.getAppArgs

    match fn with
    | .const name _ =>
      trace[sparkle.compiler] "translateExprToWireApp name={name} args.size={args.size}"

      -- Note: We don't detect unbundle2 usage here because:
      -- 1. unbundle2 itself is fine (returns a tuple)
      -- 2. Pattern matching on unbundle2 gets compiled away before synthesis
      -- 3. We'd only catch non-problematic uses, creating false positives

      handleErrorPatterns e name args hint isNamed  -- throws or returns ()
      if let some w ← handleTupleProjections e name args hint isNamed then return w
      if let some w ← handleApplicative e name args hint isNamed then return w
      if let some w ← handleBitVecOps e name args hint isNamed then return w
      if let some w ← handleRegister e name args hint isNamed then return w
      if let some w ← handleMux e name args hint isNamed then return w
      if let some w ← handleMemory e name args hint isNamed then return w
      if let some w ← handleLoop e name args hint isNamed then return w
      if let some w ← handleDefinitionUnfold e name args hint isNamed then return w
      -- Not a valid module - throw error with debug info
      CompilerM.liftMetaM $ do
        if name.toString.contains "ite" || name.toString.contains "Decidable" then
          throwError s!"Detected problematic pattern {name}.\n\n\
            This might be from if-then-else which cannot be synthesized.\n\
            Use Signal.mux instead:\n\
            ❌ WRONG: if cond then a else b\n\
            ✓ RIGHT:  Signal.mux cond a b"
        else
          throwError s!"Cannot instantiate {name}: not a hardware module definition"

    | _ =>
      let fn := e.getAppFn
      CompilerM.liftMetaM $ throwError s!"Unsupported application: {e}\nHead: {fn} (ctor: {fn.ctorName})"

  /-- Translate a Nat shift amount argument to a hardware wire.
      Unwraps BitVec.toNat / Fin.val if the Nat came from a BitVec signal,
      otherwise treats it as a constant shift amount. -/
  partial def translateShiftAmount (natExpr : Lean.Expr) (hint : String) : CompilerM Sparkle.IR.AST.Expr := do
    let natExpr' ← CompilerM.liftMetaM (whnf natExpr)
    let natFn := natExpr'.getAppFn
    let natArgs := natExpr'.getAppArgs
    if let .const natName _ := natFn then
      if natName == ``BitVec.toNat && natArgs.size >= 2 then
        return .ref (← translateExprToWire natArgs[natArgs.size - 1]! hint)
      if natName == ``Fin.val && natArgs.size >= 2 then
        return .ref (← translateExprToWire natArgs[natArgs.size - 1]! hint)
    -- Fallback: retain a constant/parameter Nat shift amount as a packed
    -- parameter constant instead of narrowing it to the data operand width.
    -- A Nat shift of K=4 on a 2-bit value must remain 4 (and therefore shift
    -- the result to zero), not become `2'b00` and accidentally shift by zero.
    let amount ← lowerDimExpr "BitVec shift amount" natExpr'
    return match amount.toNat? with
      | some concrete =>
          let width := Nat.max 1 (Nat.log2 concrete + 1)
          Sparkle.IR.AST.Expr.const (Int.ofNat concrete) width
      | none => .paramConst amount amount.natValueBitWidthBound

  partial def getPrimitiveNameFromLambda (e : Lean.Expr) : CompilerM Name := do
    match e with
    | .lam _ _ body _ => getPrimitiveNameFromLambda body
    | _ =>
      let fn := e.getAppFn
      match fn with
      | .const name _ => return name
      | _ => CompilerM.liftMetaM $ throwError s!"Could not identify primitive in lambda body: {e}"

  partial def synthesizeCombinational (declName : Name)
      (parameterDefaults : List (String × Nat) := [])
      : MetaM (Sparkle.IR.AST.Module × Sparkle.IR.AST.Design) := do
    for (name, _) in parameterDefaults do
      if parameterDefaults.countP (fun entry => entry.1 == name) > 1 then
        throwError m!"Duplicate SystemVerilog parameter default for '{name}'."
    let constInfo ← getConstInfo declName
    match constInfo with
    | .defnInfo defnInfo =>
      let declaredDefaults ← collectDeclaredNatDefaults defnInfo.type
      let parameterDefaults := mergeParameterDefaults declaredDefaults parameterDefaults
      let body := defnInfo.value
      let compiler : CompilerM String := do
        let resultWire ← translateExprToWire body "result" (isTopLevel := true)
        -- The result is normally still in the builder's pending wire list.
        let outputType ← CompilerM.getWireType resultWire
        CompilerM.addOutput "out" outputType
        CompilerM.emitAssign "out" (.ref resultWire)
        return resultWire
      let circuitState := CircuitM.init declName.toString
      let compilerState : CompilerState := {
        varMap := [], dimMap := [], parameterDefaults := parameterDefaults,
        clockWire := none, resetWire := none
      }
      let (_, finalCircuitState) ← (compiler.run compilerState).run circuitState
      -- A synthesized module is an external/hierarchical commit point: expose
      -- the complete ordered netlist, never the builder's materialized prefix.
      let mut module := Sparkle.IR.Builder.materializeModule finalCircuitState
      let hasRegisters := module.body.any (fun stmt =>
        match stmt with
        | .register .. => true
        | .memory .. => true
        | _ => false
      )
      -- Check if any register uses reset (clock name doesn't end with __neg or __norst)
      let hasResetRegisters := module.body.any (fun stmt =>
        match stmt with
        | .register _ clock _ _ _ => !(clock.endsWith "__neg" || clock.endsWith "__norst")
        | .memory .. => true
        | _ => false
      )
      if hasRegisters then
        unless module.inputs.any (fun port => port.name == "clk") do
          module := module.addInput { name := "clk", ty := .bit }
        if hasResetRegisters then
          unless module.inputs.any (fun port => port.name == "rst") do
            module := module.addInput { name := "rst", ty := .bit }
      for (name, _) in parameterDefaults do
        unless module.parameters.any (fun p => p.name == name) do
          throwError m!"No top-level Nat binder named '{name}' was found in {declName}."
      validateParameterizedModule module
      return (module, finalCircuitState.design)
    | _ =>
      throwError s!"Cannot synthesize {declName}: not a definition"
end

def printModule (m : Sparkle.IR.AST.Module) : MetaM Unit := do
  IO.println s!"Module: {m.name}"
  IO.println s!"Inputs: {m.inputs.length}"
  for input in m.inputs do
    IO.println s!"  - {input.name}: {input.ty}"
  IO.println s!"Outputs: {m.outputs.length}"
  for output in m.outputs do
    IO.println s!"  - {output.name}: {output.ty}"
  IO.println s!"Wires: {m.wires.length}"
  for wire in m.wires do
    IO.println s!"  - {wire.name}: {wire.ty}"
  IO.println s!"Statements: {m.body.length}"
  for stmt in m.body do
    IO.println s!"  {stmt}"

declare_syntax_cat sparkleParameterDefault
syntax ident " := " num : sparkleParameterDefault

private def parseParameterDefaults
    (defaults : Array (TSyntax `sparkleParameterDefault))
    : CommandElabM (List (String × Nat)) := do
  let mut result : List (String × Nat) := []
  for defaultSyntax in defaults do
    let raw := defaultSyntax.raw
    unless raw.getNumArgs == 3 do
      throwError "Malformed SystemVerilog parameter default"
    let args := Lean.Syntax.getArgs raw
    let name : TSyntax `ident := ⟨args[0]!⟩
    let value : TSyntax `num := ⟨args[2]!⟩
    result := result ++ [(name.getId.toString, value.getNat)]
  return result

/-!
## Kernel-checked universal theorem certificates

The certificate emitted by `#sparkleUniversalTheorem` is deliberately about a
Lean theorem only.  In particular, it does not claim that the Sparkle compiler
or generated SystemVerilog preserves the theorem.  Downstream tooling can use
the single-line JSON marker without confusing a finite parameter sweep with a
theorem quantified over natural-number parameters.
-/

private def universalTheoremPPOptions (options : Options) : Options :=
  options
    |>.set `pp.explicit true
    |>.set `pp.fullNames true
    |>.set `pp.universes true
    |>.set `pp.piBinderNames true
    |>.set `pp.deepTerms true
    |>.set `pp.proofs true
    -- The standalone certifier deliberately does not load candidate-owned
    -- environment extensions.  Preserve a complete raw proposition if a
    -- custom pretty-printer is therefore unavailable.
    |>.set `pp.rawOnError true
    |>.set `pp.maxSteps 1000000

private def renderUniversalTheoremExpr (expr : Lean.Expr) : MetaM String :=
  withOptions universalTheoremPPOptions do
    return toString (← ppExpr expr)

private def universalTheoremBinderInfoName : BinderInfo → String
  | .default => "explicit"
  | .implicit => "implicit"
  | .strictImplicit => "strict_implicit"
  | .instImplicit => "instance_implicit"

/--
The only noncomputational principles accepted by a universal theorem
certificate.  These are Lean's standard logical axioms; project- or
candidate-defined axioms are deliberately excluded.
-/
private def universalTheoremAllowedAxioms : Array Name :=
  #[``propext, ``Classical.choice, ``Quot.sound]

/--
Validate a universal-width theorem directly against Lean's kernel-checked
environment and return its structured certificate.  This is the trusted API
for evaluator-owned checkers: callers should consume the returned `Json`
directly instead of trusting marker-shaped text emitted by candidate code.

An evaluator may supply a fresh nonce to bind a serialized copy of the result
to one controlled checker invocation.  A nonce is not a substitute for calling
this API from trusted code.
-/
def certifyUniversalTheorem
    (declName : Name) (parameterNames : Array Name)
    (nonce : Option String := none) : MetaM Json := do
  if parameterNames.isEmpty then
    throwError "A universal theorem certificate must name at least one Nat parameter."
  if let some nonce := nonce then
    if nonce.isEmpty then
      throwError "A universal theorem certificate nonce must not be empty."
  for parameterName in parameterNames do
    if parameterNames.count parameterName != 1 then
      throwError m!"Duplicate universal theorem parameter '{parameterName}'."

  -- Read from the kernel-checked environment rather than only the elaborator
  -- environment.  This is what justifies the `kernel_checked` field below.
  let env ← getEnv
  let theoremInfo ← match env.checked.get.find? declName with
    | some (.thmInfo info) => pure info
    | some _ =>
        throwError m!"'{declName}' is not a Lean theorem or lemma."
    | none =>
        throwError m!"'{declName}' is not present in Lean's kernel-checked environment."

  if theoremInfo.value.hasSorry then
    throwError m!"Theorem '{declName}' contains 'sorry' and cannot receive a universal theorem certificate."
  let axioms ← Lean.collectAxioms declName
  if axioms.any (fun axiomName => axiomName == ``sorryAx) then
    throwError m!"Theorem '{declName}' transitively depends on 'sorry' and cannot receive a universal theorem certificate."
  let disallowedAxioms := axioms.filter fun axiomName =>
    !universalTheoremAllowedAxioms.contains axiomName
  unless disallowedAxioms.isEmpty do
    let renderedDisallowed := String.intercalate ", " <|
      (disallowedAxioms.qsort Name.lt).toList.map Name.toString
    let renderedAllowed := String.intercalate ", " <|
      universalTheoremAllowedAxioms.toList.map Name.toString
    throwError m!"Theorem '{declName}' depends on non-allowlisted axiom(s): {renderedDisallowed}. Universal theorem certificates only allow Lean's standard logical axioms: {renderedAllowed}."

  let proposition ← renderUniversalTheoremExpr theoremInfo.type
  let (binders, domain, conclusion) ←
      forallTelescopeReducing theoremInfo.type fun fvars conclusion => do
    let localDecls ← fvars.mapM getFVarLocalDecl
    for parameterName in parameterNames do
      let parameterMatches := localDecls.filter (fun localDecl => localDecl.userName == parameterName)
      if parameterMatches.size == 0 then
        throwError m!"Theorem '{declName}' does not universally bind a parameter named '{parameterName}'."
      if parameterMatches.size != 1 then
        throwError m!"Theorem '{declName}' has multiple binders named '{parameterName}', so the requested parameter is ambiguous."
      let parameterType ← whnf parameterMatches[0]!.type
      unless parameterType.isConstOf ``Nat do
        let renderedType ← renderUniversalTheoremExpr parameterMatches[0]!.type
        throwError m!"Universal theorem parameter '{parameterName}' has type '{renderedType}', not Nat."
      let parameterFVarId := parameterMatches[0]!.fvarId
      let occursInDomain := localDecls.any fun localDecl =>
        localDecl.fvarId != parameterFVarId && localDecl.type.containsFVar parameterFVarId
      unless occursInDomain || conclusion.containsFVar parameterFVarId do
        throwError m!"Universal theorem parameter '{parameterName}' does not occur in the theorem domain or conclusion; refusing to certify a fixed-width statement with an unused dummy parameter."

    let mut binderJson : Array Json := #[]
    let mut domainJson : Array Json := #[]
    for localDecl in localDecls do
      let renderedType ← renderUniversalTheoremExpr localDecl.type
      let isParameter := parameterNames.contains localDecl.userName
      let isPremise ← isProp localDecl.type
      let role := if isParameter then "parameter" else if isPremise then "premise" else "quantified"
      let entry := Json.mkObj [
        ("binder_info", .str (universalTheoremBinderInfoName localDecl.binderInfo)),
        ("name", .str localDecl.userName.toString),
        ("role", .str role),
        ("type", .str renderedType)
      ]
      binderJson := binderJson.push entry
      unless isParameter do
        domainJson := domainJson.push entry
    let renderedConclusion ← renderUniversalTheoremExpr conclusion
    return (binderJson, domainJson, renderedConclusion)

  let sortedAxioms := axioms.qsort Name.lt
  let certificate := Json.mkObj [
    ("axioms", .arr (sortedAxioms.map fun axiomName => .str axiomName.toString)),
    ("binders", .arr binders),
    ("compiler_correctness_claimed", .bool false),
    ("conclusion", .str conclusion),
    ("domain", .arr domain),
    ("evidence_kind", .str "universal_lean_theorem"),
    ("kernel_checked", .bool true),
    ("verification_nonce", nonce.map Json.str |>.getD .null),
    ("parameters", .arr (parameterNames.map fun parameterName => .str parameterName.toString)),
    ("proposition", .str proposition),
    ("schema_version", (1 : Json)),
    ("status", .str "proved"),
    ("theorem", .str declName.toString)
  ]
  return certificate

private def emitUniversalTheoremCertificate
    (theoremId : TSyntax `ident) (parameterNames : Array Name)
    (nonce : Option String := none) : CommandElabM Unit := do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload theoremId
  let certificate ← Lean.Elab.Command.liftTermElabM do
    certifyUniversalTheorem declName parameterNames nonce
  logInfo m!"SPARKLE_UNIVERSAL_THEOREM_JSON:{certificate.compress}"

/--
Emit a machine-readable certificate for a kernel-checked Lean theorem that
universally binds the named `Nat` parameters.  Any later binders and premises
are preserved in `domain`, and the entire theorem type is preserved in
`proposition`.  This command certifies no relationship to generated hardware.
-/
elab "#sparkleUniversalTheorem" theoremId:ident "parameters" "[" parameters:ident,* "]" : command => do
  let parameterNames := parameters.getElems.map TSyntax.getId
  emitUniversalTheoremCertificate theoremId parameterNames

/-- Evaluator-owned variant carrying a fresh challenge nonce in the marker. -/
elab "#sparkleUniversalTheorem" theoremId:ident verificationNonce:str "parameters" "[" parameterIds:ident,* "]" : command => do
  let parameterNames := parameterIds.getElems.map TSyntax.getId
  emitUniversalTheoremCertificate theoremId parameterNames (some verificationNonce.getString)

elab "#synthesize" id:ident : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  Lean.Elab.Command.liftTermElabM do
    let (module, _) ← synthesizeCombinational declName
    printModule module
    IO.println "\n-- IR successfully generated!"

elab "#synthesize" id:ident "parameters" "[" defaults:sparkleParameterDefault,* "]" : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  let parameterDefaults ← parseParameterDefaults defaults
  Lean.Elab.Command.liftTermElabM do
    let (module, _) ← synthesizeCombinational declName parameterDefaults
    printModule module
    IO.println "\n-- Parameterized IR successfully generated!"

def runDesignDRC (design : Sparkle.IR.AST.Design) : MetaM Unit := do
  for m in design.modules do
    let warnings := Sparkle.Compiler.DRC.checkRegisteredOutputs m
    for w in warnings do
      Lean.logWarning m!"{w}"

def emitVerilogChecked (module : Sparkle.IR.AST.Module)
    (maxMemoryDepth : Nat := Sparkle.IR.Type.DimExpr.maxNatWorkWidth) : MetaM String :=
  match Sparkle.Backend.Verilog.toVerilogChecked module maxMemoryDepth with
  | .ok verilog => pure verilog
  | .error message => throwError message

def emitVerilogDesignChecked (design : Sparkle.IR.AST.Design)
    (maxMemoryDepth : Nat := Sparkle.IR.Type.DimExpr.maxNatWorkWidth) : MetaM String := do
  match Sparkle.Backend.Verilog.toVerilogDesignChecked design maxMemoryDepth with
  | .ok verilog => pure verilog
  | .error message => throwError message

elab "#synthesizeVerilog" id:ident : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  Lean.Elab.Command.liftTermElabM do
    let (module, _) ← synthesizeCombinational declName
    let warnings := Sparkle.Compiler.DRC.checkRegisteredOutputs module
    for w in warnings do
      Lean.logWarning m!"{w}"
    let verilog ← emitVerilogChecked module
    IO.println verilog
    IO.println "\n-- Verilog successfully generated!"

elab "#synthesizeVerilog" id:ident "parameters" "[" defaults:sparkleParameterDefault,* "]" : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  let parameterDefaults ← parseParameterDefaults defaults
  Lean.Elab.Command.liftTermElabM do
    let (module, _) ← synthesizeCombinational declName parameterDefaults
    let warnings := Sparkle.Compiler.DRC.checkRegisteredOutputs module
    for w in warnings do
      Lean.logWarning m!"{w}"
    let verilog ← emitVerilogChecked module
    IO.println verilog
    IO.println "\n-- Parameterized Verilog successfully generated!"

def synthesizeHierarchical (declName : Name)
    (parameterDefaults : List (String × Nat) := []) : MetaM Sparkle.IR.AST.Design := do
  let (module, design) ← synthesizeCombinational declName parameterDefaults
  let design' := if (design.modules.any (·.name == module.name)) then design else design.addModule module
  validateDesignForEmission design'
  return design'

elab "#synthesizeDesign" id:ident : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  Lean.Elab.Command.liftTermElabM do
    let design ← synthesizeHierarchical declName
    for m in design.modules do
      printModule m
    IO.println "\n-- Hierarchical IR successfully generated!"

elab "#synthesizeDesign" id:ident "parameters" "[" defaults:sparkleParameterDefault,* "]" : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  let parameterDefaults ← parseParameterDefaults defaults
  Lean.Elab.Command.liftTermElabM do
    let design ← synthesizeHierarchical declName parameterDefaults
    for m in design.modules do
      printModule m
    IO.println "\n-- Parameterized hierarchical IR successfully generated!"

elab "#synthesizeVerilogDesign" id:ident : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  Lean.Elab.Command.liftTermElabM do
    let design ← synthesizeHierarchical declName
    runDesignDRC design
    let verilog ← emitVerilogDesignChecked design
    IO.println verilog
    IO.println "\n-- Hierarchical Verilog successfully generated!"

elab "#synthesizeVerilogDesign" id:ident "parameters" "[" defaults:sparkleParameterDefault,* "]" : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  let parameterDefaults ← parseParameterDefaults defaults
  Lean.Elab.Command.liftTermElabM do
    let design ← synthesizeHierarchical declName parameterDefaults
    runDesignDRC design
    let verilog ← emitVerilogDesignChecked design
    IO.println verilog
    IO.println "\n-- Parameterized hierarchical Verilog successfully generated!"

elab "#writeVerilogDesign" id:ident str:str : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  Lean.Elab.Command.liftTermElabM do
    let design ← synthesizeHierarchical declName
    runDesignDRC design
    let verilog ← emitVerilogDesignChecked design
    let path := str.getString
    if let some dir := (System.FilePath.mk path).parent then
      IO.FS.createDirAll dir
    IO.FS.writeFile path verilog
    IO.println s!"Written {design.modules.length} modules to {path}"

elab "#writeVerilogDesign" id:ident str:str "parameters" "[" defaults:sparkleParameterDefault,* "]" : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  let parameterDefaults ← parseParameterDefaults defaults
  Lean.Elab.Command.liftTermElabM do
    let design ← synthesizeHierarchical declName parameterDefaults
    runDesignDRC design
    let verilog ← emitVerilogDesignChecked design
    let path := str.getString
    if let some dir := (System.FilePath.mk path).parent then
      IO.FS.createDirAll dir
    IO.FS.writeFile path verilog
    IO.println s!"Written {design.modules.length} parameterized modules to {path}"

elab "#writeCppSimDesign" id:ident str:str : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  Lean.Elab.Command.liftTermElabM do
    let parameterizedDesign ← synthesizeHierarchical declName
    let design ← match Sparkle.IR.Specialize.specializeDesign parameterizedDesign [] with
      | .ok design => pure design
      | .error message => throwError message
    if parameterizedDesign.modules.any (fun module_ => !module_.parameters.isEmpty) then
      match Sparkle.Backend.CppSim.validateSpecializedDesign design with
      | .ok _ => pure ()
      | .error message => throwError message
    let optimized := Sparkle.IR.Optimize.optimizeDesign design
    let cpp ← match Sparkle.Backend.CppSim.toCppSimDesignChecked optimized with
      | .ok cpp => pure cpp
      | .error message => throwError message
    let path := str.getString
    if let some dir := (System.FilePath.mk path).parent then
      IO.FS.createDirAll dir
    IO.FS.writeFile path cpp
    IO.println s!"Written C++ simulation ({optimized.modules.length} modules) to {path}"

elab "#writeCppSimDesign" id:ident str:str "parameters" "[" defaults:sparkleParameterDefault,* "]" : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  let parameterDefaults ← parseParameterDefaults defaults
  Lean.Elab.Command.liftTermElabM do
    let parameterizedDesign ← synthesizeHierarchical declName parameterDefaults
    let design ← match Sparkle.IR.Specialize.specializeDesign
        parameterizedDesign parameterDefaults with
      | .ok design => pure design
      | .error message => throwError message
    if parameterizedDesign.modules.any (fun module_ => !module_.parameters.isEmpty) then
      match Sparkle.Backend.CppSim.validateSpecializedDesign design with
      | .ok _ => pure ()
      | .error message => throwError message
    let optimized := Sparkle.IR.Optimize.optimizeDesign design
    let cpp ← match Sparkle.Backend.CppSim.toCppSimDesignChecked optimized with
      | .ok cpp => pure cpp
      | .error message => throwError m!"{message}. Use #writeVerilogDesign for native parameterized output, or synthesize a concrete wrapper before requesting CppSim."
    let path := str.getString
    if let some dir := (System.FilePath.mk path).parent then
      IO.FS.createDirAll dir
    IO.FS.writeFile path cpp
    IO.println s!"Written C++ simulation ({optimized.modules.length} modules) to {path}"

/-- Evaluate an Array String constant at elaboration time -/
private unsafe def evalStringArrayImpl (name : Name) : TermElabM (Array String) :=
  Lean.Meta.evalExpr (Array String)
    (mkApp (mkConst ``Array [.zero]) (mkConst ``String []))
    (mkConst name [])

@[implemented_by evalStringArrayImpl]
private opaque evalStringArray (name : Name) : TermElabM (Array String)

/-- Core implementation for #writeDesign -/
private def writeDesignCore (declName : Name) (svPath cppPath : String)
    (observableWires : Option (List String))
    (parameterDefaults : List (String × Nat) := [])
    (maxMemoryDepth : Nat := Sparkle.IR.Type.DimExpr.maxNatWorkWidth) : TermElabM Unit := do
  let parameterizedDesign ← synthesizeHierarchical declName parameterDefaults
  let design ← match Sparkle.IR.Specialize.specializeDesign
      parameterizedDesign parameterDefaults with
    | .ok design => pure design
    | .error message => throwError message
  if parameterizedDesign.modules.any (fun module_ => !module_.parameters.isEmpty) then
    match Sparkle.Backend.CppSim.validateSpecializedDesign design maxMemoryDepth with
    | .ok _ => pure ()
    | .error message => throwError message
  runDesignDRC design
  -- Generate every artifact from the same concrete specialization so that
  -- the Verilog, CppSim, and JIT models cannot silently disagree about widths.
  let optimized := Sparkle.IR.Optimize.optimizeDesign design
  let cpp ← match Sparkle.Backend.CppSim.toCppSimDesignChecked optimized none maxMemoryDepth with
    | .ok cpp => pure cpp
    | .error message => throwError m!"{message}. #writeDesign includes a fixed-width C++ simulator; use #writeVerilogDesign for native parameterized output, or synthesize a concrete wrapper."
  let jitOptimized := Sparkle.IR.Optimize.optimizeDesign design observableWires
  let jitCpp ← match Sparkle.Backend.CppSim.toCppSimJITChecked
      jitOptimized observableWires maxMemoryDepth with
    | .ok cpp => pure cpp
    | .error message => throwError message
  -- Ensure output directories exist
  if let some svDir := (System.FilePath.mk svPath).parent then
    IO.FS.createDirAll svDir
  if let some cppDir := (System.FilePath.mk cppPath).parent then
    IO.FS.createDirAll cppDir
  -- Verilog (unoptimized)
  let verilog ← emitVerilogDesignChecked design maxMemoryDepth
  IO.FS.writeFile svPath verilog
  IO.println s!"Written {design.modules.length} modules to {svPath}"
  -- CppSim (optimized, no observableWires — keep all _gen_ as members for header)
  IO.FS.writeFile cppPath cpp
  IO.println s!"Written C++ simulation ({optimized.modules.length} modules) to {cppPath}"
  -- JIT wrapper (optimized with observableWires — demote non-observable to locals)
  let jitPath := cppPath.replace "_cppsim.h" "_jit.cpp"
  IO.FS.writeFile jitPath jitCpp
  IO.println s!"Written JIT wrapper to {jitPath}"

/-- Combined command: synthesize once, emit both Verilog and optimized C++ simulation -/
elab "#writeDesign" id:ident svPath:str cppPath:str : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  Lean.Elab.Command.liftTermElabM do
    writeDesignCore declName svPath.getString cppPath.getString none

elab "#writeDesign" id:ident svPath:str cppPath:str "parameters" "[" defaults:sparkleParameterDefault,* "]" : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  let parameterDefaults ← parseParameterDefaults defaults
  Lean.Elab.Command.liftTermElabM do
    writeDesignCore declName svPath.getString cppPath.getString none parameterDefaults

/-- Combined command with observable wires: emit both Verilog and optimized C++ simulation,
    with JIT code restricted to only the specified observable wires -/
elab "#writeDesign" id:ident svPath:str cppPath:str wiresId:ident : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  Lean.Elab.Command.liftTermElabM do
    let wiresName ← Lean.resolveGlobalConstNoOverload wiresId
    let wiresArr ← evalStringArray wiresName
    writeDesignCore declName svPath.getString cppPath.getString (some wiresArr.toList)

/-- Combined command with observable wires and an explicit opt-in memory-depth
    limit shared by checked SystemVerilog and CppSim emission.  The ordinary
    command retains the conservative default limit, and the opt-in never
    relaxes packed widths or symbolic memory declarations. -/
elab "#writeDesign" id:ident svPath:str cppPath:str wiresId:ident
    "maxMemoryDepth" limit:num : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  Lean.Elab.Command.liftTermElabM do
    let wiresName ← Lean.resolveGlobalConstNoOverload wiresId
    let wiresArr ← evalStringArray wiresName
    let maxMemoryDepth := limit.getNat
    if maxMemoryDepth == 0 then
      throwError "#writeDesign maxMemoryDepth must be positive"
    writeDesignCore declName svPath.getString cppPath.getString
      (some wiresArr.toList) [] maxMemoryDepth

elab "#writeDesign" id:ident svPath:str cppPath:str wiresId:ident "parameters" "[" defaults:sparkleParameterDefault,* "]" : command => do
  let declName ← Lean.Elab.Command.liftCoreM do
    Lean.resolveGlobalConstNoOverload id
  let parameterDefaults ← parseParameterDefaults defaults
  Lean.Elab.Command.liftTermElabM do
    let wiresName ← Lean.resolveGlobalConstNoOverload wiresId
    let wiresArr ← evalStringArray wiresName
    writeDesignCore declName svPath.getString cppPath.getString (some wiresArr.toList)
      parameterDefaults

end Sparkle.Compiler.Elab
