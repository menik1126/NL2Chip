import Sparkle.Backend.Verilog
import Tools.SVParser.Lower

open Sparkle.IR.AST
open Sparkle.Backend.Verilog
open Tools.SVParser.Lower

/-!
# Native parameterized generate/loop regression tests

These tests deliberately exercise the symbolic SystemVerilog path.  A native
module must retain elaboration constructs until SystemVerilog elaboration (or
an explicit concrete specialization) rather than selecting/unrolling them at
the declared parameter default.

Run with:

    lake exe native-generate-loop-tests
-/

private def contains (text fragment : String) : Bool :=
  decide ((text.splitOn fragment).length > 1)

private def ensure (condition : Bool) (message : String) : IO Unit :=
  unless condition do
    throw (IO.userError message)

private def requireOk : Except String α → IO α
  | .ok value => pure value
  | .error message => throw (IO.userError message)

private def requireModule (design : Design) (name : String) : IO Module :=
  match design.findModule name with
  | some module_ => pure module_
  | none => throw (IO.userError s!"expected module '{name}'")

/- `nativeItems` and these constructors are the boundary between symbolic
   elaboration/procedural IR and the normalized core IR.  Keeping an explicit
   structural assertion here prevents a future implementation from passing
   only because it happened to optimize one of the behavioral fixtures. -/
private def hasGenerateIf (module_ : Module) : Bool :=
  module_.nativeItems.any fun
    | .generateIf .. => true
    | _ => false

private def containsProceduralFor
    (statements : List Sparkle.IR.AST.ProcStmt) : Bool :=
  statements.any fun
    | .forLoop .. => true
    | _ => false

private def hasCombinationalFor (module_ : Module) : Bool :=
  module_.nativeItems.any fun
    | .process .comb statements => containsProceduralFor statements
    | _ => false

private def runIcarus (label verilog bench marker : String) : IO Unit := do
  let tempDir := "/tmp/sparkle_native_generate_loop_tests"
  let sourcePath := s!"{tempDir}/{label}.sv"
  let executablePath := s!"{tempDir}/{label}.vvp"
  IO.FS.createDirAll tempDir
  IO.FS.writeFile sourcePath (verilog ++ "\n" ++ bench)
  let compiled ← IO.Process.output {
    cmd := "iverilog"
    args := #["-g2012", "-s", "tb", "-o", executablePath, sourcePath]
  }
  unless compiled.exitCode == 0 do
    throw (IO.userError
      s!"{label}: Icarus compilation failed:\n{compiled.stdout}{compiled.stderr}")
  let simulated ← IO.Process.output {
    cmd := "vvp"
    args := #[executablePath]
  }
  unless simulated.exitCode == 0 do
    throw (IO.userError
      s!"{label}: Icarus simulation failed:\n{simulated.stdout}{simulated.stderr}")
  ensure (contains simulated.stdout marker)
    s!"{label}: simulation did not print completion marker '{marker}'"

-- Parameter-dependent generate-if -------------------------------------------

private def generateOverrideSource : String :=
  "module generate_override #(parameter W = 3) (\n" ++
  "  input logic [W-1:0] x, output logic [W-1:0] y);\n" ++
  "generate if (W == 1) begin : width_one\n" ++
  "  assign y = x;\n" ++
  "end else begin : width_many\n" ++
  "  assign y = ~x;\n" ++
  "end endgenerate\n" ++
  "endmodule\n"

private def generateOverrideBench : String :=
  "module tb;\n" ++
  "logic x1; logic [2:0] x3; wire y1; wire [2:0] y3;\n" ++
  "generate_override #(.W(1)) d1 (.x(x1), .y(y1));\n" ++
  "generate_override #(.W(3)) d3 (.x(x3), .y(y3));\n" ++
  "initial begin\n" ++
  "  x1 = 1'b1; x3 = 3'b001; #1;\n" ++
  "  if (y1 !== 1'b1) $fatal(1, \"W=1 selected the wrong generate branch\");\n" ++
  "  if (y3 !== 3'b110) $fatal(1, \"W=3 selected the wrong generate branch\");\n" ++
  "  $display(\"PASS native generate override\"); $finish;\n" ++
  "end endmodule\n"

private def checkGenerateOverride : IO Unit := do
  let design ← requireOk (parseAndLowerNative generateOverrideSource)
  ensure (design.topModule == "generate_override")
    s!"generate override: wrong top module '{design.topModule}'"
  let module_ ← requireModule design "generate_override"
  ensure (hasGenerateIf module_)
    "generate override: parameter-dependent generate-if was not retained in native IR"
  let verilog ← requireOk (toVerilogDesignChecked design)
  ensure (module_.parameters.any fun parameter =>
      parameter.name == "W" && parameter.defaultValue == 3)
    "generate override: module lost parameter W with default 3"
  ensure (contains verilog "generate")
    "generate override: emitted module lost its generate region"
  runIcarus "generate_override" verilog generateOverrideBench
    "PASS native generate override"

-- A child instantiated only inside a generate branch must still be recognized
-- as a child when selecting the design root.  The branch-local wire also makes
-- accidental flattening/hoisting visible to the SystemVerilog oracle.
private def generatedHierarchySource : String :=
  "module generated_leaf #(parameter W = 3) (\n" ++
  "  input logic [W-1:0] x, output logic [W-1:0] y);\n" ++
  "assign y = x;\n" ++
  "endmodule\n" ++
  "module generated_top #(parameter W = 3) (\n" ++
  "  input logic [W-1:0] x, output logic [W-1:0] y);\n" ++
  "generate if (W > 1) begin : child_path\n" ++
  "  logic [W-1:0] branch_wire;\n" ++
  "  generated_leaf #(.W(W)) u_leaf (.x(x), .y(branch_wire));\n" ++
  "  assign y = branch_wire;\n" ++
  "end else begin : bypass_path\n" ++
  "  assign y = ~x;\n" ++
  "end endgenerate\n" ++
  "endmodule\n"

private def generatedHierarchyBench : String :=
  "module tb;\n" ++
  "logic x1; logic [2:0] x3; wire y1; wire [2:0] y3;\n" ++
  "generated_top #(.W(1)) d1 (.x(x1), .y(y1));\n" ++
  "generated_top #(.W(3)) d3 (.x(x3), .y(y3));\n" ++
  "initial begin\n" ++
  "  x1 = 1'b1; x3 = 3'b101; #1;\n" ++
  "  if (y1 !== 1'b0) $fatal(1, \"W=1 bypass branch failed\");\n" ++
  "  if (y3 !== 3'b101) $fatal(1, \"W=3 generated child branch failed\");\n" ++
  "  $display(\"PASS generated hierarchy\"); $finish;\n" ++
  "end endmodule\n"

private def checkGeneratedHierarchy : IO Unit := do
  let design ← requireOk (parseAndLowerNative generatedHierarchySource)
  ensure (design.topModule == "generated_top")
    s!"generated hierarchy: child-in-generate confused root discovery; got '{design.topModule}'"
  ensure (design.modules.length == 2)
    "generated hierarchy: expected exactly the leaf and top modules"
  let top ← requireModule design "generated_top"
  ensure (hasGenerateIf top)
    "generated hierarchy: generate-if was not retained in the top module"
  let verilog ← requireOk (toVerilogDesignChecked design)
  ensure (contains verilog "branch_wire" && contains verilog "generated_leaf")
    "generated hierarchy: branch-local wire or child instance was lost"
  runIcarus "generated_hierarchy" verilog generatedHierarchyBench
    "PASS generated hierarchy"

-- Parameter-dependent procedural loop ---------------------------------------

private def proceduralLoopSource : String :=
  "module procedural_reverse #(parameter W = 3) (\n" ++
  "  input logic [W-1:0] x, output logic [W-1:0] y);\n" ++
  "integer i;\n" ++
  "always_comb begin\n" ++
  "  y = x;\n" ++
  "  for (i = 0; i < W; i = i + 1) begin\n" ++
  "    y[i] = x[W - 1 - i];\n" ++
  "  end\n" ++
  "end\n" ++
  "endmodule\n"

private def proceduralLoopBench : String :=
  "module tb;\n" ++
  "logic [2:0] x3; logic [16:0] x17; wire [2:0] y3; wire [16:0] y17;\n" ++
  "procedural_reverse #(.W(3)) d3 (.x(x3), .y(y3));\n" ++
  "procedural_reverse #(.W(17)) d17 (.x(x17), .y(y17));\n" ++
  "initial begin\n" ++
  "  x3 = 3'b110; x17 = 17'h00003; #1;\n" ++
  "  if (y3 !== 3'b011) $fatal(1, \"W=3 procedural loop failed\");\n" ++
  "  if (y17 !== 17'h18000) $fatal(1, \"W=17 procedural loop failed\");\n" ++
  "  $display(\"PASS native procedural loop\"); $finish;\n" ++
  "end endmodule\n"

private def checkProceduralLoop : IO Unit := do
  let design ← requireOk (parseAndLowerNative proceduralLoopSource)
  let module_ ← requireModule design "procedural_reverse"
  ensure (hasCombinationalFor module_)
    "procedural loop: canonical for-loop was not retained in native procedural IR"
  let verilog ← requireOk (toVerilogDesignChecked design)
  ensure (contains verilog "for (" || contains verilog "for(")
    "procedural loop: emitted SystemVerilog does not contain the loop"
  runIcarus "procedural_loop" verilog proceduralLoopBench
    "PASS native procedural loop"

-- Zero iterations must execute the body zero times.  This specifically guards
-- against the historical fallback that lowered a residual loop body once.
private def zeroTripSource : String :=
  "module zero_trip #(parameter N = 0) (\n" ++
  "  input logic [7:0] x, output logic [7:0] y);\n" ++
  "integer i;\n" ++
  "always_comb begin\n" ++
  "  y = x;\n" ++
  "  for (i = 0; i < N; i = i + 1) begin\n" ++
  "    y = 8'h3c;\n" ++
  "  end\n" ++
  "end\n" ++
  "endmodule\n"

private def zeroTripBench : String :=
  "module tb;\n" ++
  "logic [7:0] x; wire [7:0] y0; wire [7:0] y2;\n" ++
  "zero_trip #(.N(0)) d0 (.x(x), .y(y0));\n" ++
  "zero_trip #(.N(2)) d2 (.x(x), .y(y2));\n" ++
  "initial begin\n" ++
  "  x = 8'ha5; #1;\n" ++
  "  if (y0 !== 8'ha5) $fatal(1, \"zero-trip loop executed its body\");\n" ++
  "  if (y2 !== 8'h3c) $fatal(1, \"positive-trip loop skipped its body\");\n" ++
  "  $display(\"PASS zero-trip loop\"); $finish;\n" ++
  "end endmodule\n"

private def checkZeroTrip : IO Unit := do
  let design ← requireOk (parseAndLowerNative zeroTripSource)
  let module_ ← requireModule design "zero_trip"
  ensure (hasCombinationalFor module_)
    "zero-trip: parameterized loop was not retained in native procedural IR"
  let verilog ← requireOk (toVerilogDesignChecked design)
  runIcarus "zero_trip" verilog zeroTripBench "PASS zero-trip loop"

-- A zero increment is non-terminating for the supported increasing-loop
-- profile.  It must be rejected, not retained for a downstream hang and not
-- treated as one iteration by the core lowering fallback.
private def invalidStepSource : String :=
  "module invalid_step #(parameter N = 4) (\n" ++
  "  input logic [7:0] x, output logic [7:0] y);\n" ++
  "integer i;\n" ++
  "always_comb begin\n" ++
  "  y = x;\n" ++
  "  for (i = 0; i < N; i = i + 0) begin\n" ++
  "    y = ~y;\n" ++
  "  end\n" ++
  "end\n" ++
  "endmodule\n"

private def checkInvalidStep : IO Unit := do
  match parseAndLowerNative invalidStepSource with
  | .ok _ =>
      throw (IO.userError
        "invalid step: native lowering accepted a non-terminating zero-increment loop")
  | .error message =>
      ensure (contains message.toLower "step" || contains message.toLower "loop" ||
        contains message.toLower "increment")
        s!"invalid step: rejection did not identify the loop/step: {message}"

def main : IO UInt32 := do
  checkGenerateOverride
  checkGeneratedHierarchy
  checkProceduralLoop
  checkZeroTrip
  checkInvalidStep
  IO.println "native generate/loop regression tests: PASS"
  return 0
