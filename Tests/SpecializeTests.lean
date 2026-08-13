/-
  Smoke tests for native-parameter IR specialization.

  These fixtures are deliberately constructed at the IR level.  That keeps the
  tests focused on the specialization contract instead of coupling them to the
  Lean elaborator's surface syntax.
-/

import Sparkle.IR.Specialize
import Sparkle.Backend.CppSim
import Tools.SVParser.Verify

open Sparkle.IR.AST Sparkle.IR.Type
open Sparkle.IR.Specialize

private def ensure (condition : Bool) (message : String) : IO Unit :=
  unless condition do
    throw (IO.userError message)

private def requireOk : Except String α → IO α
  | .ok value => pure value
  | .error message => throw (IO.userError message)

private def isError : Except String α → Bool
  | .ok _ => false
  | .error _ => true

private def contains (text fragment : String) : Bool :=
  decide ((text.splitOn fragment).length > 1)

private def allPorts (module_ : Module) : List Port :=
  module_.inputs ++ module_.outputs ++ module_.wires

private def portWidth? (module_ : Module) (name : String) : Option Nat :=
  (allPorts module_).find? (fun port => port.name == name)
    |>.bind (fun port => port.ty.bitWidth?)

private def requireTop (design : Design) : IO Module :=
  match design.findModule design.topModule with
  | some top => pure top
  | none => throw (IO.userError s!"specialized top '{design.topModule}' is absent")

/-- Every successful specialization result must be acceptable to concrete-only
    consumers: it has no declarations, symbolic dimensions, or instance
    overrides left. -/
private def checkConcreteModule (module_ : Module) : IO Unit := do
  ensure module_.parameters.isEmpty
    s!"{module_.name}: parameter declarations were not erased"
  for dimension in module_.dimensionExpressions do
    ensure dimension.isConcrete
      s!"{module_.name}: symbolic dimension remains after specialization"
  for (role, dimension) in module_.positiveDimensions do
    match dimension.toNat? with
    | some width => ensure (width > 0) s!"{role}: non-positive dimension"
    | none => throw (IO.userError s!"{role}: symbolic dimension remains")
  for statement in module_.body do
    match statement with
    | .inst _ instanceName _ overrides =>
      ensure overrides.isEmpty
        s!"{module_.name}.{instanceName}: parameter overrides were not consumed"
    | _ => pure ()

private def checkConcreteDesign (design : Design) : IO Unit :=
  for module_ in design.modules do
    checkConcreteModule module_

-- Single-module width sweep ---------------------------------------------------

private def paramAdd : Module :=
  let width : DimExpr := .param "W"
  { (Module.empty "param_add") with
    parameters := [{ name := "W", defaultValue := 8 }]
    inputs :=
      [{ name := "a", ty := .bitVector width },
       { name := "b", ty := .bitVector width }]
    outputs := [{ name := "y", ty := .bitVector width }]
    body := [.assign "y" (.op .add [.ref "a", .ref "b"])] }

private def addDesign : Design :=
  { topModule := paramAdd.name, modules := [paramAdd] }

-- Derived dimensions and parameter-sized expression dimensions ---------------

private def paramDerived : Module :=
  let width : DimExpr := .param "W"
  let derived := width + 1
  { (Module.empty "param_derived") with
    parameters := [{ name := "W", defaultValue := 8 }]
    inputs := [{ name := "x", ty := .bitVector derived }]
    outputs := [{ name := "y", ty := .bitVector derived }]
    wires := [{ name := "one", ty := .bitVector derived }]
    body :=
      [.assign "one" (.const 1 derived),
       .assign "y" (.op .xor [.ref "x", .ref "one"])] }

private def derivedDesign : Design :=
  { topModule := paramDerived.name, modules := [paramDerived] }

-- Sequential fixture used by both CppSim and verification-model consumers -----

private def paramSeq : Module :=
  let width : DimExpr := .param "W"
  { (Module.empty "param_seq") with
    parameters := [{ name := "W", defaultValue := 8 }]
    inputs :=
      [{ name := "clk", ty := .bit },
       { name := "rst", ty := .bit },
       { name := "x", ty := .bitVector width }]
    outputs := [{ name := "y", ty := .bitVector width }]
    wires := [{ name := "q", ty := .bitVector width }]
    body :=
      [.register "q" "clk" "rst" (.ref "x") 0,
       .assign "y" (.ref "q")] }

private def seqDesign : Design :=
  { topModule := paramSeq.name, modules := [paramSeq] }

-- Independently parameterized address and data dimensions ---------------------

private def paramMemory : Module :=
  let addrWidth : DimExpr := .param "AW"
  let dataWidth : DimExpr := .param "DW"
  { (Module.empty "param_memory") with
    parameters :=
      [{ name := "AW", defaultValue := 4 },
       { name := "DW", defaultValue := 8 }]
    inputs :=
      [{ name := "clk", ty := .bit },
       { name := "rst", ty := .bit },
       { name := "wr_addr", ty := .bitVector addrWidth },
       { name := "wr_data", ty := .bitVector dataWidth },
       { name := "wr_en", ty := .bit },
       { name := "rd_addr", ty := .bitVector addrWidth }]
    outputs := [{ name := "rd_data", ty := .bitVector dataWidth }]
    wires := [{ name := "rd", ty := .bitVector dataWidth }]
    body :=
      [.memory "storage" addrWidth dataWidth "clk"
        (.ref "wr_addr") (.ref "wr_data") (.ref "wr_en")
        (.ref "rd_addr") "rd" true,
       .assign "rd_data" (.ref "rd")] }

private def memoryDesign : Design :=
  { topModule := paramMemory.name, modules := [paramMemory] }

-- One generic child used with two different concrete environments -------------

private def paramChild : Module :=
  let width : DimExpr := .param "W"
  { (Module.empty "param_child") with
    parameters := [{ name := "W", defaultValue := 8 }]
    inputs := [{ name := "x", ty := .bitVector width }]
    outputs := [{ name := "y", ty := .bitVector width }]
    body := [.assign "y" (.ref "x")] }

private def unusedParamModule : Module :=
  let width : DimExpr := .param "UNUSED_W"
  { (Module.empty "unused_param_module") with
    parameters := [{ name := "UNUSED_W", defaultValue := 12 }]
    inputs := [{ name := "x", ty := .bitVector width }]
    outputs := [{ name := "y", ty := .bitVector width }]
    body := [.assign "y" (.ref "x")] }

private def paramTop : Module :=
  let width : DimExpr := .param "N"
  { (Module.empty "param_top") with
    parameters := [{ name := "N", defaultValue := 8 }]
    inputs :=
      [{ name := "a", ty := .bitVector width },
       { name := "b", ty := .bitVector (width + 1) }]
    outputs :=
      [{ name := "z0", ty := .bitVector width },
       { name := "z1", ty := .bitVector (width + 1) }]
    body :=
      [.inst "param_child" "u0"
        [("x", .ref "a"), ("y", .ref "z0")] [("W", width)],
       .inst "param_child" "u1"
        [("x", .ref "b"), ("y", .ref "z1")] [("W", width + 1)]] }

private def hierarchyDesign : Design :=
  { topModule := paramTop.name,
    modules := [paramChild, unusedParamModule, paramTop] }

private def reuseTop : Module :=
  let width : DimExpr := .param "N"
  { (Module.empty "reuse_top") with
    parameters := [{ name := "N", defaultValue := 8 }]
    inputs :=
      [{ name := "a", ty := .bitVector width },
       { name := "b", ty := .bitVector width }]
    outputs :=
      [{ name := "z0", ty := .bitVector width },
       { name := "z1", ty := .bitVector width }]
    body :=
      [.inst "param_child" "u0"
        [("x", .ref "a"), ("y", .ref "z0")] [("W", width)],
       .inst "param_child" "u1"
        [("x", .ref "b"), ("y", .ref "z1")] [("W", width)]] }

private def reuseDesign : Design :=
  { topModule := reuseTop.name, modules := [paramChild, reuseTop] }

-- Three-level specialization ensures overrides are resolved at every parent
-- environment, rather than only at the design root.
private def nestedGrandchild : Module :=
  let width : DimExpr := .param "G"
  { (Module.empty "nested_grandchild") with
    parameters := [{ name := "G", defaultValue := 8 }]
    inputs := [{ name := "x", ty := .bitVector width }]
    outputs := [{ name := "y", ty := .bitVector width }]
    body := [.assign "y" (.ref "x")] }

private def nestedChild : Module :=
  let width : DimExpr := .param "M"
  { (Module.empty "nested_child") with
    parameters := [{ name := "M", defaultValue := 8 }]
    inputs := [{ name := "x", ty := .bitVector (width + 1) }]
    outputs := [{ name := "y", ty := .bitVector (width + 1) }]
    wires := [{ name := "marker", ty := .bitVector width }]
    body :=
      [.inst "nested_grandchild" "u_grandchild"
        [("x", .ref "x"), ("y", .ref "y")] [("G", width + 1)]] }

private def nestedTop : Module :=
  let width : DimExpr := .param "N"
  { (Module.empty "nested_top") with
    parameters := [{ name := "N", defaultValue := 8 }]
    inputs := [{ name := "x", ty := .bitVector (width + 2) }]
    outputs := [{ name := "y", ty := .bitVector (width + 2) }]
    body :=
      [.inst "nested_child" "u_child"
        [("x", .ref "x"), ("y", .ref "y")] [("M", width + 1)]] }

private def nestedDesign : Design :=
  { topModule := nestedTop.name,
    modules := [nestedGrandchild, nestedChild, nestedTop] }

-- Fail-closed fixtures --------------------------------------------------------

private def missingModuleTop : Module :=
  { (Module.empty "missing_module_top") with
    body := [.inst "does_not_exist" "u_missing" [] []] }

private def missingModuleDesign : Design :=
  { topModule := missingModuleTop.name, modules := [missingModuleTop] }

private def residualOverrideTop : Module :=
  { (Module.empty "residual_override_top") with
    body :=
      [.inst "param_child" "u_residual" [] [("W", .param "GHOST")]] }

private def residualOverrideDesign : Design :=
  { topModule := residualOverrideTop.name,
    modules := [paramChild, residualOverrideTop] }

private def unknownChildOverrideTop : Module :=
  { (Module.empty "unknown_child_override_top") with
    body :=
      [.inst "param_child" "u_unknown" [] [("NOT_W", .literal 3)]] }

private def unknownChildOverrideDesign : Design :=
  { topModule := unknownChildOverrideTop.name,
    modules := [paramChild, unknownChildOverrideTop] }

private def duplicateChildOverrideTop : Module :=
  { (Module.empty "duplicate_child_override_top") with
    body :=
      [.inst "param_child" "u_duplicate" []
        [("W", .literal 3), ("W", .literal 4)]] }

private def duplicateChildOverrideDesign : Design :=
  { topModule := duplicateChildOverrideTop.name,
    modules := [paramChild, duplicateChildOverrideTop] }

private def duplicateDeclarationModule : Module :=
  let width : DimExpr := .param "W"
  { (Module.empty "duplicate_declaration") with
    parameters :=
      [{ name := "W", defaultValue := 3 },
       { name := "W", defaultValue := 4 }]
    inputs := [{ name := "x", ty := .bitVector width }]
    outputs := [{ name := "y", ty := .bitVector width }]
    body := [.assign "y" (.ref "x")] }

private def duplicateDeclarationDesign : Design :=
  { topModule := duplicateDeclarationModule.name,
    modules := [duplicateDeclarationModule] }

private def zeroIndexModule : Module :=
  let start : DimExpr := .param "Start"
  { (Module.empty "zero_index") with
    parameters := [{ name := "Start", defaultValue := 1 }]
    inputs := [{ name := "x", ty := .bitVector 8 }]
    outputs := [{ name := "y", ty := .bitVector 8 }]
    body :=
      [.assign "y" (.slice (.ref "x") (start + 7) start)] }

private def zeroIndexDesign : Design :=
  { topModule := zeroIndexModule.name, modules := [zeroIndexModule] }

private def reversedSliceModule : Module :=
  { (Module.empty "reversed_slice") with
    parameters :=
      [{ name := "Hi", defaultValue := 7 },
       { name := "Lo", defaultValue := 0 }]
    inputs := [{ name := "x", ty := .bitVector 8 }]
    outputs := [{ name := "y", ty := .bitVector 8 }]
    body := [.assign "y" (.slice (.ref "x") (.param "Hi") (.param "Lo"))] }

private def reversedSliceDesign : Design :=
  { topModule := reversedSliceModule.name, modules := [reversedSliceModule] }

private def parameterizedPrimitive : Module :=
  let width : DimExpr := .param "W"
  { name := "parameterized_primitive",
    parameters := [{ name := "W", defaultValue := 8 }],
    inputs := [{ name := "x", ty := .bitVector width }],
    outputs := [{ name := "y", ty := .bitVector width }],
    wires := [], body := [], assertions := [], isPrimitive := true }

private def parameterizedPrimitiveDesign : Design :=
  { topModule := parameterizedPrimitive.name,
    modules := [parameterizedPrimitive] }

private def concretePrimitive : Module :=
  Module.primitive "vendor_primitive" [{ name := "x", ty := .bit }]
    [{ name := "y", ty := .bit }]

private def concretePrimitiveTop : Module :=
  { (Module.empty "concrete_primitive_top") with
    inputs := [{ name := "x", ty := .bit }]
    outputs := [{ name := "y", ty := .bit }]
    body := [.inst concretePrimitive.name "u_vendor"
      [("x", .ref "x"), ("y", .ref "y")] []] }

private def concretePrimitiveDesign : Design :=
  { topModule := concretePrimitiveTop.name,
    modules := [concretePrimitive, concretePrimitiveTop] }

private def collisionChild : Module :=
  { (Module.empty "collision_child") with
    parameters := [{ name := "W", defaultValue := 8 }]
    inputs := [{ name := "x", ty := .bitVector (.param "W") }]
    outputs := [{ name := "y", ty := .bitVector (.param "W") }]
    body := [.assign "y" (.ref "x")] }

private def collisionTop : Module :=
  { (Module.empty "collision_top") with
    inputs := [{ name := "x", ty := .bitVector 8 }]
    outputs := [{ name := "y", ty := .bitVector 8 }]
    body := [.inst collisionChild.name "u_child"
      [("x", .ref "x"), ("y", .ref "y")] [("W", 8)]] }

private def rawSanitizedCollision : Module :=
  Module.empty "collision_child--specialized-0"

private def collisionDesign : Design :=
  { topModule := collisionTop.name,
    modules := [collisionChild, rawSanitizedCollision, collisionTop] }

private def cycleA : Module :=
  { (Module.empty "cycle_a") with
    body := [.inst "cycle_b" "u_b" [] []] }

private def cycleB : Module :=
  { (Module.empty "cycle_b") with
    body := [.inst "cycle_a" "u_a" [] []] }

private def cycleDesign : Design :=
  { topModule := cycleA.name, modules := [cycleA, cycleB] }

private def missingTopDesign : Design :=
  { topModule := "absent_top", modules := [paramAdd] }

-- Checks ---------------------------------------------------------------------

private def checkWidth (width : Nat) : IO Unit := do
  let specialized ← requireOk (specializeDesign addDesign [("W", width)])
  checkConcreteDesign specialized
  let top ← requireTop specialized
  ensure (portWidth? top "a" == some width)
    s!"W={width}: input width mismatch"
  ensure (portWidth? top "y" == some width)
    s!"W={width}: output width mismatch"
  -- CppSim's current wide-value ABI is not a functional 257-bit simulator.
  -- Exercise the concrete C++ consumer only in its supported scalar range;
  -- W=257 still checks specialization itself and the concrete SV/Verify paths.
  if width ≤ 64 then
    let cpp ← requireOk
      (Sparkle.Backend.CppSim.toCppSimDesignChecked specialized)
    ensure (!(contains cpp "#error"))
      s!"W={width}: CppSim emitted #error"
    ensure (!(contains cpp "skipped:"))
      s!"W={width}: CppSim skipped a supported-width assignment"
  else
    ensure (isError
      (Sparkle.Backend.CppSim.validateSpecializedDesign specialized))
      s!"W={width}: CppSim accepted a wide value it cannot execute"

private def checkDerived : IO Unit := do
  let specialized ← requireOk
    (specializeDesign derivedDesign [("W", 17)])
  checkConcreteDesign specialized
  let top ← requireTop specialized
  ensure (portWidth? top "x" == some 18)
    "derived W+1 input did not become 18"
  ensure (portWidth? top "one" == some 18)
    "derived W+1 wire did not become 18"
  match top.body with
  | .assign "one" (.const 1 width) :: _ =>
    ensure (width.toNat? == some 18)
      "derived expression-constant width did not become 18"
  | _ => throw (IO.userError "derived constant statement was lost")

private def checkHierarchy : IO Unit := do
  let specialized ← requireOk
    (specializeDesign hierarchyDesign [("N", 17)])
  checkConcreteDesign specialized
  ensure (!(specialized.modules.any (fun module_ =>
    module_.name == unusedParamModule.name)))
    "unreachable generic module was retained"
  let top ← requireTop specialized
  ensure (portWidth? top "a" == some 17) "top N width mismatch"
  ensure (portWidth? top "b" == some 18) "top N+1 width mismatch"
  let instances := top.body.filterMap fun statement =>
    match statement with
    | .inst moduleName instanceName _ overrides =>
      some (moduleName, instanceName, overrides)
    | _ => none
  ensure (instances.length == 2)
    "expected two specialized child instances"
  for (_, instanceName, overrides) in instances do
    ensure overrides.isEmpty
      s!"{instanceName}: parameter override was not consumed"
  let childWidths := instances.filterMap fun (moduleName, _, _) =>
    match specialized.findModule moduleName with
    | some child => portWidth? child "x"
    | none => none
  ensure
    (childWidths.length == 2 &&
      childWidths.contains 17 && childWidths.contains 18)
    "the W=N and W=N+1 child variants were not specialized independently"
  match instances with
  | first :: second :: _ =>
    ensure (first.1 != second.1)
      "distinct child environments incorrectly reused one module"
  | _ => throw (IO.userError "child instance list unexpectedly changed")
  let _ ← requireOk
    (Sparkle.Backend.CppSim.toCppSimDesignChecked specialized)

private def checkCloneReuse : IO Unit := do
  let specialized ← requireOk
    (specializeDesign reuseDesign [("N", 17)])
  checkConcreteDesign specialized
  let top ← requireTop specialized
  let childNames := top.body.filterMap fun statement =>
    match statement with
    | .inst moduleName _ _ _ => some moduleName
    | _ => none
  match childNames with
  | [first, second] =>
    ensure (first == second)
      "identical child environments did not reuse one specialized clone"
    ensure (specialized.modules.countP (fun module_ =>
      module_.name == first) == 1)
      "a reused child specialization was emitted more than once"
  | _ => throw (IO.userError "clone-reuse fixture lost an instance")
  ensure (specialized.modules.length == 2)
    "clone-reuse design should contain exactly one child and one top"

private def checkNestedHierarchy : IO Unit := do
  let specialized ← requireOk
    (specializeDesign nestedDesign [("N", 3)])
  checkConcreteDesign specialized
  ensure (specialized.modules.length == 3)
    "three-level hierarchy did not produce exactly three concrete modules"
  let top ← requireTop specialized
  ensure (portWidth? top "x" == some 5)
    "nested top N+2 width mismatch"
  let childName ← match top.body with
    | [.inst moduleName "u_child" _ []] => pure moduleName
    | _ => throw (IO.userError "nested top instance was not rewritten")
  let child ← match specialized.findModule childName with
    | some module_ => pure module_
    | none => throw (IO.userError "specialized nested child is missing")
  ensure (portWidth? child "x" == some 5)
    "nested child M+1 width mismatch"
  ensure (portWidth? child "marker" == some 4)
    "nested child did not receive M=N+1"
  let grandchildName ← match child.body with
    | [.inst moduleName "u_grandchild" _ []] => pure moduleName
    | _ => throw (IO.userError "nested grandchild instance was not rewritten")
  let grandchild ← match specialized.findModule grandchildName with
    | some module_ => pure module_
    | none => throw (IO.userError "specialized nested grandchild is missing")
  ensure (portWidth? grandchild "x" == some 5)
    "nested grandchild did not receive G=M+1"
  let _ ← requireOk
    (Sparkle.Backend.CppSim.toCppSimDesignChecked specialized)

private def checkMemory : IO Unit := do
  let specialized ← requireOk
    (specializeDesign memoryDesign [("AW", 3), ("DW", 17)])
  checkConcreteDesign specialized
  let top ← requireTop specialized
  ensure (portWidth? top "wr_addr" == some 3)
    "memory AW port mismatch"
  ensure (portWidth? top "wr_data" == some 17)
    "memory DW port mismatch"
  let dimensions := top.body.filterMap fun statement =>
    match statement with
    | .memory _ addrWidth dataWidth .. =>
      some (addrWidth.toNat?, dataWidth.toNat?)
    | _ => none
  ensure (dimensions == [(some 3, some 17)])
    "memory statement dimensions were not specialized"
  let _ ← requireOk
    (Sparkle.Backend.CppSim.toCppSimDesignChecked specialized)

private def checkDefaultsAndErrors : IO Unit := do
  -- Omitted top-level values follow SystemVerilog parameter semantics and use
  -- the declared default; "missing" here is therefore not an error.
  let defaults ← requireOk (specializeDesign addDesign [])
  let defaultTop ← requireTop defaults
  ensure (portWidth? defaultTop "a" == some 8)
    "omitted W did not use its declared default"

  ensure (isError (specializeDesign addDesign [("UNKNOWN", 3)]))
    "unknown top parameter was accepted"
  ensure (isError
    (specializeDesign addDesign [("W", 3), ("W", 4)]))
    "duplicate top parameter was accepted"
  ensure (isError (specializeDesign addDesign [("W", 0)]))
    "zero hardware width was accepted"
  ensure (isError (specializeDesign missingTopDesign []))
    "missing top module was accepted"
  ensure (isError (specializeDesign missingModuleDesign []))
    "missing child module was accepted"
  ensure (isError (specializeDesign unknownChildOverrideDesign []))
    "unknown child override was accepted"
  ensure (isError (specializeDesign duplicateChildOverrideDesign []))
    "duplicate child override was accepted"
  ensure (isError (specializeDesign duplicateDeclarationDesign []))
    "duplicate module parameter declaration was accepted"
  ensure (isError (specializeDesign residualOverrideDesign []))
    "residual symbolic child override was accepted"
  ensure (isError (specializeDesign cycleDesign []))
    "recursive hierarchy was accepted"
  ensure (isError (specializeDesign parameterizedPrimitiveDesign [("W", 8)]))
    "parameterized primitive was accepted"
  ensure (isError
    (specializeDesign reversedSliceDesign [("Hi", 0), ("Lo", 7)]))
    "reversed concrete slice was accepted"

  let primitiveSpecialized ← requireOk (specializeDesign concretePrimitiveDesign [])
  ensure (isError
    (Sparkle.Backend.CppSim.validateSpecializedDesign primitiveSpecialized))
    "CppSim accepted a reachable primitive without an executable model"

  let collisionSpecialized ← requireOk (specializeDesign collisionDesign [])
  let emittedNames := collisionSpecialized.modules.map fun module_ =>
    Sparkle.Backend.CppSim.sanitizeName module_.name
  for emittedName in emittedNames do
    ensure (emittedNames.count emittedName == 1)
      "specialized clone name collided after C++/SystemVerilog sanitization"

private def checkZeroIndexParameter : IO Unit := do
  let specialized ← requireOk
    (specializeDesign zeroIndexDesign [("Start", 0)])
  checkConcreteDesign specialized
  let top ← requireTop specialized
  match top.body with
  | [.assign "y" (.slice (.ref "x") hi lo)] =>
    ensure (hi.toNat? == some 7 && lo.toNat? == some 0)
      "zero-valued index parameter was not substituted"
  | _ => throw (IO.userError "zero-index slice was not retained")
  let _ ← requireOk
    (Sparkle.Backend.CppSim.toCppSimDesignChecked specialized)

private def checkVerificationConsumer (width : Nat) : IO Unit := do
  let specialized ← requireOk
    (specializeDesign seqDesign [("W", width)])
  checkConcreteDesign specialized
  let top ← requireTop specialized
  if width ≤ 64 then
    let _ ← requireOk
      (Sparkle.Backend.CppSim.toCppSimDesignChecked specialized)
  let leanSource ← requireOk (Tools.SVParser.Verify.moduleToLean top)
  ensure (contains leanSource s!"BitVec {width}")
    s!"verification model lost the specialized width W={width}"

def main : IO UInt32 := do
  for width in [3, 17, 257] do
    checkWidth width
  checkDerived
  checkHierarchy
  checkCloneReuse
  checkNestedHierarchy
  checkMemory
  checkDefaultsAndErrors
  checkZeroIndexParameter
  for width in [3, 17, 257] do
    checkVerificationConsumer width
  IO.println "specialization smoke tests: PASS"
  return 0
