import Sparkle

namespace Tests.UniversalTheoremCertificateTests

set_option linter.unusedVariables false

theorem arbitraryWidth
    (W : Nat) (legal : 0 < W) (x : BitVec W) : x = x := by
  rfl

#sparkleUniversalTheorem Tests.UniversalTheoremCertificateTests.arbitraryWidth parameters [W]

#sparkleUniversalTheorem Tests.UniversalTheoremCertificateTests.arbitraryWidth
  "universal-certificate-test-nonce" parameters [W]

/--
error: A universal theorem certificate nonce must not be empty.
-/
#guard_msgs in
#sparkleUniversalTheorem arbitraryWidth "" parameters [W]

example : True := by
  run_tac
    let certificate ← Sparkle.Compiler.Elab.certifyUniversalTheorem
      `Tests.UniversalTheoremCertificateTests.arbitraryWidth #[`W]
      (some "direct-api-test-nonce")
    let verificationNonce ← match certificate.getObjValAs? String "verification_nonce" with
      | .ok value => pure value
      | .error message => throwError message
    unless verificationNonce == "direct-api-test-nonce" do
      throwError "Trusted universal theorem certificate API lost its nonce binding."
  trivial

theorem arbitraryShape
    {W D : Nat} (legalW : 0 < W) (legalD : 0 < D)
    (x : BitVec (W * D)) : x = x := by
  rfl

#sparkleUniversalTheorem arbitraryShape parameters [W, D]

theorem fixedWidth (x : BitVec 8) : x = x := by
  rfl

/--
error: Theorem 'Tests.UniversalTheoremCertificateTests.fixedWidth' does not universally bind a parameter named 'W'.
-/
#guard_msgs in
#sparkleUniversalTheorem fixedWidth parameters [W]

theorem fixedWidthWithDummyParameter
    (W : Nat) (x : BitVec 8) : x = x := by
  rfl

/--
error: Universal theorem parameter 'W' does not occur in the theorem domain or conclusion; refusing to certify a fixed-width statement with an unused dummy parameter.
-/
#guard_msgs in
#sparkleUniversalTheorem fixedWidthWithDummyParameter parameters [W]

/--
error: Theorem 'Tests.UniversalTheoremCertificateTests.arbitraryWidth' does not universally bind a parameter named 'D'.
-/
#guard_msgs in
#sparkleUniversalTheorem arbitraryWidth parameters [W, D]

theorem nonNatParameter (W : Type) : True := by
  trivial

/--
error: Universal theorem parameter 'W' has type 'Type', not Nat.
-/
#guard_msgs in
#sparkleUniversalTheorem nonNatParameter parameters [W]

def propositionDefinition (W : Nat) : Prop := W = W

/--
error: 'Tests.UniversalTheoremCertificateTests.propositionDefinition' is not a Lean theorem or lemma.
-/
#guard_msgs in
#sparkleUniversalTheorem propositionDefinition parameters [W]

/--
warning: declaration uses `sorry`
-/
#guard_msgs in
theorem sorriedWidth (W : Nat) : W = W := by
  sorry

/--
error: Theorem 'Tests.UniversalTheoremCertificateTests.sorriedWidth' contains 'sorry' and cannot receive a universal theorem certificate.
-/
#guard_msgs in
#sparkleUniversalTheorem sorriedWidth parameters [W]

theorem transitivelySorriedWidth (W : Nat) : W = W :=
  sorriedWidth W

/--
error: Theorem 'Tests.UniversalTheoremCertificateTests.transitivelySorriedWidth' transitively depends on 'sorry' and cannot receive a universal theorem certificate.
-/
#guard_msgs in
#sparkleUniversalTheorem transitivelySorriedWidth parameters [W]

theorem standardAxiomWidth (W : Nat) : (W = W) = (W = W) := by
  exact propext Iff.rfl

#sparkleUniversalTheorem standardAxiomWidth parameters [W]

axiom magic : False

theorem magicWidth (W : Nat) : W = W := by
  exact False.elim magic

/--
error: Theorem 'Tests.UniversalTheoremCertificateTests.magicWidth' depends on non-allowlisted axiom(s): Tests.UniversalTheoremCertificateTests.magic. Universal theorem certificates only allow Lean's standard logical axioms: propext, Classical.choice, Quot.sound.
-/
#guard_msgs in
#sparkleUniversalTheorem magicWidth parameters [W]

/--
error: Duplicate universal theorem parameter 'W'.
-/
#guard_msgs in
#sparkleUniversalTheorem arbitraryWidth parameters [W, W]

/--
error: A universal theorem certificate must name at least one Nat parameter.
-/
#guard_msgs in
#sparkleUniversalTheorem arbitraryWidth parameters []

end Tests.UniversalTheoremCertificateTests
