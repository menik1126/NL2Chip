/-
  Independent acceptance tests for the phase-one, explicitly deep-embedded
  combinational compiler theorem.

  These tests deliberately do not exercise `Compiler.Elab` or the ordinary
  `Signal` frontend.  They check that the verified source/core boundary is
  nonempty, width-parametric, interface-sensitive, and capable of detecting
  representative incorrect lowerings.
-/

import Lean
import Lean.Compiler.ImplementedByAttr
import Sparkle.Compiler.CombCorrectness

namespace Tests.CombCorrectnessTests

open Lean
open Sparkle.IR.AST
open Sparkle.IR.Type
open Sparkle.Compiler.CombCorrectness

private def w : DimExpr := .param "W"
private def wMinusOne : DimExpr := .sub w 1
private def wPlusOne : DimExpr := .add w 1

def widthConfig (width : Nat) : Config :=
  fun name => if name == "W" then width else 0

/-- A nontrivial supported circuit.  Its selected path exercises add, a
    parameter-dependent slice, concat, resize, and mux at every positive W. -/
def auditDesign : CombDesign :=
  { name := "comb_correctness_audit"
    parameters := [{ name := "W", defaultValue := 3 }]
    inputs :=
      [{ name := "sel", width := 1 },
       { name := "a", width := w },
       { name := "b", width := w }]
    wires :=
      [{ name := "sum", width := w,
         rhs := .binary .add (.ref "a") (.ref "b") },
       { name := "low", width := w,
         rhs := .slice (.ref "sum") wMinusOne 0 },
       { name := "tagged", width := wPlusOne,
         rhs := .concat (.const 1 1) (.ref "low") },
       { name := "narrowed", width := w,
         rhs := .resize w (.ref "tagged") }]
    outputs :=
      [{ name := "y", width := w,
         rhs := .mux (.ref "sel") (.ref "narrowed") (.ref "b") }] }

theorem auditDesign_supported : SupportedComb auditDesign := by
  simp [SupportedComb, configDomain, ParametersDeclared, dimensions,
    auditDesign, allBindings, BindingsScoped, CombExpr.refs,
    CombExpr.dimensions, dimParameters, w, wMinusOne, wPlusOne]

/-- In particular, `SupportedComb` is not an empty predicate. -/
theorem supportedComb_nonempty : ∃ design, SupportedComb design :=
  ⟨auditDesign, auditDesign_supported⟩

def certifiedAuditDesign : CertifiedCombDesign :=
  { design := auditDesign, supported := auditDesign_supported }

example : CoreCombWellFormed (compileSupportedComb certifiedAuditDesign) :=
  compileSupportedComb_wellFormed certifiedAuditDesign

theorem auditDesign_valid (width : Nat) (positive : 0 < width) :
    ValidConfig auditDesign (widthConfig width) := by
  simp [ValidConfig, positiveDimensions, divisors, dimensions, dimDivisors,
    auditDesign, allBindings, CombExpr.positiveDimensions,
    CombExpr.dimensions, CombExpr.SlicesValid, evalDim, widthConfig, w,
    wMinusOne, wPlusOne, DimExpr.mkSub, DimExpr.mkAdd, positive]

def auditInputs (width : Nat) (selected : Bool) : ValueEnv :=
  [("sel", PackedValue.ofNat 1 (if selected then 1 else 0)),
   ("a", PackedValue.ofNat width 1),
   ("b", PackedValue.ofNat width 2)]

theorem auditInputs_valid (width : Nat) (selected : Bool) :
    ValidInputs auditDesign (widthConfig width) (auditInputs width selected) := by
  simp [ValidInputs, auditDesign, auditInputs, PackedValue.ofNat, evalDim,
    widthConfig, w, List.lookup]

theorem auditDesign_total (width : Nat) (positive : 0 < width)
    (selected : Bool) :
    ∃ result,
      evalSourceDesign auditDesign (widthConfig width)
          (auditInputs width selected) = some result ∧
      evalCoreModule (compileSupportedComb certifiedAuditDesign)
          (widthConfig width) (auditInputs width selected) = some result :=
  compileSupportedComb_correct_total certifiedAuditDesign
    (widthConfig width) (auditInputs width selected)
    (auditDesign_valid width positive) (auditInputs_valid width selected)

/-- The production non-vacuity witness composes with the production totality
    theorem: at every positive width there is a genuinely successful source
    and compiled-core evaluation, not merely an equality of two failures. -/
theorem productionWitness_total_at_every_positive_width
    (width : Nat) (positive : 0 < width) :
    ∃ inputs result,
      ValidConfig compilerCorrectnessWitness.design
          (compilerCorrectnessWitnessConfig width) ∧
        ValidInputs compilerCorrectnessWitness.design
          (compilerCorrectnessWitnessConfig width) inputs ∧
        evalSourceDesign compilerCorrectnessWitness.design
            (compilerCorrectnessWitnessConfig width) inputs = some result ∧
        evalCoreModule (compileSupportedComb compilerCorrectnessWitness)
            (compilerCorrectnessWitnessConfig width) inputs = some result := by
  rcases compilerCorrectnessDomain_inhabited width positive with
    ⟨inputs, validConfig, validInputs⟩
  rcases compileSupportedComb_correct_total compilerCorrectnessWitness
      (compilerCorrectnessWitnessConfig width) inputs validConfig validInputs with
    ⟨result, sourceDefined, coreDefined⟩
  exact ⟨inputs, result, validConfig, validInputs, sourceDefined, coreDefined⟩

private def outputValue? (result : Option Evaluation) (name : String) :
    Option PackedValue := do
  let evaluation ← result
  let (_, _, value) ← evaluation.outputs.find? fun (portName, _, _) =>
    portName == name
  value

def expectedAuditInputs : List Port :=
  [{ name := "sel", ty := .bitVector 1 },
   { name := "a", ty := .bitVector w },
   { name := "b", ty := .bitVector w }]

def expectedAuditWires : List Port :=
  [{ name := "sum", ty := .bitVector w },
   { name := "low", ty := .bitVector w },
   { name := "tagged", ty := .bitVector wPlusOne },
   { name := "narrowed", ty := .bitVector w }]

def expectedAuditOutputs : List Port :=
  [{ name := "y", ty := .bitVector w }]

def expectedAuditBody : List Stmt :=
  [.assign "sum" (.resize w (.op .add [.ref "a", .ref "b"])),
   .assign "low" (.resize w (.slice (.ref "sum") wMinusOne 0)),
   .assign "tagged"
     (.resize wPlusOne (.concat [.const 1 1, .ref "low"])),
   .assign "narrowed" (.resize w (.resize w (.ref "tagged"))),
   .assign "y"
     (.resize w (.op .mux [.ref "sel", .ref "narrowed", .ref "b"]))]

/-- A wrong lowering of the first operation.  At W=3, a=1 and b=2, this
    computes 7 rather than 3 and must not satisfy the source semantics. -/
def wrongOperatorModule : Sparkle.IR.AST.Module :=
  { compileSupportedComb certifiedAuditDesign with
    body :=
      [.assign "sum" (.resize w (.op .sub [.ref "a", .ref "b"])),
       .assign "low" (.resize w (.slice (.ref "sum") wMinusOne 0)),
       .assign "tagged"
         (.resize wPlusOne (.concat [.const 1 1, .ref "low"])),
       .assign "narrowed" (.resize w (.resize w (.ref "tagged"))),
       .assign "y"
         (.resize w (.op .mux [.ref "sel", .ref "narrowed", .ref "b"]))] }

/-- A wrong module interface with otherwise unchanged logic.  The verified
    observation includes declared port widths, so this mutation is visible. -/
def wrongPortWidthModule : Sparkle.IR.AST.Module :=
  { compileSupportedComb certifiedAuditDesign with
    outputs := [{ name := "y", ty := .bitVector wPlusOne }] }

private def requireCheck (label : String) (condition : Bool) : IO Unit :=
  unless condition do
    throw <| IO.userError s!"comb-correctness acceptance failed: {label}"

private def checkWidth (width : Nat) : IO Unit := do
  let config := widthConfig width
  for selected in [false, true] do
    let inputs := auditInputs width selected
    let source := evalSourceDesign auditDesign config inputs
    let core := evalCoreModule (compileSupportedComb certifiedAuditDesign) config inputs
    requireCheck s!"source result is inhabited at W={width}, sel={selected}"
      source.isSome
    requireCheck s!"core result is inhabited at W={width}, sel={selected}"
      core.isSome
    requireCheck s!"source/core equality at W={width}, sel={selected}" (source == core)
    let expected := PackedValue.ofNat width (if selected then 3 else 2)
    requireCheck s!"nontrivial output at W={width}, sel={selected}"
      (outputValue? source "y" == some expected)

private def checkProductionWitnessWidth (width : Nat) : IO Unit := do
  let config := compilerCorrectnessWitnessConfig width
  let inputs := compilerCorrectnessWitnessInputs width
  let source := evalSourceDesign compilerCorrectnessWitness.design config inputs
  let core := evalCoreModule
    (compileSupportedComb compilerCorrectnessWitness) config inputs
  requireCheck s!"production witness maps W to the requested width {width}"
    (config "W" == width)
  requireCheck s!"production witness source result is inhabited at W={width}"
    source.isSome
  requireCheck s!"production witness core result is inhabited at W={width}"
    core.isSome
  requireCheck s!"production witness source/core equality at W={width}"
    (source == core)
  requireCheck s!"production witness output has the requested width W={width}"
    (outputValue? source "y" == some (PackedValue.ofNat width 0))

private def assertSafeDefinition (declName : Name) : CoreM Unit := do
  let env ← getEnv
  match env.find? declName with
  | some (.defnInfo info) =>
      unless info.safety == .safe do
        throwError m!"{declName} is not a total safe definition"
      if (Lean.Compiler.getImplementedBy? env declName).isSome then
        throwError m!"{declName} has an unproved @[implemented_by] runtime replacement"
  | some _ => throwError m!"{declName} is not a transparent production definition"
  | none => throwError m!"missing declaration {declName}"

private def inCombCorrectnessModule (env : Environment)
    (declName : Name) : Bool :=
  match env.getModuleIdxFor? declName with
  | some moduleIdx =>
      env.header.modules[moduleIdx]!.module == `Sparkle.Compiler.CombCorrectness
  | none => false

/-- Follow definition bodies only inside the trusted compiler module.  Sharing
    primitive BitVec operations is intentional, but neither interpreter may
    call the other interpreter (or recover source semantics through the
    compiler). -/
private partial def transitivelyDependsOn (env : Environment)
    (root target : Name) (visited : List Name := []) : Bool :=
  if root == target then true
  else if visited.contains root then false
  else
    match env.find? root with
    | none => false
    | some info =>
      match info.value? with
      | none => false
      | some value =>
        value.getUsedConstants.any fun dependency =>
          inCombCorrectnessModule env dependency &&
            transitivelyDependsOn env dependency target (root :: visited)

private def assertIndependentSemantics : CoreM Unit := do
  let env ← getEnv
  let forbidden := [
    (``evalSourceExpr, ``evalCoreExpr),
    (``evalSourceDesign, ``evalCoreModule),
    (``evalSourceExpr, ``compileExpr),
    (``evalSourceDesign, ``compileComb),
    (``evalCoreExpr, ``evalSourceExpr),
    (``evalCoreModule, ``evalSourceDesign),
    (``evalCoreExpr, ``compileExpr),
    (``evalCoreModule, ``compileComb),
    (``compileExpr, ``evalSourceExpr),
    (``compileExpr, ``evalCoreExpr),
    (``compileComb, ``evalSourceDesign),
    (``compileComb, ``evalCoreModule)
  ]
  for (root, target) in forbidden do
    if transitivelyDependsOn env root target then
      throwError m!"trusted definition {root} transitively depends on forbidden {target}"

/-- This is a CI assertion, rather than a diagnostic-only `#print axioms`.
    Only Lean's standard logical principles are accepted; in particular,
    `sorryAx` and project-defined axioms fail this gate. -/
example : True := by
  run_tac
    let allowedAxioms : Array Name :=
      #[``propext, ``Classical.choice, ``Quot.sound]
    for theoremName in
        [``compileExpr_correct, ``compileComb_correct,
         ``compileComb_correct_total, ``compileComb_wellFormed,
         ``compileSupportedComb_correct, ``compileSupportedComb_correct_total,
         ``compileSupportedComb_wellFormed,
         ``compilerCorrectnessWitness_supported,
         ``compilerCorrectnessWitness_validConfig,
         ``compilerCorrectnessWitness_validInputs,
         ``compilerCorrectnessDomain_inhabited] do
      let axioms ← Lean.collectAxioms theoremName
      let disallowed := axioms.filter fun axiomName =>
        !allowedAxioms.contains axiomName
      unless disallowed.isEmpty do
        throwError m!"{theoremName} depends on disallowed axioms: {disallowed}"
    for definitionName in
        [``compileExpr, ``compileComb, ``compileSupportedComb,
         ``evalSourceExpr, ``evalSourceDesign, ``evalCoreExpr,
         ``evalCoreModule, ``compilerCorrectnessWitnessDesign,
         ``compilerCorrectnessWitnessConfig,
         ``compilerCorrectnessWitnessInputs,
         ``compilerCorrectnessWitness] do
      assertSafeDefinition definitionName
    assertIndependentSemantics
  trivial

def runTests : IO Unit := do
  let compiled := compileSupportedComb certifiedAuditDesign
  requireCheck "parameter declarations preserved"
    (compiled.parameters == auditDesign.parameters)
  requireCheck "input interface widths preserved"
    (compiled.inputs == expectedAuditInputs)
  requireCheck "wire widths preserved"
    (compiled.wires == expectedAuditWires)
  requireCheck "output interface widths preserved"
    (compiled.outputs == expectedAuditOutputs)
  requireCheck "lowered statement structure"
    (compiled.body == expectedAuditBody)

  let witnessCompiled := compileSupportedComb compilerCorrectnessWitness
  requireCheck "production witness has the fixed symbolic-W parameter"
    (witnessCompiled.parameters == [{ name := "W", defaultValue := 1 }])
  requireCheck "production witness input is truly W-bit"
    (witnessCompiled.inputs ==
      [{ name := "x", ty := .bitVector (.param "W") }])
  requireCheck "production witness output is truly W-bit"
    (witnessCompiled.outputs ==
      [{ name := "y", ty := .bitVector (.param "W") }])
  requireCheck "production witness lowers a real width-parametric statement"
    (witnessCompiled.body ==
      [.assign "y" (.resize (.param "W") (.ref "x"))])

  -- These widths intentionally include the one-bit corner case, odd widths,
  -- and a width large enough to catch accidental small-integer specialization.
  for width in [1, 3, 17, 257] do
    checkWidth width
    checkProductionWitnessWidth width

  let config := widthConfig 3
  let inputs := auditInputs 3 true
  let source := evalSourceDesign auditDesign config inputs
  requireCheck "wrong operator mutation is observable"
    (evalCoreModule wrongOperatorModule config inputs != source)
  requireCheck "wrong port-width mutation is observable"
    (evalCoreModule wrongPortWidthModule config inputs != source)

  let malformedArity : Sparkle.IR.AST.Module :=
    { compiled with
      body := [.assign "y" (.op .add [.ref "a"])] }
  requireCheck "malformed core operator arity fails closed"
    ((evalCoreModule malformedArity config inputs).isNone)

  let unsupportedCore : Sparkle.IR.AST.Module :=
    { compiled with
      body := [.assign "y" (.op .asr [.ref "a", .ref "b"])] }
  requireCheck "operator outside the verified subset fails closed"
    ((evalCoreModule unsupportedCore config inputs).isNone)

  IO.println "Comb compiler correctness acceptance tests passed"

end Tests.CombCorrectnessTests

def main : IO Unit :=
  Tests.CombCorrectnessTests.runTests
