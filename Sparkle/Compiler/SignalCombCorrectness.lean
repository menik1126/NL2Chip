/-
  Proof-carrying reification for exact same-width ordinary Signal declarations.

  The public certificate carriers are indexed by the original shallow Signal
  declaration.  There is no replaceable `source` field: every bridge proof and
  every public correctness conclusion refers directly to that index.
-/

import Sparkle.Compiler.CombCorrectness
import Sparkle.Core.Signal

namespace Sparkle.Compiler.SignalCombCorrectness

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Compiler.CombCorrectness

/-! ## Exact ordinary-declaration carriers -/

abbrev UnarySameWidthDecl :=
  ∀ {dom : DomainConfig} {W : Nat},
    Signal dom (BitVec W) → Signal dom (BitVec W)

abbrev BinarySameWidthDecl :=
  ∀ {dom : DomainConfig} {W : Nat},
    Signal dom (BitVec W) → Signal dom (BitVec W) → Signal dom (BitVec W)

def sameWidthParameterName : String := "W"

def sameWidthDim : Sparkle.IR.Type.DimExpr :=
  .param sameWidthParameterName

/-- The canonical complete configuration used by explicit arbitrary-width
    corollaries.  The canonical designs declare no parameter except `W`. -/
def sameWidthConfig (width : Nat) : Config :=
  fun name => if name == sameWidthParameterName then width else 0

@[simp] theorem sameWidthConfig_parameter (width : Nat) :
    sameWidthConfig width sameWidthParameterName = width := by
  simp [sameWidthConfig, sameWidthParameterName]

def unarySameWidthDesign (moduleName : String) (rhs : CombExpr) : CombDesign :=
  { name := moduleName
    parameters := [{ name := sameWidthParameterName, defaultValue := 1 }]
    inputs := [{ name := "x", width := sameWidthDim }]
    outputs := [{ name := "y", width := sameWidthDim, rhs := rhs }] }

def binarySameWidthDesign (moduleName : String) (rhs : CombExpr) : CombDesign :=
  { name := moduleName
    parameters := [{ name := sameWidthParameterName, defaultValue := 1 }]
    inputs :=
      [{ name := "a", width := sameWidthDim },
       { name := "b", width := sameWidthDim }]
    outputs := [{ name := "y", width := sameWidthDim, rhs := rhs }] }

def packedSignalSample {dom : DomainConfig} {width : Nat}
    (signal : Signal dom (BitVec width)) (time : Nat) : PackedValue :=
  { width, bits := signal.val time }

def unarySameWidthInputEnv {dom : DomainConfig} (config : Config)
    (input : Signal dom (BitVec (config sameWidthParameterName)))
    (time : Nat) : ValueEnv :=
  [("x", packedSignalSample input time)]

def binarySameWidthInputEnv {dom : DomainConfig} (config : Config)
    (lhs rhs : Signal dom (BitVec (config sameWidthParameterName)))
    (time : Nat) : ValueEnv :=
  [("a", packedSignalSample lhs time),
   ("b", packedSignalSample rhs time)]

theorem unarySameWidthInputEnv_valid
    (moduleName : String) (rhs : CombExpr) (dom : DomainConfig)
    (config : Config)
    (input : Signal dom (BitVec (config sameWidthParameterName)))
    (time : Nat) :
    ValidInputs (unarySameWidthDesign moduleName rhs) config
      (unarySameWidthInputEnv config input time) := by
  simp [ValidInputs, unarySameWidthDesign, unarySameWidthInputEnv,
    packedSignalSample, sameWidthDim, evalDim, List.lookup]

theorem binarySameWidthInputEnv_valid
    (moduleName : String) (rhs : CombExpr) (dom : DomainConfig)
    (config : Config)
    (lhs rhsSignal : Signal dom (BitVec (config sameWidthParameterName)))
    (time : Nat) :
    ValidInputs (binarySameWidthDesign moduleName rhs) config
      (binarySameWidthInputEnv config lhs rhsSignal time) := by
  simp [ValidInputs, binarySameWidthDesign, binarySameWidthInputEnv,
    packedSignalSample, sameWidthDim, evalDim, List.lookup]

/-! ## Original-indexed proof-carrying certificates -/

structure CertifiedUnarySignalCombDesign (original : UnarySameWidthDecl) where
  moduleName : String
  rhs : CombExpr
  supported : SupportedComb (unarySameWidthDesign moduleName rhs)
  widthLegal : ∀ width : Nat, 0 < width →
    ValidConfig (unarySameWidthDesign moduleName rhs) (sameWidthConfig width)
  bridge : ∀ (dom : DomainConfig) (config : Config)
      (input : Signal dom (BitVec (config sameWidthParameterName)))
      (time : Nat),
    ValidConfig (unarySameWidthDesign moduleName rhs) config →
    ∃ result,
      evalSourceDesign (unarySameWidthDesign moduleName rhs) config
          (unarySameWidthInputEnv config input time) = some result ∧
      result.outputs =
        [("y", config sameWidthParameterName,
          some {
            width := config sameWidthParameterName
            bits := (@original dom (config sameWidthParameterName) input).val time
          })]

structure CertifiedBinarySignalCombDesign (original : BinarySameWidthDecl) where
  moduleName : String
  rhs : CombExpr
  supported : SupportedComb (binarySameWidthDesign moduleName rhs)
  widthLegal : ∀ width : Nat, 0 < width →
    ValidConfig (binarySameWidthDesign moduleName rhs) (sameWidthConfig width)
  bridge : ∀ (dom : DomainConfig) (config : Config)
      (lhs rhsSignal : Signal dom (BitVec (config sameWidthParameterName)))
      (time : Nat),
    ValidConfig (binarySameWidthDesign moduleName rhs) config →
    ∃ result,
      evalSourceDesign (binarySameWidthDesign moduleName rhs) config
          (binarySameWidthInputEnv config lhs rhsSignal time) = some result ∧
      result.outputs =
        [("y", config sameWidthParameterName,
          some {
            width := config sameWidthParameterName
            bits :=
              (@original dom (config sameWidthParameterName) lhs rhsSignal).val time
          })]

namespace CertifiedUnarySignalCombDesign

def design {original : UnarySameWidthDecl}
    (certified : CertifiedUnarySignalCombDesign original) : CombDesign :=
  unarySameWidthDesign certified.moduleName certified.rhs

def comb {original : UnarySameWidthDecl}
    (certified : CertifiedUnarySignalCombDesign original) : CertifiedCombDesign :=
  { design := certified.design, supported := certified.supported }

def compile {original : UnarySameWidthDecl}
    (certified : CertifiedUnarySignalCombDesign original) :=
  compileSupportedComb certified.comb

theorem compile_wellFormed {original : UnarySameWidthDecl}
    (certified : CertifiedUnarySignalCombDesign original) :
    CoreCombWellFormed certified.compile :=
  compileSupportedComb_wellFormed certified.comb

end CertifiedUnarySignalCombDesign

namespace CertifiedBinarySignalCombDesign

def design {original : BinarySameWidthDecl}
    (certified : CertifiedBinarySignalCombDesign original) : CombDesign :=
  binarySameWidthDesign certified.moduleName certified.rhs

def comb {original : BinarySameWidthDecl}
    (certified : CertifiedBinarySignalCombDesign original) : CertifiedCombDesign :=
  { design := certified.design, supported := certified.supported }

def compile {original : BinarySameWidthDecl}
    (certified : CertifiedBinarySignalCombDesign original) :=
  compileSupportedComb certified.comb

theorem compile_wellFormed {original : BinarySameWidthDecl}
    (certified : CertifiedBinarySignalCombDesign original) :
    CoreCombWellFormed certified.compile :=
  compileSupportedComb_wellFormed certified.comb

end CertifiedBinarySignalCombDesign

/-! ## End-to-end theorems -/

theorem compileCertifiedUnarySignalComb_correct
    {original : UnarySameWidthDecl}
    (certified : CertifiedUnarySignalCombDesign original)
    (dom : DomainConfig) (config : Config)
    (input : Signal dom (BitVec (config sameWidthParameterName)))
    (time : Nat) (valid : ValidConfig certified.design config) :
    ∃ result,
      evalSourceDesign certified.design config
          (unarySameWidthInputEnv config input time) = some result ∧
      evalCoreModule certified.compile config
          (unarySameWidthInputEnv config input time) = some result ∧
      result.outputs =
        [("y", config sameWidthParameterName,
          some {
            width := config sameWidthParameterName
            bits := (@original dom (config sameWidthParameterName) input).val time
          })] := by
  have validInputs := unarySameWidthInputEnv_valid certified.moduleName
    certified.rhs dom config input time
  rcases certified.bridge dom config input time valid with
    ⟨result, sourceEq, outputsEq⟩
  refine ⟨result, sourceEq, ?_, outputsEq⟩
  have preserved := compileSupportedComb_correct certified.comb config
    (unarySameWidthInputEnv config input time) valid validInputs
  have normalized :
      evalCoreModule certified.compile config
          (unarySameWidthInputEnv config input time) =
        evalSourceDesign certified.design config
          (unarySameWidthInputEnv config input time) := by
    simpa [CertifiedUnarySignalCombDesign.compile,
      CertifiedUnarySignalCombDesign.comb,
      CertifiedUnarySignalCombDesign.design] using preserved
  exact normalized.trans <| by
    simpa [CertifiedUnarySignalCombDesign.design] using sourceEq

theorem compileCertifiedBinarySignalComb_correct
    {original : BinarySameWidthDecl}
    (certified : CertifiedBinarySignalCombDesign original)
    (dom : DomainConfig) (config : Config)
    (lhs rhsSignal : Signal dom (BitVec (config sameWidthParameterName)))
    (time : Nat) (valid : ValidConfig certified.design config) :
    ∃ result,
      evalSourceDesign certified.design config
          (binarySameWidthInputEnv config lhs rhsSignal time) = some result ∧
      evalCoreModule certified.compile config
          (binarySameWidthInputEnv config lhs rhsSignal time) = some result ∧
      result.outputs =
        [("y", config sameWidthParameterName,
          some {
            width := config sameWidthParameterName
            bits :=
              (@original dom (config sameWidthParameterName) lhs rhsSignal).val time
          })] := by
  have validInputs := binarySameWidthInputEnv_valid certified.moduleName
    certified.rhs dom config lhs rhsSignal time
  rcases certified.bridge dom config lhs rhsSignal time valid with
    ⟨result, sourceEq, outputsEq⟩
  refine ⟨result, sourceEq, ?_, outputsEq⟩
  have preserved := compileSupportedComb_correct certified.comb config
    (binarySameWidthInputEnv config lhs rhsSignal time) valid validInputs
  have normalized :
      evalCoreModule certified.compile config
          (binarySameWidthInputEnv config lhs rhsSignal time) =
        evalSourceDesign certified.design config
          (binarySameWidthInputEnv config lhs rhsSignal time) := by
    simpa [CertifiedBinarySignalCombDesign.compile,
      CertifiedBinarySignalCombDesign.comb,
      CertifiedBinarySignalCombDesign.design] using preserved
  exact normalized.trans <| by
    simpa [CertifiedBinarySignalCombDesign.design] using sourceEq

theorem compileCertifiedUnarySignalComb_correct_for_width
    {original : UnarySameWidthDecl}
    (certified : CertifiedUnarySignalCombDesign original)
    (dom : DomainConfig) (width : Nat) (positive : 0 < width)
    (input : Signal dom (BitVec width)) (time : Nat) :
    ∃ result,
      evalSourceDesign certified.design (sameWidthConfig width)
          (unarySameWidthInputEnv (sameWidthConfig width) input time) = some result ∧
      evalCoreModule certified.compile (sameWidthConfig width)
          (unarySameWidthInputEnv (sameWidthConfig width) input time) = some result ∧
      result.outputs =
        [("y", width, some {
          width := width
          bits := (@original dom width input).val time
        })] := by
  simpa [sameWidthConfig_parameter] using
    compileCertifiedUnarySignalComb_correct certified dom (sameWidthConfig width)
      input time (certified.widthLegal width positive)

theorem compileCertifiedBinarySignalComb_correct_for_width
    {original : BinarySameWidthDecl}
    (certified : CertifiedBinarySignalCombDesign original)
    (dom : DomainConfig) (width : Nat) (positive : 0 < width)
    (lhs rhsSignal : Signal dom (BitVec width)) (time : Nat) :
    ∃ result,
      evalSourceDesign certified.design (sameWidthConfig width)
          (binarySameWidthInputEnv (sameWidthConfig width) lhs rhsSignal time) =
        some result ∧
      evalCoreModule certified.compile (sameWidthConfig width)
          (binarySameWidthInputEnv (sameWidthConfig width) lhs rhsSignal time) =
        some result ∧
      result.outputs =
        [("y", width, some {
          width := width
          bits := (@original dom width lhs rhsSignal).val time
        })] := by
  simpa [sameWidthConfig_parameter] using
    compileCertifiedBinarySignalComb_correct certified dom (sameWidthConfig width)
      lhs rhsSignal time (certified.widthLegal width positive)

/-! ## Stable indexed certificate statements -/

def CertifiedUnarySignalCombCorrectnessStatement
    (original : UnarySameWidthDecl)
    (certified : CertifiedUnarySignalCombDesign original) : Prop :=
  ∀ (dom : DomainConfig) (config : Config)
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
          some {
            width := config sameWidthParameterName
            bits := (@original dom (config sameWidthParameterName) input).val time
          })]

def CertifiedBinarySignalCombCorrectnessStatement
    (original : BinarySameWidthDecl)
    (certified : CertifiedBinarySignalCombDesign original) : Prop :=
  ∀ (dom : DomainConfig) (config : Config)
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
          some {
            width := config sameWidthParameterName
            bits :=
              (@original dom (config sameWidthParameterName) lhs rhsSignal).val time
          })]

def SignalCombCompilerCorrectnessStatement : Prop :=
  (∀ (original : UnarySameWidthDecl)
      (certified : CertifiedUnarySignalCombDesign original),
    CertifiedUnarySignalCombCorrectnessStatement original certified) ∧
  (∀ (original : BinarySameWidthDecl)
      (certified : CertifiedBinarySignalCombDesign original),
    CertifiedBinarySignalCombCorrectnessStatement original certified)

theorem signalCombCompiler_correct :
    SignalCombCompilerCorrectnessStatement := by
  constructor
  · intro original certified dom config input time valid
    exact compileCertifiedUnarySignalComb_correct certified dom config
      input time valid
  · intro original certified dom config lhs rhsSignal time valid
    exact compileCertifiedBinarySignalComb_correct certified dom config
      lhs rhsSignal time valid

def CertifiedUnarySignalCombCorrectnessForWidthStatement
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
        [("y", width, some {
          width := width
          bits := (@original dom width input).val time
        })]

def CertifiedBinarySignalCombCorrectnessForWidthStatement
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
        [("y", width, some {
          width := width
          bits := (@original dom width lhs rhsSignal).val time
        })]

def SignalCombCompilerCorrectnessForWidthStatement : Prop :=
  (∀ (original : UnarySameWidthDecl)
      (certified : CertifiedUnarySignalCombDesign original),
    CertifiedUnarySignalCombCorrectnessForWidthStatement original certified) ∧
  (∀ (original : BinarySameWidthDecl)
      (certified : CertifiedBinarySignalCombDesign original),
    CertifiedBinarySignalCombCorrectnessForWidthStatement original certified)

theorem signalCombCompiler_correct_for_width :
    SignalCombCompilerCorrectnessForWidthStatement := by
  constructor
  · intro original certified dom width positive input time
    exact compileCertifiedUnarySignalComb_correct_for_width certified dom width
      positive input time
  · intro original certified dom width positive lhs rhsSignal time
    exact compileCertifiedBinarySignalComb_correct_for_width certified dom width
      positive lhs rhsSignal time

def CertifiedUnarySignalCombNonvacuityStatement
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
        [("y", width, some {
          width := width
          bits := (@original dom width input).val time
        })]

def CertifiedBinarySignalCombNonvacuityStatement
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
        [("y", width, some {
          width := width
          bits := (@original dom width lhs rhsSignal).val time
        })]

theorem certifiedUnarySignalComb_nonvacuous
    (original : UnarySameWidthDecl)
    (certified : CertifiedUnarySignalCombDesign original) :
    CertifiedUnarySignalCombNonvacuityStatement original certified := by
  intro dom width positive
  let input : Signal dom (BitVec width) :=
    Signal.pure (BitVec.ofNat width 0)
  refine ⟨input, ?_⟩
  intro time
  exact compileCertifiedUnarySignalComb_correct_for_width certified dom width
    positive input time

theorem certifiedBinarySignalComb_nonvacuous
    (original : BinarySameWidthDecl)
    (certified : CertifiedBinarySignalCombDesign original) :
    CertifiedBinarySignalCombNonvacuityStatement original certified := by
  intro dom width positive
  let lhs : Signal dom (BitVec width) :=
    Signal.pure (BitVec.ofNat width 0)
  let rhsSignal : Signal dom (BitVec width) :=
    Signal.pure (BitVec.ofNat width 0)
  refine ⟨lhs, rhsSignal, ?_⟩
  intro time
  exact compileCertifiedBinarySignalComb_correct_for_width certified dom width
    positive lhs rhsSignal time

def SignalCombAllCertificatesNonvacuousStatement : Prop :=
  (∀ (original : UnarySameWidthDecl)
      (certified : CertifiedUnarySignalCombDesign original),
    CertifiedUnarySignalCombNonvacuityStatement original certified) ∧
  (∀ (original : BinarySameWidthDecl)
      (certified : CertifiedBinarySignalCombDesign original),
    CertifiedBinarySignalCombNonvacuityStatement original certified)

theorem signalCombAllCertificates_nonvacuous :
    SignalCombAllCertificatesNonvacuousStatement := by
  exact ⟨certifiedUnarySignalComb_nonvacuous,
    certifiedBinarySignalComb_nonvacuous⟩

/-! ## Closed indexed witnesses -/

def unarySignalCombWitnessOriginal : UnarySameWidthDecl :=
  fun input => input

def binarySignalCombWitnessOriginal : BinarySameWidthDecl :=
  fun lhs _rhs => lhs

theorem unarySignalCombWitness_supported :
    SupportedComb
      (unarySameWidthDesign "signal_comb_unary_identity" (.ref "x")) := by
  simp [SupportedComb, configDomain, ParametersDeclared, dimensions,
    unarySameWidthDesign, allBindings, BindingsScoped, CombExpr.refs,
    CombExpr.dimensions, dimParameters, sameWidthDim,
    sameWidthParameterName]

theorem binarySignalCombWitness_supported :
    SupportedComb
      (binarySameWidthDesign "signal_comb_binary_left" (.ref "a")) := by
  simp [SupportedComb, configDomain, ParametersDeclared, dimensions,
    binarySameWidthDesign, allBindings, BindingsScoped, CombExpr.refs,
    CombExpr.dimensions, dimParameters, sameWidthDim,
    sameWidthParameterName]

theorem unarySignalCombWitness_widthLegal
    (width : Nat) (positive : 0 < width) :
    ValidConfig
      (unarySameWidthDesign "signal_comb_unary_identity" (.ref "x"))
      (sameWidthConfig width) := by
  simp [ValidConfig, positiveDimensions, divisors, dimensions, dimDivisors,
    unarySameWidthDesign, allBindings, CombExpr.positiveDimensions,
    CombExpr.dimensions, CombExpr.SlicesValid, evalDim, sameWidthDim,
    sameWidthConfig, sameWidthParameterName, positive]

theorem binarySignalCombWitness_widthLegal
    (width : Nat) (positive : 0 < width) :
    ValidConfig
      (binarySameWidthDesign "signal_comb_binary_left" (.ref "a"))
      (sameWidthConfig width) := by
  simp [ValidConfig, positiveDimensions, divisors, dimensions, dimDivisors,
    binarySameWidthDesign, allBindings, CombExpr.positiveDimensions,
    CombExpr.dimensions, CombExpr.SlicesValid, evalDim, sameWidthDim,
    sameWidthConfig, sameWidthParameterName, positive]

theorem unarySignalCombWitness_bridge
    (dom : DomainConfig) (config : Config)
    (input : Signal dom (BitVec (config sameWidthParameterName)))
    (time : Nat) (_valid :
      ValidConfig
        (unarySameWidthDesign "signal_comb_unary_identity" (.ref "x"))
        config) :
    ∃ result,
      evalSourceDesign
          (unarySameWidthDesign "signal_comb_unary_identity" (.ref "x"))
          config (unarySameWidthInputEnv config input time) = some result ∧
      result.outputs =
        [("y", config sameWidthParameterName,
          some {
            width := config sameWidthParameterName
            bits :=
              (@unarySignalCombWitnessOriginal dom
                (config sameWidthParameterName) input).val time
          })] := by
  simp [evalSourceDesign, evalSourceBindings, evalSourceBinding,
    evalSourceExpr, observeSourcePorts, observeSourceBindings,
    observeSourceWidths, observeParameters, portValuesWellTyped,
    unarySameWidthDesign, unarySameWidthInputEnv, packedSignalSample,
    unarySignalCombWitnessOriginal, allBindings, PackedValue.resize,
    sameWidthDim, evalDim, List.lookup]

theorem binarySignalCombWitness_bridge
    (dom : DomainConfig) (config : Config)
    (lhs rhsSignal : Signal dom (BitVec (config sameWidthParameterName)))
    (time : Nat) (_valid :
      ValidConfig
        (binarySameWidthDesign "signal_comb_binary_left" (.ref "a"))
        config) :
    ∃ result,
      evalSourceDesign
          (binarySameWidthDesign "signal_comb_binary_left" (.ref "a"))
          config (binarySameWidthInputEnv config lhs rhsSignal time) =
        some result ∧
      result.outputs =
        [("y", config sameWidthParameterName,
          some {
            width := config sameWidthParameterName
            bits :=
              (@binarySignalCombWitnessOriginal dom
                (config sameWidthParameterName) lhs rhsSignal).val time
          })] := by
  simp [evalSourceDesign, evalSourceBindings, evalSourceBinding,
    evalSourceExpr, observeSourcePorts, observeSourceBindings,
    observeSourceWidths, observeParameters, portValuesWellTyped,
    binarySameWidthDesign, binarySameWidthInputEnv, packedSignalSample,
    binarySignalCombWitnessOriginal, allBindings, PackedValue.resize,
    sameWidthDim, evalDim, List.lookup]

def unarySignalCombWitness :
    CertifiedUnarySignalCombDesign unarySignalCombWitnessOriginal :=
  { moduleName := "signal_comb_unary_identity"
    rhs := .ref "x"
    supported := unarySignalCombWitness_supported
    widthLegal := unarySignalCombWitness_widthLegal
    bridge := unarySignalCombWitness_bridge }

def binarySignalCombWitness :
    CertifiedBinarySignalCombDesign binarySignalCombWitnessOriginal :=
  { moduleName := "signal_comb_binary_left"
    rhs := .ref "a"
    supported := binarySignalCombWitness_supported
    widthLegal := binarySignalCombWitness_widthLegal
    bridge := binarySignalCombWitness_bridge }

/-- Stable closed non-vacuity statement.  Both indexed carrier families have
    a concrete certificate, and each certificate succeeds for one fixed set
    of input streams at every time and every positive width. -/
def SignalCombCorrectnessDomainInhabitedStatement : Prop :=
  CertifiedUnarySignalCombNonvacuityStatement
      unarySignalCombWitnessOriginal unarySignalCombWitness ∧
    CertifiedBinarySignalCombNonvacuityStatement
      binarySignalCombWitnessOriginal binarySignalCombWitness

theorem signalCombCorrectnessDomain_inhabited :
    SignalCombCorrectnessDomainInhabitedStatement := by
  exact ⟨certifiedUnarySignalComb_nonvacuous
      unarySignalCombWitnessOriginal unarySignalCombWitness,
    certifiedBinarySignalComb_nonvacuous
      binarySignalCombWitnessOriginal binarySignalCombWitness⟩

end Sparkle.Compiler.SignalCombCorrectness
