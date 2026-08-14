/-
  A fail-closed certificate for the phase-one combinational compiler theorem.

  Unlike the generic universal-theorem certificate, this module accepts no
  caller-selected declaration.  It is hard-bound to the trusted source AST,
  compiler, source/core semantics, domain predicates, preservation theorem,
  and non-vacuity theorem in `Sparkle.Compiler.CombCorrectness`.
-/

import Lean
import Lean.Compiler.ImplementedByAttr
import Sparkle.Compiler.CombCorrectness

namespace Sparkle.Compiler.CombCertificate

open Lean Meta

/-- The only scope for which this certificate makes a correctness claim. -/
def certifiedScope : String := "supported_comb_ast_to_core_ir"

/-- The module containing every declaration in the phase-one trust boundary. -/
def trustedModule : Name := `Sparkle.Compiler.CombCorrectness

/-- The fixed module containing the checker and its metadata sentinel. -/
def certificateModule : Name := `Sparkle.Compiler.CombCertificate

private def implementedByMetadataSentinelImpl : Nat := 0

/-- Never executed; its persisted attribute lets the standalone checker prove
    that `implemented_by` metadata is visible before auditing the real roots. -/
@[implemented_by implementedByMetadataSentinelImpl]
def implementedByMetadataSentinel : Nat := 0

/-! The non-vacuity witness is duplicated as a small trusted specification in
    the checker module.  The production definitions must be definitionally
    equal to these terms; merely retaining their declaration names is not
    sufficient because an empty design would otherwise inhabit the domain. -/

private def expectedDomainWitnessDesign :
    Sparkle.Compiler.CombCorrectness.CombDesign :=
  { name := "comb_correctness_width_witness"
    parameters := [{ name := "W", defaultValue := 1 }]
    inputs := [{ name := "x", width := .param "W" }]
    outputs := [{ name := "y", width := .param "W", rhs := .ref "x" }] }

private def expectedDomainWitnessConfig (width : Nat) :
    Sparkle.Compiler.CombCorrectness.Config :=
  fun name => if name == "W" then width else 0

private def expectedDomainWitnessInputs (width : Nat) :
    Sparkle.Compiler.CombCorrectness.ValueEnv :=
  [("x", Sparkle.Compiler.CombCorrectness.PackedValue.ofNat width 0)]

private def expectedPositiveWidth (width : Nat) : Prop :=
  0 < width

/-- The fixed semantic-preservation statement. -/
def correctnessStatementName : Name :=
  ``Sparkle.Compiler.CombCorrectness.CompilerCorrectnessStatement

/-- The fixed semantic-preservation proof. -/
def correctnessTheoremName : Name :=
  ``Sparkle.Compiler.CombCorrectness.compileComb_correct

/-- The fixed statement ruling out equality caused by both interpreters failing. -/
def totalityStatementName : Name :=
  ``Sparkle.Compiler.CombCorrectness.CompilerCorrectnessTotalStatement

/-- The fixed proof ruling out equality caused by both interpreters failing. -/
def totalityTheoremName : Name :=
  ``Sparkle.Compiler.CombCorrectness.compileComb_correct_total

/-- Fixed statement that the certified domain is inhabited at every positive width. -/
def domainInhabitedStatementName : Name :=
  ``Sparkle.Compiler.CombCorrectness.CompilerCorrectnessDomainInhabitedStatement

/-- Fixed proof that the certified domain is inhabited at every positive width. -/
def domainInhabitedTheoremName : Name :=
  ``Sparkle.Compiler.CombCorrectness.compilerCorrectnessDomain_inhabited

/-- Fixed proof-carrying design used by the non-vacuity theorem. -/
def domainWitnessName : Name :=
  ``Sparkle.Compiler.CombCorrectness.compilerCorrectnessWitness

/-- Fixed axiom-audited proof that the exact witness design is supported. -/
def domainWitnessSupportedTheoremName : Name :=
  ``Sparkle.Compiler.CombCorrectness.compilerCorrectnessWitness_supported

/-- The proof-carrying production compiler entry point. -/
def productionEntryName : Name :=
  ``Sparkle.Compiler.CombCorrectness.compileSupportedComb

/-- Correctness proof for the proof-carrying production entry point. -/
def productionCorrectnessTheoremName : Name :=
  ``Sparkle.Compiler.CombCorrectness.compileSupportedComb_correct

/-- Non-vacuity proof for the proof-carrying production entry point. -/
def productionTotalityTheoremName : Name :=
  ``Sparkle.Compiler.CombCorrectness.compileSupportedComb_correct_total

/-- Core-shape proof for the proof-carrying production entry point. -/
def productionWellFormedTheoremName : Name :=
  ``Sparkle.Compiler.CombCorrectness.compileSupportedComb_wellFormed

private structure TrustedConstant where
  role : String
  name : Name

private def trustedConstants : Array TrustedConstant := #[
  { role := "source_ast", name := ``Sparkle.Compiler.CombCorrectness.CombDesign },
  { role := "certified_source_ast", name := ``Sparkle.Compiler.CombCorrectness.CertifiedCombDesign },
  { role := "configuration", name := ``Sparkle.Compiler.CombCorrectness.Config },
  { role := "input_environment", name := ``Sparkle.Compiler.CombCorrectness.ValueEnv },
  { role := "supported_domain", name := ``Sparkle.Compiler.CombCorrectness.SupportedComb },
  { role := "valid_configuration", name := ``Sparkle.Compiler.CombCorrectness.ValidConfig },
  { role := "valid_inputs", name := ``Sparkle.Compiler.CombCorrectness.ValidInputs },
  { role := "compiler", name := ``Sparkle.Compiler.CombCorrectness.compileComb },
  { role := "production_entry", name := productionEntryName },
  { role := "source_semantics", name := ``Sparkle.Compiler.CombCorrectness.evalSourceDesign },
  { role := "core_semantics", name := ``Sparkle.Compiler.CombCorrectness.evalCoreModule },
  { role := "correctness_statement", name := correctnessStatementName },
  { role := "correctness_theorem", name := correctnessTheoremName },
  { role := "totality_statement", name := totalityStatementName },
  { role := "totality_theorem", name := totalityTheoremName },
  { role := "domain_inhabited_statement", name := domainInhabitedStatementName },
  { role := "domain_inhabited_theorem", name := domainInhabitedTheoremName },
  { role := "domain_witness", name := domainWitnessName },
  { role := "domain_witness_supported_theorem",
    name := domainWitnessSupportedTheoremName },
  { role := "domain_witness_design",
    name := ``Sparkle.Compiler.CombCorrectness.compilerCorrectnessWitnessDesign },
  { role := "domain_witness_config",
    name := ``Sparkle.Compiler.CombCorrectness.compilerCorrectnessWitnessConfig },
  { role := "domain_witness_inputs",
    name := ``Sparkle.Compiler.CombCorrectness.compilerCorrectnessWitnessInputs },
  { role := "core_well_formed", name := ``Sparkle.Compiler.CombCorrectness.CoreCombWellFormed },
  { role := "production_correctness_theorem", name := productionCorrectnessTheoremName },
  { role := "production_totality_theorem", name := productionTotalityTheoremName },
  { role := "production_well_formed_theorem", name := productionWellFormedTheoremName }
]

/-- Only Lean's standard logical axioms are admitted into either proof. -/
private def allowedAxioms : Array Name :=
  #[``propext, ``Classical.choice, ``Quot.sound]

private def ppOptions (options : Options) : Options :=
  options
    |>.set `pp.explicit true
    |>.set `pp.fullNames true
    |>.set `pp.universes true
    |>.set `pp.piBinderNames true
    |>.set `pp.deepTerms true
    |>.set `pp.proofs true
    |>.set `pp.rawOnError true
    |>.set `pp.maxSteps 1000000

private def renderExpr (expr : Expr) : MetaM String :=
  withOptions ppOptions do
    return toString (← ppExpr expr)

private def requireOwnedByTrustedModule (env : Environment) (declName : Name) : MetaM Unit := do
  let moduleIdx ← match env.getModuleIdxFor? declName with
    | some moduleIdx => pure moduleIdx
    | none => throwError m!"Trusted declaration '{declName}' is missing module ownership metadata."
  let declaringModule := env.header.modules[moduleIdx]!.module
  unless declaringModule == trustedModule do
    throwError m!"Trusted declaration '{declName}' is owned by '{declaringModule}', not '{trustedModule}'."

private def isOwnedByTrustedModule (env : Environment) (declName : Name) : Bool :=
  match env.getModuleIdxFor? declName with
  | some moduleIdx => env.header.modules[moduleIdx]!.module == trustedModule
  | none => false

private def requireSafeDefinition (env : Environment) (declName : Name) : MetaM Unit := do
  match env.checked.get.find? declName with
  | some (.defnInfo info) =>
      unless info.safety == .safe do
        throwError m!"Trusted definition '{declName}' is not a total safe definition."
      if (Lean.Compiler.getImplementedBy? env declName).isSome then
        throwError m!"Trusted definition '{declName}' has an unproved @[implemented_by] replacement."
  | some _ =>
      throwError m!"Trusted declaration '{declName}' is not a transparent definition."
  | none =>
      throwError m!"Trusted definition '{declName}' is absent from Lean's kernel-checked environment."

/--
Recursively audit every executable definition in the fixed module that is
reachable from the production compiler and the two interpreters.  Looking only
at the three roots would miss an unproved `implemented_by` replacement on a
lowering helper such as `compileExpr`, `evalDim`, or `evalCoreExpr`.
-/
private partial def auditExecutableClosure
    (env : Environment) (pending : List Name)
    (visited : Array Name := #[]) (audited : Array Name := #[]) : MetaM (Array Name) := do
  match pending with
  | [] => return audited.qsort Name.lt
  | declName :: rest =>
      if visited.contains declName then
        auditExecutableClosure env rest visited audited
      else
        let visited := visited.push declName
        if !isOwnedByTrustedModule env declName then
          auditExecutableClosure env rest visited audited
        else
          match env.checked.get.find? declName with
          | some (.defnInfo info) =>
              requireSafeDefinition env declName
              let dependencies := info.value.getUsedConstants.toList
              auditExecutableClosure env (dependencies ++ rest) visited
                (audited.push declName)
          | some (.thmInfo _) | some (.inductInfo _) | some (.ctorInfo _)
          | some (.recInfo _) | some (.quotInfo _) =>
              auditExecutableClosure env rest visited audited
          | some (.opaqueInfo _) =>
              throwError m!"Executable trust closure reaches opaque declaration '{declName}'."
          | some (.axiomInfo _) =>
              throwError m!"Executable trust closure reaches axiom '{declName}'."
          | none =>
              throwError m!"Executable trust closure reaches missing declaration '{declName}'."

private def requireDefEq (label : String) (actual expected : Expr) : MetaM Unit := do
  unless ← withTransparency .all <| isDefEq actual expected do
    let actualText ← renderExpr actual
    let expectedText ← renderExpr expected
    throwError m!"{label} has the wrong kernel type. Expected '{expectedText}', got '{actualText}'."

private def requireDomainWitnessShape : MetaM Unit := do
  requireDefEq "domain witness design"
    (mkConst ``Sparkle.Compiler.CombCorrectness.compilerCorrectnessWitnessDesign)
    (mkConst ``expectedDomainWitnessDesign)
  requireDefEq "domain witness configuration function"
    (mkConst ``Sparkle.Compiler.CombCorrectness.compilerCorrectnessWitnessConfig)
    (mkConst ``expectedDomainWitnessConfig)
  requireDefEq "domain witness input function"
    (mkConst ``Sparkle.Compiler.CombCorrectness.compilerCorrectnessWitnessInputs)
    (mkConst ``expectedDomainWitnessInputs)
  let witnessDesign ← mkAppM
    ``Sparkle.Compiler.CombCorrectness.CertifiedCombDesign.design
    #[mkConst domainWitnessName]
  requireDefEq "proof-carrying domain witness design" witnessDesign
    (mkConst ``expectedDomainWitnessDesign)

private def requireDomainWitnessSupportedShape (theoremType : Expr) : MetaM Unit := do
  forallTelescopeReducing theoremType fun fvars conclusion => do
    unless fvars.isEmpty do
      throwError m!"Domain witness supported theorem must have no binders; found {fvars.size}."
    let expectedConclusion ← mkAppM
      ``Sparkle.Compiler.CombCorrectness.SupportedComb
      #[mkConst ``expectedDomainWitnessDesign]
    requireDefEq "domain witness supported conclusion" conclusion expectedConclusion

private def checkedTheorem (declName : Name) : MetaM (Expr × Array Name) := do
  let env ← getEnv
  let info ← match env.checked.get.find? declName with
    | some (.thmInfo info) => pure info
    | some _ => throwError m!"Trusted declaration '{declName}' is not a Lean theorem or lemma."
    | none => throwError m!"Trusted theorem '{declName}' is absent from Lean's kernel-checked environment."
  if info.value.hasSorry then
    throwError m!"Trusted theorem '{declName}' directly contains 'sorry'."
  let axioms ← Lean.collectAxioms declName
  if axioms.contains ``sorryAx then
    throwError m!"Trusted theorem '{declName}' transitively depends on 'sorry'."
  let disallowed := axioms.filter fun axiomName => !allowedAxioms.contains axiomName
  unless disallowed.isEmpty do
    let rendered := String.intercalate ", " <|
      (disallowed.qsort Name.lt).toList.map Name.toString
    throwError m!"Trusted theorem '{declName}' depends on non-allowlisted axiom(s): {rendered}."
  return (info.type, axioms.qsort Name.lt)

private def requireDefaultBinder (localDecl : LocalDecl) : MetaM Unit := do
  unless localDecl.binderInfo == .default do
    throwError m!"Compiler correctness binder '{localDecl.userName}' must be explicit."

private def requireCorrectnessShape (theoremType : Expr) : MetaM Unit := do
  forallTelescopeReducing theoremType fun fvars conclusion => do
    unless fvars.size == 6 do
      throwError m!"Compiler correctness theorem must have exactly six binders; found {fvars.size}."
    let decls ← fvars.mapM getFVarLocalDecl
    for decl in decls do
      requireDefaultBinder decl

    let design := fvars[0]!
    let config := fvars[1]!
    let inputs := fvars[2]!
    requireDefEq "design binder" decls[0]!.type (mkConst ``Sparkle.Compiler.CombCorrectness.CombDesign)
    requireDefEq "configuration binder" decls[1]!.type (mkConst ``Sparkle.Compiler.CombCorrectness.Config)
    requireDefEq "input-environment binder" decls[2]!.type (mkConst ``Sparkle.Compiler.CombCorrectness.ValueEnv)
    requireDefEq "supported-domain premise" decls[3]!.type
      (← mkAppM ``Sparkle.Compiler.CombCorrectness.SupportedComb #[design])
    requireDefEq "valid-configuration premise" decls[4]!.type
      (← mkAppM ``Sparkle.Compiler.CombCorrectness.ValidConfig #[design, config])
    requireDefEq "valid-input premise" decls[5]!.type
      (← mkAppM ``Sparkle.Compiler.CombCorrectness.ValidInputs #[design, config, inputs])

    let compiled ← mkAppM ``Sparkle.Compiler.CombCorrectness.compileComb #[design]
    let coreResult ← mkAppM ``Sparkle.Compiler.CombCorrectness.evalCoreModule #[compiled, config, inputs]
    let sourceResult ← mkAppM ``Sparkle.Compiler.CombCorrectness.evalSourceDesign #[design, config, inputs]
    let expectedConclusion ← mkAppM ``Eq #[coreResult, sourceResult]
    requireDefEq "compiler correctness conclusion" conclusion expectedConclusion

private def requireTotalityShape (theoremType : Expr) : MetaM Unit := do
  forallTelescopeReducing theoremType fun fvars conclusion => do
    unless fvars.size == 6 do
      throwError m!"Compiler correctness totality theorem must have exactly six binders; found {fvars.size}."
    let decls ← fvars.mapM getFVarLocalDecl
    for decl in decls do
      requireDefaultBinder decl

    let design := fvars[0]!
    let config := fvars[1]!
    let inputs := fvars[2]!
    requireDefEq "totality design binder" decls[0]!.type (mkConst ``Sparkle.Compiler.CombCorrectness.CombDesign)
    requireDefEq "totality configuration binder" decls[1]!.type (mkConst ``Sparkle.Compiler.CombCorrectness.Config)
    requireDefEq "totality input-environment binder" decls[2]!.type (mkConst ``Sparkle.Compiler.CombCorrectness.ValueEnv)
    requireDefEq "totality supported-domain premise" decls[3]!.type
      (← mkAppM ``Sparkle.Compiler.CombCorrectness.SupportedComb #[design])
    requireDefEq "totality valid-configuration premise" decls[4]!.type
      (← mkAppM ``Sparkle.Compiler.CombCorrectness.ValidConfig #[design, config])
    requireDefEq "totality valid-input premise" decls[5]!.type
      (← mkAppM ``Sparkle.Compiler.CombCorrectness.ValidInputs #[design, config, inputs])

    let sourceResult ← mkAppM ``Sparkle.Compiler.CombCorrectness.evalSourceDesign #[design, config, inputs]
    let compiled ← mkAppM ``Sparkle.Compiler.CombCorrectness.compileComb #[design]
    let coreResult ← mkAppM ``Sparkle.Compiler.CombCorrectness.evalCoreModule #[compiled, config, inputs]
    withLocalDeclD `result (mkConst ``Sparkle.Compiler.CombCorrectness.Evaluation) fun result => do
      let someResult ← mkAppM ``Option.some #[result]
      let sourceDefined ← mkAppM ``Eq #[sourceResult, someResult]
      let coreDefined ← mkAppM ``Eq #[coreResult, someResult]
      let bothDefined ← mkAppM ``And #[sourceDefined, coreDefined]
      let predicate ← mkLambdaFVars #[result] bothDefined
      let expectedConclusion ← mkAppM ``Exists #[predicate]
      requireDefEq "compiler correctness totality conclusion" conclusion expectedConclusion

/-- Require the fixed witness family to inhabit the exact certified domain for
    every positive natural-number width. -/
private def requireDomainInhabitedShape (theoremType : Expr) : MetaM Unit := do
  forallTelescopeReducing theoremType fun fvars conclusion => do
    unless fvars.size == 2 do
      throwError m!"Compiler correctness domain theorem must have exactly two binders; found {fvars.size}."
    let decls ← fvars.mapM getFVarLocalDecl
    for decl in decls do
      requireDefaultBinder decl

    let width := fvars[0]!
    requireDefEq "domain witness width binder" decls[0]!.type (mkConst ``Nat)
    let positive ← mkAppM ``expectedPositiveWidth #[width]
    requireDefEq "domain witness positivity premise" decls[1]!.type positive

    let witness := mkConst domainWitnessName
    let design ← mkAppM
      ``Sparkle.Compiler.CombCorrectness.CertifiedCombDesign.design #[witness]
    let config ← mkAppM
      ``Sparkle.Compiler.CombCorrectness.compilerCorrectnessWitnessConfig #[width]
    withLocalDeclD `inputs
        (mkConst ``Sparkle.Compiler.CombCorrectness.ValueEnv) fun inputs => do
      let validConfig ← mkAppM ``Sparkle.Compiler.CombCorrectness.ValidConfig
        #[design, config]
      let validInputs ← mkAppM ``Sparkle.Compiler.CombCorrectness.ValidInputs
        #[design, config, inputs]
      let validDomain ← mkAppM ``And #[validConfig, validInputs]
      let predicate ← mkLambdaFVars #[inputs] validDomain
      let expectedConclusion ← mkAppM ``Exists #[predicate]
      requireDefEq "compiler correctness domain witness conclusion"
        conclusion expectedConclusion

private def requireProductionCorrectnessShape (theoremType : Expr) : MetaM Unit := do
  forallTelescopeReducing theoremType fun fvars conclusion => do
    unless fvars.size == 5 do
      throwError m!"Production compiler correctness theorem must have exactly five binders; found {fvars.size}."
    let decls ← fvars.mapM getFVarLocalDecl
    for decl in decls do
      requireDefaultBinder decl

    let certified := fvars[0]!
    let config := fvars[1]!
    let inputs := fvars[2]!
    requireDefEq "certified-design binder" decls[0]!.type
      (mkConst ``Sparkle.Compiler.CombCorrectness.CertifiedCombDesign)
    requireDefEq "production configuration binder" decls[1]!.type
      (mkConst ``Sparkle.Compiler.CombCorrectness.Config)
    requireDefEq "production input-environment binder" decls[2]!.type
      (mkConst ``Sparkle.Compiler.CombCorrectness.ValueEnv)
    let design ← mkAppM ``Sparkle.Compiler.CombCorrectness.CertifiedCombDesign.design
      #[certified]
    requireDefEq "production valid-configuration premise" decls[3]!.type
      (← mkAppM ``Sparkle.Compiler.CombCorrectness.ValidConfig #[design, config])
    requireDefEq "production valid-input premise" decls[4]!.type
      (← mkAppM ``Sparkle.Compiler.CombCorrectness.ValidInputs #[design, config, inputs])

    let compiled ← mkAppM ``Sparkle.Compiler.CombCorrectness.compileSupportedComb
      #[certified]
    let coreResult ← mkAppM ``Sparkle.Compiler.CombCorrectness.evalCoreModule
      #[compiled, config, inputs]
    let sourceResult ← mkAppM ``Sparkle.Compiler.CombCorrectness.evalSourceDesign
      #[design, config, inputs]
    let expectedConclusion ← mkAppM ``Eq #[coreResult, sourceResult]
    requireDefEq "production compiler correctness conclusion" conclusion expectedConclusion

private def requireProductionTotalityShape (theoremType : Expr) : MetaM Unit := do
  forallTelescopeReducing theoremType fun fvars conclusion => do
    unless fvars.size == 5 do
      throwError m!"Production compiler totality theorem must have exactly five binders; found {fvars.size}."
    let decls ← fvars.mapM getFVarLocalDecl
    for decl in decls do
      requireDefaultBinder decl

    let certified := fvars[0]!
    let config := fvars[1]!
    let inputs := fvars[2]!
    requireDefEq "total production certified-design binder" decls[0]!.type
      (mkConst ``Sparkle.Compiler.CombCorrectness.CertifiedCombDesign)
    requireDefEq "total production configuration binder" decls[1]!.type
      (mkConst ``Sparkle.Compiler.CombCorrectness.Config)
    requireDefEq "total production input-environment binder" decls[2]!.type
      (mkConst ``Sparkle.Compiler.CombCorrectness.ValueEnv)
    let design ← mkAppM ``Sparkle.Compiler.CombCorrectness.CertifiedCombDesign.design
      #[certified]
    requireDefEq "total production valid-configuration premise" decls[3]!.type
      (← mkAppM ``Sparkle.Compiler.CombCorrectness.ValidConfig #[design, config])
    requireDefEq "total production valid-input premise" decls[4]!.type
      (← mkAppM ``Sparkle.Compiler.CombCorrectness.ValidInputs #[design, config, inputs])

    let sourceResult ← mkAppM ``Sparkle.Compiler.CombCorrectness.evalSourceDesign
      #[design, config, inputs]
    let compiled ← mkAppM ``Sparkle.Compiler.CombCorrectness.compileSupportedComb
      #[certified]
    let coreResult ← mkAppM ``Sparkle.Compiler.CombCorrectness.evalCoreModule
      #[compiled, config, inputs]
    withLocalDeclD `result
        (mkConst ``Sparkle.Compiler.CombCorrectness.Evaluation) fun result => do
      let someResult ← mkAppM ``Option.some #[result]
      let sourceDefined ← mkAppM ``Eq #[sourceResult, someResult]
      let coreDefined ← mkAppM ``Eq #[coreResult, someResult]
      let bothDefined ← mkAppM ``And #[sourceDefined, coreDefined]
      let predicate ← mkLambdaFVars #[result] bothDefined
      let expectedConclusion ← mkAppM ``Exists #[predicate]
      requireDefEq "production compiler totality conclusion" conclusion expectedConclusion

private def requireProductionWellFormedShape (theoremType : Expr) : MetaM Unit := do
  forallTelescopeReducing theoremType fun fvars conclusion => do
    unless fvars.size == 1 do
      throwError m!"Production well-formedness theorem must have exactly one binder; found {fvars.size}."
    let certifiedDecl ← getFVarLocalDecl fvars[0]!
    requireDefaultBinder certifiedDecl
    requireDefEq "well-formedness certified-design binder" certifiedDecl.type
      (mkConst ``Sparkle.Compiler.CombCorrectness.CertifiedCombDesign)
    let compiled ← mkAppM ``Sparkle.Compiler.CombCorrectness.compileSupportedComb
      #[fvars[0]!]
    let expectedConclusion ← mkAppM
      ``Sparkle.Compiler.CombCorrectness.CoreCombWellFormed #[compiled]
    requireDefEq "production well-formedness conclusion" conclusion expectedConclusion

private def trustedConstantsJson : Json :=
  .arr <| trustedConstants.map fun constant => Json.mkObj [
    ("name", .str constant.name.toString),
    ("role", .str constant.role)
  ]

/--
Independently validate the fixed phase-one compiler theorem and its non-vacuity
companion against Lean's checked environment.  There are intentionally no
module or theorem arguments: this API cannot upgrade an arbitrary theorem or
ordinary universal marker into compiler-correctness evidence.
-/
def certifyCombCompilerCorrectness (nonce : Option String := none) : MetaM Json := do
  if let some nonce := nonce then
    if nonce.isEmpty then
      throwError "A compiler-correctness certificate nonce must not be empty."

  let env ← getEnv
  -- `ParametricAttribute.getParam?` reads imported per-module entries even
  -- when `Lean.importModules` uses `loadExts := false`.  Check a standard Lean
  -- declaration known to carry this attribute so a toolchain that does not
  -- expose those entries fails closed instead of silently disabling the audit.
  unless (Lean.Compiler.getImplementedBy? env ``implementedByMetadataSentinel).isSome do
    throwError "The certifier cannot read imported @[implemented_by] metadata."
  for constant in trustedConstants do
    requireOwnedByTrustedModule env constant.name

  -- These functions are the executable semantic boundary.  Safe definitions
  -- cannot depend on unsafe code, and an `implemented_by` replacement would
  -- otherwise make executable tests observe code not justified by the kernel.
  for definitionName in
      [``Sparkle.Compiler.CombCorrectness.compileComb, ``Sparkle.Compiler.CombCorrectness.evalSourceDesign, ``Sparkle.Compiler.CombCorrectness.evalCoreModule] do
    requireSafeDefinition env definitionName

  let (correctnessType, correctnessAxioms) ← checkedTheorem correctnessTheoremName
  let (totalityType, totalityAxioms) ← checkedTheorem totalityTheoremName
  let (domainInhabitedType, domainInhabitedAxioms) ←
    checkedTheorem domainInhabitedTheoremName
  let (domainWitnessSupportedType, domainWitnessSupportedAxioms) ←
    checkedTheorem domainWitnessSupportedTheoremName
  let (productionCorrectnessType, productionCorrectnessAxioms) ←
    checkedTheorem productionCorrectnessTheoremName
  let (productionTotalityType, productionTotalityAxioms) ←
    checkedTheorem productionTotalityTheoremName
  let (productionWellFormedType, productionWellFormedAxioms) ←
    checkedTheorem productionWellFormedTheoremName

  requireDefEq "compiler correctness theorem" correctnessType
    (mkConst correctnessStatementName)
  requireDefEq "compiler correctness totality theorem" totalityType
    (mkConst totalityStatementName)
  requireDefEq "compiler correctness domain theorem" domainInhabitedType
    (mkConst domainInhabitedStatementName)
  requireDomainWitnessShape
  requireDomainWitnessSupportedShape domainWitnessSupportedType
  requireCorrectnessShape correctnessType
  requireTotalityShape totalityType
  requireDomainInhabitedShape domainInhabitedType
  requireProductionCorrectnessShape productionCorrectnessType
  requireProductionTotalityShape productionTotalityType
  requireProductionWellFormedShape productionWellFormedType

  let executableTcb ← auditExecutableClosure env
    [productionEntryName,
     ``Sparkle.Compiler.CombCorrectness.compileComb,
     ``Sparkle.Compiler.CombCorrectness.evalSourceDesign,
     ``Sparkle.Compiler.CombCorrectness.evalCoreModule,
     domainWitnessName,
     ``Sparkle.Compiler.CombCorrectness.compilerCorrectnessWitnessConfig,
     ``Sparkle.Compiler.CombCorrectness.compilerCorrectnessWitnessInputs]

  let proposition ← renderExpr correctnessType
  let totalityProposition ← renderExpr totalityType
  let domainInhabitedProposition ← renderExpr domainInhabitedType
  return Json.mkObj [
    ("axioms", .arr (correctnessAxioms.map fun name => .str name.toString)),
    ("comb_ast_to_core_ir_correctness_claimed", .bool true),
    ("compiler_correctness_claimed", .bool true),
    ("conclusion", .str
      "evalCoreModule (compileComb design) config inputs = evalSourceDesign design config inputs"),
    ("domain_inhabited_axioms",
      .arr (domainInhabitedAxioms.map fun name => .str name.toString)),
    ("domain_inhabited_proposition", .str domainInhabitedProposition),
    ("domain_inhabited_statement", .str domainInhabitedStatementName.toString),
    ("domain_inhabited_theorem", .str domainInhabitedTheoremName.toString),
    ("domain_witness", .str domainWitnessName.toString),
    ("domain_witness_shape_checked", .bool true),
    ("domain_witness_supported_axioms",
      .arr (domainWitnessSupportedAxioms.map fun name => .str name.toString)),
    ("domain_witness_supported_theorem",
      .str domainWitnessSupportedTheoremName.toString),
    ("evidence_kind", .str "compiler_correctness_lean_theorem"),
    ("kernel_checked", .bool true),
    ("non_vacuity_checked", .bool true),
    ("non_vacuity_scope", .str "all_positive_natural_widths"),
    ("optimizer_correctness_claimed", .bool false),
    ("production_correctness_axioms",
      .arr (productionCorrectnessAxioms.map fun name => .str name.toString)),
    ("production_correctness_theorem", .str productionCorrectnessTheoremName.toString),
    ("production_entry", .str productionEntryName.toString),
    ("production_totality_axioms",
      .arr (productionTotalityAxioms.map fun name => .str name.toString)),
    ("production_totality_theorem", .str productionTotalityTheoremName.toString),
    ("production_well_formed_axioms",
      .arr (productionWellFormedAxioms.map fun name => .str name.toString)),
    ("production_well_formed_theorem", .str productionWellFormedTheoremName.toString),
    ("proposition", .str proposition),
    ("schema_version", (2 : Json)),
    ("scope", .str certifiedScope),
    ("signal_frontend_correctness_claimed", .bool false),
    ("status", .str "proved"),
    ("theorem", .str correctnessTheoremName.toString),
    ("totality_axioms", .arr (totalityAxioms.map fun name => .str name.toString)),
    ("totality_proposition", .str totalityProposition),
    ("totality_theorem", .str totalityTheoremName.toString),
    ("trusted_constants", trustedConstantsJson),
    ("executable_tcb", .arr (executableTcb.map fun name => .str name.toString)),
    ("verification_nonce", nonce.map Json.str |>.getD .null),
    ("verilog_emitter_correctness_claimed", .bool false)
  ]

end Sparkle.Compiler.CombCertificate
