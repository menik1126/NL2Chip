import Sparkle
import Sparkle.Compiler.Elab
import Tests.TestCircuits
import Tools.SVParser.Lower
import Tools.SVParser.Verify
import LSpec

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Compiler.Elab
open Sparkle.Backend.Verilog
open Lean.Elab.Command
open Lean (Name)
open LSpec

/-!
# Verilog Generation Unit Tests using LSpec

Tests that verify the generated Verilog code by synthesizing modules
and checking the output contains expected patterns.

Run tests: `lake exe verilog-tests`
-/

-- ============================================================================
-- Helper Functions
-- ============================================================================

/-- Check if a string contains a substring -/
def String.containsSubstr (s : String) (sub : String) : Bool :=
  decide ((s.splitOn sub).length > 1)

/-- Synthesize a module and return its Verilog as a string -/
def synthesizeToString (declName : Name) : Lean.MetaM String := do
  let (module, _) ← synthesizeCombinational declName
  return toVerilog module

/-- Synthesize one native-parameter module and return its Verilog. -/
def synthesizeParameterizedToString (declName : Name)
    (defaults : List (String × Nat)) : Lean.MetaM String := do
  let (module, _) ← synthesizeCombinational declName defaults
  return toVerilog module

/-- Synthesize a hierarchical design and return its Verilog as a string -/
def synthesizeDesignToString (declName : Name) : Lean.MetaM String := do
  let design ← synthesizeHierarchical declName
  return toVerilogDesign design

/-- Extract a specific module from multi-module Verilog output -/
def extractModule (verilog : String) (moduleName : String) : String :=
  let lines := verilog.splitOn "\n"
  let moduleStart := s!"module {moduleName}"
  let startIdx := lines.findIdx? (·.containsSubstr moduleStart)
  match startIdx with
  | none => ""
  | some start =>
    let endIdx := lines.drop start |>.findIdx? (·.containsSubstr "endmodule")
    match endIdx with
    | none => ""
    | some relEnd =>
      let endPos := start + relEnd
      String.intercalate "\n" (lines.toArray[start:endPos+1].toList)

-- Manual IR fixtures exercise contracts that the Lean elaborator normally
-- establishes before reaching a backend.
def zeroWidthIR : Sparkle.IR.AST.Module :=
  { (Sparkle.IR.AST.Module.empty "zero_width_ir") with
    inputs := [{ name := "x", ty := .bitVector 0 }]
    outputs := [{ name := "y", ty := .bitVector 0 }]
    body := [.assign "y" (.ref "x")] }

def nativeWidthIR : Sparkle.IR.AST.Module :=
  { (Sparkle.IR.AST.Module.empty "native_width_ir") with
    «parameters» := [{ name := "W", defaultValue := 8 }]
    inputs := [{ name := "x", ty := .bitVector (.param "W") }]
    outputs := [{ name := "y", ty := .bitVector (.param "W") }]
    body := [.assign "y" (.ref "x")] }

def derivedZeroIR : Sparkle.IR.AST.Module :=
  { (Sparkle.IR.AST.Module.empty "derived_zero_ir") with
    «parameters» := [{ name := "W", defaultValue := 4 }]
    inputs := [{ name := "x", ty := .bitVector (.param "W" - 8) }]
    outputs := [{ name := "y", ty := .bitVector (.param "W" - 8) }]
    body := [.assign "y" (.ref "x")] }

def zeroOffsetIR : Sparkle.IR.AST.Module :=
  { (Sparkle.IR.AST.Module.empty "zero_offset_ir") with
    «parameters» := [{ name := "Start", defaultValue := 0 }]
    inputs := [{ name := "x", ty := .bitVector 8 }]
    outputs := [{ name := "y", ty := .bitVector 8 }]
    body := [.assign "y" (.slice (.ref "x") (.param "Start" + 7) (.param "Start"))] }

def collidingNameIR : Sparkle.IR.AST.Module :=
  { (Sparkle.IR.AST.Module.empty "colliding_name_ir") with
    «parameters» := [{ name := "data-width", defaultValue := 8 }]
    inputs := [{ name := "data.width", ty := .bitVector 8 }]
    outputs := [{ name := "out", ty := .bitVector 8 }]
    body := [.assign "out" (.ref "data.width")] }

def exceptIsError : Except String α → Bool
  | .error _ => true
  | .ok _ => false

def exceptIsOk : Except String α → Bool
  | .ok _ => true
  | .error _ => false

def nativeChildIR : Sparkle.IR.AST.Module :=
  { (Sparkle.IR.AST.Module.empty "native_child_ir") with
    «parameters» := [{ name := "W", defaultValue := 8 }]
    inputs := [{ name := "x", ty := .bitVector (.param "W") }]
    outputs := [{ name := "y", ty := .bitVector (.param "W") }]
    body := [.assign "y" (.ref "x")] }

def nativeTopIR : Sparkle.IR.AST.Module :=
  { (Sparkle.IR.AST.Module.empty "native_top_ir") with
    «parameters» := [{ name := "N", defaultValue := 8 }]
    inputs :=
      [{ name := "a", ty := .bitVector (.param "N") },
       { name := "b", ty := .bitVector (.param "N" + 1) }]
    outputs :=
      [{ name := "z0", ty := .bitVector (.param "N") },
       { name := "z1", ty := .bitVector (.param "N" + 1) }]
    wires := []
    body :=
      [.inst "native_child_ir" "u0" [("x", .ref "a"), ("y", .ref "z0")]
        [("W", .param "N")],
       .inst "native_child_ir" "u1" [("x", .ref "b"), ("y", .ref "z1")]
        [("W", .param "N" + 1)]] }

def nativeInternalWireIR : Sparkle.IR.AST.Module :=
  { (Sparkle.IR.AST.Module.empty "native_internal_wire_ir") with
    «parameters» := [{ name := "W", defaultValue := 8 }]
    inputs := [{ name := "x", ty := .bitVector (.param "W") }]
    outputs := [{ name := "y", ty := .bitVector (.param "W") }]
    wires := [{ name := "tmp", ty := .bitVector (.param "W") }]
    body :=
      [.assign "tmp" (.const 1 (.param "W")),
       .assign "y" (.op .xor [.ref "x", .ref "tmp"])] }

def collidingModuleDesign : Sparkle.IR.AST.Design :=
  { topModule := "Audit.A.B"
    modules :=
      [Sparkle.IR.AST.Module.empty "Audit.A.B",
       Sparkle.IR.AST.Module.empty "Audit.A_B"] }

def parsedNativeWidthVerilog : Except String String := do
  let source := "module parsed_native #(parameter W = 8) (\n" ++
    "  input wire [W-1:0] x,\n" ++
    "  output wire [W-1:0] y\n" ++
    ");\n  assign y = x;\nendmodule\n"
  let design ← Tools.SVParser.Lower.parseAndLowerNative source
  let module_ ← match design.modules with
    | [module_] => pure module_
    | _ => throw "expected exactly one parsed module"
  Sparkle.Backend.Verilog.toVerilogChecked module_

def parsedNativeWidthOutput : String :=
  match parsedNativeWidthVerilog with
  | .ok output => output
  | .error error => s!"/* ERROR: {error} */"

def generatedNativeRoundTrip : Except String String := do
  let design ← Tools.SVParser.Lower.parseAndLowerNative parsedNativeWidthOutput
  let module_ ← match design.modules with
    | [module_] => pure module_
    | _ => throw "expected exactly one reparsed module"
  Sparkle.Backend.Verilog.toVerilogChecked module_

def generatedNativeRoundTripOutput : String :=
  match generatedNativeRoundTrip with
  | .ok output => output
  | .error error => s!"/* ERROR: {error} */"

def generatedInternalWireRoundTrip : Except String String := do
  let emitted ← Sparkle.Backend.Verilog.toVerilogChecked nativeInternalWireIR
  let parsed ← Tools.SVParser.Lower.parseAndLowerNative emitted
  let module_ ← match parsed.modules with
    | [module_] => pure module_
    | _ => throw "expected exactly one reparsed internal-wire module"
  Sparkle.Backend.Verilog.toVerilogChecked module_

def generatedInternalWireRoundTripOutput : String :=
  match generatedInternalWireRoundTrip with
  | .ok output => output
  | .error error => s!"/* ERROR: {error} */"

def generatedSequentialRoundTrip : Except String String := do
  let source := "module native_seq #(parameter W = 8) (\n" ++
    "  input logic [(((W) > 0 ? (W) : 1) - 1):0] x, input logic clk, input logic rst,\n" ++
    "  output logic [(((W) > 0 ? (W) : 1) - 1):0] y);\n" ++
    "logic [(((W) > 0 ? (W) : 1) - 1):0] q;\n" ++
    "always_ff @(posedge clk or posedge rst) begin if (rst) q <= $unsigned(((W) > 0 ? (W) : 1)'(0)); else q <= x; end\n" ++
    "assign y = q; endmodule\n"
  let parsed ← Tools.SVParser.Lower.parseAndLowerNative source
  let module_ ← match parsed.modules with
    | [module_] => pure module_
    | _ => throw "expected exactly one sequential module"
  Sparkle.Backend.Verilog.toVerilogChecked module_

def generatedSequentialRoundTripOutput : String :=
  match generatedSequentialRoundTrip with
  | .ok output => output
  | .error error => s!"/* ERROR: {error} */"

def generatedAllOnesSequentialRoundTrip : Except String String := do
  let source := "module native_seq_ones #(parameter W = 8) (\n" ++
    "  input logic [(((W) > 0 ? (W) : 1) - 1):0] x, input logic clk, input logic rst,\n" ++
    "  output logic [(((W) > 0 ? (W) : 1) - 1):0] y);\n" ++
    "logic [(((W) > 0 ? (W) : 1) - 1):0] q;\n" ++
    "always_ff @(posedge clk or posedge rst) begin if (rst) q <= $unsigned(((W) > 0 ? (W) : 1)'(-1)); else q <= x; end\n" ++
    "assign y = q; endmodule\n"
  let parsed ← Tools.SVParser.Lower.parseAndLowerNative source
  let module_ ← match parsed.modules with
    | [module_] => pure module_
    | _ => throw "expected exactly one all-ones sequential module"
  Sparkle.Backend.Verilog.toVerilogChecked module_

def generatedAllOnesSequentialRoundTripOutput : String :=
  match generatedAllOnesSequentialRoundTrip with
  | .ok output => output
  | .error error => s!"/* ERROR: {error} */"

def generatedPositiveSequentialRoundTrip : Except String String := do
  let source := "module native_seq_positive #(parameter W = 8) (\n" ++
    "  input logic [(((W) > 0 ? (W) : 1) - 1):0] x, input logic clk, input logic rst,\n" ++
    "  output logic [(((W) > 0 ? (W) : 1) - 1):0] y);\n" ++
    "logic [(((W) > 0 ? (W) : 1) - 1):0] q;\n" ++
    "always_ff @(posedge clk or posedge rst) begin " ++
    "if (rst) q <= $unsigned(((W) > 0 ? (W) : 1)'(511)); else q <= x; end\n" ++
    "assign y = q; endmodule\n"
  let parsed ← Tools.SVParser.Lower.parseAndLowerNative source
  let module_ ← match parsed.modules with
    | [module_] => pure module_
    | _ => throw "expected exactly one positive-reset sequential module"
  Sparkle.Backend.Verilog.toVerilogChecked module_

def generatedPositiveSequentialRoundTripOutput : String :=
  match generatedPositiveSequentialRoundTrip with
  | .ok output => output
  | .error error => s!"/* ERROR: {error} */"

def symbolicMemoryFailsClosed : Bool :=
  let source := "module mem #(parameter AW = 4, parameter DW = 8) (input wire clk);\n" ++
    "reg [DW-1:0] storage [0:(2*AW)-1];\nendmodule\n"
  exceptIsError (Tools.SVParser.Lower.parseAndLowerNative source)

/-- A parameter-sized cast of a nonliteral value cannot be erased: inside a
    concat, dropping it changes field boundaries and therefore functionality. -/
def symbolicNonliteralSizedCastSupported : Bool :=
  let source := "module casted #(parameter W = 3) " ++
    "(input wire [W:0] x, output wire [(2*W)-1:0] y); " ++
    "assign y = {(W)'(x), (W)'(x)}; endmodule\n"
  exceptIsOk (Tools.SVParser.Lower.parseAndLowerNative source)

def parameterizedAlwaysCombFailsClosed : Bool :=
  let source := "module procedural #(parameter W = 257) " ++
    "(input logic [W-1:0] x, output logic [W-1:0] y); " ++
    "always_comb begin y = x; end endmodule\n"
  exceptIsError (Tools.SVParser.Lower.parseAndLowerNative source)

def parameterizedSignedCastFailsClosed : Bool :=
  let source := "module signed_shift #(parameter W = 257) " ++
    "(input logic [W-1:0] x, output logic [W-1:0] y); " ++
    "assign y = $signed(x) >>> 1; endmodule\n"
  exceptIsError (Tools.SVParser.Lower.parseAndLowerNative source)

def nativeSignedDeclarationsFailClosed : Bool :=
  let sources :=
    ["module signed_port (input logic signed [7:0] x, output logic [7:0] y); assign y = x; endmodule\n",
     "module signed_param #(parameter signed [7:0] P = 1) (input logic x, output logic y); assign y = x; endmodule\n",
     "module signed_wire (input logic [7:0] x, output logic [7:0] y); wire signed [7:0] w = x; assign y = w; endmodule\n",
     "module signed_reg (input logic [7:0] x, output logic [7:0] y); reg signed [7:0] r; assign y = r; endmodule\n"]
  sources.all fun source =>
    exceptIsError (Tools.SVParser.Lower.parseAndLowerNative source)

def legacySignedDeclarationStillAccepted : Bool :=
  let source :=
    "module legacy_signed (input logic signed [7:0] x, output logic [7:0] y); " ++
    "assign y = x; endmodule\n"
  exceptIsOk (Tools.SVParser.Lower.parseAndLower source)

def signedOperandSizedCastFailsClosed : Bool :=
  let source :=
    "module signed_resize (input logic [31:0] x, output logic [63:0] y); " ++
    "assign y = (64)'($signed(x)); endmodule\n"
  exceptIsError (Tools.SVParser.Lower.parseAndLowerNative source) &&
    exceptIsError (Tools.SVParser.Lower.parseAndLower source)

def legacyStandaloneSignedStillAccepted : Bool :=
  let source :=
    "module standalone_signed (input logic [31:0] x, output logic [31:0] y); " ++
    "assign y = $signed(x); endmodule\n"
  exceptIsOk (Tools.SVParser.Lower.parseAndLower source)

def concreteNonliteralSizedCastSupported : Bool :=
  let source := "module casted #(parameter W = 3) " ++
    "(input wire [W:0] x, output wire [(2*W)-1:0] y); " ++
    "assign y = {(3)'(x), (3)'(x)}; endmodule\n"
  exceptIsOk (Tools.SVParser.Lower.parseAndLowerNative source)

def parameterizedGenerateRejected : Bool :=
  let source := "module generated #(parameter W = 8) (input wire x, output wire y);\n" ++
    "generate if (W > 1) begin assign y = x; end else begin assign y = 1'b0; end endgenerate\n" ++
    "endmodule\n"
  exceptIsError (Tools.SVParser.Lower.parseAndLowerNative source)

def parameterizedForRejected : Bool :=
  let source := "module looped #(parameter W = 8) (input wire [W-1:0] x, output reg [W-1:0] y);\n" ++
    "integer i; always @* begin for (i = 0; i < W; i = i + 1) y[i] = x[i]; end\n" ++
    "endmodule\n"
  exceptIsError (Tools.SVParser.Lower.parseAndLowerNative source)

def noncanonicalFatalGenerateRejected : Bool :=
  let source := "module guarded #(parameter W = 8) (input wire x, output wire y);\n" ++
    "generate if (W == 0) begin : user_required_guard initial $fatal(1, \"W invalid\"); end endgenerate\n" ++
    "assign y = x; endmodule\n"
  exceptIsError (Tools.SVParser.Lower.parseAndLowerNative source)

def dependentParameterDefaultRejected : Bool :=
  let source := "module dependent #(parameter W = 8, parameter X = 2*W) " ++
    "(input wire [X-1:0] x, output wire [X-1:0] y); assign y = x; endmodule\n"
  exceptIsError (Tools.SVParser.Lower.parseAndLowerNative source)

def constantEqualityGenerateSelectsThen : Bool :=
  let source := "module gen_eq (input wire x, output wire y); " ++
    "generate if (1 == 1) begin assign y = x; end else begin assign y = 1'b0; end endgenerate " ++
    "endmodule\n"
  match Tools.SVParser.Lower.parseAndLower source with
  | .ok { modules := [module_], .. } =>
    module_.body.any fun statement => match statement with
      | .assign "y" (.ref "x") => true
      | _ => false
  | _ => false

def unsupportedGenerateConditionRejected : Bool :=
  let source := "module gen_unknown (input wire x, output wire y); " ++
    "generate if ({x,x}) begin assign y = x; end else begin assign y = 1'b0; end endgenerate " ++
    "endmodule\n"
  exceptIsError (Tools.SVParser.Lower.parseAndLower source)

def legacyParameterizedWidthSpecializes : Bool :=
  let source := "module legacy #(parameter W = 8) " ++
    "(input wire [W-1:0] x, output wire [W-1:0] y); assign y = x; endmodule\n"
  match Tools.SVParser.Lower.parseAndLower source with
  | .ok { modules := [module_], .. } =>
    module_.parameters.isEmpty && module_.inputs.head?.bind (·.ty.bitWidth) == some 8
  | _ => false

def legacyHierarchyOverrideSpecializes : Bool :=
  let source := "module child #(parameter W = 8) (input wire x, output wire y); " ++
    "generate if (W == 4) begin assign y = x; end else begin assign y = 1'b0; end endgenerate endmodule\n" ++
    "module top #(parameter P = 3) (input wire x, output wire y); " ++
    "child #(.W(P+1)) u (.x(x), .y(y)); endmodule\n"
  match Tools.SVParser.Lower.parseAndLowerFlat source with
  | .ok { modules := [module_], .. } =>
    module_.body.any fun statement => match statement with
      | .assign "_gen_u_y" (.ref "_gen_u_x") => true
      | .assign "y" (.ref _) => true
      | _ => false
  | _ => false

def nativeHierarchyRoundTrip : Except String String := do
  let source := "module native_child #(parameter W = 8) " ++
    "(input wire [W-1:0] x, output wire [W-1:0] y); assign y = x; endmodule\n" ++
    "module native_top #(parameter N = 8) " ++
    "(input wire [N-1:0] a, input wire [N:0] b, output wire [N-1:0] z0, output wire [N:0] z1); " ++
    "native_child #(.W(N)) u0 (.x(a), .y(z0)); " ++
    "native_child #(.W(N+1)) u1 (.x(b), .y(z1)); endmodule\n"
  let design ← Tools.SVParser.Lower.parseAndLowerNative source
  unless design.topModule == "native_top" do
    throw s!"wrong native hierarchy root: {design.topModule}"
  Sparkle.Backend.Verilog.toVerilogDesignChecked design

def nativeHierarchyRoundTripOutput : String :=
  match nativeHierarchyRoundTrip with
  | .ok output => output
  | .error error => s!"/* ERROR: {error} */"

def nativeWideComplementRoundTrip : Except String String := do
  let source := "module wide_not #(parameter W = 257) " ++
    "(output wire [W-1:0] y); " ++
    "assign y = ~$unsigned(((W) > 0 ? (W) : 1)'(0)); endmodule\n"
  let design ← Tools.SVParser.Lower.parseAndLowerNative source
  let module_ ← match design.modules with
    | [module_] => pure module_
    | _ => throw "expected one wide complement module"
  Sparkle.Backend.Verilog.toVerilogChecked module_

def nativeWideComplementRoundTripOutput : String :=
  match nativeWideComplementRoundTrip with
  | .ok output => output
  | .error error => s!"/* ERROR: {error} */"

def nativeWideReductionAndRoundTrip : Except String String := do
  let source := "module wide_reduce #(parameter W = 257) " ++
    "(input wire [W-1:0] x, output wire y); assign y = &x; endmodule\n"
  let design ← Tools.SVParser.Lower.parseAndLowerNative source
  let module_ ← match design.modules with
    | [module_] => pure module_
    | _ => throw "expected one wide reduction module"
  Sparkle.Backend.Verilog.toVerilogChecked module_

def nativeWideReductionAndRoundTripOutput : String :=
  match nativeWideReductionAndRoundTrip with
  | .ok output => output
  | .error error => s!"/* ERROR: {error} */"

def symbolicSequentialResetSemantics : IO Bool := do
  let tempDir := "/tmp/sparkle_verilog_tests"
  let sourcePath := s!"{tempDir}/symbolic_sequential_resets.sv"
  let executable := s!"{tempDir}/symbolic_sequential_resets.vvp"
  let bench :=
    "module tb;\n" ++
    "logic clk, rst; logic [2:0] x3; logic [16:0] x17; logic [15:0] x16;\n" ++
    "wire [2:0] ones3; wire [16:0] ones17; wire [15:0] positive16;\n" ++
    "native_seq_ones #(.W(3)) d3 " ++
    "(.x(x3), .clk(clk), .rst(rst), .y(ones3));\n" ++
    "native_seq_ones #(.W(17)) d17 " ++
    "(.x(x17), .clk(clk), .rst(rst), .y(ones17));\n" ++
    "native_seq_positive #(.W(16)) d16 " ++
    "(.x(x16), .clk(clk), .rst(rst), .y(positive16));\n" ++
    "initial begin clk=0; rst=0; x3='0; x17='0; x16='0; #1; rst=1; #1;\n" ++
    "  if (ones3 !== 3'b111) $fatal(1, \"W=3 all-ones reset failed\");\n" ++
    "  if (ones17 !== 17'h1ffff) $fatal(1, \"W=17 all-ones reset failed\");\n" ++
    "  if (positive16 !== 16'h01ff) $fatal(1, \"W=16 positive reset failed\");\n" ++
    "  $display(\"PASS symbolic sequential resets\"); $finish;\n" ++
    "end endmodule\n"
  IO.FS.createDirAll tempDir
  IO.FS.writeFile sourcePath
    (generatedAllOnesSequentialRoundTripOutput ++ "\n" ++
      generatedPositiveSequentialRoundTripOutput ++ "\n" ++ bench)
  let compiled ← IO.Process.output {
    cmd := "iverilog"
    args := #["-g2012", "-s", "tb", "-o", executable, sourcePath]
  }
  if compiled.exitCode != 0 then
    IO.eprintln s!"symbolic reset Icarus compile failed:\n{compiled.stderr}"
    return false
  let simulated ← IO.Process.output { cmd := "vvp", args := #[executable] }
  if simulated.exitCode != 0 then
    IO.eprintln s!"symbolic reset Icarus simulation failed:\n{simulated.stdout}{simulated.stderr}"
    return false
  return simulated.stdout.containsSubstr "PASS symbolic sequential resets"

-- ============================================================================
-- Test Suite
-- ============================================================================

/-- Structure to hold synthesized Verilog for testing -/
structure VerilogOutputs where
  addVerilog : String
  andVerilog : String
  muxVerilog : String
  flipflopVerilog : String
  hierarchicalVerilog : String
  generic4Verilog : String
  generic16Verilog : String
  closedWidthVerilog : String
  nativeIdentityVerilog : String
  nativeAddVerilog : String
  nativeDerivedVerilog : String
  nativeZeroExtendLambdaVerilog : String
  nativeZeroExtendPartialVerilog : String
  nativeSetWidthNarrowVerilog : String
  nativeConstantVerilog : String
  nativeRegisterVerilog : String
  nativeMemoryVerilog : String

/-- Synthesize all modules for testing -/
def synthesizeAll : Lean.MetaM VerilogOutputs := do
  let addVerilog ← synthesizeToString `test_add
  let andVerilog ← synthesizeToString `test_and
  let muxVerilog ← synthesizeToString `test_mux
  let flipflopVerilog ← synthesizeToString `test_flipflop
  let hierarchicalVerilog ← synthesizeDesignToString `test_hierarchical_alu
  let generic4Verilog ← synthesizeToString `testGenericIdentity4
  let generic16Verilog ← synthesizeToString `testGenericIdentity16
  let closedWidthVerilog ← synthesizeToString `testClosedWidthIdentity
  let nativeIdentityVerilog ← synthesizeParameterizedToString `testGenericIdentity [("width", 8)]
  let nativeAddVerilog ← synthesizeParameterizedToString `testNativeAdd [("width", 8)]
  let nativeDerivedVerilog ← synthesizeParameterizedToString `testNativeDerivedWidth [("width", 8)]
  let nativeZeroExtendLambdaVerilog ←
    synthesizeParameterizedToString `testNativeZeroExtendLambda [("width", 8)]
  let nativeZeroExtendPartialVerilog ←
    synthesizeParameterizedToString `testNativeZeroExtendPartial [("width", 8)]
  let nativeSetWidthNarrowVerilog ←
    synthesizeParameterizedToString `testNativeSetWidthNarrow [("width", 8)]
  let nativeConstantVerilog ← synthesizeParameterizedToString `testNativeConstant [("width", 8)]
  let nativeRegisterVerilog ← synthesizeParameterizedToString `testNativeRegister [("width", 8)]
  let nativeMemoryVerilog ← synthesizeParameterizedToString `testNativeMemory
    [("addrWidth", 4), ("dataWidth", 8)]
  return {
    addVerilog, andVerilog, muxVerilog, flipflopVerilog, hierarchicalVerilog,
    generic4Verilog, generic16Verilog, closedWidthVerilog,
    nativeIdentityVerilog, nativeAddVerilog, nativeDerivedVerilog,
    nativeZeroExtendLambdaVerilog, nativeZeroExtendPartialVerilog,
    nativeSetWidthNarrowVerilog,
    nativeConstantVerilog, nativeRegisterVerilog, nativeMemoryVerilog
  }

/-- Create test suite from synthesized outputs -/
def makeTests (outputs : VerilogOutputs)
    (symbolicResetSemantics : Bool) : TestSeq :=
  let addModule := extractModule outputs.addVerilog "test_add"
  let hierTopModule := extractModule outputs.hierarchicalVerilog "test_hierarchical_alu"
  let nativeIRVerilog := toVerilog nativeWidthIR
  let flattenedNative := Tools.SVParser.Lower.flattenDesign {
    topModule := nativeTopIR.name, modules := [nativeTopIR, nativeChildIR] }
  let flattenedNativeVerilog := toVerilogDesign flattenedNative

  group "Verilog Generation Tests" (
    group "Combinational Circuits" (
      group "test_add (Addition)" (
        test "module declared" (outputs.addVerilog.containsSubstr "module test_add") $
        test "has assign statement" (outputs.addVerilog.containsSubstr "assign") $
        test "has addition operation" (outputs.addVerilog.containsSubstr " + ") $
        test "NO always block (combinational)" (!addModule.containsSubstr "always") $
        test "NO clock signal (combinational)" (!addModule.containsSubstr "clk")
      ) ++
      group "test_and (AND Gate)" (
        test "module declared" (outputs.andVerilog.containsSubstr "module test_and") $
        test "has AND operation" (outputs.andVerilog.containsSubstr " & ")
      ) ++
      group "test_mux (Multiplexer)" (
        test "module declared" (outputs.muxVerilog.containsSubstr "module test_mux") $
        test "has ternary operator" (outputs.muxVerilog.containsSubstr " ? ")
      )
    ) ++
    group "Hierarchical Circuits" (
      group "test_hierarchical_alu" (
        test "top module declared"
          (outputs.hierarchicalVerilog.containsSubstr "module test_hierarchical_alu") $
        test "has addition (inlined test_add)"
          (hierTopModule.containsSubstr "_gen_addResult") $
        test "has subtraction (inlined test_sub)"
          (hierTopModule.containsSubstr "_gen_subResult") $
        test "has mux for op select"
          (hierTopModule.containsSubstr "_gen_op ? ")
      )
    ) ++
    group "Sequential Circuits" (
      group "test_flipflop (Register)" (
        test "module declared"
          (outputs.flipflopVerilog.containsSubstr "module test_flipflop") $
        test "has clock port"
          (outputs.flipflopVerilog.containsSubstr "input logic clk") $
        test "has reset port"
          (outputs.flipflopVerilog.containsSubstr "input logic rst") $
        test "has sequential block"
          (outputs.flipflopVerilog.containsSubstr "always_ff @(posedge clk") $
        test "has reset condition"
          (outputs.flipflopVerilog.containsSubstr "if (rst)")
      )
    ) ++
    group "Concrete Generic Specialization" (
      test "4-bit wrapper emits 4-bit ports"
        (outputs.generic4Verilog.containsSubstr "input logic [3:0]") $
      test "16-bit wrapper emits 16-bit ports"
        (outputs.generic16Verilog.containsSubstr "input logic [15:0]") $
      test "16-bit wrapper is not silently narrowed to 8 bits"
        (!outputs.generic16Verilog.containsSubstr "input logic [7:0]") $
      test "closed width expressions reduce before synthesis"
        (outputs.closedWidthVerilog.containsSubstr "input logic [15:0]")
    ) ++
    group "Native Symbolic Widths" (
      test "module declares an overrideable width parameter"
        (outputs.nativeIdentityVerilog.containsSubstr "parameter width = 8") $
      test "identity ports retain symbolic width"
        (outputs.nativeIdentityVerilog.containsSubstr
          "logic [(((width) > 0 ? (width) : 1) - 1):0]") $
      test "arithmetic datapath retains symbolic width"
        (outputs.nativeAddVerilog.containsSubstr "assign _tmp_result_0 = (_gen_a + _gen_b)") $
      test "derived widths remain symbolic"
        (outputs.nativeDerivedVerilog.containsSubstr
          "logic [((((width + 1)) > 0 ? ((width + 1)) : 1) - 1):0]") $
      test "zeroExtend lambda lowers to a native resize"
        (outputs.nativeZeroExtendLambdaVerilog.containsSubstr "$unsigned(" &&
          outputs.nativeZeroExtendLambdaVerilog.containsSubstr "width + 1") $
      test "zeroExtend partial application lowers to a native resize"
        (outputs.nativeZeroExtendPartialVerilog.containsSubstr "$unsigned(" &&
          outputs.nativeZeroExtendPartialVerilog.containsSubstr "width + 1") $
      test "setWidth narrowing lowers to a native resize"
        (outputs.nativeSetWidthNarrowVerilog.containsSubstr "$unsigned(" &&
          outputs.nativeSetWidthNarrowVerilog.containsSubstr "parameter width = 8") $
      test "parameter-sized constant uses an SV sized cast"
        (outputs.nativeConstantVerilog.containsSubstr
          "$unsigned(((width) > 0 ? (width) : 1)'(1))") $
      test "parameter-sized reset uses an SV sized cast"
        (outputs.nativeRegisterVerilog.containsSubstr
          "<= $unsigned(((width) > 0 ? (width) : 1)'(0))") $
      test "memory exposes both native parameters"
        (outputs.nativeMemoryVerilog.containsSubstr "parameter addrWidth = 4") $
      test "memory depth and data width remain symbolic"
        (outputs.nativeMemoryVerilog.containsSubstr
          "logic [(((dataWidth) > 0 ? (dataWidth) : 1) - 1):0] _tmp_result_0 [0:((((2 ** addrWidth)) > 0 ? ((2 ** addrWidth)) : 1) - 1)]") $
      test "symbolic ranges are clamped before reporting a zero override"
        (nativeIRVerilog.containsSubstr "((W) > 0 ? (W) : 1)") $
      test "zero-width overrides receive a controlled fatal guard"
        (nativeIRVerilog.containsSubstr "Sparkle invalid hardware dimension") $
      test "negative Nat overrides receive a controlled fatal guard"
        (nativeIRVerilog.containsSubstr "Sparkle Nat parameter W must be nonnegative") $
      test "literal zero widths are rejected by checked Verilog"
        (exceptIsError (toVerilogChecked zeroWidthIR)) $
      test "derived zero widths are rejected under defaults"
        (exceptIsError (toVerilogChecked derivedZeroIR)) $
      test "zero remains legal for an index-only Nat parameter"
        (exceptIsOk (toVerilogChecked zeroOffsetIR)) $
      test "sanitized parameter/port name collisions are rejected"
        (exceptIsError (toVerilogChecked collidingNameIR)) $
      test "sanitized module-name collisions are rejected across a design"
        (exceptIsError (toVerilogDesignChecked collidingModuleDesign)) $
      test "CppSim rejects literal zero widths"
        (exceptIsError (Sparkle.Backend.CppSim.toCppSimChecked zeroWidthIR)) $
      test "verification-model generation rejects native parameters"
        (exceptIsError (Tools.SVParser.Verify.moduleToLean nativeWidthIR)) $
      test "SV parser retains a module parameter instead of a constant wire"
        (parsedNativeWidthOutput.containsSubstr "parameter W = 8" &&
          !parsedNativeWidthOutput.containsSubstr "assign W =") $
      test "SV parser retains W-1:0 as a native symbolic width"
        (parsedNativeWidthOutput.containsSubstr "((W) > 0 ? (W) : 1)" &&
          !parsedNativeWidthOutput.containsSubstr "[31:0]") $
      test "backend safe symbolic ranges parse and emit again"
        (generatedNativeRoundTripOutput.containsSubstr "parameter W = 8" &&
          generatedNativeRoundTripOutput.containsSubstr "((W) > 0 ? (W) : 1)") $
      test "backend logic declarations and parameter-sized constants round-trip"
        (generatedInternalWireRoundTripOutput.containsSubstr "logic" &&
          generatedInternalWireRoundTripOutput.containsSubstr "$unsigned(" &&
          generatedInternalWireRoundTripOutput.containsSubstr "'(1))") $
      test "always_ff and parameter-sized reset constants round-trip"
        (generatedSequentialRoundTripOutput.containsSubstr "always_ff" &&
          generatedSequentialRoundTripOutput.containsSubstr "$unsigned(" &&
          generatedSequentialRoundTripOutput.containsSubstr "'(0))") $
      test "symbolic reset constants drive the actual always_ff reset branch"
        symbolicResetSemantics $
      test "symbolic unpacked memory depth fails closed"
        symbolicMemoryFailsClosed $
      test "symbolic nonliteral sized casts lower to explicit resize nodes"
        symbolicNonliteralSizedCastSupported $
      test "parameterized always_comb fails closed instead of narrowing SSA wires"
        parameterizedAlwaysCombFailsClosed $
      test "parameterized signed casts fail closed instead of losing signedness"
        parameterizedSignedCastFailsClosed $
      test "native lowering rejects signed declarations instead of erasing signedness"
        nativeSignedDeclarationsFailClosed $
      test "legacy lowering remains compatible with signed declarations"
        legacySignedDeclarationStillAccepted $
      test "sized casts reject nested signed operands on every lowering path"
        signedOperandSizedCastFailsClosed $
      test "legacy standalone signed conversion remains accepted"
        legacyStandaloneSignedStillAccepted $
      test "concrete nonliteral sized casts lower to explicit resize nodes"
        concreteNonliteralSizedCastSupported $
      test "parameter-dependent generate fails closed for native overrides"
        parameterizedGenerateRejected $
      test "parameter-dependent procedural for fails closed for native overrides"
        parameterizedForRejected $
      test "noncanonical user fatal-generate is not silently discarded"
        noncanonicalFatalGenerateRejected $
      test "dependent native parameter defaults fail closed"
        dependentParameterDefaultRejected $
      test "constant equality generate selects the then branch"
        constantEqualityGenerateSelectsThen $
      test "unevaluable generate condition fails closed"
        unsupportedGenerateConditionRejected $
      test "legacy SV lowering specializes parameterized port widths"
        legacyParameterizedWidthSpecializes $
      test "legacy hierarchy specializes parent-dependent child overrides"
        legacyHierarchyOverrideSpecializes $
      test "native hierarchy selects the root and preserves distinct overrides"
        (nativeHierarchyRoundTripOutput.containsSubstr "module native_top" &&
          nativeHierarchyRoundTripOutput.containsSubstr ".W(N)" &&
          nativeHierarchyRoundTripOutput.containsSubstr ".W((N + 1))") $
      test "wide bitwise complement remains width preserving"
        (nativeWideComplementRoundTripOutput.containsSubstr "~" &&
          nativeWideComplementRoundTripOutput.containsSubstr "$unsigned(" &&
          !nativeWideComplementRoundTripOutput.containsSubstr "32'hffffffff") $
      test "wide reduction AND uses a width-preserving complement"
        (nativeWideReductionAndRoundTripOutput.containsSubstr "~x" &&
          !nativeWideReductionAndRoundTripOutput.containsSubstr "32'hffffffff") $
      test "flattening preserves the parent parameter"
        (flattenedNativeVerilog.containsSubstr "parameter N = 8") $
      test "flattening substitutes each child override independently"
        (flattenedNativeVerilog.containsSubstr "(N + 1)" &&
          !flattenedNativeVerilog.containsSubstr "parameter W =")
    )
  )

-- ============================================================================
-- Main Entry Point
-- ============================================================================

def main : IO UInt32 := do
  IO.println "╔════════════════════════════════════════╗"
  IO.println "║  Verilog Generation Unit Tests        ║"
  IO.println "╚════════════════════════════════════════╝"
  IO.println ""

  -- Initialize Lean search path
  Lean.initSearchPath (← Lean.findSysroot)

  -- Import required modules
  let env ← Lean.importModules
    #[{module := `Sparkle.Compiler.Elab}, {module := `Sparkle.Backend.Verilog}, {module := `Tests.TestCircuits}]
    {}
    (trustLevel := 1024)

  let coreCtx : Lean.Core.Context := {
    fileName := "<tests>"
    fileMap := default
  }
  let coreState : Lean.Core.State := { env := env }

  let (outputs, _) ← Lean.Meta.MetaM.toIO
    synthesizeAll
    coreCtx
    coreState

  -- Create and run tests
  let symbolicResetSemantics ← symbolicSequentialResetSemantics
  let tests := makeTests outputs symbolicResetSemantics
  lspecIO (Std.HashMap.ofList [("verilog", [tests])]) []
