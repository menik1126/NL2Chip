import Sparkle.Compiler.SignalCombElab

namespace Tests.SignalCombElabTests

set_option warningAsError true
set_option linter.unusedVariables false

open Lean
open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Compiler.CombCorrectness
open Sparkle.Compiler.CombElab
open Sparkle.Compiler.SignalCombCorrectness
open Sparkle.Compiler.SignalCombElab

/-! ## Kernel-indexed positive end-to-end cases -/

def passthrough {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  input

def add2 {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  lhs + rhs

def xor2 {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  lhs ^^^ rhs

def nestedAddXor {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  lhs + (lhs ^^^ rhs)

def unaryAddXor {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  input + (input ^^^ input)

#certifySignalComb passthrough
#certifySignalComb add2
#certifySignalComb xor2
#certifySignalComb nestedAddXor
#certifySignalComb unaryAddXor

#check passthrough.certifiedSignalComb
#check add2.certifiedSignalComb
#check xor2.certifiedSignalComb
#check nestedAddXor.certifiedSignalComb
#check unaryAddXor.certifiedSignalComb

-- The carrier type itself binds each certificate to the exact ordinary source.
example : CertifiedUnarySignalCombDesign passthrough :=
  passthrough.certifiedSignalComb

example : CertifiedBinarySignalCombDesign add2 :=
  add2.certifiedSignalComb

-- Closed aliases enter the existing proof-carrying production compiler.
#synthesizeComb passthrough.certifiedComb
#synthesizeComb add2.certifiedComb
#synthesizeComb nestedAddXor.certifiedComb

#check passthrough.signalCombCorrect
#check passthrough.signalCombCorrectForWidth
#check nestedAddXor.signalCombCorrect
#check nestedAddXor.signalCombCorrectForWidth

/-! ## Arbitrary positive widths and the W=0 boundary -/

example : ValidConfig add2.certifiedSignalComb.design (sameWidthConfig 1) :=
  add2.certifiedSignalComb.widthLegal 1 (by decide)

example : ValidConfig add2.certifiedSignalComb.design (sameWidthConfig 3) :=
  add2.certifiedSignalComb.widthLegal 3 (by decide)

example : ValidConfig add2.certifiedSignalComb.design (sameWidthConfig 17) :=
  add2.certifiedSignalComb.widthLegal 17 (by decide)

example : ValidConfig add2.certifiedSignalComb.design (sameWidthConfig 257) :=
  add2.certifiedSignalComb.widthLegal 257 (by decide)

example (dom : DomainConfig)
    (lhs rhs : Signal dom (BitVec 257)) (time : Nat) : True := by
  have _preservedAt257 :=
    nestedAddXor.signalCombCorrectForWidth dom 257 (by decide) lhs rhs time
  trivial

example :
    ¬ ValidConfig passthrough.certifiedSignalComb.design (sameWidthConfig 0) := by
  simp [CertifiedUnarySignalCombDesign.design,
    passthrough.certifiedSignalComb, ValidConfig, positiveDimensions,
    unarySameWidthDesign, allBindings, CombExpr.positiveDimensions,
    sameWidthDim, evalDim]

/-! ## Hostile simp injection stays outside generated proof closure -/

axiom hostileSetWidth {W : Nat} (value : BitVec W) :
  BitVec.setWidth W value = value

attribute [simp] hostileSetWidth

def safeAfterHostileSimp {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  lhs + rhs

#certifySignalComb safeAfterHostileSimp
#synthesizeComb safeAfterHostileSimp.certifiedComb

/-! Every generated declaration is visible in the checked environment at the
    next top-level boundary, is safe/transparent or a theorem, and depends only
    on Lean's standard logical axioms. -/
example : True := by
  run_tac
    let env ← getEnv
    let roots : Array Name := #[
      `Tests.SignalCombElabTests.passthrough,
      `Tests.SignalCombElabTests.add2,
      `Tests.SignalCombElabTests.xor2,
      `Tests.SignalCombElabTests.nestedAddXor,
      `Tests.SignalCombElabTests.unaryAddXor,
      `Tests.SignalCombElabTests.safeAfterHostileSimp]
    let suffixes : Array String := #[
      "signalCombSupported", "signalCombWidthLegal", "signalCombBridge",
      "certifiedSignalComb", "certifiedComb", "signalCombCorrect",
      "signalCombCorrectForWidth"]
    let allowed : Array Name :=
      #[``propext, ``Classical.choice, ``Quot.sound]
    for root in roots do
      for suffix in suffixes do
        let declName := Name.str root suffix
        let info ← match env.checked.get.find? declName with
          | some info => pure info
          | none => throwError m!"generated declaration missing from checked environment: {declName}"
        match info with
        | .defnInfo definition =>
            unless definition.safety == .safe do
              throwError m!"generated definition is unsafe: {declName}"
            if definition.type.hasSorry || definition.value.hasSorry then
              throwError m!"generated definition contains sorry: {declName}"
        | .thmInfo theoremInfo =>
            if theoremInfo.type.hasSorry || theoremInfo.value.hasSorry then
              throwError m!"generated theorem contains sorry: {declName}"
        | _ =>
            throwError m!"generated declaration is neither transparent def nor theorem: {declName}"
        if (Lean.Compiler.getImplementedBy? env declName).isSome then
          throwError m!"generated declaration has implemented_by: {declName}"
        let axioms ← Lean.collectAxioms declName
        let disallowed := axioms.filter fun axiomName =>
          !allowed.contains axiomName
        unless disallowed.isEmpty do
          throwError m!"generated declaration has non-allowlisted axioms: {declName}: {disallowed}"
        if axioms.contains `Tests.SignalCombElabTests.hostileSetWidth then
          throwError m!"hostile simp axiom leaked into generated proof: {declName}"
  trivial

/-! ## Fail-closed body attacks -/

def registerBad {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Signal.register (BitVec.ofNat W 0) input

/--
error: SignalComb body rejected: state, loop, or memory primitive 'Sparkle.Core.Signal.Signal.register' is outside the pure combinational subset.
-/
#guard_msgs in
#certifySignalComb registerBad

def loopBad {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Signal.loop fun _ => input

/--
error: SignalComb body rejected: state, loop, or memory primitive 'Sparkle.Core.Signal.Signal.loop' is outside the pure combinational subset.
-/
#guard_msgs in
#certifySignalComb loopBad

def memoryBad {dom : DomainConfig} {W : Nat}
    (writeAddr writeData : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Signal.memory writeAddr writeData (Signal.pure true) writeAddr

/--
error: SignalComb body rejected: state, loop, or memory primitive 'Sparkle.Core.Signal.Signal.memory' is outside the pure combinational subset.
-/
#guard_msgs in
#certifySignalComb memoryBad

def helper {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) := input

def helperBad {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  helper input

/--
error: SignalComb body rejected: non-whitelisted constant 'Tests.SignalCombElabTests.helper'; helper calls and hierarchy fail closed.
-/
#guard_msgs in
#certifySignalComb helperBad

def deadRegisterBad {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  let _dead := Signal.register (BitVec.ofNat W 0) input
  input

/--
error: SignalComb body rejected: state, loop, or memory primitive 'Sparkle.Core.Signal.Signal.register' is outside the pure combinational subset.
-/
#guard_msgs in
#certifySignalComb deadRegisterBad

def deadHelperBad {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  let _dead := helper input
  input

/--
error: SignalComb body rejected: non-whitelisted constant 'Tests.SignalCombElabTests.helper'; helper calls and hierarchy fail closed.
-/
#guard_msgs in
#certifySignalComb deadHelperBad

def fakeAdd {dom : DomainConfig} {W : Nat} :
    HAdd (Signal dom (BitVec W)) (Signal dom (BitVec W))
      (Signal dom (BitVec W)) where
  hAdd lhs _rhs := lhs

def customInstanceBad {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  HAdd.hAdd (self := fakeAdd) lhs rhs

/--
error: SignalComb body rejected: substituted operator instance; expected exactly 'Sparkle.Core.Signal.instHAddSignalBitVec'.
-/
#guard_msgs in
#certifySignalComb customInstanceBad

/-! ## Fail-closed signature attacks -/

def boolBad {dom : DomainConfig} {W : Nat}
    (input : Signal dom Bool) : Signal dom Bool := input

/--
error: SignalComb signature rejected: input binder 1 must be exactly Signal dom (BitVec W); Bool, tuples, state bundles, and hierarchy are not accepted.
-/
#guard_msgs in
#certifySignalComb boolBad

def zeroArityBad {dom : DomainConfig} {W : Nat} :
    Signal dom (BitVec W) :=
  Signal.pure (BitVec.ofNat W 0)

/--
error: SignalComb signature rejected: expected exactly two implicit binders and one or two explicit inputs; found 2 total binders.
-/
#guard_msgs in
#certifySignalComb zeroArityBad

def threeArityBad {dom : DomainConfig} {W : Nat}
    (first second third : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  first

/--
error: SignalComb signature rejected: expected exactly two implicit binders and one or two explicit inputs; found 5 total binders.
-/
#guard_msgs in
#certifySignalComb threeArityBad

def binderOrderBad {W : Nat} {dom : DomainConfig}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) := input

/--
error: SignalComb signature rejected: the first binder must be exactly implicit {dom : DomainConfig}.
-/
#guard_msgs in
#certifySignalComb binderOrderBad

def derivedWidthBad {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec (W + 1))) : Signal dom (BitVec (W + 1)) :=
  input

/--
error: SignalComb signature rejected: input binder 1 must be exactly Signal dom (BitVec W); derived widths, casts, and alternate domains are not accepted.
-/
#guard_msgs in
#certifySignalComb derivedWidthBad

def implicitInputBad {dom : DomainConfig} {W : Nat}
    {input : Signal dom (BitVec W)} : Signal dom (BitVec W) := input

/--
error: SignalComb signature rejected: input binder 1 must be explicit.
-/
#guard_msgs in
#certifySignalComb implicitInputBad

/-! ## Fail-closed trust attacks -/

axiom untrustedSource {dom : DomainConfig} {W : Nat} :
  Signal dom (BitVec W) → Signal dom (BitVec W)

noncomputable def axiomBackedBad {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  untrustedSource input

/--
error: SignalComb source 'Tests.SignalCombElabTests.axiomBackedBad' depends on non-allowlisted axiom(s): Tests.SignalCombElabTests.untrustedSource.
-/
#guard_msgs in
#certifySignalComb axiomBackedBad

unsafe def unsafeBad {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) := input

/--
error: SignalComb source 'Tests.SignalCombElabTests.unsafeBad' is unsafe.
-/
#guard_msgs in
#certifySignalComb unsafeBad

opaque opaqueBad {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) := input

/--
error: SignalComb source 'Tests.SignalCombElabTests.opaqueBad' is opaque; a safe transparent definition is required.
-/
#guard_msgs in
#certifySignalComb opaqueBad

unsafe def implementedReplacement {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) := input

@[implemented_by implementedReplacement]
def implementedBad {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) := input

/--
error: SignalComb source 'Tests.SignalCombElabTests.implementedBad' has an unproved @[implemented_by] replacement.
-/
#guard_msgs in
#certifySignalComb implementedBad

/-! ## Atomic target-name preflight -/

def preflightCollision {dom : DomainConfig} {W : Nat}
    (input : Signal dom (BitVec W)) : Signal dom (BitVec W) := input

axiom preflightCollision.signalCombCorrectForWidth : False

/--
error: SignalComb generation refused: target declaration(s) already exist: Tests.SignalCombElabTests.preflightCollision.signalCombCorrectForWidth.
-/
#guard_msgs in
#certifySignalComb preflightCollision

example : True := by
  run_tac
    let env ← getEnv
    let source := `Tests.SignalCombElabTests.preflightCollision
    let absentSuffixes : Array String := #[
      "signalCombSupported", "signalCombWidthLegal", "signalCombBridge",
      "certifiedSignalComb", "certifiedComb", "signalCombCorrect"]
    for suffix in absentSuffixes do
      let target := Name.str source suffix
      if (env.checked.get.find? target).isSome then
        throwError m!"preflight collision left a partial generated declaration: {target}"
  trivial

end Tests.SignalCombElabTests
