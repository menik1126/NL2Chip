/-
  Fixed-scope certificate for the original-indexed, proof-carrying ordinary
  `Signal` combinational compiler path.

  The Meta reifier is outside this trust boundary.  It can propose a unary or
  binary certificate only at the exact ordinary declaration used as the
  carrier index.  Lean's kernel must check `SupportedComb`, legality for every
  positive width, and the pointwise bridge before this certificate applies.
-/

import Lean
import Lean.Compiler.ImplementedByAttr
import Sparkle.Compiler.CombCertificate
import Sparkle.Compiler.SignalCombCorrectness

namespace Sparkle.Compiler.SignalCombCertificate

open Lean Meta
open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Compiler.CombCorrectness
open Sparkle.Compiler.SignalCombCorrectness

def certifiedScope : String :=
  "proof_carrying_signal_comb_subset_to_core_ir"

def trustedModule : Name := `Sparkle.Compiler.SignalCombCorrectness
def signalModule : Name := `Sparkle.Core.Signal
def certificateModule : Name := `Sparkle.Compiler.SignalCombCertificate

def correctnessStatementName : Name :=
  ``Sparkle.Compiler.SignalCombCorrectness.SignalCombCompilerCorrectnessStatement

def correctnessTheoremName : Name :=
  ``Sparkle.Compiler.SignalCombCorrectness.signalCombCompiler_correct

def widthStatementName : Name :=
  ``Sparkle.Compiler.SignalCombCorrectness.SignalCombCompilerCorrectnessForWidthStatement

def widthTheoremName : Name :=
  ``Sparkle.Compiler.SignalCombCorrectness.signalCombCompiler_correct_for_width

def allCertificatesNonvacuousStatementName : Name :=
  ``Sparkle.Compiler.SignalCombCorrectness.SignalCombAllCertificatesNonvacuousStatement

def allCertificatesNonvacuousTheoremName : Name :=
  ``Sparkle.Compiler.SignalCombCorrectness.signalCombAllCertificates_nonvacuous

def domainInhabitedStatementName : Name :=
  ``Sparkle.Compiler.SignalCombCorrectness.SignalCombCorrectnessDomainInhabitedStatement

def domainInhabitedTheoremName : Name :=
  ``Sparkle.Compiler.SignalCombCorrectness.signalCombCorrectnessDomain_inhabited

def unaryDomainWitnessName : Name :=
  ``Sparkle.Compiler.SignalCombCorrectness.unarySignalCombWitness

def binaryDomainWitnessName : Name :=
  ``Sparkle.Compiler.SignalCombCorrectness.binarySignalCombWitness

def unaryProductionEntryName : Name :=
  ``Sparkle.Compiler.SignalCombCorrectness.CertifiedUnarySignalCombDesign.compile

def binaryProductionEntryName : Name :=
  ``Sparkle.Compiler.SignalCombCorrectness.CertifiedBinarySignalCombDesign.compile

private structure TrustedConstant where
  role : String
  name : Name
  owner : Name

private def trustedConstants : Array TrustedConstant := #[
  { role := "ordinary_signal_type", name := ``Sparkle.Core.Signal.Signal,
    owner := signalModule },
  { role := "unary_original_type",
    name := ``Sparkle.Compiler.SignalCombCorrectness.UnarySameWidthDecl,
    owner := trustedModule },
  { role := "binary_original_type",
    name := ``Sparkle.Compiler.SignalCombCorrectness.BinarySameWidthDecl,
    owner := trustedModule },
  { role := "certified_unary_original_indexed_carrier",
    name := ``Sparkle.Compiler.SignalCombCorrectness.CertifiedUnarySignalCombDesign,
    owner := trustedModule },
  { role := "certified_binary_original_indexed_carrier",
    name := ``Sparkle.Compiler.SignalCombCorrectness.CertifiedBinarySignalCombDesign,
    owner := trustedModule },
  { role := "same_width_parameter_name",
    name := ``Sparkle.Compiler.SignalCombCorrectness.sameWidthParameterName,
    owner := trustedModule },
  { role := "same_width_dimension",
    name := ``Sparkle.Compiler.SignalCombCorrectness.sameWidthDim,
    owner := trustedModule },
  { role := "same_width_configuration",
    name := ``Sparkle.Compiler.SignalCombCorrectness.sameWidthConfig,
    owner := trustedModule },
  { role := "unary_same_width_design",
    name := ``Sparkle.Compiler.SignalCombCorrectness.unarySameWidthDesign,
    owner := trustedModule },
  { role := "binary_same_width_design",
    name := ``Sparkle.Compiler.SignalCombCorrectness.binarySameWidthDesign,
    owner := trustedModule },
  { role := "packed_signal_sample",
    name := ``Sparkle.Compiler.SignalCombCorrectness.packedSignalSample,
    owner := trustedModule },
  { role := "unary_same_width_input_environment",
    name := ``Sparkle.Compiler.SignalCombCorrectness.unarySameWidthInputEnv,
    owner := trustedModule },
  { role := "binary_same_width_input_environment",
    name := ``Sparkle.Compiler.SignalCombCorrectness.binarySameWidthInputEnv,
    owner := trustedModule },
  { role := "unary_production_entry", name := unaryProductionEntryName,
    owner := trustedModule },
  { role := "binary_production_entry", name := binaryProductionEntryName,
    owner := trustedModule },
  { role := "unary_statement",
    name := ``Sparkle.Compiler.SignalCombCorrectness.CertifiedUnarySignalCombCorrectnessStatement,
    owner := trustedModule },
  { role := "binary_statement",
    name := ``Sparkle.Compiler.SignalCombCorrectness.CertifiedBinarySignalCombCorrectnessStatement,
    owner := trustedModule },
  { role := "correctness_statement", name := correctnessStatementName,
    owner := trustedModule },
  { role := "correctness_theorem", name := correctnessTheoremName,
    owner := trustedModule },
  { role := "unary_width_statement",
    name := ``Sparkle.Compiler.SignalCombCorrectness.CertifiedUnarySignalCombCorrectnessForWidthStatement,
    owner := trustedModule },
  { role := "binary_width_statement",
    name := ``Sparkle.Compiler.SignalCombCorrectness.CertifiedBinarySignalCombCorrectnessForWidthStatement,
    owner := trustedModule },
  { role := "width_statement", name := widthStatementName,
    owner := trustedModule },
  { role := "width_theorem", name := widthTheoremName,
    owner := trustedModule },
  { role := "unary_nonvacuity_statement",
    name := ``Sparkle.Compiler.SignalCombCorrectness.CertifiedUnarySignalCombNonvacuityStatement,
    owner := trustedModule },
  { role := "binary_nonvacuity_statement",
    name := ``Sparkle.Compiler.SignalCombCorrectness.CertifiedBinarySignalCombNonvacuityStatement,
    owner := trustedModule },
  { role := "all_certificates_nonvacuous_statement",
    name := allCertificatesNonvacuousStatementName, owner := trustedModule },
  { role := "all_certificates_nonvacuous_theorem",
    name := allCertificatesNonvacuousTheoremName, owner := trustedModule },
  { role := "domain_inhabited_statement", name := domainInhabitedStatementName,
    owner := trustedModule },
  { role := "domain_inhabited_theorem", name := domainInhabitedTheoremName,
    owner := trustedModule },
  { role := "unary_domain_witness_original",
    name := ``Sparkle.Compiler.SignalCombCorrectness.unarySignalCombWitnessOriginal,
    owner := trustedModule },
  { role := "binary_domain_witness_original",
    name := ``Sparkle.Compiler.SignalCombCorrectness.binarySignalCombWitnessOriginal,
    owner := trustedModule },
  { role := "unary_domain_witness", name := unaryDomainWitnessName,
    owner := trustedModule },
  { role := "binary_domain_witness", name := binaryDomainWitnessName,
    owner := trustedModule },
  { role := "unary_domain_witness_bridge",
    name := ``Sparkle.Compiler.SignalCombCorrectness.unarySignalCombWitness_bridge,
    owner := trustedModule },
  { role := "binary_domain_witness_bridge",
    name := ``Sparkle.Compiler.SignalCombCorrectness.binarySignalCombWitness_bridge,
    owner := trustedModule },
  { role := "unary_production_well_formed",
    name := ``Sparkle.Compiler.SignalCombCorrectness.CertifiedUnarySignalCombDesign.compile_wellFormed,
    owner := trustedModule },
  { role := "binary_production_well_formed",
    name := ``Sparkle.Compiler.SignalCombCorrectness.CertifiedBinarySignalCombDesign.compile_wellFormed,
    owner := trustedModule }
]

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

private def declaringModule? (env : Environment) (declName : Name) : Option Name :=
  env.getModuleIdxFor? declName |>.map fun moduleIdx =>
    env.header.modules[moduleIdx]!.module

private def requireOwnedBy
    (env : Environment) (declName expectedOwner : Name) : MetaM Unit := do
  let actualOwner ← match declaringModule? env declName with
    | some owner => pure owner
    | none =>
        throwError m!"Trusted declaration '{declName}' is missing module ownership metadata."
  unless actualOwner == expectedOwner do
    throwError m!"Trusted declaration '{declName}' is owned by '{actualOwner}', not '{expectedOwner}'."

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

private def checkedAxiomClosure
    (declName : Name) (value : Expr) : MetaM (Array Name) := do
  if value.hasSorry then
    throwError m!"Trusted declaration '{declName}' directly contains 'sorry'."
  let axioms ← Lean.collectAxioms declName
  if axioms.contains ``sorryAx then
    throwError m!"Trusted declaration '{declName}' transitively depends on 'sorry'."
  let disallowed := axioms.filter fun name => !allowedAxioms.contains name
  unless disallowed.isEmpty do
    let rendered := String.intercalate ", " <|
      (disallowed.qsort Name.lt).toList.map Name.toString
    throwError m!"Trusted declaration '{declName}' depends on non-allowlisted axiom(s): {rendered}."
  return axioms.qsort Name.lt

private def checkedTheorem (declName : Name) : MetaM (Expr × Array Name) := do
  let env ← getEnv
  let info ← match env.checked.get.find? declName with
    | some (.thmInfo info) => pure info
    | some _ =>
        throwError m!"Trusted declaration '{declName}' is not a Lean theorem or lemma."
    | none =>
        throwError m!"Trusted theorem '{declName}' is absent from Lean's kernel-checked environment."
  return (info.type, ← checkedAxiomClosure declName info.value)

/-- Audit a witness definition itself, including all proof fields, rather than
    relying on proof-projection definitional equality. -/
private def checkedWitnessDefinition
    (declName : Name) : MetaM (Expr × Array Name) := do
  let env ← getEnv
  let info ← match env.checked.get.find? declName with
    | some (.defnInfo info) => pure info
    | some _ =>
        throwError m!"Domain witness '{declName}' is not a transparent definition."
    | none =>
        throwError m!"Domain witness '{declName}' is absent from Lean's kernel-checked environment."
  requireSafeDefinition env declName
  return (info.type, ← checkedAxiomClosure declName info.value)

private def isExecutableTrustedModule (env : Environment) (declName : Name) : Bool :=
  match declaringModule? env declName with
  | some owner => owner == trustedModule || owner == signalModule
  | none => false

private partial def auditExecutableClosure
    (env : Environment) (pending : List Name)
    (visited : Array Name := #[]) (audited : Array Name := #[]) :
    MetaM (Array Name) := do
  match pending with
  | [] => return audited.qsort Name.lt
  | declName :: rest =>
      if visited.contains declName then
        auditExecutableClosure env rest visited audited
      else
        let visited := visited.push declName
        if !isExecutableTrustedModule env declName then
          auditExecutableClosure env rest visited audited
        else
          match env.checked.get.find? declName with
          | some (.defnInfo info) =>
              requireSafeDefinition env declName
              auditExecutableClosure env
                (info.value.getUsedConstants.toList ++ rest)
                visited (audited.push declName)
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

/-! Checker-owned exact copies of every stable statement leaf. -/

private def expectedUnaryCorrectness
    (original : UnarySameWidthDecl)
    (certified : CertifiedUnarySignalCombDesign original) : Prop :=
  ∀ (dom : DomainConfig) (config : Sparkle.Compiler.CombCorrectness.Config)
      (input : Signal dom (BitVec (config sameWidthParameterName)))
      (time : Nat),
    ValidConfig certified.design config →
    ∃ result,
      evalSourceDesign certified.design config
          (unarySameWidthInputEnv config input time) = some result ∧
      evalCoreModule certified.compile config
          (unarySameWidthInputEnv config input time) = some result ∧
      result.outputs =
        [("y", config sameWidthParameterName,
          some { width := config sameWidthParameterName
                 bits := (@original dom (config sameWidthParameterName) input).val time })]

private def expectedBinaryCorrectness
    (original : BinarySameWidthDecl)
    (certified : CertifiedBinarySignalCombDesign original) : Prop :=
  ∀ (dom : DomainConfig) (config : Sparkle.Compiler.CombCorrectness.Config)
      (lhs rhsSignal : Signal dom (BitVec (config sameWidthParameterName)))
      (time : Nat),
    ValidConfig certified.design config →
    ∃ result,
      evalSourceDesign certified.design config
          (binarySameWidthInputEnv config lhs rhsSignal time) = some result ∧
      evalCoreModule certified.compile config
          (binarySameWidthInputEnv config lhs rhsSignal time) = some result ∧
      result.outputs =
        [("y", config sameWidthParameterName,
          some { width := config sameWidthParameterName
                 bits := (@original dom (config sameWidthParameterName)
                   lhs rhsSignal).val time })]

private def expectedCorrectnessStatement : Prop :=
  (∀ (original : UnarySameWidthDecl)
      (certified : CertifiedUnarySignalCombDesign original),
    expectedUnaryCorrectness original certified) ∧
  (∀ (original : BinarySameWidthDecl)
      (certified : CertifiedBinarySignalCombDesign original),
    expectedBinaryCorrectness original certified)

private def expectedUnaryWidthCorrectness
    (original : UnarySameWidthDecl)
    (certified : CertifiedUnarySignalCombDesign original) : Prop :=
  ∀ (dom : DomainConfig) (width : Nat), 0 < width →
    ∀ (input : Signal dom (BitVec width)) (time : Nat),
    ∃ result,
      evalSourceDesign certified.design (sameWidthConfig width)
          (unarySameWidthInputEnv (sameWidthConfig width) input time) =
        some result ∧
      evalCoreModule certified.compile (sameWidthConfig width)
          (unarySameWidthInputEnv (sameWidthConfig width) input time) =
        some result ∧
      result.outputs =
        [("y", width, some { width := width, bits := (@original dom width input).val time })]

private def expectedBinaryWidthCorrectness
    (original : BinarySameWidthDecl)
    (certified : CertifiedBinarySignalCombDesign original) : Prop :=
  ∀ (dom : DomainConfig) (width : Nat), 0 < width →
    ∀ (lhs rhsSignal : Signal dom (BitVec width)) (time : Nat),
    ∃ result,
      evalSourceDesign certified.design (sameWidthConfig width)
          (binarySameWidthInputEnv (sameWidthConfig width) lhs rhsSignal time) =
        some result ∧
      evalCoreModule certified.compile (sameWidthConfig width)
          (binarySameWidthInputEnv (sameWidthConfig width) lhs rhsSignal time) =
        some result ∧
      result.outputs =
        [("y", width, some { width := width, bits := (@original dom width lhs rhsSignal).val time })]

private def expectedWidthStatement : Prop :=
  (∀ (original : UnarySameWidthDecl)
      (certified : CertifiedUnarySignalCombDesign original),
    expectedUnaryWidthCorrectness original certified) ∧
  (∀ (original : BinarySameWidthDecl)
      (certified : CertifiedBinarySignalCombDesign original),
    expectedBinaryWidthCorrectness original certified)

private def expectedUnaryNonvacuity
    (original : UnarySameWidthDecl)
    (certified : CertifiedUnarySignalCombDesign original) : Prop :=
  ∀ (dom : DomainConfig) (width : Nat), 0 < width →
    ∃ input : Signal dom (BitVec width), ∀ time : Nat, ∃ result,
      evalSourceDesign certified.design (sameWidthConfig width)
          (unarySameWidthInputEnv (sameWidthConfig width) input time) =
        some result ∧
      evalCoreModule certified.compile (sameWidthConfig width)
          (unarySameWidthInputEnv (sameWidthConfig width) input time) =
        some result ∧
      result.outputs =
        [("y", width, some { width := width, bits := (@original dom width input).val time })]

private def expectedBinaryNonvacuity
    (original : BinarySameWidthDecl)
    (certified : CertifiedBinarySignalCombDesign original) : Prop :=
  ∀ (dom : DomainConfig) (width : Nat), 0 < width →
    ∃ lhs rhsSignal : Signal dom (BitVec width), ∀ time : Nat, ∃ result,
      evalSourceDesign certified.design (sameWidthConfig width)
          (binarySameWidthInputEnv (sameWidthConfig width) lhs rhsSignal time) =
        some result ∧
      evalCoreModule certified.compile (sameWidthConfig width)
          (binarySameWidthInputEnv (sameWidthConfig width) lhs rhsSignal time) =
        some result ∧
      result.outputs =
        [("y", width, some { width := width, bits := (@original dom width lhs rhsSignal).val time })]

private def expectedAllCertificatesNonvacuousStatement : Prop :=
  (∀ (original : UnarySameWidthDecl)
      (certified : CertifiedUnarySignalCombDesign original),
    expectedUnaryNonvacuity original certified) ∧
  (∀ (original : BinarySameWidthDecl)
      (certified : CertifiedBinarySignalCombDesign original),
    expectedBinaryNonvacuity original certified)

private def expectedUnaryWitnessOriginal : UnarySameWidthDecl :=
  fun input => input

private def expectedBinaryWitnessOriginal : BinarySameWidthDecl :=
  fun lhs _rhs => lhs

private def expectedUnaryWitnessDesign : CombDesign :=
  unarySameWidthDesign "signal_comb_unary_identity" (.ref "x")

private def expectedBinaryWitnessDesign : CombDesign :=
  binarySameWidthDesign "signal_comb_binary_left" (.ref "a")

private def expectedDomainInhabitedStatement : Prop :=
  expectedUnaryNonvacuity unarySignalCombWitnessOriginal
      unarySignalCombWitness ∧
    expectedBinaryNonvacuity binarySignalCombWitnessOriginal
      binarySignalCombWitness

private def expectedUnaryWitnessBridgeStatement : Prop :=
  ∀ (dom : DomainConfig) (config : Sparkle.Compiler.CombCorrectness.Config)
      (input : Signal dom (BitVec (config sameWidthParameterName)))
      (time : Nat),
    ValidConfig expectedUnaryWitnessDesign config →
    ∃ result,
      evalSourceDesign expectedUnaryWitnessDesign config
          (unarySameWidthInputEnv config input time) = some result ∧
      result.outputs =
        [("y", config sameWidthParameterName,
          some { width := config sameWidthParameterName
                 bits := (@unarySignalCombWitnessOriginal dom
                   (config sameWidthParameterName) input).val time })]

private def expectedBinaryWitnessBridgeStatement : Prop :=
  ∀ (dom : DomainConfig) (config : Sparkle.Compiler.CombCorrectness.Config)
      (lhs rhsSignal : Signal dom (BitVec (config sameWidthParameterName)))
      (time : Nat),
    ValidConfig expectedBinaryWitnessDesign config →
    ∃ result,
      evalSourceDesign expectedBinaryWitnessDesign config
          (binarySameWidthInputEnv config lhs rhsSignal time) = some result ∧
      result.outputs =
        [("y", config sameWidthParameterName,
          some { width := config sameWidthParameterName
                 bits := (@binarySignalCombWitnessOriginal dom
                   (config sameWidthParameterName) lhs rhsSignal).val time })]

private def expectedUnaryWellFormedStatement : Prop :=
  ∀ {original : UnarySameWidthDecl}
      (certified : CertifiedUnarySignalCombDesign original),
    CoreCombWellFormed certified.compile

private def expectedBinaryWellFormedStatement : Prop :=
  ∀ {original : BinarySameWidthDecl}
      (certified : CertifiedBinarySignalCombDesign original),
    CoreCombWellFormed certified.compile

private def requireStableStatementShapes : MetaM Unit := do
  requireDefEq "unary indexed correctness statement"
    (mkConst ``Sparkle.Compiler.SignalCombCorrectness.CertifiedUnarySignalCombCorrectnessStatement)
    (mkConst ``expectedUnaryCorrectness)
  requireDefEq "binary indexed correctness statement"
    (mkConst ``Sparkle.Compiler.SignalCombCorrectness.CertifiedBinarySignalCombCorrectnessStatement)
    (mkConst ``expectedBinaryCorrectness)
  requireDefEq "aggregate indexed correctness statement"
    (mkConst correctnessStatementName) (mkConst ``expectedCorrectnessStatement)
  requireDefEq "unary arbitrary-width statement"
    (mkConst ``Sparkle.Compiler.SignalCombCorrectness.CertifiedUnarySignalCombCorrectnessForWidthStatement)
    (mkConst ``expectedUnaryWidthCorrectness)
  requireDefEq "binary arbitrary-width statement"
    (mkConst ``Sparkle.Compiler.SignalCombCorrectness.CertifiedBinarySignalCombCorrectnessForWidthStatement)
    (mkConst ``expectedBinaryWidthCorrectness)
  requireDefEq "aggregate arbitrary-width statement"
    (mkConst widthStatementName) (mkConst ``expectedWidthStatement)
  requireDefEq "unary all-certificate non-vacuity statement"
    (mkConst ``Sparkle.Compiler.SignalCombCorrectness.CertifiedUnarySignalCombNonvacuityStatement)
    (mkConst ``expectedUnaryNonvacuity)
  requireDefEq "binary all-certificate non-vacuity statement"
    (mkConst ``Sparkle.Compiler.SignalCombCorrectness.CertifiedBinarySignalCombNonvacuityStatement)
    (mkConst ``expectedBinaryNonvacuity)
  requireDefEq "aggregate all-certificate non-vacuity statement"
    (mkConst allCertificatesNonvacuousStatementName)
    (mkConst ``expectedAllCertificatesNonvacuousStatement)
  requireDefEq "fixed indexed witness domain statement"
    (mkConst domainInhabitedStatementName)
    (mkConst ``expectedDomainInhabitedStatement)

private def requireWitnessShapes
    (unaryWitnessType binaryWitnessType : Expr) : MetaM Unit := do
  requireDefEq "unary witness original declaration"
    (mkConst ``Sparkle.Compiler.SignalCombCorrectness.unarySignalCombWitnessOriginal)
    (mkConst ``expectedUnaryWitnessOriginal)
  requireDefEq "binary witness original declaration"
    (mkConst ``Sparkle.Compiler.SignalCombCorrectness.binarySignalCombWitnessOriginal)
    (mkConst ``expectedBinaryWitnessOriginal)
  let expectedUnaryType ← mkAppM
    ``Sparkle.Compiler.SignalCombCorrectness.CertifiedUnarySignalCombDesign
    #[mkConst ``Sparkle.Compiler.SignalCombCorrectness.unarySignalCombWitnessOriginal]
  let expectedBinaryType ← mkAppM
    ``Sparkle.Compiler.SignalCombCorrectness.CertifiedBinarySignalCombDesign
    #[mkConst ``Sparkle.Compiler.SignalCombCorrectness.binarySignalCombWitnessOriginal]
  requireDefEq "unary indexed witness type" unaryWitnessType expectedUnaryType
  requireDefEq "binary indexed witness type" binaryWitnessType expectedBinaryType
  let unaryDesign ← mkAppM
    ``Sparkle.Compiler.SignalCombCorrectness.CertifiedUnarySignalCombDesign.design
    #[mkConst unaryDomainWitnessName]
  let binaryDesign ← mkAppM
    ``Sparkle.Compiler.SignalCombCorrectness.CertifiedBinarySignalCombDesign.design
    #[mkConst binaryDomainWitnessName]
  requireDefEq "unary indexed witness design" unaryDesign
    (mkConst ``expectedUnaryWitnessDesign)
  requireDefEq "binary indexed witness design" binaryDesign
    (mkConst ``expectedBinaryWitnessDesign)

private def requireBaseCertificate (base : Json) : MetaM Unit := do
  let status ← match base.getObjValAs? String "status" with
    | .ok value => pure value
    | .error message => throwError m!"Base CombCertificate omitted status: {message}"
  unless status == "proved" do
    throwError "Base CombCertificate did not prove its fixed compiler scope."
  let scope ← match base.getObjValAs? String "scope" with
    | .ok value => pure value
    | .error message => throwError m!"Base CombCertificate omitted scope: {message}"
  unless scope == Sparkle.Compiler.CombCertificate.certifiedScope do
    throwError m!"Base CombCertificate changed scope to '{scope}'."
  let signalClaim ←
    match base.getObjValAs? Bool "signal_frontend_correctness_claimed" with
    | .ok value => pure value
    | .error message =>
        throwError m!"Base CombCertificate omitted its Signal exclusion: {message}"
  if signalClaim then
    throwError "Base CombCertificate improperly widened itself to the Signal frontend."

private def mergeAxioms (sets : Array (Array Name)) : Array Name :=
  (sets.foldl (fun accumulated names =>
    names.foldl (fun accumulated name =>
      if accumulated.contains name then accumulated else accumulated.push name)
      accumulated) #[]).qsort Name.lt

private def trustedConstantsJson : Json :=
  .arr <| trustedConstants.map fun constant => Json.mkObj [
    ("name", .str constant.name.toString),
    ("owner", .str constant.owner.toString),
    ("role", .str constant.role)
  ]

def certifySignalCombCompilerCorrectness
    (nonce : Option String := none) : MetaM Json := do
  if let some nonce := nonce then
    if nonce.isEmpty then
      throwError "A Signal-combination certificate nonce must not be empty."

  let baseCertificate ←
    Sparkle.Compiler.CombCertificate.certifyCombCompilerCorrectness none
  requireBaseCertificate baseCertificate

  let env ← getEnv
  for constant in trustedConstants do
    requireOwnedBy env constant.name constant.owner

  for definitionName in
      [unaryProductionEntryName, binaryProductionEntryName,
       ``Sparkle.Compiler.SignalCombCorrectness.unarySameWidthDesign,
       ``Sparkle.Compiler.SignalCombCorrectness.binarySameWidthDesign,
       ``Sparkle.Compiler.SignalCombCorrectness.unarySameWidthInputEnv,
       ``Sparkle.Compiler.SignalCombCorrectness.binarySameWidthInputEnv,
       ``Sparkle.Compiler.SignalCombCorrectness.packedSignalSample,
       ``Sparkle.Compiler.SignalCombCorrectness.sameWidthConfig,
       ``Sparkle.Compiler.SignalCombCorrectness.unarySignalCombWitnessOriginal,
       ``Sparkle.Compiler.SignalCombCorrectness.binarySignalCombWitnessOriginal] do
    requireSafeDefinition env definitionName

  let (correctnessType, correctnessAxioms) ←
    checkedTheorem correctnessTheoremName
  let (widthType, widthAxioms) ← checkedTheorem widthTheoremName
  let (allNonvacuousType, allNonvacuousAxioms) ←
    checkedTheorem allCertificatesNonvacuousTheoremName
  let (domainType, domainAxioms) ←
    checkedTheorem domainInhabitedTheoremName
  let (unaryWellFormedType, unaryWellFormedAxioms) ←
    checkedTheorem
      ``Sparkle.Compiler.SignalCombCorrectness.CertifiedUnarySignalCombDesign.compile_wellFormed
  let (binaryWellFormedType, binaryWellFormedAxioms) ←
    checkedTheorem
      ``Sparkle.Compiler.SignalCombCorrectness.CertifiedBinarySignalCombDesign.compile_wellFormed
  let (unaryBridgeType, unaryBridgeAxioms) ← checkedTheorem
    ``Sparkle.Compiler.SignalCombCorrectness.unarySignalCombWitness_bridge
  let (binaryBridgeType, binaryBridgeAxioms) ← checkedTheorem
    ``Sparkle.Compiler.SignalCombCorrectness.binarySignalCombWitness_bridge
  let (unaryWitnessType, unaryWitnessAxioms) ←
    checkedWitnessDefinition unaryDomainWitnessName
  let (binaryWitnessType, binaryWitnessAxioms) ←
    checkedWitnessDefinition binaryDomainWitnessName

  requireStableStatementShapes
  requireDefEq "indexed correctness theorem" correctnessType
    (mkConst ``expectedCorrectnessStatement)
  requireDefEq "indexed arbitrary-width theorem" widthType
    (mkConst ``expectedWidthStatement)
  requireDefEq "all indexed certificates non-vacuity theorem"
    allNonvacuousType (mkConst ``expectedAllCertificatesNonvacuousStatement)
  requireDefEq "fixed indexed witness domain theorem" domainType
    (mkConst ``expectedDomainInhabitedStatement)
  requireDefEq "unary fixed witness bridge" unaryBridgeType
    (mkConst ``expectedUnaryWitnessBridgeStatement)
  requireDefEq "binary fixed witness bridge" binaryBridgeType
    (mkConst ``expectedBinaryWitnessBridgeStatement)
  requireDefEq "unary production well-formed theorem" unaryWellFormedType
    (mkConst ``expectedUnaryWellFormedStatement)
  requireDefEq "binary production well-formed theorem" binaryWellFormedType
    (mkConst ``expectedBinaryWellFormedStatement)
  requireWitnessShapes unaryWitnessType binaryWitnessType

  let executableTcb ← auditExecutableClosure env
    [unaryProductionEntryName, binaryProductionEntryName,
     ``Sparkle.Compiler.SignalCombCorrectness.unarySameWidthDesign,
     ``Sparkle.Compiler.SignalCombCorrectness.binarySameWidthDesign,
     ``Sparkle.Compiler.SignalCombCorrectness.unarySameWidthInputEnv,
     ``Sparkle.Compiler.SignalCombCorrectness.binarySameWidthInputEnv,
     ``Sparkle.Compiler.SignalCombCorrectness.packedSignalSample,
     ``Sparkle.Compiler.SignalCombCorrectness.sameWidthConfig,
     ``Sparkle.Compiler.SignalCombCorrectness.unarySignalCombWitnessOriginal,
     ``Sparkle.Compiler.SignalCombCorrectness.binarySignalCombWitnessOriginal,
     unaryDomainWitnessName, binaryDomainWitnessName]

  let domainWitnessAxioms := mergeAxioms
    #[unaryWitnessAxioms, binaryWitnessAxioms]
  let proposition ← renderExpr correctnessType
  let widthProposition ← renderExpr widthType
  let allNonvacuousProposition ← renderExpr allNonvacuousType
  let domainProposition ← renderExpr domainType
  return Json.mkObj [
    ("all_certificates_nonvacuity_axioms",
      .arr (allNonvacuousAxioms.map fun name => .str name.toString)),
    ("all_certificates_nonvacuity_checked", .bool true),
    ("all_certificates_nonvacuity_proposition", .str allNonvacuousProposition),
    ("all_certificates_nonvacuity_statement",
      .str allCertificatesNonvacuousStatementName.toString),
    ("all_certificates_nonvacuity_theorem",
      .str allCertificatesNonvacuousTheoremName.toString),
    ("arbitrary_positive_width_checked", .bool true),
    ("axioms", .arr (correctnessAxioms.map fun name => .str name.toString)),
    ("base_comb_certificate", baseCertificate),
    ("binary_domain_witness", .str binaryDomainWitnessName.toString),
    ("binary_domain_witness_axioms",
      .arr (binaryWitnessAxioms.map fun name => .str name.toString)),
    ("binary_domain_witness_bridge_axioms",
      .arr (binaryBridgeAxioms.map fun name => .str name.toString)),
    ("binary_original_index_checked", .bool true),
    ("comb_ast_to_core_ir_certificate_reused", .bool true),
    ("conclusion", .str
      "unary and binary compiled Core observations equal their exact original-indexed ordinary Signal declarations"),
    ("domain_inhabited_axioms",
      .arr (domainAxioms.map fun name => .str name.toString)),
    ("domain_inhabited_proposition", .str domainProposition),
    ("domain_inhabited_statement", .str domainInhabitedStatementName.toString),
    ("domain_inhabited_theorem", .str domainInhabitedTheoremName.toString),
    ("domain_witness_axioms",
      .arr (domainWitnessAxioms.map fun name => .str name.toString)),
    ("domain_witness_definitions_checked", .bool true),
    ("domain_witness_shape_checked", .bool true),
    ("evidence_kind", .str
      "original_indexed_proof_carrying_signal_comb_lean_theorem"),
    ("executable_tcb", .arr (executableTcb.map fun name => .str name.toString)),
    ("extractor_output_requires_kernel_checked_bridge", .bool true),
    ("general_signal_frontend_correctness_claimed", .bool false),
    ("hierarchy_correctness_claimed", .bool false),
    ("kernel_bridge_checked", .bool true),
    ("kernel_checked", .bool true),
    ("memory_correctness_claimed", .bool false),
    ("meta_reifier_correctness_claimed", .bool false),
    ("meta_reifier_trusted", .bool false),
    ("non_vacuity_checked", .bool true),
    ("non_vacuity_scope", .str
      "all_certificates_all_positive_natural_widths_all_times"),
    ("optimizer_correctness_claimed", .bool false),
    ("ordinary_signal_proof_carrying_comb_subset_correctness_claimed", .bool true),
    ("original_indexed_carriers_checked", .bool true),
    ("production_entries", .arr #[.str unaryProductionEntryName.toString,
      .str binaryProductionEntryName.toString]),
    ("production_well_formed_axioms", .arr
      ((mergeAxioms #[unaryWellFormedAxioms, binaryWellFormedAxioms]).map
        fun name => .str name.toString)),
    ("proof_carrying_signal_comb_subset_to_core_ir_correctness_claimed", .bool true),
    ("proof_closure_checked", .bool true),
    ("proposition", .str proposition),
    ("schema_version", (2 : Json)),
    ("scope", .str certifiedScope),
    ("sequential_signal_correctness_claimed", .bool false),
    ("signal_frontend_correctness_claimed", .bool false),
    ("status", .str "proved"),
    ("theorem", .str correctnessTheoremName.toString),
    ("trusted_constants", trustedConstantsJson),
    ("unary_domain_witness", .str unaryDomainWitnessName.toString),
    ("unary_domain_witness_axioms",
      .arr (unaryWitnessAxioms.map fun name => .str name.toString)),
    ("unary_domain_witness_bridge_axioms",
      .arr (unaryBridgeAxioms.map fun name => .str name.toString)),
    ("unary_original_index_checked", .bool true),
    ("verification_nonce", nonce.map Json.str |>.getD .null),
    ("verilog_emitter_correctness_claimed", .bool false),
    ("width_axioms", .arr (widthAxioms.map fun name => .str name.toString)),
    ("width_proposition", .str widthProposition),
    ("width_statement", .str widthStatementName.toString),
    ("width_theorem", .str widthTheoremName.toString)
  ]

end Sparkle.Compiler.SignalCombCertificate
