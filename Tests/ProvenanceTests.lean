import Sparkle.Compiler.Elab
import Sparkle.Backend.Verilog
import Sparkle.Backend.CppSim
import Sparkle.IR.Optimize
import Tests.ProvenanceFixtures

open Lean
open Sparkle.Compiler.Elab
open Sparkle.IR.AST

namespace Tests.ProvenanceTests

structure Synthesized where
  namedLoop : Sparkle.IR.AST.Module
  fvarAlias : Sparkle.IR.AST.Module
  alreadyNamed : Sparkle.IR.AST.Module
  nameBoundary : Sparkle.IR.AST.Module
  shadowed : Sparkle.IR.AST.Module
  numericSuffixBoundary : Sparkle.IR.AST.Module
  nestedSameName : Sparkle.IR.AST.Module
  namedMemory : Sparkle.IR.AST.Module
  wideAlias : Sparkle.IR.AST.Module
  unpackedArrayAlias : Sparkle.IR.AST.Module
  inlineBoolConversions : Sparkle.IR.AST.Module

private def synthesizeOne (name : Name) : MetaM Sparkle.IR.AST.Module := do
  return (← synthesizeCombinational name).1

private def synthesizeFixtures : MetaM Synthesized := do
  return {
    namedLoop := ← synthesizeOne `Tests.ProvenanceFixtures.namedLoop
    fvarAlias := ← synthesizeOne `Tests.ProvenanceFixtures.fvarAlias
    alreadyNamed := ← synthesizeOne `Tests.ProvenanceFixtures.alreadyNamed
    nameBoundary := ← synthesizeOne `Tests.ProvenanceFixtures.nameBoundary
    shadowed := ← synthesizeOne `Tests.ProvenanceFixtures.shadowed
    numericSuffixBoundary :=
      ← synthesizeOne `Tests.ProvenanceFixtures.numericSuffixBoundary
    nestedSameName := ← synthesizeOne `Tests.ProvenanceFixtures.nestedSameName
    namedMemory := ← synthesizeOne `Tests.ProvenanceFixtures.namedMemory
    wideAlias := ← synthesizeOne `Tests.ProvenanceFixtures.wideAlias
    unpackedArrayAlias := ← synthesizeOne `Tests.ProvenanceFixtures.unpackedArrayAlias
    inlineBoolConversions :=
      ← synthesizeOne `Tests.ProvenanceFixtures.inlineBoolConversions
  }

private def ensure (condition : Bool) (message : String) : IO Unit := do
  unless condition do
    throw (IO.userError message)

private def containsSubstr (value needle : String) : Bool :=
  (value.splitOn needle).length > 1

private def wireCount (module_ : Sparkle.IR.AST.Module) (name : String) : Nat :=
  module_.wires.countP (fun wire => wire.name == name)

private def hasAssignRef (module_ : Sparkle.IR.AST.Module) (lhs rhs : String) : Bool :=
  module_.body.any fun statement =>
    match statement with
    | .assign actualLhs (.ref actualRhs) =>
        actualLhs == lhs && actualRhs == rhs
    | _ => false

private def hasAssignRefFrom (module_ : Sparkle.IR.AST.Module) (lhs : String)
    (sourcePrefix : String) : Bool :=
  module_.body.any fun statement =>
    match statement with
    | .assign actualLhs (.ref actualRhs) =>
        actualLhs == lhs && actualRhs.startsWith sourcePrefix
    | _ => false

private def hasSelfAssign (module_ : Sparkle.IR.AST.Module) : Bool :=
  module_.body.any fun statement =>
    match statement with
    | .assign lhs (.ref rhs) => lhs == rhs
    | _ => false

private def hasMemoryABI (module_ : Sparkle.IR.AST.Module)
    (memoryName readDataName : String) : Bool :=
  module_.body.any fun statement =>
    match statement with
    | .memory name _ _ _ _ _ _ _ _ readData _ =>
        name == memoryName && readData == readDataName
    | _ => false

private def hasConcatRefs (module_ : Sparkle.IR.AST.Module) (high low : String) : Bool :=
  module_.body.any fun statement =>
    match statement with
    | .assign _ (.concat [.ref actualHigh, .ref actualLow]) =>
        actualHigh == high && actualLow == low
    | _ => false

private def hasMuxOrEqAssign (module_ : Sparkle.IR.AST.Module) : Bool :=
  module_.body.any fun statement =>
    match statement with
    | .assign _ (.op op _) => op == .mux || op == .eq
    | _ => false

private def checkRawIR (modules : Synthesized) : IO Unit := do
  ensure (wireCount modules.namedLoop "_gen_state" == 1)
    "named Signal.loop result did not materialize exactly one _gen_state alias"
  ensure (hasAssignRefFrom modules.namedLoop "_gen_state" "_tmp_loop_body_")
    "named Signal.loop result alias does not reference the lowered loop body"
  ensure (hasAssignRef modules.namedLoop "out" "_gen_state")
    "named Signal.loop output bypassed its source-level alias"

  ensure (wireCount modules.fvarAlias "_gen_copied" == 1)
    "hardware let bound to an fvar did not materialize _gen_copied"
  ensure (hasAssignRef modules.fvarAlias "_gen_copied" "_gen_a")
    "fvar hardware-let alias does not reference its source wire"
  ensure (hasAssignRef modules.fvarAlias "out" "_gen_copied")
    "fvar hardware-let output bypassed its source-level alias"

  ensure (wireCount modules.alreadyNamed "_gen_sum" == 1)
    "already-named arithmetic result was duplicated or omitted"
  ensure (wireCount modules.alreadyNamed "_gen_sum_1" == 0)
    "already-correct arithmetic result received a redundant alias"

  ensure (wireCount modules.nameBoundary "_gen_foo_helper" == 1)
    "nested helper lost its own stable source name"
  ensure (wireCount modules.nameBoundary "_gen_foo" == 1)
    "_gen_foo_helper was incorrectly accepted as the exact name for foo"
  ensure (hasAssignRef modules.nameBoundary "_gen_foo" "_gen_foo_helper")
    "outer foo alias does not reference its nested foo_helper"

  ensure (wireCount modules.numericSuffixBoundary "_gen_output" == 1 &&
      wireCount modules.numericSuffixBoundary "_gen_output_1" == 1)
    "numeric helper suffix was mistaken for the outer output binding"
  ensure (hasAssignRef modules.numericSuffixBoundary "_gen_output_1" "_gen_a" &&
      hasAssignRef modules.numericSuffixBoundary "_gen_output" "_gen_output_1")
    "numeric-suffix nested aliases do not preserve both binding boundaries"
  ensure (hasAssignRef modules.numericSuffixBoundary "out" "_gen_output")
    "numeric-suffix fixture output bypassed its outer binding"

  ensure (wireCount modules.nestedSameName "_gen_foo" == 1 &&
      wireCount modules.nestedSameName "_gen_foo_1" == 1)
    "same-name nested lets collapsed into one provenance wire"
  ensure (hasAssignRef modules.nestedSameName "_gen_foo" "_gen_a" &&
      hasAssignRef modules.nestedSameName "_gen_foo_1" "_gen_foo")
    "same-name nested aliases do not preserve both lexical boundaries"
  ensure (hasAssignRef modules.nestedSameName "out" "_gen_foo_1")
    "same-name nested fixture output bypassed its outer binding"

  ensure (hasMemoryABI modules.namedMemory
      "_gen_storage" "_gen_storage_rdata")
    "hardware-let provenance changed the memory instance/read-port ABI"
  ensure (wireCount modules.namedMemory "_gen_storage_1" == 1 &&
      hasAssignRef modules.namedMemory "_gen_storage_1" "_gen_storage_rdata" &&
      hasAssignRef modules.namedMemory "out" "_gen_storage_1")
    "packed memory read result did not receive a distinct safe alias"

  ensure (wireCount modules.wideAlias "_gen_copied_wide" == 1 &&
      hasAssignRef modules.wideAlias "_gen_copied_wide" "_gen_a" &&
      hasAssignRef modules.wideAlias "out" "_gen_copied_wide")
    "wide packed hardware-let alias was omitted or bypassed"

  ensure (wireCount modules.shadowed "_gen_foo" == 1 &&
      wireCount modules.shadowed "_gen_foo_1" == 1)
    "shadowed hardware lets did not receive distinct stable names"
  ensure (hasAssignRef modules.shadowed "_gen_foo" "_gen_a" &&
      hasAssignRef modules.shadowed "_gen_foo_1" "_gen_foo")
    "shadowed hardware-let aliases do not preserve lexical provenance"
  ensure (hasAssignRef modules.shadowed "out" "_gen_foo_1")
    "shadowed result output bypassed the innermost alias"

  ensure (wireCount modules.unpackedArrayAlias "_gen_copied_array" == 0)
    "unpacked-array lets were unexpectedly added to the packed alias contract"
  ensure (hasAssignRef modules.unpackedArrayAlias "out" "_gen_a")
    "unpacked-array alias handling changed the pre-existing direct output path"

  ensure (hasConcatRefs modules.inlineBoolConversions "_gen_flag" "_gen_bit")
    "inline Bool/BV1 conversions did not preserve direct tuple-leaf references"
  ensure (!hasMuxOrEqAssign modules.inlineBoolConversions)
    "Bool/BV1 representation casts still lowered into mux/equality logic"

  for module_ in [modules.namedLoop, modules.fvarAlias, modules.alreadyNamed,
      modules.nameBoundary, modules.shadowed, modules.numericSuffixBoundary,
      modules.nestedSameName, modules.namedMemory, modules.wideAlias,
      modules.unpackedArrayAlias, modules.inlineBoolConversions] do
    ensure (!hasSelfAssign module_)
      s!"provenance aliasing emitted a self assignment in {module_.name}"

private def checkBackendsAndOptimizer (modules : Synthesized) : IO Unit := do
  let aliasVerilog := Sparkle.Backend.Verilog.toVerilog modules.fvarAlias
  ensure (containsSubstr aliasVerilog "assign _gen_copied = _gen_a;")
    "Verilog backend did not retain the fvar provenance alias"

  let boolVerilog :=
    Sparkle.Backend.Verilog.toVerilog modules.inlineBoolConversions
  ensure (containsSubstr boolVerilog "{_gen_flag, _gen_bit}")
    "Verilog tuple output lost direct Bool/BV1 leaf provenance"

  let nestedVerilog :=
    Sparkle.Backend.Verilog.toVerilog modules.numericSuffixBoundary
  ensure (containsSubstr nestedVerilog "assign _gen_output_1 = _gen_a;" &&
      containsSubstr nestedVerilog "assign _gen_output = _gen_output_1;")
    "Verilog lost one numeric-suffix nested binding boundary"

  let memoryVerilog := Sparkle.Backend.Verilog.toVerilog modules.namedMemory
  ensure (containsSubstr memoryVerilog "_gen_storage" &&
      containsSubstr memoryVerilog "_gen_storage_rdata" &&
      containsSubstr memoryVerilog "assign _gen_storage_1 = _gen_storage_rdata;")
    "Verilog changed memory ABI names or omitted its packed read alias"

  let wideVerilog := Sparkle.Backend.Verilog.toVerilog modules.wideAlias
  ensure (containsSubstr wideVerilog "_gen_copied_wide" &&
      containsSubstr wideVerilog "assign _gen_copied_wide = _gen_a;")
    "Verilog omitted the wide packed provenance alias"

  let optimized := Sparkle.IR.Optimize.optimizeModule modules.fvarAlias
  ensure (wireCount optimized "_gen_copied" == 1 &&
      hasAssignRef optimized "_gen_copied" "_gen_a" &&
      hasAssignRef optimized "out" "_gen_copied")
    "optimizer removed or bypassed a final-used stable provenance alias"
  ensure (!hasSelfAssign optimized)
    "optimizer introduced a self assignment in the provenance fixture"

  let optimizedWide := Sparkle.IR.Optimize.optimizeModule modules.wideAlias
  ensure (wireCount optimizedWide "_gen_copied_wide" == 1 &&
      hasAssignRef optimizedWide "_gen_copied_wide" "_gen_a" &&
      hasAssignRef optimizedWide "out" "_gen_copied_wide" &&
      !hasSelfAssign optimizedWide)
    "optimizer removed, bypassed, or corrupted the wide provenance alias"

  let optimizedMemory := Sparkle.IR.Optimize.optimizeModule modules.namedMemory
  ensure (hasMemoryABI optimizedMemory "_gen_storage" "_gen_storage_rdata" &&
      wireCount optimizedMemory "_gen_storage_1" == 1 &&
      hasAssignRef optimizedMemory "_gen_storage_1" "_gen_storage_rdata" &&
      hasAssignRef optimizedMemory "out" "_gen_storage_1" &&
      !hasSelfAssign optimizedMemory)
    "optimizer changed the memory ABI or its packed read provenance alias"

  let design : Design := {
    topModule := optimized.name
    modules := [optimized]
  }
  match Sparkle.Backend.CppSim.toCppSimJITChecked design with
  | .error message =>
      throw (IO.userError s!"CppSim JIT rejected packed provenance aliases: {message}")
  | .ok cpp =>
      ensure (containsSubstr cpp "return \"_gen_copied\";")
        "CppSim JIT reflection did not expose the stable provenance alias"

  match Sparkle.Backend.CppSim.toCppSimChecked optimizedWide with
  | .ok _ =>
      throw (IO.userError "CppSim unexpectedly accepted an 80-bit top-level value")
  | .error message =>
      ensure (containsSubstr message "80-bit value" &&
          containsSubstr message "above 64 bits")
        "CppSim returned the wrong controlled wide-value ABI rejection"

  let memoryDesign : Design := {
    topModule := optimizedMemory.name
    modules := [optimizedMemory]
  }
  match Sparkle.Backend.CppSim.toCppSimJITChecked memoryDesign with
  | .error message =>
      throw (IO.userError s!"CppSim JIT rejected the memory ABI fixture: {message}")
  | .ok cpp =>
      ensure (containsSubstr cpp "s->_gen_storage[" &&
          containsSubstr cpp "return \"_gen_storage_1\";" &&
          containsSubstr cpp "jit_num_memories()" &&
          containsSubstr cpp "return 1;")
        "CppSim JIT changed the indexed memory ABI or omitted its read provenance alias"

def main : IO UInt32 := do
  Lean.initSearchPath (← Lean.findSysroot)
  let env ← Lean.importModules
    #[{module := `Sparkle.Compiler.Elab},
      {module := `Tests.ProvenanceFixtures}]
    {}
    (trustLevel := 1024)
  let coreContext : Core.Context := {
    fileName := "<provenance-tests>"
    fileMap := default
  }
  let coreState : Core.State := { env }
  let (modules, _) ← Lean.Meta.MetaM.toIO synthesizeFixtures coreContext coreState

  checkRawIR modules
  checkBackendsAndOptimizer modules
  IO.println "PASS: hardware-let output provenance"
  return 0

end Tests.ProvenanceTests

def main : IO UInt32 :=
  Tests.ProvenanceTests.main
