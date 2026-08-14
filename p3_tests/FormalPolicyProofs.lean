import Sparkle

/-- One theorem quantifies over every legal BitVec width. -/
theorem p3_identity_generic_correct {WIDTH : Nat} (x : BitVec WIDTH) : x = x := by
  rfl

/-- Concrete obligations model a backend that cannot prove a generic family. -/
theorem p3_identity_w3_correct (x : BitVec 3) : x = x := by
  rfl

theorem p3_identity_w17_correct (x : BitVec 17) : x = x := by
  rfl

theorem p3_identity_w65_correct (x : BitVec 65) : x = x := by
  rfl
