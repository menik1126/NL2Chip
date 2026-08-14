import Sparkle.IR.Builder

open Sparkle.IR.AST
open Sparkle.IR.Builder
open Sparkle.IR.Type
open Sparkle.IR.Builder.CircuitM

private def ensure (condition : Bool) (message : String) : IO Unit := do
  unless condition do
    throw (IO.userError message)

private def bodyLabel : Stmt → String
  | .assign lhs _ => s!"assign:{lhs}"
  | .register output _ _ _ _ => s!"register:{output}"
  | .memory name _ _ _ _ _ _ _ _ _ _ => s!"memory:{name}"
  | .inst moduleName instName _ _ => s!"instance:{moduleName}:{instName}"

/-- Wires and every statement kind must retain emission order even though the
    builder stores its hot-path suffix newest-first. -/
private def testMixedEmissionOrder : IO Unit := do
  let (module, (first, registered, readData, last)) := CircuitM.run "mixed_order" do
    addInput "clk" .bit
    addInput "rst" .bit
    addInput "addr" (.bitVector 4)
    addInput "din" (.bitVector 8)
    addInput "we" .bit
    let first ← makeWire "first" (.bitVector 8)
    emitAssign first (.ref "din")
    let registered ←
      emitRegister "registered" "clk" "rst" (.ref first) 0 (.bitVector 8)
    let readData ←
      emitMemory "ram" 4 8 "clk" (.ref "addr") (.ref registered)
        (.ref "we") (.ref "addr")
    emitInstance "Child" "u_child"
      [("x", .ref readData), ("y", .ref registered)]
    let last ← makeWire "last" (.bitVector 8)
    emitAssign last (.ref readData)
    return (first, registered, readData, last)

  ensure (module.wires.map (fun port => port.name) ==
      [first, registered, readData, last])
    s!"builder changed wire order: {module.wires.map (fun port => port.name)}"
  ensure (module.body.map bodyLabel ==
      [s!"assign:{first}", s!"register:{registered}", "memory:_tmp_ram_2",
       "instance:Child:u_child", s!"assign:{last}"])
    s!"builder changed statement order: {module.body.map bodyLabel}"

/-- A just-created wire remains visible to construction-time type lookup, and
    observing the full module does not repeatedly commit/copy the pending lists. -/
private def testPendingLookupAndMaterialization : IO Unit := do
  let action : CircuitM (String × Module × String) := do
    let early ← makeWire "early" (.bitVector 13)
    let observed ← getModule
    let late ← makeWire "late" (.bitVector 21)
    return (early, observed, late)
  let ((early, observed, late), state) := StateT.run action (init "lookup")

  ensure (observed.wires.map (fun port => port.name) == [early])
    "getModule omitted a pending wire"
  ensure (state.module.wires.isEmpty && state.pendingWiresRev.length == 2)
    "getModule unexpectedly committed the pending suffix"
  ensure ((state.findPort? early |>.map (fun port => port.ty)) ==
      some (.bitVector 13))
    "construction-time lookup missed the earlier pending wire"
  ensure ((state.findPort? late |>.map (fun port => port.ty)) ==
      some (.bitVector 21))
    "construction-time lookup missed the newest pending wire"
  let complete := materializeModule state
  ensure (complete.wires.map (fun port => port.name) == [early, late])
    "final materialization changed pending wire order"

  let (_, committed) := StateT.run commitPending state
  ensure (committed.pendingWiresRev.isEmpty && committed.pendingBodyRev.isEmpty &&
      committed.module.wires.map (fun port => port.name) == [early, late])
    "commitPending did not materialize and clear the pending suffix"

/-- Replacing the current module is a commit/reset boundary.  A module obtained
    from `getModule` must not have its former pending suffix appended twice. -/
private def testSetModuleBoundaryAndPrefix : IO Unit := do
  let action : CircuitM (String × String) := do
    let before ← makeWire "before" (.bitVector 8)
    emitAssign before (.const 1 8)
    let snapshot ← getModule
    setModule snapshot
    let after ← makeWire "after" (.bitVector 8)
    emitAssign after (.ref before)
    return (before, after)
  let (module, (before, after)) := CircuitM.run "set_boundary" action
  ensure (module.wires.map (fun port => port.name) == [before, after] &&
      module.body.map bodyLabel == [s!"assign:{before}", s!"assign:{after}"])
    "getModule/setModule duplicated or reordered the pending suffix"

  let prefixWire : Port := { name := "prefix", ty := .bitVector 8 }
  let prefixStmt : Stmt := .assign "prefix" (.const 0 8)
  let initial :=
    { init "prefixed" with
      module := (Module.empty "prefixed").addWire prefixWire |>.addStmt prefixStmt }
  let ((suffix : String), finalState) := StateT.run (makeWire "suffix" (.bitVector 8)) initial
  let complete := materializeModule finalState
  ensure (complete.wires.map (fun port => port.name) == ["prefix", suffix] &&
      complete.body.map bodyLabel == ["assign:prefix"])
    "materialization did not preserve a nonempty committed prefix"

/-- A moderately large circuit guards the asymptotic regression that motivated
    the pending suffix: this would make the old repeated `++ [item]` path do
    roughly two hundred million list-cell visits. -/
private def testLargeLinearAccumulation : IO Unit := do
  let count := 20000
  let module := runModule "large_linear" do
    for i in [:count] do
      let wire ← makeWire s!"w{i}" (.bitVector 8)
      emitAssign wire (.const (Int.ofNat i) 8)
  ensure (module.wires.length == count && module.body.length == count)
    "large builder accumulation returned the wrong number of wires/statements"
  ensure (module.wires.head?.map (fun port => port.name) == some "_tmp_w0_0" &&
      module.wires.getLast?.map (fun port => port.name) ==
        some s!"_tmp_w{count - 1}_{count - 1}")
    "large builder accumulation changed first/last wire order"

/-- Both a child returned by `runModule` and the top committed by `runDesign`
    must enter the design with complete, ordered wires/statements. -/
private def testHierarchicalCommit : IO Unit := do
  let child := runModule "Child" do
    addInput "x" (.bitVector 8)
    addOutput "y" (.bitVector 8)
    let internal ← makeWire "child_internal" (.bitVector 8)
    emitAssign internal (.ref "x")
    emitAssign "y" (.ref internal)

  let design := runDesign "Top" do
    addInput "x" (.bitVector 8)
    addOutput "y" (.bitVector 8)
    addModuleToDesign child
    let internal ← makeWire "top_internal" (.bitVector 8)
    emitAssign internal (.ref "x")
    emitInstance "Child" "u_child"
      [("x", .ref internal), ("y", .ref "y")]

  ensure (design.modules.map (fun module => module.name) == ["Child", "Top"])
    "hierarchical design commit changed module order"
  match design.findModule "Child", design.findModule "Top" with
  | some committedChild, some committedTop =>
      ensure (committedChild.wires.length == 1 && committedChild.body.length == 2)
        "child module was committed without its pending netlist suffix"
      ensure (committedTop.wires.length == 1 &&
          committedTop.body.map bodyLabel ==
            [s!"assign:{committedTop.wires.head!.name}", "instance:Child:u_child"])
        "top module was committed without its ordered pending netlist suffix"
  | _, _ => throw (IO.userError "hierarchical design omitted child or top module")

/-- The linear sanitized-name index retains exact duplicate and error-priority
    behavior of the prior distinct/count implementation. -/
private def testSanitizedNameValidation : IO Unit := do
  let duplicateNet : Module :=
    { (Module.empty "duplicate_net") with
      inputs := [{ name := "shared", ty := .bit }]
      outputs := [{ name := "shared", ty := .bit }] }
  ensure ((duplicateNet.validateSanitizedNames id).isOk)
    "exact duplicate port/wire declarations no longer share one IR net"

  let priority : Module :=
    { (Module.empty "priority") with
      parameters := [{ name := "first", defaultValue := 1 }]
      inputs := [{ name := "empty", ty := .bit }, { name := "alias", ty := .bit }] }
  let sanitize := fun name =>
    if name == "first" || name == "alias" then "same"
    else if name == "empty" then "" else name
  match priority.validateSanitizedNames sanitize with
  | .ok _ => throw (IO.userError "sanitized collision was accepted")
  | .error message =>
      ensure (message ==
          "module 'priority' has colliding SystemVerilog identifier 'same' after sanitizing parameter 'first'")
        s!"sanitized-name error precedence/message changed: {message}"

def main : IO UInt32 := do
  testMixedEmissionOrder
  testPendingLookupAndMaterialization
  testSetModuleBoundaryAndPrefix
  testHierarchicalCommit
  testSanitizedNameValidation
  testLargeLinearAccumulation
  IO.println "Builder accumulation tests passed"
  return 0
