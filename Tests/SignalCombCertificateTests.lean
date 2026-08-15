import Sparkle.Compiler.SignalCombCertificate

namespace Tests.SignalCombCertificateTests

open Sparkle.Compiler.SignalCombCertificate

example : True := by
  run_tac
    let certificate ← certifySignalCombCompilerCorrectness
      (some "signal-comb-certificate-direct-test")
    let status ← match certificate.getObjValAs? String "status" with
      | .ok value => pure value
      | .error message => throwError message
    unless status == "proved" do
      throwError "Signal-combination certificate did not report a proved theorem."
    let scope ← match certificate.getObjValAs? String "scope" with
      | .ok value => pure value
      | .error message => throwError message
    unless scope == certifiedScope do
      throwError "Signal-combination certificate widened or changed its scope."
    for field in
        ["kernel_checked",
         "kernel_bridge_checked",
         "extractor_output_requires_kernel_checked_bridge",
         "comb_ast_to_core_ir_certificate_reused",
         "ordinary_signal_proof_carrying_comb_subset_correctness_claimed",
         "proof_carrying_signal_comb_subset_to_core_ir_correctness_claimed",
         "original_indexed_carriers_checked",
         "unary_original_index_checked",
         "binary_original_index_checked",
         "all_certificates_nonvacuity_checked",
         "non_vacuity_checked",
         "arbitrary_positive_width_checked",
         "domain_witness_definitions_checked",
         "domain_witness_shape_checked",
         "proof_closure_checked"] do
      let value ← match certificate.getObjValAs? Bool field with
        | .ok value => pure value
        | .error message => throwError message
      unless value do
        throwError "Signal-combination certificate lost required evidence at '{field}'."
    for field in
        ["signal_frontend_correctness_claimed",
         "general_signal_frontend_correctness_claimed",
         "meta_reifier_correctness_claimed",
         "meta_reifier_trusted",
         "sequential_signal_correctness_claimed",
         "memory_correctness_claimed",
         "hierarchy_correctness_claimed",
         "optimizer_correctness_claimed",
         "verilog_emitter_correctness_claimed"] do
      let value ← match certificate.getObjValAs? Bool field with
        | .ok value => pure value
        | .error message => throwError message
      if value then
        throwError "Signal-combination certificate improperly widened coverage at '{field}'."
    let nonVacuityScope ←
      match certificate.getObjValAs? String "non_vacuity_scope" with
      | .ok value => pure value
      | .error message => throwError message
    unless nonVacuityScope ==
        "all_certificates_all_positive_natural_widths_all_times" do
      throwError "Indexed Signal certificates lost arbitrary-width/all-time non-vacuity."
    for field in
        ["axioms", "width_axioms", "all_certificates_nonvacuity_axioms",
         "domain_inhabited_axioms", "domain_witness_axioms",
         "unary_domain_witness_axioms", "binary_domain_witness_axioms",
         "unary_domain_witness_bridge_axioms",
         "binary_domain_witness_bridge_axioms",
         "production_well_formed_axioms"] do
      match certificate.getObjVal? field with
      | .ok _ => pure ()
      | .error message => throwError message
    let trustedJson ← match certificate.getObjVal? "trusted_constants" with
      | .ok value => pure value
      | .error message => throwError message
    let trustedEntries ← match trustedJson.getArr? with
      | .ok value => pure value
      | .error message => throwError message
    for (expectedRole, expectedName) in
        [("same_width_parameter_name",
          "Sparkle.Compiler.SignalCombCorrectness.sameWidthParameterName"),
         ("same_width_dimension",
          "Sparkle.Compiler.SignalCombCorrectness.sameWidthDim"),
         ("same_width_configuration",
          "Sparkle.Compiler.SignalCombCorrectness.sameWidthConfig"),
         ("unary_same_width_design",
          "Sparkle.Compiler.SignalCombCorrectness.unarySameWidthDesign"),
         ("binary_same_width_design",
          "Sparkle.Compiler.SignalCombCorrectness.binarySameWidthDesign"),
         ("packed_signal_sample",
          "Sparkle.Compiler.SignalCombCorrectness.packedSignalSample"),
         ("unary_same_width_input_environment",
          "Sparkle.Compiler.SignalCombCorrectness.unarySameWidthInputEnv"),
         ("binary_same_width_input_environment",
          "Sparkle.Compiler.SignalCombCorrectness.binarySameWidthInputEnv")] do
      let mut actualName : Option String := none
      for entry in trustedEntries do
        let role ← match entry.getObjValAs? String "role" with
          | .ok value => pure value
          | .error message => throwError message
        if role == expectedRole then
          actualName ← match entry.getObjValAs? String "name" with
            | .ok value => pure (some value)
            | .error message => throwError message
      unless actualName == some expectedName do
        throwError "Trusted constant role {expectedRole} is not locked to {expectedName}."
    let unaryWitness ←
      match certificate.getObjValAs? String "unary_domain_witness" with
      | .ok value => pure value
      | .error message => throwError message
    unless unaryWitness.endsWith ".unarySignalCombWitness" do
      throwError "Certificate is not bound to the fixed unary indexed witness."
    let binaryWitness ←
      match certificate.getObjValAs? String "binary_domain_witness" with
      | .ok value => pure value
      | .error message => throwError message
    unless binaryWitness.endsWith ".binarySignalCombWitness" do
      throwError "Certificate is not bound to the fixed binary indexed witness."
    let base ← match certificate.getObjVal? "base_comb_certificate" with
      | .ok value => pure value
      | .error message => throwError message
    let baseStatus ← match base.getObjValAs? String "status" with
      | .ok value => pure value
      | .error message => throwError message
    unless baseStatus == "proved" do
      throwError "Composed CombCertificate was not proved."
    let baseSignalClaim ←
      match base.getObjValAs? Bool "signal_frontend_correctness_claimed" with
      | .ok value => pure value
      | .error message => throwError message
    if baseSignalClaim then
      throwError "Composed CombCertificate was improperly modified to claim Signal."
  trivial

end Tests.SignalCombCertificateTests
