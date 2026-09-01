import Sparkle.Core.JIT
import Tests.MultiDomainElab

open Sparkle.Core.JIT
open Sparkle.IR.AST
open Sparkle.IR.Type
open Sparkle.Backend.CppSim
open Sparkle.Compiler.DRC

private def require (condition : Bool) (message : String) : IO Unit :=
  unless condition do throw (IO.userError message)

private def testLevel : IO Unit := do
  let handle ← JIT.compileAndLoad ".lake/build/gen/tests/multidomain_elab_jit.cpp"
  try
    let domains ← JIT.domains handle
    require (domains.size == 2) s!"expected two domains, got {domains.size}"
    require (domains[0]!.name == "source") s!"unexpected source domain: {reprStr domains[0]!}"
    require (domains[0]!.periodPs == 8000) s!"unexpected source period: {reprStr domains[0]!}"
    require (domains[1]!.name == "destination") s!"unexpected destination domain: {reprStr domains[1]!}"
    require (domains[1]!.periodPs == 12000) s!"unexpected destination period: {reprStr domains[1]!}"

    JIT.reset handle
    JIT.setInput handle 0 1
    JIT.runForPicoseconds handle 36000
    let output ← JIT.getOutput handle 0
    require (output == 1) s!"synchronized level did not arrive, output={output}"
  finally
    JIT.destroy handle

private def testPulse : IO Unit := do
  let handle ← JIT.compileAndLoad ".lake/build/gen/tests/multidomain_pulse_jit.cpp"
  try
    JIT.reset handle
    JIT.setInput handle 0 1
    JIT.eval handle
    JIT.tickDomain handle 0
    JIT.setInput handle 0 0

    let mut observed := #[]
    for _ in [:3] do
      JIT.eval handle
      JIT.tickDomain handle 1
      JIT.eval handle
      observed := observed.push (← JIT.getOutput handle 0)
    require (observed == #[0, 1, 0]) s!"destination pulse shape mismatch: {observed}"
  finally
    JIT.destroy handle

private def testPulseVector : IO Unit := do
  let handle ← JIT.compileAndLoad ".lake/build/gen/tests/multidomain_pulse_vector_jit.cpp"
  try
    JIT.reset handle
    JIT.setInput handle 0 5
    JIT.eval handle
    JIT.tickDomain handle 0
    JIT.setInput handle 0 0

    let mut observed := #[]
    for _ in [:3] do
      JIT.eval handle
      JIT.tickDomain handle 1
      JIT.eval handle
      observed := observed.push (← JIT.getOutput handle 0)
    require (observed == #[0, 5, 0]) s!"destination pulse-vector shape mismatch: {observed}"
  finally
    JIT.destroy handle

private def testHeterogeneousOutputs : IO Unit := do
  let handle ← JIT.compileAndLoad ".lake/build/gen/tests/multidomain_outputs_jit.cpp"
  try
    JIT.reset handle
    JIT.eval handle
    require ((← JIT.getOutput handle 1) == 1)
      "source reset synchronizer did not start asserted"
    require ((← JIT.getOutput handle 2) == 1)
      "destination reset synchronizer did not start asserted"

    for _ in [:2] do
      JIT.evalTickDomain handle 0
    JIT.eval handle
    require ((← JIT.getOutput handle 1) == 0)
      "source reset synchronizer did not deassert in the source domain"
    require ((← JIT.getOutput handle 2) == 1)
      "source-domain ticks incorrectly committed destination-domain state"

    for _ in [:2] do
      JIT.evalTickDomain handle 1
    JIT.eval handle
    require ((← JIT.getOutput handle 2) == 0)
      "destination reset synchronizer did not deassert in the destination domain"

    JIT.reset handle
    JIT.setInput handle 0 1
    JIT.evalTickDomain handle 0
    JIT.setInput handle 0 0
    let mut observed := #[]
    for _ in [:3] do
      JIT.evalTickDomain handle 1
      JIT.eval handle
      observed := observed.push (← JIT.getOutput handle 0)
    require (observed == #[0, 1, 0])
      s!"heterogeneous destination pulse shape mismatch: {observed}"
  finally
    JIT.destroy handle

private def hierarchySourceDomain : ClockDomain :=
  { id := "source", clock := "src_clk", reset := some "src_rst", periodPs := 8000 }

private def hierarchyDestinationDomain : ClockDomain :=
  { id := "destination", clock := "dst_clk", reset := some "dst_rst", periodPs := 12000 }

private def hierarchyChild : Module :=
  { name := "multidomain_child"
  , inputs :=
      [ { name := "source_data", ty := .bitVector 8, domain := some "source" }
      , { name := "destination_data", ty := .bitVector 8, domain := some "destination" }
      , { name := "src_clk", ty := .bit }
      , { name := "src_rst", ty := .bit }
      , { name := "dst_clk", ty := .bit }
      , { name := "dst_rst", ty := .bit }
      ]
  , outputs :=
      [ { name := "source_q", ty := .bitVector 8, domain := some "source" }
      , { name := "destination_q", ty := .bitVector 8, domain := some "destination" }
      ]
  , wires :=
      [ { name := "source_reg", ty := .bitVector 8, domain := some "source" }
      , { name := "destination_reg", ty := .bitVector 8, domain := some "destination" }
      ]
  , body :=
      [ .register "source_reg" "source" .domain (.ref "source_data") (.const 0 8)
      , .register "destination_reg" "destination" .domain (.ref "destination_data") (.const 0 8)
      , .assign "source_q" (.ref "source_reg")
      , .assign "destination_q" (.ref "destination_reg")
      ]
  , clockDomains := [hierarchySourceDomain, hierarchyDestinationDomain]
  }

private def hierarchyTop : Module :=
  { name := "multidomain_top"
  , inputs :=
      [ { name := "source_data", ty := .bitVector 8, domain := some "source" }
      , { name := "destination_data", ty := .bitVector 8, domain := some "destination" }
      , { name := "src_clk", ty := .bit }
      , { name := "src_rst", ty := .bit }
      , { name := "dst_clk", ty := .bit }
      , { name := "dst_rst", ty := .bit }
      ]
  , outputs :=
      [ { name := "source_q", ty := .bitVector 8, domain := some "source" }
      , { name := "destination_q", ty := .bitVector 8, domain := some "destination" }
      ]
  , wires :=
      [ { name := "child_source_q", ty := .bitVector 8, domain := some "source" }
      , { name := "child_destination_q", ty := .bitVector 8, domain := some "destination" }
      ]
  , body :=
      [ .inst "multidomain_child" "child"
          [ ("source_data", .ref "source_data")
          , ("destination_data", .ref "destination_data")
          , ("src_clk", .ref "src_clk")
          , ("src_rst", .ref "src_rst")
          , ("dst_clk", .ref "dst_clk")
          , ("dst_rst", .ref "dst_rst")
          , ("source_q", .ref "child_source_q")
          , ("destination_q", .ref "child_destination_q")
          ]
          []
          [("source", "source"), ("destination", "destination")]
      , .assign "source_q" (.ref "child_source_q")
      , .assign "destination_q" (.ref "child_destination_q")
      ]
  , clockDomains := [hierarchySourceDomain, hierarchyDestinationDomain]
  }

private def hierarchyDesign : Design :=
  { topModule := hierarchyTop.name, modules := [hierarchyChild, hierarchyTop] }

private def testHierarchyDomainMapping : IO Unit := do
  let drcIssues := checkDesignClockDomains hierarchyDesign
  require drcIssues.isEmpty s!"hierarchical multi-domain DRC failed: {drcIssues}"
  let jitPath := ".lake/build/gen/tests/multidomain_hierarchy_jit.cpp"
  IO.FS.writeFile jitPath (toCppSimJIT hierarchyDesign)
  let handle ← JIT.compileAndLoad jitPath
  try
    JIT.reset handle
    JIT.setInput handle 0 9
    JIT.setInput handle 1 6
    JIT.evalTickDomain handle 0
    JIT.eval handle
    require ((← JIT.getOutput handle 0) == 9)
      "parent source-domain tick did not commit the child source domain"
    require ((← JIT.getOutput handle 1) == 0)
      "parent source-domain tick incorrectly committed the child destination domain"
    JIT.evalTickDomain handle 1
    JIT.eval handle
    require ((← JIT.getOutput handle 1) == 6)
      "parent destination-domain tick did not commit the child destination domain"
  finally
    JIT.destroy handle

def main : IO UInt32 := do
  testLevel
  testPulse
  testPulseVector
  testHeterogeneousOutputs
  testHierarchyDomainMapping
  IO.println "multi-domain level/pulse/vector/heterogeneous/hierarchy JIT scheduler: PASS"
  return 0
