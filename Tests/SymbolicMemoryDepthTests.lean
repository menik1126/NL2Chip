/-
  Regression tests for exact, parameter-dependent memory depths.

  These tests intentionally enter through the native SystemVerilog parser.
  That exercises the complete path that must retain DEPTH independently from
  the address width:

      SV array declaration -> IR -> specialization -> Verilog / CppSim

  In particular, depths 3, 10, and 17 must not be rounded up to 4, 16, and 32.
-/

import Sparkle.Backend.CppSim
import Sparkle.Backend.Verilog
import Sparkle.Core.JIT
import Sparkle.IR.Optimize
import Sparkle.IR.Specialize
import Tools.SVParser
import Tools.SVParser.Verify

open Sparkle.IR.AST Sparkle.IR.Specialize
open Tools.SVParser.Lower

private def tempDir : String := "/tmp/sparkle_symbolic_memory_depth_tests"

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

private def requireErrorContaining
    (result : Except String α) (fragment label : String) : IO Unit :=
  match result with
  | .ok _ => throw (IO.userError s!"{label}: unexpectedly succeeded")
  | .error message =>
    ensure (contains message fragment)
      s!"{label}: error did not mention '{fragment}': {message}"

private def lineContaining? (text fragment : String) : Option String :=
  (text.splitOn "\n").find? (contains · fragment)

private def requireMemoryDecl (verilog : String) : IO String :=
  match lineContaining? verilog "storage [0:" with
  | some line => pure line
  | none => throw (IO.userError s!"generated Verilog has no storage declaration:\n{verilog}")

private def hasInstanceOverride
    (module_ : Module) (instanceName parameterName : String)
    (expected : Sparkle.IR.Type.DimExpr) : Bool :=
  module_.body.any fun
    | .inst _ foundInstance _ overrides =>
      foundInstance == instanceName && overrides.any fun (name, value) =>
        name == parameterName && value == expected
    | _ => false

private def exactMemorySource : String :=
  "module exact_memory #(\n" ++
  "  parameter AW = 2, parameter DW = 7, parameter DEPTH = 3\n" ++
  ") (\n" ++
  "  input logic clk,\n" ++
  "  input logic [AW-1:0] wr_addr,\n" ++
  "  input logic [DW-1:0] wr_data,\n" ++
  "  input logic wr_en,\n" ++
  "  input logic [AW-1:0] rd_addr,\n" ++
  "  output logic [DW-1:0] rd_data\n" ++
  ");\n" ++
  "logic [DW-1:0] storage [0:DEPTH-1];\n" ++
  "assign rd_data = storage[rd_addr];\n" ++
  "always_ff @(posedge clk) begin\n" ++
  "  if (wr_en) storage[wr_addr] <= wr_data;\n" ++
  "end\n" ++
  "endmodule\n"

/-- Two instances of the same parameterized memory child.  The first uses a
    concrete, three-entry configuration.  The second inherits three top-level
    dimensions, which are overridden together during specialization. -/
private def hierarchySource : String :=
  exactMemorySource ++
  "module exact_memory_top #(\n" ++
  "  parameter TOP_AW = 4, parameter TOP_DW = 9, parameter TOP_DEPTH = 10\n" ++
  ") (input logic clk);\n" ++
  "logic [1:0] addr3;\n" ++
  "logic [6:0] data3;\n" ++
  "logic we3;\n" ++
  "logic [6:0] read3;\n" ++
  "logic [TOP_AW-1:0] addrN;\n" ++
  "logic [TOP_DW-1:0] dataN;\n" ++
  "logic weN;\n" ++
  "logic [TOP_DW-1:0] readN;\n" ++
  "exact_memory #(.AW(2), .DW(7), .DEPTH(3)) u_depth3 (\n" ++
  "  .clk(clk), .wr_addr(addr3), .wr_data(data3), .wr_en(we3),\n" ++
  "  .rd_addr(addr3), .rd_data(read3));\n" ++
  "exact_memory #(.AW(TOP_AW), .DW(TOP_DW), .DEPTH(TOP_DEPTH)) u_native (\n" ++
  "  .clk(clk), .wr_addr(addrN), .wr_data(dataN), .wr_en(weN),\n" ++
  "  .rd_addr(addrN), .rd_data(readN));\n" ++
  "endmodule\n"

private def specializeExact (aw dw depth : Nat) : IO Design := do
  let native ← requireOk (parseAndLowerNative exactMemorySource)
  specializeDesign native [("AW", aw), ("DW", dw), ("DEPTH", depth)]
    |> requireOk

private def checkParameterizedVerilog : IO Unit := do
  let native ← requireOk (parseAndLowerNative exactMemorySource)
  let module_ ← match native.findModule "exact_memory" with
    | some module_ => pure module_
    | none => throw (IO.userError "native memory design lost module 'exact_memory'")
  let verilog ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked native)
  let declaration ← requireMemoryDecl verilog
  ensure (contains declaration "DEPTH")
    s!"native memory declaration baked in its default DEPTH:\n{declaration}"
  ensure (!contains declaration "2 ** AW")
    s!"native memory depth was reconstructed as 2**AW:\n{declaration}"
  ensure (module_.parameters.any fun parameter =>
      parameter.name == "DEPTH" && parameter.defaultValue == 3)
    "DEPTH was not retained as a module parameter with default 3"
  ensure (contains verilog "assign rd_data = storage[rd_addr]")
    "native memory lowering did not retain the declared read-data/read-address ports"
  ensure (contains verilog "if (wr_en)")
    "native memory lowering did not retain the declared write enable"
  ensure (contains verilog "storage[wr_addr] <= wr_data")
    "native memory lowering did not retain the declared write address/data ports"

  let renamedClock := exactMemorySource
    |>.replace "input logic clk," "input logic mem_clock,"
    |>.replace "posedge clk" "posedge mem_clock"
  let renamedDesign ← requireOk (parseAndLowerNative renamedClock)
  let renamedVerilog ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked renamedDesign)
  ensure (contains renamedVerilog "always_ff @(posedge mem_clock)")
    "native memory lowering replaced the source memory clock with a hard-coded name"

private def checkConcreteDepth (aw dw depth roundedDepth : Nat) : IO Design := do
  let specialized ← specializeExact aw dw depth
  let optimized := Sparkle.IR.Optimize.optimizeDesign specialized
  let verilog ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked optimized)
  let declaration ← requireMemoryDecl verilog
  ensure (contains declaration s!"[0:{depth - 1}]")
    s!"DEPTH={depth} did not produce an exact {depth}-entry declaration:\n{declaration}"
  ensure (!contains declaration s!"[0:{roundedDepth - 1}]")
    s!"DEPTH={depth} was silently expanded to {roundedDepth}:\n{declaration}"
  return optimized

private def checkDepthSweep : IO Unit := do
  let _ ← checkConcreteDepth 2 7 3 4
  let _ ← checkConcreteDepth 4 9 10 16
  let _ ← checkConcreteDepth 5 13 17 32

private def runSmallCppSim : IO Unit := do
  let specialized3 ← specializeExact 2 7 3
  let cpp3 ← requireOk
    (Sparkle.Backend.CppSim.toCppSimDesignChecked specialized3)
  ensure (contains cpp3 ", 3> storage;")
    "CppSim did not allocate exactly three memory entries"

  let specialized10 ← specializeExact 4 9 10
  let cpp10 ← requireOk
    (Sparkle.Backend.CppSim.toCppSimDesignChecked specialized10)
  ensure (contains cpp10 ", 10> storage;")
    "CppSim did not allocate exactly ten memory entries"

  -- The class backend can store a 40-bit word in uint64_t, but the public JIT
  -- memory accessors still use uint32_t.  Checked JIT generation must reject
  -- that configuration instead of silently truncating its high eight bits.
  let specialized40 ← specializeExact 4 40 10
  let _ ← requireOk
    (Sparkle.Backend.CppSim.toCppSimDesignChecked specialized40)
  ensure (isError (Sparkle.Backend.CppSim.toCppSimJITChecked specialized40))
    "CppSim JIT accepted a 40-bit memory through its 32-bit memory ABI"

  -- Exercise the concrete three-entry storage through the existing JIT ABI.
  let jitSource ← requireOk
    (Sparkle.Backend.CppSim.toCppSimJITChecked specialized3)
  IO.FS.createDirAll tempDir
  let sourcePath := s!"{tempDir}/depth3.cpp"
  IO.FS.writeFile sourcePath jitSource
  let handle ← Sparkle.Core.JIT.JIT.compileAndLoad sourcePath
  Sparkle.Core.JIT.JIT.reset handle
  Sparkle.Core.JIT.JIT.setMem handle 0 2 0x55
  let value ← Sparkle.Core.JIT.JIT.getMem handle 0 2
  Sparkle.Core.JIT.JIT.destroy handle
  ensure (value == 0x55)
    s!"CppSim/JIT did not retain the last valid word of DEPTH=3 (got {value})"

/-- A synchronous-read memory exercises two semantics that cannot be checked
    by inspecting the allocation alone: out-of-range accesses fail safely, and
    a same-cycle read/write collision observes the old word (SV NBA ordering). -/
private def checkSynchronousCppSemantics : IO Unit := do
  let syncModule : Module :=
    { (Module.empty "sync_exact_depth") with
      inputs :=
        [{ name := "clk", ty := .bit },
         { name := "write_addr", ty := .bitVector 2 },
         { name := "write_data", ty := .bitVector 8 },
         { name := "write_enable", ty := .bit },
         { name := "read_addr", ty := .bitVector 2 }]
      outputs := [{ name := "read_data", ty := .bitVector 8 }]
      body :=
        [.memory "storage" 2 8 3 "clk"
          (.ref "write_addr") (.ref "write_data") (.ref "write_enable")
          (.ref "read_addr") "read_data" false] }
  let design : Design :=
    { topModule := syncModule.name, modules := [syncModule] }
  let source ← requireOk (Sparkle.Backend.CppSim.toCppSimJITChecked design)
  let sourcePath := s!"{tempDir}/sync_depth3.cpp"
  IO.FS.writeFile sourcePath source
  let handle ← Sparkle.Core.JIT.JIT.compileAndLoad sourcePath
  Sparkle.Core.JIT.JIT.reset handle
  Sparkle.Core.JIT.JIT.setMem handle 0 1 0x11
  Sparkle.Core.JIT.JIT.setInput handle 0 1
  Sparkle.Core.JIT.JIT.setInput handle 1 0x22
  Sparkle.Core.JIT.JIT.setInput handle 2 1
  Sparkle.Core.JIT.JIT.setInput handle 3 1
  Sparkle.Core.JIT.JIT.eval handle
  Sparkle.Core.JIT.JIT.tick handle
  let collisionRead ← Sparkle.Core.JIT.JIT.getOutput handle 0
  let writtenValue ← Sparkle.Core.JIT.JIT.getMem handle 0 1
  Sparkle.Core.JIT.JIT.setMem handle 0 3 0x77
  let outOfRange ← Sparkle.Core.JIT.JIT.getMem handle 0 3
  Sparkle.Core.JIT.JIT.destroy handle
  ensure (collisionRead == 0x11)
    s!"synchronous same-address read/write did not observe the old word (got {collisionRead})"
  ensure (writtenValue == 0x22)
    s!"synchronous write did not commit after the read (got {writtenValue})"
  ensure (outOfRange == 0)
    s!"out-of-range JIT memory access did not fail safely (got {outOfRange})"

private def checkHierarchy : IO Unit := do
  let native ← requireOk (parseAndLowerNative hierarchySource)
  let top ← match native.findModule "exact_memory_top" with
    | some module_ => pure module_
    | none => throw (IO.userError "hierarchical design lost module 'exact_memory_top'")
  ensure (hasInstanceOverride top "u_depth3" "DEPTH" (.literal 3))
    "concrete child DEPTH override was not retained"
  ensure (hasInstanceOverride top "u_native" "DEPTH" (.param "TOP_DEPTH"))
    "parent-dependent child DEPTH override was not retained"

  let specialized ← requireOk
    (specializeDesign native
      [("TOP_AW", 5), ("TOP_DW", 13), ("TOP_DEPTH", 17)])
  ensure (specialized.modules.length == 3)
    s!"two memory configurations did not create two child clones: {specialized.modules.map (·.name)}"
  let concreteVerilog ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked specialized)
  ensure (contains concreteVerilog "storage [0:2]")
    "hierarchical DEPTH=3 child was not specialized exactly"
  ensure (contains concreteVerilog "storage [0:16]")
    "hierarchical DEPTH=17 child was not specialized exactly"
  ensure (!contains concreteVerilog "storage [0:3]")
    "hierarchical DEPTH=3 child expanded to four entries"
  ensure (!contains concreteVerilog "storage [0:31]")
    "hierarchical DEPTH=17 child expanded to 32 entries"
  let _ ← requireOk
    (Sparkle.Backend.CppSim.toCppSimDesignChecked specialized)

private def checkFailClosed : IO Unit := do
  let native ← requireOk (parseAndLowerNative exactMemorySource)
  ensure (isError (Sparkle.Backend.CppSim.toCppSimDesignChecked native))
    "CppSim accepted an unspecialized symbolic memory"
  ensure (isError (specializeDesign native
      [("AW", 2), ("DW", 7), ("DEPTH", 0)]))
    "zero memory depth survived specialization"
  ensure (isError (specializeDesign native [("NOT_A_PARAMETER", 3)]))
    "unknown memory parameter override was silently ignored"

  let oversizedMemory : Module :=
    { (Module.empty "oversized_memory") with
      body :=
        [.memory "storage" 21 8
          (.literal (Sparkle.IR.Type.DimExpr.maxNatWorkWidth + 1)) "clk"
          (.const 0 21) (.const 0 8) (.const 0 1)
          (.const 0 21) "read_data" false] }
  ensure (isError (Sparkle.Backend.CppSim.toCppSimChecked oversizedMemory))
    "CppSim accepted a concrete memory whose std::array depth exceeds the safe resource limit"

  let undeclaredDepth :=
    "module undeclared_depth #(parameter AW=2, parameter DW=7) (input logic clk); " ++
    "logic [DW-1:0] storage [0:DEPTH-1]; endmodule\n"
  ensure (isError (parseAndLowerNative undeclaredDepth))
    "undeclared symbolic memory depth did not fail closed"

  let multipleWrites := exactMemorySource.replace
    "  if (wr_en) storage[wr_addr] <= wr_data;"
    "  if (wr_en) storage[wr_addr] <= wr_data;\n  if (wr_en) storage[rd_addr] <= wr_data;"
  ensure (isError (parseAndLowerNative multipleWrites))
    "native memory lowering silently chose one of multiple write sites"

  let multipleReads := exactMemorySource.replace
    "assign rd_data = storage[rd_addr];"
    "assign rd_data = storage[rd_addr];\nassign rd_data = storage[wr_addr];"
  ensure (isError (parseAndLowerNative multipleReads))
    "native memory lowering silently chose one of multiple read sites"

private def checkVerifyRejectsUnsupportedStatements : IO Unit := do
  let memoryModule : Module :=
    { (Module.empty "verify_memory") with
      body :=
        [.memory "storage" 2 8 3 "clk"
          (.const 0 2) (.const 0 8) (.const 0 1)
          (.const 0 2) "read_data" false] }
  requireErrorContaining
    (Tools.SVParser.Verify.extractModelChecked memoryModule)
    "memory 'storage'" "verification model memory rejection"
  requireErrorContaining
    (Tools.SVParser.Verify.moduleToLeanChecked memoryModule)
    "memory 'storage'" "verification source memory rejection"

  let instanceModule : Module :=
    { (Module.empty "verify_instance") with
      body := [.inst "child" "u_child" [] []] }
  requireErrorContaining
    (Tools.SVParser.Verify.extractModelChecked instanceModule)
    "instance 'u_child'" "verification model instance rejection"
  requireErrorContaining
    (Tools.SVParser.Verify.moduleToLeanChecked instanceModule)
    "instance 'u_child'" "verification source instance rejection"

def main : IO UInt32 := do
  try
    checkParameterizedVerilog
    checkDepthSweep
    runSmallCppSim
    checkSynchronousCppSemantics
    checkHierarchy
    checkFailClosed
    checkVerifyRejectsUnsupportedStatements
    IO.println "PASS: exact symbolic memory depth"
    return 0
  catch error =>
    IO.eprintln s!"FAIL: exact symbolic memory depth: {error}"
    return 1
