/-
  Strict, fail-closed reification of a small ordinary `Signal` combinational
  subset into the original-indexed proof-carrying kernel API.

  The metaprogram is deliberately untrusted.  It proposes a canonical deep
  expression and proof terms; Lean's kernel checks a certificate whose type is
  indexed by the exact ordinary declaration.  There is no replaceable source
  adapter in the generated certificate.
-/

import Lean
import Lean.Compiler.ImplementedByAttr
import Sparkle.Compiler.CombElab
import Sparkle.Compiler.SignalCombCorrectness

namespace Sparkle.Compiler.SignalCombElab

open Lean Elab Command Meta
open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Compiler.CombCorrectness
open Sparkle.Compiler.SignalCombCorrectness

private inductive SourceArity where
  | unary
  | binary
  deriving BEq

private structure ReifiedOperators where
  add : Bool := false
  xor : Bool := false

private def ReifiedOperators.merge
    (lhs rhs : ReifiedOperators) : ReifiedOperators :=
  { add := lhs.add || rhs.add, xor := lhs.xor || rhs.xor }

private structure SourceInput where
  fvarId : FVarId
  portName : String

private structure ReifiedDeclaration where
  arity : SourceArity
  rhs : CombExpr
  operators : ReifiedOperators

private def allowedAxioms : Array Name :=
  #[``propext, ``Classical.choice, ``Quot.sound]

private def renderNames (names : Array Name) : String :=
  String.intercalate ", " <| (names.qsort Name.lt).toList.map Name.toString

private def rejectDisallowedAxioms (label : String) (declName : Name) : MetaM Unit := do
  let axioms ← Lean.collectAxioms declName
  if axioms.contains ``sorryAx then
    throwError m!"{label} '{declName}' transitively depends on 'sorry'."
  let disallowed := axioms.filter fun axiomName =>
    !allowedAxioms.contains axiomName
  unless disallowed.isEmpty do
    throwError m!"{label} '{declName}' depends on non-allowlisted axiom(s): {renderNames disallowed}."

private def auditSourceDeclaration (declName : Name) : MetaM DefinitionVal := do
  let env ← getEnv
  let info ← match env.checked.get.find? declName with
    | some (.defnInfo info) => pure info
    | some (.opaqueInfo _) =>
        throwError m!"SignalComb source '{declName}' is opaque; a safe transparent definition is required."
    | some _ =>
        throwError m!"SignalComb source '{declName}' is not a transparent definition."
    | none =>
        throwError m!"SignalComb source '{declName}' is absent from Lean's kernel-checked environment."
  unless info.safety == .safe do
    throwError m!"SignalComb source '{declName}' is unsafe."
  if info.type.hasSorry || info.value.hasSorry then
    throwError m!"SignalComb source '{declName}' contains 'sorry'."
  if (Lean.Compiler.getImplementedBy? env declName).isSome then
    throwError m!"SignalComb source '{declName}' has an unproved @[implemented_by] replacement."
  rejectDisallowedAxioms "SignalComb source" declName
  return info

/-- Audit executable definitions introduced in the current module.  Imported
    kernel/compiler primitives are separately trusted by their owning modules;
    current-module helpers cannot hide unsafe, opaque, sorry-based, or
    `implemented_by` computation in a generated production alias. -/
private partial def auditLocalRuntimeClosure
    (env : Environment) (pending roots : List Name)
    (visited : Array Name := #[]) : MetaM Unit := do
  match pending with
  | [] => pure ()
  | declName :: rest =>
      if visited.contains declName then
        auditLocalRuntimeClosure env rest roots visited
      else
        let visited := visited.push declName
        let isRoot := roots.contains declName
        let isCurrent := (env.getModuleIdxFor? declName).isNone
        if !isRoot && !isCurrent then
          auditLocalRuntimeClosure env rest roots visited
        else
          match env.find? declName with
          | some (.defnInfo info) =>
              unless info.safety == .safe do
                throwError m!"SignalComb runtime closure reaches unsafe definition '{declName}'."
              if info.type.hasSorry || info.value.hasSorry then
                throwError m!"SignalComb runtime closure reaches 'sorry' in '{declName}'."
              if (Lean.Compiler.getImplementedBy? env declName).isSome then
                throwError m!"SignalComb runtime closure reaches unproved @[implemented_by] definition '{declName}'."
              auditLocalRuntimeClosure env
                (info.value.getUsedConstants.toList ++ rest) roots visited
          | some (.opaqueInfo _) =>
              throwError m!"SignalComb runtime closure reaches opaque declaration '{declName}'."
          | some (.axiomInfo _) =>
              throwError m!"SignalComb runtime closure reaches axiom '{declName}'."
          | some (.thmInfo _) | some (.inductInfo _) | some (.ctorInfo _)
          | some (.recInfo _) | some (.quotInfo _) =>
              auditLocalRuntimeClosure env rest roots visited
          | none =>
              throwError m!"SignalComb runtime closure reaches missing declaration '{declName}'."

private def auditGeneratedDeclaration
    (declName : Name) (executable : Bool) : MetaM Unit := do
  let env ← getEnv
  match env.find? declName with
  | some (.defnInfo info) =>
      unless info.safety == .safe do
        throwError m!"Generated SignalComb definition '{declName}' is unsafe."
      if info.type.hasSorry || info.value.hasSorry then
        throwError m!"Generated SignalComb definition '{declName}' contains 'sorry'."
      if (Lean.Compiler.getImplementedBy? env declName).isSome then
        throwError m!"Generated SignalComb definition '{declName}' has an unproved @[implemented_by] replacement."
  | some (.thmInfo info) =>
      if info.type.hasSorry || info.value.hasSorry then
        throwError m!"Generated SignalComb theorem '{declName}' contains 'sorry'."
      if (Lean.Compiler.getImplementedBy? env declName).isSome then
        throwError m!"Generated SignalComb theorem '{declName}' has an unexpected @[implemented_by] replacement."
  | some (.opaqueInfo _) =>
      throwError m!"Generated SignalComb declaration '{declName}' is unexpectedly opaque."
  | some _ =>
      throwError m!"Generated SignalComb declaration '{declName}' has an unexpected kernel declaration kind."
  | none =>
      let nearby := env.constants.map₂.toList.map (·.1) |>.filter fun name =>
        name.toString.endsWith declName.getString!
      throwError m!"Generated SignalComb declaration '{declName}' is absent from the immediate kernel-added environment; suffix matches: {nearby}."
  rejectDisallowedAxioms "Generated SignalComb declaration" declName
  if executable then
    auditLocalRuntimeClosure env [declName] [declName]

private def signatureError (detail : String) : MetaM α :=
  throwError m!"SignalComb signature rejected: {detail}"

private def signalBitVecType? (type : Expr) : Option (Expr × Expr) :=
  let args := type.getAppArgs
  if type.getAppFn.isConstOf ``Sparkle.Core.Signal.Signal && args.size == 2 then
    let valueType := args[1]!
    let valueArgs := valueType.getAppArgs
    if valueType.getAppFn.isConstOf ``BitVec && valueArgs.size == 1 then
      some (args[0]!, valueArgs[0]!)
    else
      none
  else
    none

private def requireExactSignalType
    (label : String) (type dom width : Expr) : MetaM Unit := do
  match signalBitVecType? type with
  | some (actualDom, actualWidth) =>
      unless actualDom == dom && actualWidth == width do
        signatureError s!"{label} must be exactly Signal dom (BitVec W); derived widths, casts, and alternate domains are not accepted."
  | none =>
      signatureError s!"{label} must be exactly Signal dom (BitVec W); Bool, tuples, state bundles, and hierarchy are not accepted."

private partial def instantiateSourceLambdas
    (value : Expr) (fvars : Array Expr) : MetaM Expr := do
  let rec consume (body argument : Expr) : MetaM Expr := do
    match body with
    | .lam _ _ lambdaBody _ => pure <| lambdaBody.instantiate1 argument
    | .letE _ _ letValue letBody _ => consume (letBody.instantiate1 letValue) argument
    | .mdata _ inner => consume inner argument
    | _ =>
        throwError "SignalComb source body does not match its exact dependent function signature."
  let mut body := value
  for fvar in fvars do
    body ← consume body fvar
  return body

private def lookupInput (inputs : Array SourceInput) (id : FVarId) : Option String :=
  inputs.findSome? fun input =>
    if input.fvarId == id then some input.portName else none

private def sameSignalExpressionType
    (expression dom width : Expr) : MetaM Unit := do
  let type ← inferType expression
  match signalBitVecType? type with
  | some (actualDom, actualWidth) =>
      unless actualDom == dom && actualWidth == width do
        throwError "SignalComb body rejected: every intermediate must have exactly type Signal dom (BitVec W)."
  | none =>
      throwError "SignalComb body rejected: every intermediate must have exactly type Signal dom (BitVec W)."

private def exactBuiltinInstance
    (application : Expr) (expected : Name) : MetaM (Array Expr) := do
  let args := application.getAppArgs
  unless args.size == 6 do
    throwError "SignalComb body rejected: malformed overloaded binary operator application."
  let instanceArg := args[3]!
  unless instanceArg.getAppFn.isConstOf expected do
    throwError m!"SignalComb body rejected: substituted operator instance; expected exactly '{expected}'."
  return args

private def forbiddenBodyConstant (name : Name) : Bool :=
  #[``Sparkle.Core.Signal.Signal.register,
    ``Sparkle.Core.Signal.Signal.registerNeg,
    ``Sparkle.Core.Signal.Signal.registerNoReset,
    ``Sparkle.Core.Signal.Signal.registerWithEnable,
    ``Sparkle.Core.Signal.Signal.loop,
    ``Sparkle.Core.Signal.Signal.memory,
    ``Sparkle.Core.Signal.Signal.memoryComboRead,
    ``Sparkle.Core.Signal.Signal.memoryWithInit].contains name

/-- Audit the closed kernel body before any beta/zeta substitution.  Binder
    types and the first four, type/instance positions of an exact overloaded
    operator are controlled positions; every computational let value and body
    is traversed, including values that later zeta reduction would discard. -/
private partial def auditRawSourceBody (expression : Expr) : MetaM Unit := do
  match expression with
  | .bvar _ => pure ()
  | .lam _ _ body _ => auditRawSourceBody body
  | .letE _ _ value body _ =>
      auditRawSourceBody value
      auditRawSourceBody body
  | .mdata _ body => auditRawSourceBody body
  | _ =>
      let fn := expression.getAppFn
      if fn.isConstOf ``HAdd.hAdd then
        let args ← exactBuiltinInstance expression
          ``Sparkle.Core.Signal.instHAddSignalBitVec
        auditRawSourceBody args[4]!
        auditRawSourceBody args[5]!
        return
      if fn.isConstOf ``HXor.hXor then
        let args ← exactBuiltinInstance expression
          ``Sparkle.Core.Signal.instHXorSignalBitVec
        auditRawSourceBody args[4]!
        auditRawSourceBody args[5]!
        return
      match fn with
      | .const name _ =>
          if forbiddenBodyConstant name then
            throwError m!"SignalComb body rejected: state, loop, or memory primitive '{name}' is outside the pure combinational subset."
          throwError m!"SignalComb body rejected: non-whitelisted constant '{name}'; helper calls and hierarchy fail closed."
      | _ =>
          throwError "SignalComb body rejected: only bound input references, lets/lambdas, metadata, and the exact built-in Signal add/xor instances are accepted."

private partial def reifySignalExpr
    (inputs : Array SourceInput) (dom width : Expr) (expression : Expr) :
    MetaM (CombExpr × ReifiedOperators) := do
  sameSignalExpressionType expression dom width
  match expression with
  | .fvar id =>
      match lookupInput inputs id with
      | some portName => return (.ref portName, {})
      | none =>
          throwError "SignalComb body rejected: output refers to a local value that is not an accepted input."
  | .letE _ _ value body _ =>
      reifySignalExpr inputs dom width (body.instantiate1 value)
  | .mdata _ body =>
      reifySignalExpr inputs dom width body
  | _ =>
      let fn := expression.getAppFn
      if fn.isConstOf ``HAdd.hAdd then
        let args ← exactBuiltinInstance expression
          ``Sparkle.Core.Signal.instHAddSignalBitVec
        let (lhs, lhsOps) ← reifySignalExpr inputs dom width args[4]!
        let (rhs, rhsOps) ← reifySignalExpr inputs dom width args[5]!
        let operators := (lhsOps.merge rhsOps).merge { add := true }
        return (.binary .add lhs rhs, operators)
      if fn.isConstOf ``HXor.hXor then
        let args ← exactBuiltinInstance expression
          ``Sparkle.Core.Signal.instHXorSignalBitVec
        let (lhs, lhsOps) ← reifySignalExpr inputs dom width args[4]!
        let (rhs, rhsOps) ← reifySignalExpr inputs dom width args[5]!
        let operators := (lhsOps.merge rhsOps).merge { xor := true }
        return (.binary .bxor lhs rhs, operators)
      match fn with
      | .const name _ =>
          if forbiddenBodyConstant name then
            throwError m!"SignalComb body rejected: state, loop, or memory primitive '{name}' is outside the pure combinational subset."
          throwError m!"SignalComb body rejected: non-whitelisted constant '{name}'; helper calls and hierarchy fail closed."
      | _ =>
          throwError "SignalComb body rejected: only input references and the exact built-in Signal add/xor instances are accepted."

private def reifyDeclaration (declName : Name) : MetaM ReifiedDeclaration := do
  let info ← auditSourceDeclaration declName
  auditLocalRuntimeClosure (← getEnv) [declName] [declName]
  forallTelescopeReducing info.type fun fvars resultType => do
    unless fvars.size == 3 || fvars.size == 4 do
      signatureError s!"expected exactly two implicit binders and one or two explicit inputs; found {fvars.size} total binders."
    let dom := fvars[0]!
    let width := fvars[1]!
    let domDecl ← getFVarLocalDecl dom
    let widthDecl ← getFVarLocalDecl width
    unless domDecl.userName == `dom && domDecl.binderInfo == .implicit do
      signatureError "the first binder must be exactly implicit {dom : DomainConfig}."
    unless widthDecl.userName == `W && widthDecl.binderInfo == .implicit do
      signatureError "the second binder must be exactly implicit {W : Nat}."
    let domType ← inferType dom
    unless domType.isConstOf ``Sparkle.Core.Domain.DomainConfig do
      signatureError "the first binder must be exactly implicit {dom : DomainConfig}."
    let widthType ← inferType width
    unless widthType.isConstOf ``Nat do
      signatureError "the second binder must be exactly implicit {W : Nat}."
    let arity := if fvars.size == 3 then SourceArity.unary else SourceArity.binary
    let mut inputs : Array SourceInput := #[]
    for index in [2:fvars.size] do
      let input := fvars[index]!
      let inputDecl ← getFVarLocalDecl input
      unless inputDecl.binderInfo == .default do
        signatureError s!"input binder {index - 1} must be explicit."
      requireExactSignalType s!"input binder {index - 1}" (← inferType input) dom width
      let portName := match arity, index with
        | .unary, 2 => "x"
        | .binary, 2 => "a"
        | .binary, 3 => "b"
        | _, _ => panic! "unreachable strict SignalComb input index"
      inputs := inputs.push { fvarId := input.fvarId!, portName }
    requireExactSignalType "the result" resultType dom width
    auditRawSourceBody info.value
    let body ← instantiateSourceLambdas info.value fvars
    let (rhs, operators) ← reifySignalExpr inputs dom width body
    return { arity, rhs, operators }

private partial def quoteCombTerm : CombExpr → CommandElabM (TSyntax `term)
  | .ref name => do
      let literal : TSyntax `str := ⟨Syntax.mkStrLit name⟩
      `(term| CombExpr.ref $literal)
  | .binary operator lhs rhs => do
      let operatorTerm ← match operator with
        | .add => `(term| PackedValue.BinaryOp.add)
        | .bxor => `(term| PackedValue.BinaryOp.bxor)
        | _ => throwError "Internal SignalComb quotation error: non-whitelisted binary operator."
      let lhsTerm ← quoteCombTerm lhs
      let rhsTerm ← quoteCombTerm rhs
      `(term| CombExpr.binary $operatorTerm $lhsTerm $rhsTerm)
  | _ =>
      throwError "Internal SignalComb quotation error: non-whitelisted expression."

private def emitAndAudit
    (command : TSyntax `command) (declName : Name) (executable : Bool) :
    CommandElabM Unit := do
  -- Synchronous addition completes kernel checking before the audit; nested
  -- declarations are immediately visible through `env.find?` and enter the
  -- outer `env.checked` snapshot at the next top-level command boundary.
  let checkedCommand ← `(command| set_option Elab.async false in $command)
  elabCommand checkedCommand
  if (← MonadLog.hasErrors) then
    throwAbortCommand
  liftTermElabM <| auditGeneratedDeclaration declName executable

private def emitCertificate
    (declName : Name) (reified : ReifiedDeclaration) : CommandElabM Name := do
  let supportedName := Name.str declName "signalCombSupported"
  let widthLegalName := Name.str declName "signalCombWidthLegal"
  let bridgeName := Name.str declName "signalCombBridge"
  let certifiedName := Name.str declName "certifiedSignalComb"
  let certifiedCombName := Name.str declName "certifiedComb"
  let correctName := Name.str declName "signalCombCorrect"
  let correctForWidthName := Name.str declName "signalCombCorrectForWidth"
  let targetNames := #[supportedName, widthLegalName, bridgeName,
    certifiedName, certifiedCombName, correctName, correctForWidthName]
  let env ← getEnv
  let collisions := targetNames.filter fun targetName =>
    (env.find? targetName).isSome
  unless collisions.isEmpty do
    throwError m!"SignalComb generation refused: target declaration(s) already exist: {renderNames collisions}."
  let sourceId := mkIdent <| `_root_ ++ declName
  let supportedId := mkIdent <| `_root_ ++ supportedName
  let widthLegalId := mkIdent <| `_root_ ++ widthLegalName
  let bridgeId := mkIdent <| `_root_ ++ bridgeName
  let certifiedId := mkIdent <| `_root_ ++ certifiedName
  let certifiedCombId := mkIdent <| `_root_ ++ certifiedCombName
  let correctId := mkIdent <| `_root_ ++ correctName
  let correctForWidthId := mkIdent <| `_root_ ++ correctForWidthName
  let rhsTerm ← quoteCombTerm reified.rhs
  let moduleName := declName.toString.replace "." "_" ++ "_certified_comb"
  let moduleLiteral : TSyntax `str := ⟨Syntax.mkStrLit moduleName⟩
  let sourceTerm ← `(term| $sourceId)
  let sourceSimp ← `(Parser.Tactic.simpLemma| $sourceTerm:term)

  let supportedCommand ← match reified.arity with
  | .unary =>
      `(command|
        set_option linter.unusedSimpArgs false in
        theorem $supportedId :
            SupportedComb (unarySameWidthDesign $moduleLiteral $rhsTerm) := by
          simp only [SupportedComb, configDomain, unarySameWidthDesign,
            sameWidthDim, List.map_cons, List.map_nil, List.nodup_cons,
            List.not_mem_nil, not_false_eq_true, List.nodup_nil, and_self,
            allBindings, List.nil_append, BindingsScoped, CombExpr.refs,
            List.mem_cons, or_false, imp_self, implies_true, String.reduceEq,
            or_self, ParametersDeclared, dimensions, List.flatMap_cons,
            CombExpr.dimensions, List.flatMap_nil, List.append_nil,
            List.cons_append, forall_eq, dimParameters, and_true, or_imp])
  | .binary =>
      `(command|
        set_option linter.unusedSimpArgs false in
        theorem $supportedId :
            SupportedComb (binarySameWidthDesign $moduleLiteral $rhsTerm) := by
          simp only [SupportedComb, configDomain, binarySameWidthDesign,
            sameWidthDim, List.map_cons, List.map_nil, List.nodup_cons,
            List.not_mem_nil, not_false_eq_true, List.nodup_nil, and_self,
            List.mem_cons, String.reduceEq, or_self, allBindings,
            List.nil_append, BindingsScoped, CombExpr.refs, List.cons_append,
            or_false, imp_self, implies_true, ParametersDeclared, dimensions,
            List.flatMap_cons, CombExpr.dimensions, List.append_nil,
            List.flatMap_nil, forall_eq, dimParameters, and_true, or_imp])
  emitAndAudit supportedCommand supportedName false

  let widthLegalCommand ← match reified.arity with
  | .unary =>
      `(command|
        set_option linter.unusedSimpArgs false in
        theorem $widthLegalId (width : Nat) (positive : 0 < width) :
            ValidConfig (unarySameWidthDesign $moduleLiteral $rhsTerm)
              (sameWidthConfig width) := by
          simpa only [ValidConfig, positiveDimensions, unarySameWidthDesign,
            sameWidthDim, List.map_cons, List.map_nil, allBindings,
            List.nil_append, List.flatMap_cons, CombExpr.positiveDimensions,
            List.flatMap_nil, List.append_nil, List.cons_append, List.mem_cons,
            List.not_mem_nil, or_false, or_self, forall_eq, evalDim,
            sameWidthConfig_parameter, divisors, dimensions,
            CombExpr.dimensions, dimDivisors, ne_eq, false_implies,
            implies_true, CombExpr.SlicesValid, and_self, and_true]
            using positive)
  | .binary =>
      `(command|
        set_option linter.unusedSimpArgs false in
        theorem $widthLegalId (width : Nat) (positive : 0 < width) :
            ValidConfig (binarySameWidthDesign $moduleLiteral $rhsTerm)
              (sameWidthConfig width) := by
          simpa only [ValidConfig, positiveDimensions, binarySameWidthDesign,
            sameWidthDim, List.map_cons, List.map_nil, allBindings,
            List.nil_append, List.flatMap_cons, CombExpr.positiveDimensions,
            List.append_nil, List.flatMap_nil, List.cons_append, List.mem_cons,
            List.not_mem_nil, or_false, or_self, forall_eq, evalDim,
            sameWidthConfig_parameter, divisors, dimensions,
            CombExpr.dimensions, dimDivisors, ne_eq, false_implies,
            implies_true, CombExpr.SlicesValid, and_self, and_true]
            using positive)
  emitAndAudit widthLegalCommand widthLegalName false

  let bridgeCommand ← match reified.arity with
  | .unary =>
      let base ← `(command|
        set_option linter.unusedSimpArgs false in
        theorem $bridgeId (dom : DomainConfig) (config : Sparkle.Compiler.CombCorrectness.Config)
            (input : Signal dom (BitVec (config sameWidthParameterName)))
            (time : Nat)
            (_valid : ValidConfig
              (unarySameWidthDesign $moduleLiteral $rhsTerm) config) :
            ∃ result,
              evalSourceDesign
                  (unarySameWidthDesign $moduleLiteral $rhsTerm) config
                  (unarySameWidthInputEnv config input time) = some result ∧
              result.outputs =
                [("y", config sameWidthParameterName,
                  some {
                    width := config sameWidthParameterName
                    bits :=
                      (@$sourceId dom (config sameWidthParameterName) input).val
                        time
                  })] := by
          simp only [evalSourceDesign, allBindings, unarySameWidthDesign,
            sameWidthDim, List.nil_append, unarySameWidthInputEnv,
            packedSignalSample, evalSourceBindings, evalSourceBinding,
            evalSourceExpr, List.lookup, BEq.rfl, String.reduceBEq,
            PackedValue.binary, HAdd.hAdd, HXor.hXor, Option.pure_def,
            Option.bind_eq_bind, Option.bind_some, PackedValue.resize, evalDim,
            Option.bind_fun_some, observeParameters, List.map_cons,
            List.map_nil, observeSourcePorts, observeSourceWidths,
            observeSourceBindings, portValuesWellTyped, List.all_cons,
            List.all_nil, Bool.and_self, Bool.and_true, Option.some.injEq,
            $sourceSimp, Seq.seq, Signal.seq, Signal.ap, Functor.map,
            Signal.map, exists_eq_left', List.cons.injEq, Prod.mk.injEq,
            PackedValue.mk.injEq, heq_eq_eq, true_and, and_true]
          repeat rw [Nat.max_self]
          repeat rw [BitVec.setWidth_eq])
      if reified.operators.add || reified.operators.xor then
        pure base
      else
        `(command|
          set_option linter.unusedSimpArgs false in
          theorem $bridgeId (dom : DomainConfig) (config : Sparkle.Compiler.CombCorrectness.Config)
              (input : Signal dom (BitVec (config sameWidthParameterName)))
              (time : Nat)
              (_valid : ValidConfig
                (unarySameWidthDesign $moduleLiteral $rhsTerm) config) :
              ∃ result,
                evalSourceDesign
                    (unarySameWidthDesign $moduleLiteral $rhsTerm) config
                    (unarySameWidthInputEnv config input time) = some result ∧
                result.outputs =
                  [("y", config sameWidthParameterName,
                    some {
                      width := config sameWidthParameterName
                      bits :=
                        (@$sourceId dom (config sameWidthParameterName) input).val
                          time
                    })] := by
            simp only [evalSourceDesign, allBindings, unarySameWidthDesign,
              sameWidthDim, List.nil_append, unarySameWidthInputEnv,
              packedSignalSample, evalSourceBindings, evalSourceBinding,
              evalSourceExpr, List.lookup, BEq.rfl, PackedValue.resize,
              evalDim, Option.pure_def, Option.bind_eq_bind, Option.bind_some,
              BitVec.setWidth_eq, Option.bind_fun_some, observeParameters,
              List.map_cons, List.map_nil, observeSourcePorts,
              observeSourceWidths, observeSourceBindings,
              portValuesWellTyped, List.all_cons, List.all_nil, Bool.and_self,
              Bool.and_true, Option.some.injEq, $sourceSimp,
              exists_eq_left'])
  | .binary =>
      let base ← `(command|
        set_option linter.unusedSimpArgs false in
        theorem $bridgeId (dom : DomainConfig) (config : Sparkle.Compiler.CombCorrectness.Config)
            (lhs rhsSignal :
              Signal dom (BitVec (config sameWidthParameterName)))
            (time : Nat)
            (_valid : ValidConfig
              (binarySameWidthDesign $moduleLiteral $rhsTerm) config) :
            ∃ result,
              evalSourceDesign
                  (binarySameWidthDesign $moduleLiteral $rhsTerm) config
                  (binarySameWidthInputEnv config lhs rhsSignal time) =
                some result ∧
              result.outputs =
                [("y", config sameWidthParameterName,
                  some {
                    width := config sameWidthParameterName
                    bits :=
                      (@$sourceId dom (config sameWidthParameterName)
                        lhs rhsSignal).val time
                  })] := by
          simp only [evalSourceDesign, allBindings, binarySameWidthDesign,
            sameWidthDim, List.nil_append, binarySameWidthInputEnv,
            packedSignalSample, evalSourceBindings, evalSourceBinding,
            evalSourceExpr, List.lookup, BEq.rfl, String.reduceBEq,
            PackedValue.binary, HAdd.hAdd, HXor.hXor, Option.pure_def,
            Option.bind_eq_bind, Option.bind_some, PackedValue.resize, evalDim,
            Option.bind_fun_some, observeParameters, List.map_cons,
            List.map_nil, observeSourcePorts, observeSourceWidths,
            observeSourceBindings, portValuesWellTyped, List.all_cons,
            List.all_nil, Bool.and_self, Bool.and_true, Option.some.injEq,
            $sourceSimp, Seq.seq, Signal.seq, Signal.ap, Functor.map,
            Signal.map, exists_eq_left', List.cons.injEq, Prod.mk.injEq,
            PackedValue.mk.injEq, heq_eq_eq, true_and, and_true]
          repeat rw [Nat.max_self]
          repeat rw [BitVec.setWidth_eq])
      if reified.operators.add || reified.operators.xor then
        pure base
      else
        `(command|
          set_option linter.unusedSimpArgs false in
          theorem $bridgeId (dom : DomainConfig) (config : Sparkle.Compiler.CombCorrectness.Config)
              (lhs rhsSignal :
                Signal dom (BitVec (config sameWidthParameterName)))
              (time : Nat)
              (_valid : ValidConfig
                (binarySameWidthDesign $moduleLiteral $rhsTerm) config) :
              ∃ result,
                evalSourceDesign
                    (binarySameWidthDesign $moduleLiteral $rhsTerm) config
                    (binarySameWidthInputEnv config lhs rhsSignal time) =
                  some result ∧
                result.outputs =
                  [("y", config sameWidthParameterName,
                    some {
                      width := config sameWidthParameterName
                      bits :=
                        (@$sourceId dom (config sameWidthParameterName)
                          lhs rhsSignal).val time
                    })] := by
            simp only [evalSourceDesign, allBindings, binarySameWidthDesign,
              sameWidthDim, List.nil_append, binarySameWidthInputEnv,
              packedSignalSample, evalSourceBindings, evalSourceBinding,
              evalSourceExpr, List.lookup, BEq.rfl, String.reduceBEq,
              PackedValue.resize, evalDim, Option.pure_def,
              Option.bind_eq_bind, Option.bind_some, BitVec.setWidth_eq,
              Option.bind_fun_some, observeParameters, List.map_cons,
              List.map_nil, observeSourcePorts, observeSourceWidths,
              observeSourceBindings, portValuesWellTyped, List.all_cons,
              List.all_nil, Bool.and_self, Bool.and_true, Option.some.injEq,
              $sourceSimp, exists_eq_left'])
  emitAndAudit bridgeCommand bridgeName false

  let certifiedCommand ← match reified.arity with
  | .unary =>
      `(command|
        def $certifiedId : CertifiedUnarySignalCombDesign $sourceId :=
          { moduleName := $moduleLiteral
            rhs := $rhsTerm
            supported := $supportedId
            widthLegal := $widthLegalId
            bridge := $bridgeId })
  | .binary =>
      `(command|
        def $certifiedId : CertifiedBinarySignalCombDesign $sourceId :=
          { moduleName := $moduleLiteral
            rhs := $rhsTerm
            supported := $supportedId
            widthLegal := $widthLegalId
            bridge := $bridgeId })
  emitAndAudit certifiedCommand certifiedName true

  let certifiedCombCommand ← match reified.arity with
  | .unary =>
      `(command|
        def $certifiedCombId : CertifiedCombDesign :=
          CertifiedUnarySignalCombDesign.comb $certifiedId)
  | .binary =>
      `(command|
        def $certifiedCombId : CertifiedCombDesign :=
          CertifiedBinarySignalCombDesign.comb $certifiedId)
  emitAndAudit certifiedCombCommand certifiedCombName true

  let correctCommand ← match reified.arity with
  | .unary =>
      `(command|
        theorem $correctId :
            CertifiedUnarySignalCombCorrectnessStatement
              $sourceId $certifiedId :=
          compileCertifiedUnarySignalComb_correct $certifiedId)
  | .binary =>
      `(command|
        theorem $correctId :
            CertifiedBinarySignalCombCorrectnessStatement
              $sourceId $certifiedId :=
          compileCertifiedBinarySignalComb_correct $certifiedId)
  emitAndAudit correctCommand correctName false

  let correctForWidthCommand ← match reified.arity with
  | .unary =>
      `(command|
        theorem $correctForWidthId :
            CertifiedUnarySignalCombCorrectnessForWidthStatement
              $sourceId $certifiedId :=
          compileCertifiedUnarySignalComb_correct_for_width $certifiedId)
  | .binary =>
      `(command|
        theorem $correctForWidthId :
            CertifiedBinarySignalCombCorrectnessForWidthStatement
              $sourceId $certifiedId :=
          compileCertifiedBinarySignalComb_correct_for_width $certifiedId)
  emitAndAudit correctForWidthCommand correctForWidthName false
  return certifiedName

/--
Certify an exact ordinary declaration of one of these two forms:

* `{dom : DomainConfig} {W : Nat} → Signal dom (BitVec W) → Signal dom (BitVec W)`
* the same form with two explicit Signal inputs.

The body may contain only input references and recursively nested uses of
Sparkle's exact built-in same-width Signal addition and XOR instances.  The
command generates `<source>.certifiedSignalComb`, a closed production alias
`<source>.certifiedComb`, and all-config/arbitrary-positive-width correctness
theorems.  Everything else is rejected fail-closed.
-/
elab "#certifySignalComb" sourceId:ident : command => do
  let declName ← liftCoreM <| Lean.resolveGlobalConstNoOverload sourceId
  let reified ← liftTermElabM <| reifyDeclaration declName
  let certifiedName ← emitCertificate declName reified
  logInfo m!"Certified exact ordinary Signal declaration '{declName}' as original-indexed certificate '{certifiedName}'."

end Sparkle.Compiler.SignalCombElab
