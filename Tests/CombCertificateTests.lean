import Sparkle.Compiler.CombCertificate

namespace Tests.CombCertificateTests

open Sparkle.Compiler.CombCertificate

example : True := by
  run_tac
    let certificate ← certifyCombCompilerCorrectness
      (some "comb-certificate-direct-test")
    let status ← match certificate.getObjValAs? String "status" with
      | .ok value => pure value
      | .error message => throwError message
    unless status == "proved" do
      throwError "Compiler correctness certificate did not report a proved theorem."
    let scope ← match certificate.getObjValAs? String "scope" with
      | .ok value => pure value
      | .error message => throwError message
    unless scope == certifiedScope do
      throwError "Compiler correctness certificate widened or changed its scope."
    let claimed ← match certificate.getObjValAs? Bool "compiler_correctness_claimed" with
      | .ok value => pure value
      | .error message => throwError message
    unless claimed do
      throwError "Compiler correctness certificate lost its explicit claim bit."
    let nonVacuity ← match certificate.getObjValAs? Bool "non_vacuity_checked" with
      | .ok value => pure value
      | .error message => throwError message
    unless nonVacuity do
      throwError "Compiler correctness certificate lost its checked domain witness."
    let witnessShape ←
      match certificate.getObjValAs? Bool "domain_witness_shape_checked" with
      | .ok value => pure value
      | .error message => throwError message
    unless witnessShape do
      throwError "Compiler correctness certificate did not bind the exact width witness."
    let nonVacuityScope ←
      match certificate.getObjValAs? String "non_vacuity_scope" with
      | .ok value => pure value
      | .error message => throwError message
    unless nonVacuityScope == "all_positive_natural_widths" do
      throwError "Compiler correctness domain witness lost its arbitrary-width scope."
    for field in
        ["signal_frontend_correctness_claimed",
         "optimizer_correctness_claimed",
         "verilog_emitter_correctness_claimed"] do
      let covered ← match certificate.getObjValAs? Bool field with
        | .ok value => pure value
        | .error message => throwError message
      if covered then
        throwError "Phase-one certificate improperly widened its coverage at '{field}'."
  trivial

end Tests.CombCertificateTests
