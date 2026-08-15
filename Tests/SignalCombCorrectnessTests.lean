/-
  Focused acceptance tests for original-indexed same-width Signal certificates.
-/

import Sparkle.Compiler.SignalCombCorrectness

namespace Tests.SignalCombCorrectnessTests

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Compiler.CombCorrectness
open Sparkle.Compiler.SignalCombCorrectness

def ramp (width bias : Nat) : Signal defaultDomain (BitVec width) :=
  ⟨fun time => BitVec.ofNat width (3 * time + bias)⟩

theorem unaryRamp_endToEnd_for_every_positive_width
    (width : Nat) (positive : 0 < width) (time : Nat) :
    ∃ result,
      evalSourceDesign unarySignalCombWitness.design (sameWidthConfig width)
          (unarySameWidthInputEnv (sameWidthConfig width)
            (ramp width 1) time) = some result ∧
      evalCoreModule unarySignalCombWitness.compile (sameWidthConfig width)
          (unarySameWidthInputEnv (sameWidthConfig width)
            (ramp width 1) time) = some result ∧
      result.outputs =
        [("y", width, some {
          width := width
          bits :=
            (@unarySignalCombWitnessOriginal defaultDomain width
              (ramp width 1)).val time
        })] :=
  compileCertifiedUnarySignalComb_correct_for_width unarySignalCombWitness
    defaultDomain width positive (ramp width 1) time

theorem binaryRamp_endToEnd_for_every_positive_width
    (width : Nat) (positive : 0 < width) (time : Nat) :
    ∃ result,
      evalSourceDesign binarySignalCombWitness.design (sameWidthConfig width)
          (binarySameWidthInputEnv (sameWidthConfig width)
            (ramp width 1) (ramp width 99) time) = some result ∧
      evalCoreModule binarySignalCombWitness.compile (sameWidthConfig width)
          (binarySameWidthInputEnv (sameWidthConfig width)
            (ramp width 1) (ramp width 99) time) = some result ∧
      result.outputs =
        [("y", width, some {
          width := width
          bits :=
            (@binarySignalCombWitnessOriginal defaultDomain width
              (ramp width 1) (ramp width 99)).val time
        })] :=
  compileCertifiedBinarySignalComb_correct_for_width binarySignalCombWitness
    defaultDomain width positive (ramp width 1) (ramp width 99) time

private def outputNat? (result : Option Evaluation) : Option Nat := do
  let evaluation ← result
  let (_, _, value) ← evaluation.outputs.find? fun (name, _, _) => name == "y"
  return (← value).bits.toNat

def compiledUnaryRampOutput (width time : Nat) : Option Nat :=
  outputNat? <| evalCoreModule unarySignalCombWitness.compile
    (sameWidthConfig width)
    (unarySameWidthInputEnv (sameWidthConfig width) (ramp width 1) time)

def compiledBinaryRampOutput (width time : Nat) : Option Nat :=
  outputNat? <| evalCoreModule binarySignalCombWitness.compile
    (sameWidthConfig width)
    (binarySameWidthInputEnv (sameWidthConfig width)
      (ramp width 1) (ramp width 99) time)

/- The required width set includes an odd width well above a machine word. -/
example : compiledUnaryRampOutput 1 0 = some 1 := by native_decide
example : compiledUnaryRampOutput 3 2 = some 7 := by native_decide
example : compiledUnaryRampOutput 17 5 = some 16 := by native_decide
example : compiledUnaryRampOutput 257 19 = some 58 := by native_decide

example : compiledBinaryRampOutput 1 0 = some 1 := by native_decide
example : compiledBinaryRampOutput 3 2 = some 7 := by native_decide
example : compiledBinaryRampOutput 17 5 = some 16 := by native_decide
example : compiledBinaryRampOutput 257 19 = some 58 := by native_decide

example : CertifiedUnarySignalCombDesign unarySignalCombWitnessOriginal :=
  unarySignalCombWitness

example : CertifiedBinarySignalCombDesign binarySignalCombWitnessOriginal :=
  binarySignalCombWitness

example : SignalCombCompilerCorrectnessStatement :=
  signalCombCompiler_correct

example : SignalCombCompilerCorrectnessForWidthStatement :=
  signalCombCompiler_correct_for_width

example : SignalCombAllCertificatesNonvacuousStatement :=
  signalCombAllCertificates_nonvacuous

example : SignalCombCorrectnessDomainInhabitedStatement :=
  signalCombCorrectnessDomain_inhabited

end Tests.SignalCombCorrectnessTests
