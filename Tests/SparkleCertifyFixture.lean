import Sparkle

namespace Tests.SparkleCertifyFixture

set_option linter.unusedVariables false

theorem arbitraryWidth
    (W : Nat) (legal : 0 < W) (x : BitVec W) : x = x := by
  rfl

axiom magic : False

theorem customAxiomWidth (W : Nat) : W = W := by
  exact False.elim magic

end Tests.SparkleCertifyFixture
