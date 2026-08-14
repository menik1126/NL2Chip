import Sparkle.Backend.Verilog
import Sparkle.IR.Specialize
import Tools.SVParser.Lower

open Sparkle.IR.AST
open Sparkle.IR.Specialize
open Sparkle.Backend.Verilog
open Tools.SVParser.Lower

/-!
# Native dependent-localparam regression tests

Run directly with:

    lake env lean Tests/DependentLocalparamTests.lean
-/

private def contains (text fragment : String) : Bool :=
  decide ((text.splitOn fragment).length > 1)

private def ensure (condition : Bool) (message : String) : IO Unit :=
  unless condition do throw (IO.userError message)

private def requireOk : Except String α → IO α
  | .ok value => pure value
  | .error message => throw (IO.userError message)

private def requireError (label source : String) : IO Unit := do
  match parseAndLowerNative source with
  | .ok _ => throw (IO.userError s!"{label}: native lowering unexpectedly succeeded")
  | .error _ => pure ()

private def requireModule (design : Design) (name : String) : IO Module :=
  match design.findModule name with
  | some module_ => pure module_
  | none => throw (IO.userError
      s!"missing module '{name}' (available: {design.modules.map (·.name)})")

private def runIcarus (verilog bench : String) : IO Unit := do
  let tempDir := "/tmp/sparkle_dependent_localparam_tests"
  let sourcePath := s!"{tempDir}/dependent_localparam.sv"
  let executablePath := s!"{tempDir}/dependent_localparam.vvp"
  IO.FS.createDirAll tempDir
  IO.FS.writeFile sourcePath (verilog ++ "\n" ++ bench)
  let compiled ← IO.Process.output {
    cmd := "iverilog"
    args := #["-g2012", "-s", "tb", "-o", executablePath, sourcePath]
  }
  unless compiled.exitCode == 0 do
    throw (IO.userError s!"Icarus compilation failed:\n{compiled.stdout}{compiled.stderr}")
  let simulated ← IO.Process.output { cmd := "vvp", args := #[executablePath] }
  unless simulated.exitCode == 0 do
    throw (IO.userError s!"Icarus simulation failed:\n{simulated.stdout}{simulated.stderr}")
  ensure (contains simulated.stdout "PASS dependent localparam")
    "Icarus did not report the dependent-localparam completion marker"

private def dependentSource : String :=
  "module dep_leaf #(parameter N = 4) (\n" ++
  "  input logic [N-1:0] x, output logic [N-1:0] y);\n" ++
  "assign y = x;\n" ++
  "endmodule\n" ++
  "module dependent_localparam #(parameter W = 3) (\n" ++
  "  input logic [W-1:0] x,\n" ++
  "  output logic [(W + 1)-1:0] cast_y,\n" ++
  "  output logic [(W + 1)-1:0] child_y,\n" ++
  "  output logic wide);\n" ++
  "localparam BASE = W;\n" ++
  "localparam K = BASE + 1;\n" ++
  "logic [K-1:0] widened;\n" ++
  "assign widened = (K)'(x);\n" ++
  "assign cast_y = widened;\n" ++
  "generate if (K > 8) begin : is_wide\n" ++
  "  assign wide = 1'b1;\n" ++
  "end else begin : is_narrow\n" ++
  "  assign wide = 1'b0;\n" ++
  "end endgenerate\n" ++
  "dep_leaf #(.N(K)) u_leaf (.x(widened), .y(child_y));\n" ++
  "endmodule\n"

private def dependentBench : String :=
  "module tb;\n" ++
  "logic [2:0] x3; logic [8:0] x9;\n" ++
  "wire [3:0] c3, l3; wire w3;\n" ++
  "wire [9:0] c9, l9; wire w9;\n" ++
  "dependent_localparam #(.W(3)) d3 (.x(x3), .cast_y(c3), .child_y(l3), .wide(w3));\n" ++
  "dependent_localparam #(.W(9)) d9 (.x(x9), .cast_y(c9), .child_y(l9), .wide(w9));\n" ++
  "initial begin\n" ++
  "  x3 = 3'b101; x9 = 9'h12d; #1;\n" ++
  "  if (c3 !== 4'b0101 || l3 !== 4'b0101 || w3 !== 1'b0) $fatal(1);\n" ++
  "  if (c9 !== 10'h12d || l9 !== 10'h12d || w9 !== 1'b1) $fatal(1);\n" ++
  "  $display(\"PASS dependent localparam\"); $finish;\n" ++
  "end endmodule\n"

private def checkDependentAlias : IO Unit := do
  let design ← requireOk (parseAndLowerNative dependentSource)
  let top ← requireModule design "dependent_localparam"
  ensure (!(top.wires.any fun wire => wire.name == "K" || wire.name == "BASE"))
    "dependent elaboration localparams were materialized as hardware wires"
  ensure (!(top.body.any fun statement => match statement with
      | .assign name _ => name == "K"
      | _ => false))
    "elaboration localparam K was materialized as a hardware assignment"
  ensure (top.dimensionExpressions.all fun dimension =>
      !dimension.parameters.contains "K")
    "localparam K escaped into IR as an undeclared symbolic parameter"
  let verilog ← requireOk (toVerilogDesignChecked design)
  ensure (!contains verilog "localparam K")
    "emitted design redeclared the internal alias instead of expanding it"
  runIcarus verilog dependentBench

  let specialized ← requireOk (specializeDesign design [("W", 9)])
  let specializedTop ← requireModule specialized "dependent_localparam"
  ensure specializedTop.parameters.isEmpty
    "native specialization retained top-level parameter W"
  ensure (specializedTop.dimensionExpressions.all (·.isConcrete))
    "native specialization left a symbolic dependent-localparam dimension"
  let specializedLeaf ← match specialized.modules.find? fun module_ =>
      module_.name.startsWith "dep_leaf" with
    | some module_ => pure module_
    | none => throw (IO.userError "native specialization lost the dependent child")
  ensure (specializedLeaf.dimensionExpressions.all (·.isConcrete))
    "native specialization did not propagate the dependent instance override"

private def checkOrdinaryDataLocalparam : IO Unit := do
  let source :=
    "module data_local #(parameter W = 8) (output logic [W-1:0] y);\n" ++
    "localparam DATA = W + 1;\n" ++
    "assign y = DATA;\n" ++
    "endmodule\n"
  let design ← requireOk (parseAndLowerNative source)
  let module_ ← requireModule design "data_local"
  ensure (module_.wires.any (·.name == "DATA"))
    "ordinary data localparam no longer follows the compatible materialized path"
  ensure (module_.body.any fun statement => match statement with
      | .assign name _ => name == "DATA"
      | _ => false)
    "ordinary data localparam lost its constant assignment"

private def checkFailures : IO Unit := do
  requireError "forward localparam" <|
    "module bad_forward #(parameter W=3) (output logic y);\n" ++
    "localparam K = J + 1;\nlocalparam J = W + 1;\n" ++
    "logic [K-1:0] t;\nassign y=t[0];\nendmodule\n"
  requireError "recursive localparam" <|
    "module bad_recursive #(parameter W=3) (output logic y);\n" ++
    "localparam K = K + 1;\nlogic [K-1:0] t;\n" ++
    "assign y=t[0];\nendmodule\n"
  requireError "unsupported localparam" <|
    "module bad_expression #(parameter W=3) (output logic y);\n" ++
    "localparam K = (W == 0) ? 1 : 2;\nlogic [K-1:0] t;\n" ++
    "assign y=t[0];\nendmodule\n"
  requireError "raw subtract localparam" <|
    "module bad_sub #(parameter W=3) (output logic y);\n" ++
    "localparam K = W - 1;\nlogic [K-1:0] t;\n" ++
    "assign y=t[0];\nendmodule\n"
  requireError "ambiguous untyped shift width" <|
    "module bad_shift_width #(parameter W=40) (output logic y);\n" ++
    "localparam K = 1 << W;\n" ++
    "generate if (K != 0) begin assign y=1'b1; end " ++
    "else begin assign y=1'b0; end endgenerate\nendmodule\n"
  requireError "narrow parameter" <|
    "module narrow_parameter #(parameter [3:0] W=3) (output logic [W-1:0] y);\nassign y='0;\nendmodule\n"
  requireError "wide parameter" <|
    "module wide_parameter #(parameter [63:0] W=3) (output logic [W-1:0] y);\nassign y='0;\nendmodule\n"
  requireError "oversized parameter default" <|
    "module oversized_parameter #(parameter W=4294967296) (output logic y);\nassign y=1'b0;\nendmodule\n"
  let canonical ← requireOk (parseAndLowerNative
    ("module canonical_parameter #(parameter [31:0] W=3) (output logic [W-1:0] y);\n" ++
      "assign y=0;\nendmodule\n"))
  let module_ ← requireModule canonical "canonical_parameter"
  ensure (module_.parameters.any fun parameter =>
      parameter.name == "W" && parameter.defaultValue == 3)
    "canonical unsigned [31:0] parameter contract was rejected"

def main : IO UInt32 := do
  checkDependentAlias
  checkOrdinaryDataLocalparam
  checkFailures
  IO.println "dependent localparam regression tests: PASS"
  return 0

#eval main
