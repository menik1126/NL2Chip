/-
  Production-facing entry points for the verified, explicitly deep-embedded
  combinational compiler.

  The semantic-preservation theorem covers `compileSupportedComb`, whose input
  is a proof-carrying `CertifiedCombDesign`.  This module deliberately does not
  import or dispatch through `Sparkle.Compiler.Elab`: ordinary `Signal`
  elaboration remains a separate, unverified frontend.

  The checked SystemVerilog helpers below validate and emit the resulting core
  IR, but the phase-one theorem does not cover the Verilog backend itself.
-/

import Lean
import Lean.Compiler.ImplementedByAttr
import Sparkle.Compiler.CombCorrectness
import Sparkle.Backend.Verilog

namespace Sparkle.Compiler.CombElab

open Lean Elab Command Meta
open Sparkle.IR.AST
open Sparkle.Compiler.CombCorrectness

/--
Compile a proof-carrying explicit combinational design with the exact compiler
entry point covered by `compileSupportedComb_correct`, then run the existing IR
and SystemVerilog-name preflight checks.  This function does not optimize or
specialize the resulting module.
-/
def toCoreIRChecked (certified : CertifiedCombDesign) :
    Except String Sparkle.IR.AST.Module := do
  let module := compileSupportedComb certified
  module.validateDimensions
  module.validateSanitizedNames Sparkle.Backend.Verilog.sanitizeName
  return module

/--
Emit checked SystemVerilog from the verified compiler's core IR.  Only the
explicit source-to-core lowering is theorem-covered; Verilog emission is an
additional, currently unverified backend step.
-/
def toVerilogChecked (certified : CertifiedCombDesign) : Except String String := do
  let module ← toCoreIRChecked certified
  Sparkle.Backend.Verilog.toVerilogChecked module

/-- Write checked SystemVerilog for an explicit certified design. -/
def writeVerilogFile (certified : CertifiedCombDesign) (filename : String) : IO Unit := do
  let verilog ← match toVerilogChecked certified with
    | .ok verilog => pure verilog
    | .error message => throw <| IO.userError message
  if let some directory := (System.FilePath.mk filename).parent then
    IO.FS.createDirAll directory
  IO.FS.writeFile filename verilog

private def printCoreModule (module : Sparkle.IR.AST.Module) : MetaM Unit := do
  IO.println s!"Module: {module.name}"
  IO.println s!"Parameters: {module.parameters.length}"
  for parameter in module.parameters do
    IO.println s!"  - {parameter.name} = {parameter.defaultValue}"
  IO.println s!"Inputs: {module.inputs.length}"
  for input in module.inputs do
    IO.println s!"  - {input.name}: {input.ty}"
  IO.println s!"Outputs: {module.outputs.length}"
  for output in module.outputs do
    IO.println s!"  - {output.name}: {output.ty}"
  IO.println s!"Wires: {module.wires.length}"
  for wire in module.wires do
    IO.println s!"  - {wire.name}: {wire.ty}"
  IO.println s!"Statements: {module.body.length}"
  for statement in module.body do
    IO.println s!"  {statement}"

private unsafe def evalCertifiedCombDesignImpl
    (declName : Name) : TermElabM CertifiedCombDesign :=
  Lean.Meta.evalExpr CertifiedCombDesign
    (mkConst ``CertifiedCombDesign) (mkConst declName)

@[implemented_by evalCertifiedCombDesignImpl]
private opaque evalCertifiedCombDesign
    (declName : Name) : TermElabM CertifiedCombDesign

/-- Audit every executable definition reachable from a design value.  Proof
    constants are erased and are checked separately through `collectAxioms`;
    executable helpers may not replace their kernel body at runtime. -/
private partial def auditDesignRuntimeClosure
    (env : Environment) (pending : List Name)
    (visited : Array Name := #[]) : MetaM Unit := do
  match pending with
  | [] => pure ()
  | declName :: rest =>
      if visited.contains declName then
        auditDesignRuntimeClosure env rest visited
      else
        let visited := visited.push declName
        match env.find? declName with
        | some (.defnInfo info) =>
            unless info.safety == .safe do
              throwError m!"Certified combinational design runtime closure reaches unsafe definition '{declName}'."
            if info.value.hasSorry then
              throwError m!"Certified combinational design runtime closure reaches 'sorry' in '{declName}'."
            if (Lean.Compiler.getImplementedBy? env declName).isSome then
              throwError m!"Certified combinational design runtime closure reaches unproved @[implemented_by] definition '{declName}'."
            auditDesignRuntimeClosure env
              (info.value.getUsedConstants.toList ++ rest) visited
        | some (.opaqueInfo _) =>
            throwError m!"Certified combinational design runtime closure reaches opaque executable definition '{declName}'."
        | some (.axiomInfo _) =>
            throwError m!"Certified combinational design runtime closure reaches axiom '{declName}'."
        | some (.thmInfo _) | some (.inductInfo _) | some (.ctorInfo _)
        | some (.recInfo _) | some (.quotInfo _) | none =>
            auditDesignRuntimeClosure env rest visited

/-- Reject runtime substitutions and unproved structural certificates before
    evaluating a production design declaration. -/
private def auditCertifiedDeclaration (declName : Name) : MetaM Unit := do
  let env ← getEnv
  match env.find? declName with
  | some (.defnInfo info) =>
      unless info.safety == .safe do
        throwError m!"Certified combinational design '{declName}' must be a safe definition."
      if info.value.hasSorry then
        throwError m!"Certified combinational design '{declName}' contains 'sorry'."
      if (Lean.Compiler.getImplementedBy? env declName).isSome then
        throwError m!"Certified combinational design '{declName}' has an unproved @[implemented_by] runtime replacement."
  | some _ =>
      throwError m!"Certified combinational design '{declName}' must be a transparent definition."
  | none =>
      throwError m!"Certified combinational design declaration '{declName}' is missing."
  let allowedAxioms : Array Name :=
    #[``propext, ``Classical.choice, ``Quot.sound]
  let axioms ← Lean.collectAxioms declName
  let disallowed := axioms.filter fun axiomName =>
    !allowedAxioms.contains axiomName
  unless disallowed.isEmpty do
    throwError m!"Certified combinational design '{declName}' depends on disallowed axiom(s): {disallowed}."
  auditDesignRuntimeClosure env [declName]

/-- Resolve and evaluate a closed `CertifiedCombDesign` declaration. -/
private def loadCertifiedCombDesign
    (declName : Name) : TermElabM CertifiedCombDesign := do
  let declarationType ← inferType (mkConst declName)
  unless ← isDefEq declarationType (mkConst ``CertifiedCombDesign) do
    throwError m!"'{declName}' has type '{declarationType}', but the verified combinational path requires a closed Sparkle.Compiler.CombCorrectness.CertifiedCombDesign."
  auditCertifiedDeclaration declName
  evalCertifiedCombDesign declName

/--
Print core IR produced by the proof-carrying combinational compiler.  This
command accepts only a closed declaration of type `CertifiedCombDesign`; it
never falls back to the ordinary `Signal` MetaM frontend.
-/
elab "#synthesizeComb" designId:ident : command => do
  let declName ← liftCoreM <| Lean.resolveGlobalConstNoOverload designId
  liftTermElabM do
    let certified ← loadCertifiedCombDesign declName
    let module ← match toCoreIRChecked certified with
      | .ok module => pure module
      | .error message => throwError message
    printCoreModule module
    IO.println "\n-- Verified explicit CombDesign lowering successfully generated Core IR."

/--
Print checked SystemVerilog for a certified explicit combinational design.
The command name is intentionally distinct from `#synthesizeVerilog`, whose
ordinary `Signal`/MetaM frontend is not covered by the phase-one theorem.
-/
elab "#synthesizeCombVerilog" designId:ident : command => do
  let declName ← liftCoreM <| Lean.resolveGlobalConstNoOverload designId
  liftTermElabM do
    let certified ← loadCertifiedCombDesign declName
    let verilog ← match toVerilogChecked certified with
      | .ok verilog => pure verilog
      | .error message => throwError message
    IO.println verilog
    IO.println "\n-- Explicit CombDesign-to-Core lowering is theorem-covered; Verilog emission is not."

/-- Write checked SystemVerilog for a certified explicit combinational design. -/
elab "#writeCombVerilog" designId:ident filename:str : command => do
  let declName ← liftCoreM <| Lean.resolveGlobalConstNoOverload designId
  liftTermElabM do
    let certified ← loadCertifiedCombDesign declName
    let path := filename.getString
    writeVerilogFile certified path
    IO.println s!"Written explicit combinational SystemVerilog to {path}"
    IO.println "-- Explicit CombDesign-to-Core lowering is theorem-covered; Verilog emission is not."

end Sparkle.Compiler.CombElab
