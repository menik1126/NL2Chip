/-
  End-to-end tests for unsigned SystemVerilog sized casts.

  The important invariant is that `(W)'(x)` is represented by an explicit IR
  resize.  Erasing that operation is unsound inside concatenations and whenever
  the source and target widths differ.
-/

import Tools.SVParser
import Tools.SVParser.Verify
import Sparkle.Backend.CppSim
import Sparkle.Backend.Verilog
import Sparkle.IR.Optimize
import Sparkle.IR.Specialize

open Sparkle.IR.AST Sparkle.IR.Type
open Sparkle.IR.Specialize
open Tools.SVParser.Lower

private def tempDir : String := "/tmp/sparkle_sized_cast_tests"

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

private def runProcess (label command : String) (args : Array String) : IO String := do
  let result ← IO.Process.output { cmd := command, args }
  unless result.exitCode == 0 do
    throw (IO.userError
      s!"{label} failed (exit {result.exitCode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
  return result.stdout

private def requireTop (design : Design) : IO Module :=
  match design.findModule design.topModule with
  | some module_ => pure module_
  | none => throw (IO.userError s!"design is missing top '{design.topModule}'")

private def assignRhs? (module_ : Module) (name : String) : Option Expr :=
  module_.body.findSome? fun statement =>
    match statement with
    | .assign lhs rhs => if lhs == name then some rhs else none
    | _ => none

private partial def collectResizeWidths : Expr → List DimExpr
  | .resize width value => width :: collectResizeWidths value
  | .op _ args | .concat args => args.flatMap collectResizeWidths
  | .slice value _ _ => collectResizeWidths value
  | .index array index => collectResizeWidths array ++ collectResizeWidths index
  | .const _ _ | .paramConst _ _ | .ref _ => []

private def moduleResizeWidths (module_ : Module) : List DimExpr :=
  module_.body.flatMap fun statement =>
    match statement with
    | .assign _ rhs => collectResizeWidths rhs
    | .register _ _ _ input _ => collectResizeWidths input
    | .memory _ _ _ _ _ writeAddr writeData writeEnable readAddr _ _ =>
      [writeAddr, writeData, writeEnable, readAddr].flatMap collectResizeWidths
    | .inst _ _ connections _ =>
      connections.flatMap (fun (_, value) => collectResizeWidths value)

private def nativeCastSource : String :=
  "module native_resize #(parameter W = 3) (\n" ++
  "  input logic [W:0] x,\n" ++
  "  output logic [W-1:0] trunc,\n" ++
  "  output logic [W:0] zext,\n" ++
  "  output logic [(2*W)-1:0] pair\n" ++
  ");\n" ++
  "wire [W-1:0] narrowed;\n" ++
  "assign narrowed = (W)'(x);\n" ++
  "assign trunc = narrowed;\n" ++
  "assign zext = (W+1)'(narrowed);\n" ++
  "assign pair = {(W)'(x), (W)'(x)};\n" ++
  "endmodule\n"

private def concreteCastSource : String :=
  "module concrete_resize (\n" ++
  "  input logic [4:0] x,\n" ++
  "  output logic [2:0] trunc,\n" ++
  "  output logic [3:0] zext,\n" ++
  "  output logic [5:0] pair\n" ++
  ");\n" ++
  "wire [2:0] narrowed;\n" ++
  "assign narrowed = (3)'(x);\n" ++
  "assign trunc = narrowed;\n" ++
  "assign zext = (4)'(narrowed);\n" ++
  "assign pair = {(3)'(x), (3)'(x)};\n" ++
  "endmodule\n"

private def literalCastSource : String :=
  "module literal_resize (\n" ++
  "  output logic [15:0] sized_positive,\n" ++
  "  output logic [15:0] sized_negative,\n" ++
  "  output logic [15:0] sized_negative_overflow,\n" ++
  "  output logic [63:0] based_positive,\n" ++
  "  output logic [63:0] based_negative,\n" ++
  "  output logic [63:0] wrapped_decimal_positive,\n" ++
  "  output logic [63:0] wrapped_decimal_negative,\n" ++
  "  output logic [63:0] wrapped_decimal_inverted,\n" ++
  "  output logic [7:0] wrapped_binary_positive\n" ++
  ");\n" ++
  "assign sized_positive = (16)'(8'h1ff);\n" ++
  "assign sized_negative = (16)'(-8'd1);\n" ++
  "assign sized_negative_overflow = (16)'(-8'd511);\n" ++
  "assign based_positive = (64)'('h100000001);\n" ++
  "assign based_negative = (64)'(-'h1);\n" ++
  "assign wrapped_decimal_positive = (64)'($unsigned(4294967297));\n" ++
  "assign wrapped_decimal_negative = (64)'($unsigned(-4294967297));\n" ++
  "assign wrapped_decimal_inverted = (64)'($unsigned(~4294967297));\n" ++
  "assign wrapped_binary_positive = (8)'($unsigned('b101));\n" ++
  "endmodule\n"

private def unsizedPositiveDecimalCastSource : String :=
  "module unsized_positive_decimal_resize (output logic [63:0] y);\n" ++
  "assign y = (64)'(4294967297);\n" ++
  "endmodule\n"

private def unsizedNegativeDecimalCastSource : String :=
  "module unsized_negative_decimal_resize (output logic [63:0] y);\n" ++
  "assign y = (64)'(-1);\n" ++
  "endmodule\n"

private def nestedCastSource : String :=
  "module nested_resize (input logic [7:0] x, output logic [7:0] y);\n" ++
  "assign y = (8)'((4)'(x));\n" ++
  "endmodule\n"

private def expressionCastSource : String :=
  "module expression_resize (input logic [7:0] x, output logic [15:0] y);\n" ++
  "assign y = (16)'(x + 1);\n" ++
  "endmodule\n"

private def optimizerWidthSource : String :=
  "module optimizer_width (input logic [7:0] x, input logic [31:0] z,\n" ++
  "  output logic [39:0] add_y, output logic [39:0] or_y,\n" ++
  "  output logic [39:0] mux_y, output logic [39:0] and_y,\n" ++
  "  output logic [15:0] inline_y, output logic [15:0] mixed_inline_y);\n" ++
  "wire [7:0] narrow; wire [7:0] mixed_narrow;\n" ++
  "assign narrow = x + 8'd1; assign mixed_narrow = x + 1;\n" ++
  "assign add_y = {8'haa, x + 0};\n" ++
  "assign or_y = {8'haa, x | 0};\n" ++
  "assign mux_y = {8'haa, 1'b1 ? x : z};\n" ++
  "assign and_y = {8'haa, 8'd0 & z};\n" ++
  "assign inline_y = {8'haa, narrow};\n" ++
  "assign mixed_inline_y = {8'haa, mixed_narrow};\n" ++
  "endmodule\n"

private def expressionVerifySource : String :=
  "module expression_verify (\n" ++
  "  input logic clk, input logic rst, input logic [7:0] x,\n" ++
  "  output logic [15:0] q\n" ++
  ");\n" ++
  "logic [15:0] q_reg; assign q = q_reg;\n" ++
  "always_ff @(posedge clk or posedge rst) begin\n" ++
  "  if (rst) q_reg <= $unsigned((16)'(0));\n" ++
  "  else q_reg <= (16)'(x + 1);\n" ++
  "end\n" ++
  "endmodule\n"

/-- Sized-cast operands are context-determined by the target width.  In
    particular, the outer 16-bit cast lets addition and left shift retain the
    carry bit that would be lost by evaluating first at eight bits. -/
private def contextCastSource : String :=
  "module context_resize (\n" ++
  "  input logic [7:0] a, input logic [7:0] b,\n" ++
  "  input logic sel, input logic [2:0] sh,\n" ++
  "  output logic [15:0] const_add, variable_add, bitwise_and, muxed,\n" ++
  "  output logic [15:0] shifted_left, shifted_right, negated, inverted,\n" ++
  "  output logic [63:0] negative_unsized\n" ++
  ");\n" ++
  "assign const_add = (16)'(8'd255 + 8'd1);\n" ++
  "assign variable_add = (16)'(a + b);\n" ++
  "assign bitwise_and = (16)'(a & b);\n" ++
  "assign muxed = (16)'(sel ? a : b);\n" ++
  "assign shifted_left = (16)'(a << sh);\n" ++
  "assign shifted_right = (16)'(a >> sh);\n" ++
  "assign negated = (16)'(-a);\n" ++
  "assign inverted = (16)'(~a);\n" ++
  "assign negative_unsized = (64)'(-'h1);\n" ++
  "endmodule\n"

private def verifyCastSource : String :=
  "module verify_resize #(parameter W = 3) (\n" ++
  "  input logic clk, input logic rst,\n" ++
  "  input logic [W:0] x,\n" ++
  "  output logic [W-1:0] q\n" ++
  ");\n" ++
  "logic [W-1:0] q_reg;\n" ++
  "assign q = q_reg;\n" ++
  "always_ff @(posedge clk or posedge rst) begin\n" ++
  "  if (rst) q_reg <= $unsigned((W)'(0));\n" ++
  "  else q_reg <= (W)'(x);\n" ++
  "end\n" ++
  "endmodule\n"

private def castBench (width : Nat) (parameterized : Bool) : String :=
  let instanceLine := if parameterized then
    "native_resize #(.W(W)) dut (.*);"
  else
    "native_resize dut (.*);"
  "module tb;\n" ++
  s!"localparam integer W = {width};\n" ++
  "logic [W:0] x;\n" ++
  "wire [W-1:0] trunc;\n" ++
  "wire [W:0] zext;\n" ++
  "wire [(2*W)-1:0] pair;\n" ++
  instanceLine ++ "\n" ++
  "initial begin\n" ++
  "  x = '0;\n" ++
  "  x[W] = 1'b1;\n" ++
  "  x[2:0] = 3'b101;\n" ++
  "  #1;\n" ++
  "  if (trunc !== {{(W-3){1'b0}}, 3'b101}) $fatal(1, \"truncation failed\");\n" ++
  "  if (zext[W] !== 1'b0 || zext[W-1:0] !== trunc) $fatal(1, \"zero extension failed\");\n" ++
  "  if (pair !== {trunc, trunc}) $fatal(1, \"concat grouping failed\");\n" ++
  "  $display(\"PASS W=%0d\", W);\n" ++
  "  $finish;\n" ++
  "end\n" ++
  "endmodule\n"

private def concreteBench : String :=
  "module tb;\n" ++
  "logic [4:0] x; wire [2:0] trunc; wire [3:0] zext; wire [5:0] pair;\n" ++
  "concrete_resize dut (.*);\n" ++
  "initial begin\n" ++
  "  x = 5'b11101; #1;\n" ++
  "  if (trunc !== 3'b101) $fatal(1, \"concrete truncation failed\");\n" ++
  "  if (zext !== 4'b0101) $fatal(1, \"concrete zero extension failed\");\n" ++
  "  if (pair !== 6'b101101) $fatal(1, \"concrete concat grouping failed\");\n" ++
  "  $display(\"PASS concrete\"); $finish;\n" ++
  "end\n" ++
  "endmodule\n"

private def literalBench : String :=
  "module tb;\n" ++
  "wire [15:0] sized_positive, sized_negative, sized_negative_overflow;\n" ++
  "wire [63:0] based_positive, based_negative, wrapped_decimal_positive;\n" ++
  "wire [63:0] wrapped_decimal_negative, wrapped_decimal_inverted;\n" ++
  "wire [7:0] wrapped_binary_positive;\n" ++
  "literal_resize dut (.*);\n" ++
  "initial begin #1;\n" ++
  "  if (sized_positive !== 16'h00ff) $fatal(1, \"sized positive widening failed\");\n" ++
  "  if (sized_negative !== 16'hffff) $fatal(1, \"direct signed literal did not sign-extend\");\n" ++
  "  if (sized_negative_overflow !== 16'hff01) $fatal(1, \"source-sized negative overflow changed\");\n" ++
  "  if (based_positive !== 64'h0000000100000001) $fatal(1, \"unsized based literal truncated\");\n" ++
  "  if (based_negative !== 64'hffffffffffffffff) $fatal(1, \"negative unsized based literal did not fill target\");\n" ++
  "  if (wrapped_decimal_positive !== 64'h0000000100000001) $fatal(1, \"$unsigned decimal literal truncated\");\n" ++
  "  if (wrapped_decimal_negative !== 64'h00000002ffffffff) $fatal(1, \"$unsigned negative decimal lost natural width\");\n" ++
  "  if (wrapped_decimal_inverted !== 64'h00000002fffffffe) $fatal(1, \"$unsigned inverted decimal lost natural width\");\n" ++
  "  if (wrapped_binary_positive !== 8'h05) $fatal(1, \"$unsigned binary literal collapsed to one bit\");\n" ++
  "  $display(\"PASS literals\"); $finish;\n" ++
  "end\n" ++
  "endmodule\n"

private def unsizedPositiveDecimalBench : String :=
  "module tb; wire [63:0] y; unsized_positive_decimal_resize dut (.*);\n" ++
  "initial begin #1;\n" ++
  "  if (y !== 64'h0000000100000001) $fatal(1, \"unsized positive decimal oracle\");\n" ++
  "  $display(\"PASS unsized positive decimal oracle\"); $finish;\n" ++
  "end endmodule\n"

private def unsizedNegativeDecimalBench : String :=
  "module tb; wire [63:0] y; unsized_negative_decimal_resize dut (.*);\n" ++
  "initial begin #1;\n" ++
  "  if (y !== 64'hffffffffffffffff) $fatal(1, \"unsized negative decimal oracle\");\n" ++
  "  $display(\"PASS unsized negative decimal oracle\"); $finish;\n" ++
  "end endmodule\n"

private def nestedBench : String :=
  "module tb; logic [7:0] x; wire [7:0] y; nested_resize dut (.*);\n" ++
  "initial begin x=8'hab; #1; " ++
  "if (y !== 8'h0b) $fatal(1, \"nested resize was collapsed\"); " ++
  "$display(\"PASS nested\"); $finish; end endmodule\n"

private def expressionBench : String :=
  "module tb; logic [7:0] x; wire [15:0] y; expression_resize dut (.*);\n" ++
  "initial begin x=8'hff; #1; " ++
  "if (y !== 16'h0100) $fatal(1, \"source expression width was lost\"); " ++
  "$display(\"PASS expression\"); $finish; end endmodule\n"

private def contextBench : String :=
  "module tb;\n" ++
  "logic [7:0] a,b; logic sel; logic [2:0] sh;\n" ++
  "wire [15:0] const_add,variable_add,bitwise_and,muxed,shifted_left,shifted_right;\n" ++
  "wire [15:0] negated,inverted;\n" ++
  "wire [63:0] negative_unsized; context_resize dut (.*);\n" ++
  "initial begin\n" ++
  "  a=8'hff; b=8'h01; sel=0; sh=1; #1;\n" ++
  "  if (const_add !== 16'h0100) $fatal(1, \"sized literal add context\");\n" ++
  "  if (variable_add !== 16'h0100) $fatal(1, \"variable add context\");\n" ++
  "  if (bitwise_and !== 16'h0001) $fatal(1, \"bitwise context\");\n" ++
  "  if (muxed !== 16'h0001) $fatal(1, \"mux context\");\n" ++
  "  if (shifted_left !== 16'h01fe) $fatal(1, \"left shift context\");\n" ++
  "  if (shifted_right !== 16'h007f) $fatal(1, \"right shift context\");\n" ++
  "  if (negated !== 16'hff01 || inverted !== 16'hff00) $fatal(1, \"unary context\");\n" ++
  "  if (negative_unsized !== 64'hffffffffffffffff) $fatal(1, \"negative unsized hex\");\n" ++
  "  $display(\"SIG1 %h %h %h %h %h %h %h %h %h\", const_add, variable_add, " ++
  "bitwise_and, muxed, shifted_left, shifted_right, negated, inverted, negative_unsized);\n" ++
  "  a=8'h80; b=8'h80; sel=1; sh=1; #1;\n" ++
  "  if (variable_add !== 16'h0100 || bitwise_and !== 16'h0080 || " ++
  "muxed !== 16'h0080 || shifted_left !== 16'h0100 || shifted_right !== 16'h0040 || " ++
  "negated !== 16'hff80 || inverted !== 16'hff7f) " ++
  "$fatal(1, \"second boundary vector\");\n" ++
  "  $display(\"SIG2 %h %h %h %h %h %h %h %h %h\", const_add, variable_add, " ++
  "bitwise_and, muxed, shifted_left, shifted_right, negated, inverted, negative_unsized);\n" ++
  "end\n" ++
  "endmodule\n"

private def optimizerWidthBench : String :=
  "module tb; logic [7:0] x; logic [31:0] z; " ++
  "wire [39:0] add_y, or_y, mux_y, and_y; " ++
  "wire [15:0] inline_y, mixed_inline_y; " ++
  "optimizer_width dut (.*);\n" ++
  "initial begin x=8'hff; z=32'hdeadbeef; #1; " ++
  "if (add_y !== 40'haa000000ff) $fatal(1, \"add field width changed\"); " ++
  "if (or_y !== 40'haa000000ff) $fatal(1, \"or field width changed\"); " ++
  "if (mux_y !== 40'haa000000ff) $fatal(1, \"mux field width changed\"); " ++
  "if (and_y !== 40'haa00000000) $fatal(1, \"and field width changed\"); " ++
  "if (inline_y !== 16'haa00) $fatal(1, \"same-width assignment boundary was lost\"); " ++
  "if (mixed_inline_y !== 16'haa00) $fatal(1, \"mixed-width assignment boundary was lost\"); " ++
  "$display(\"PASS optimizer widths\"); $finish; end endmodule\n"

private def runIcarusCapture (label source bench : String) : IO String := do
  IO.FS.createDirAll tempDir
  let sourcePath := s!"{tempDir}/{label}.sv"
  let executable := s!"{tempDir}/{label}.vvp"
  IO.FS.writeFile sourcePath (source ++ "\n" ++ bench)
  let _ ← runProcess s!"iverilog {label}" "iverilog"
    #["-g2012", "-s", "tb", "-o", executable, sourcePath]
  runProcess s!"vvp {label}" "vvp" #[executable]

private def runIcarus (label source bench : String) : IO Unit := do
  let output ← runIcarusCapture label source bench
  ensure (contains output "PASS") s!"{label}: testbench did not report PASS"

private def checkNativeShape (module_ : Module) : IO Unit := do
  let width : DimExpr := .param "W"
  match assignRhs? module_ "narrowed" with
  | some (.resize target (.ref "x")) =>
    ensure (target == width) "(W)'(x) did not lower to resize<W>(x)"
  | _ => throw (IO.userError "native narrowing resize was lost")
  match assignRhs? module_ "zext" with
  | some (.resize target (.ref "narrowed")) =>
    ensure (target == width + 1) "(W+1)'(narrowed) lost its target width"
  | _ => throw (IO.userError "native zero-extension resize was lost")
  match assignRhs? module_ "pair" with
  | some (.concat [.resize leftWidth (.ref "x"),
                   .resize rightWidth (.ref "x")]) =>
    ensure (leftWidth == width && rightWidth == width)
      "casts inside concat did not retain independent W-bit grouping"
  | _ => throw (IO.userError "concat-local native resizes were lost")

private def checkConcreteShape (module_ : Module) : IO Unit := do
  match assignRhs? module_ "narrowed" with
  | some (.resize target (.ref "x")) =>
    ensure (target.toNat? == some 3) "(3)'(x) target width changed"
  | _ => throw (IO.userError "concrete narrowing resize was lost")
  match assignRhs? module_ "zext" with
  | some (.resize target (.ref "narrowed")) =>
    ensure (target.toNat? == some 4) "concrete zero-extension width changed"
  | _ => throw (IO.userError "concrete zero-extension resize was lost")
  match assignRhs? module_ "pair" with
  | some (.concat [.resize leftWidth _, .resize rightWidth _]) =>
    ensure (leftWidth.toNat? == some 3 && rightWidth.toNat? == some 3)
      "concrete concat-local casts lost their grouping"
  | _ => throw (IO.userError "concrete concat-local resizes were lost")

private def parseEmitNative : IO (Design × String) := do
  let design ← requireOk (parseAndLowerNative nativeCastSource)
  let top ← requireTop design
  checkNativeShape top
  let emitted ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked design)
  ensure (contains emitted "$unsigned")
    "Verilog backend did not make resize zero-extension explicit"
  let reparsed ← requireOk (parseAndLowerNative emitted)
  checkNativeShape (← requireTop reparsed)
  return (design, emitted)

private def checkConcreteRoundTrip : IO Unit := do
  let design ← requireOk (parseAndLowerNative concreteCastSource)
  checkConcreteShape (← requireTop design)
  let emitted ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked design)
  let reparsed ← requireOk (parseAndLowerNative emitted)
  checkConcreteShape (← requireTop reparsed)
  runIcarus "concrete_resize" emitted concreteBench

private def checkLiteralRoundTrip : IO Unit := do
  let design ← requireOk (parseAndLowerNative literalCastSource)
  let emitted ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked design)
  -- Reparse the backend's `$unsigned` form before running it.  This catches a
  -- parser that accepts the input cast but not Sparkle's canonical output.
  let reparsed ← requireOk (parseAndLowerNative emitted)
  let reemitted ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked reparsed)
  runIcarus "literal_resize" reemitted literalBench

  let cpp ← requireOk (Sparkle.Backend.CppSim.toCppSimDesignChecked reparsed)
  IO.FS.createDirAll tempDir
  let sourcePath := s!"{tempDir}/literal_resize.cpp"
  let executable := s!"{tempDir}/literal_resize"
  let harness :=
    "\nint main() { literal_resize d; d.eval(); " ++
    "return d.sized_positive == 0x00ffULL && " ++
    "d.sized_negative == 0xffffULL && " ++
    "d.sized_negative_overflow == 0xff01ULL && " ++
    "d.based_positive == 0x0000000100000001ULL && " ++
    "d.based_negative == 0xffffffffffffffffULL && " ++
    "d.wrapped_decimal_positive == 0x0000000100000001ULL && " ++
    "d.wrapped_decimal_negative == 0x00000002ffffffffULL && " ++
    "d.wrapped_decimal_inverted == 0x00000002fffffffeULL && " ++
    "d.wrapped_binary_positive == 0x05ULL ? 0 : 1; }\n"
  IO.FS.writeFile sourcePath (cpp ++ harness)
  let _ ← runProcess "C++ compile literal resize" "c++"
    #["-std=c++17", "-Wall", "-Wextra", "-Werror", sourcePath, "-o", executable]
  let _ ← runProcess "CppSim literal resize semantics" executable #[]

  -- Unsized decimal literals are signed SystemVerilog expressions.  Icarus
  -- records the language semantics, while both unsigned-IR lowering paths
  -- must reject them instead of silently changing their result signedness.
  runIcarus "unsized_positive_decimal_oracle"
    unsizedPositiveDecimalCastSource unsizedPositiveDecimalBench
  ensure (isError (parseAndLowerNative unsizedPositiveDecimalCastSource) &&
      isError (parseAndLower unsizedPositiveDecimalCastSource))
    "an unsized positive decimal sized-cast operand was accepted"
  runIcarus "unsized_negative_decimal_oracle"
    unsizedNegativeDecimalCastSource unsizedNegativeDecimalBench
  ensure (isError (parseAndLowerNative unsizedNegativeDecimalCastSource) &&
      isError (parseAndLower unsizedNegativeDecimalCastSource))
    "an unsized negative decimal sized-cast operand was accepted"

private def checkSymbolicConstantMaterialization : IO Unit := do
  let source :=
    "module symbolic_constant_sum #(parameter W = 8, parameter V = 16) " ++
    "(output logic [V-1:0] y); " ++
    "assign y = (W)'(-8'd1) + (V)'(-8'd1); endmodule\n"
  let bench :=
    "module tb; wire [15:0] y; symbolic_constant_sum dut (.*); " ++
    "initial begin #1; if (y !== 16'h00fe) " ++
    "$fatal(1, \"symbolic constants changed signedness\"); " ++
    "$display(\"SIG symbolic constants %h\", y); $finish; end endmodule\n"
  let oracle ← runIcarusCapture "symbolic_constant_sum_original" source bench
  ensure (contains oracle "00fe")
    "original symbolic constant oracle did not produce 16'h00fe"
  let design ← requireOk (parseAndLowerNative source)
  let emitted ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked design)
  ensure (contains emitted "$unsigned(" && contains emitted "W" &&
      contains emitted "V" && contains emitted "'(-1))")
    "backend did not immediately materialize symbolic constants as unsigned"
  let reparsed ← requireOk (parseAndLowerNative emitted)
  let reemitted ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked reparsed)
  let roundtrip ←
    runIcarusCapture "symbolic_constant_sum_roundtrip" reemitted bench
  ensure (contains roundtrip "SIG symbolic constants 00fe")
    "symbolic constant backend roundtrip changed the Icarus result"

private def checkNestedRoundTrip : IO Unit := do
  let design ← requireOk (parseAndLowerNative nestedCastSource)
  let top ← requireTop design
  match assignRhs? top "y" with
  | some (.resize outerWidth (.resize innerWidth (.ref "x"))) =>
    ensure (outerWidth.toNat? == some 8 && innerWidth.toNat? == some 4)
      "nested x8→4→8 resize structure changed"
  | _ => throw (IO.userError "nested resize was erased or collapsed")
  let emitted ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked design)
  let reparsed ← requireOk (parseAndLowerNative emitted)
  let reparsedTop ← requireTop reparsed
  match assignRhs? reparsedTop "y" with
  | some (.resize outerWidth (.resize innerWidth _)) =>
    ensure (outerWidth.toNat? == some 8 && innerWidth.toNat? == some 4)
      "backend roundtrip collapsed nested resize"
  | _ => throw (IO.userError "backend roundtrip lost nested resize")
  runIcarus "nested_resize" emitted nestedBench

/-- The original simulator establishes the expected value, but Sparkle rejects
    this raw context-determined operand until that sizing relation has a direct
    IR representation. -/
private def checkExpressionSourceWidth : IO Unit := do
  runIcarus "expression_resize_original" expressionCastSource expressionBench
  ensure (isError (parseAndLowerNative expressionCastSource))
    "native lowering accepted raw context-determined x+1 cast"
  ensure (isError (parseAndLower expressionCastSource))
    "legacy lowering accepted raw context-determined x+1 cast"

private def checkOptimizerPreservesExpressionWidths : IO Unit := do
  let design ← requireOk (parseAndLowerNative optimizerWidthSource)
  let optimized := Sparkle.IR.Optimize.optimizeDesign design
  let emitted ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked optimized)
  runIcarus "optimizer_width" emitted optimizerWidthBench
  let cpp ← requireOk (Sparkle.Backend.CppSim.toCppSimDesignChecked optimized)
  IO.FS.createDirAll tempDir
  let sourcePath := s!"{tempDir}/optimizer_width.cpp"
  let executable := s!"{tempDir}/optimizer_width"
  let harness :=
    "\nint main() { optimizer_width d; d.x=0xff; d.z=0xdeadbeef; d.eval(); " ++
    "return d.add_y==0xaa000000ffULL && d.or_y==0xaa000000ffULL && " ++
    "d.mux_y==0xaa000000ffULL && d.and_y==0xaa00000000ULL && " ++
    "d.inline_y==0xaa00 && d.mixed_inline_y==0xaa00 ? 0 : 1; }\n"
  IO.FS.writeFile sourcePath (cpp ++ harness)
  let _ ← runProcess "C++ compile optimizer width regression" "c++"
    #["-std=c++17", "-Wall", "-Wextra", "-Werror", sourcePath, "-o", executable]
  let _ ← runProcess "CppSim optimizer width semantics" executable #[]

private def checkExpressionVerify : IO Unit := do
  ensure (isError (parseAndLowerNative expressionVerifySource))
    "native lowering admitted a context-determined arithmetic cast into Verify"
  ensure (isError (parseAndLower expressionVerifySource))
    "legacy lowering admitted a context-determined arithmetic cast into Verify"

private def checkSequentialResetLiteralCasts : IO Unit := do
  let source :=
    "module sequential_reset_cast (\n" ++
    "  input logic clk, input logic rst, input logic [15:0] d,\n" ++
    "  output logic [15:0] wide_q, output logic [15:0] narrow_q\n" ++
    ");\n" ++
    "logic [15:0] wide_reg, narrow_reg;\n" ++
    "assign wide_q = wide_reg; assign narrow_q = narrow_reg;\n" ++
    "always_ff @(posedge clk or posedge rst) begin\n" ++
    "  if (rst) begin\n" ++
    "    wide_reg <= (16)'(-8'd511);\n" ++
    "    narrow_reg <= (8)'(-8'd1);\n" ++
    "  end else begin\n" ++
    "    wide_reg <= d; narrow_reg <= d;\n" ++
    "  end\n" ++
    "end\nendmodule\n"
  let bench :=
    "module tb; logic clk, rst; logic [15:0] d; " ++
    "wire [15:0] wide_q, narrow_q; sequential_reset_cast dut (.*); " ++
    "initial begin clk=0; rst=0; d=0; #1; rst=1; #1; " ++
    "if (wide_q !== 16'hff01) " ++
    "$fatal(1, \"reset literal ignored its 8-bit source width\"); " ++
    "if (narrow_q !== 16'h00ff) " ++
    "$fatal(1, \"8-bit reset cast was erased before 16-bit assignment\"); " ++
    "$display(\"PASS sequential reset casts %h %h\", wide_q, narrow_q); " ++
    "$finish; end endmodule\n"
  runIcarus "sequential_reset_cast_original" source bench
  let nativeDesign ← requireOk (parseAndLowerNative source)
  let nativeVerilog ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked nativeDesign)
  runIcarus "sequential_reset_cast_native" nativeVerilog bench
  let legacyDesign ← requireOk (parseAndLower source)
  let legacyVerilog ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked legacyDesign)
  runIcarus "sequential_reset_cast_legacy" legacyVerilog bench

/-- Icarus locks the IEEE context-determined results.  Sparkle deliberately
    rejects these general operand forms until the IR can represent contextual
    expression sizing without guessing. -/
private def checkContextOperandsFailClosed : IO Unit := do
  let originalOutput ←
    runIcarusCapture "context_resize_original" contextCastSource contextBench
  ensure (contains originalOutput "SIG1" && contains originalOutput "SIG2")
    "original SystemVerilog context oracle did not complete"
  ensure (isError (parseAndLowerNative contextCastSource))
    "native lowering accepted unsupported context-determined cast operands"
  ensure (isError (parseAndLower contextCastSource))
    "legacy lowering accepted unsupported context-determined cast operands"

private def checkFailClosedOrSemantics
    (label source bench : String) : IO Unit :=
  match parseAndLowerNative source with
  | .error _ => pure ()
  | .ok design =>
    match Sparkle.Backend.Verilog.toVerilogDesignChecked design with
    | .error _ => pure ()
    | .ok emitted => runIcarus label emitted bench

private def checkConcreteSignedCasts : IO Unit := do
  let signedFunction :=
    "module signed_function_resize (output logic [63:0] y); " ++
    "assign y = (64)'($signed(32'hffffffff)); endmodule\n"
  let signedFunctionBench :=
    "module tb; wire [63:0] y; signed_function_resize dut (.*); " ++
    "initial begin #1; if (y !== 64'hffffffffffffffff) " ++
    "$fatal(1, \"$signed cast silently zero-extended\"); " ++
    "$display(\"PASS signed function\"); $finish; end endmodule\n"
  checkFailClosedOrSemantics "signed_function_resize" signedFunction signedFunctionBench

  let signedDeclaration :=
    "module signed_decl_resize (input logic signed [7:0] x, output logic [15:0] y); " ++
    "assign y = (16)'(x); endmodule\n"
  let signedDeclarationBench :=
    "module tb; logic signed [7:0] x; wire [15:0] y; signed_decl_resize dut (.*); " ++
    "initial begin x=-1; #1; if (y !== 16'hffff) " ++
    "$fatal(1, \"signed declaration silently zero-extended\"); " ++
    "$display(\"PASS signed declaration\"); $finish; end endmodule\n"
  checkFailClosedOrSemantics
    "signed_declaration_resize" signedDeclaration signedDeclarationBench
  ensure (isError (parseAndLower signedDeclaration))
    "legacy lowering silently treated a signed declaration cast as unsigned"

  let implicitSignedExpression :=
    "module implicit_signed_resize (output logic [63:0] y); " ++
    "assign y = (64)'(~0); endmodule\n"
  ensure (isError (parseAndLowerNative implicitSignedExpression) &&
      isError (parseAndLower implicitSignedExpression))
    "an implicitly signed sized-cast operand was silently zero-extended"

  let inferredSignedParameter :=
    "module inferred_signed_parameter #(parameter P=1) " ++
    "(output logic [63:0] y); assign y = (64)'(~P); endmodule\n"
  ensure (isError (parseAndLowerNative inferredSignedParameter) &&
      isError (parseAndLower inferredSignedParameter))
    "an untyped signed parameter was silently treated as unsigned in a sized cast"

  let signedComparison :=
    "module signed_comparison_resize (output logic [63:0] y); " ++
    "assign y = (64)'(-1 < 0); endmodule\n"
  ensure (isError (parseAndLowerNative signedComparison) &&
      isError (parseAndLower signedComparison))
    "signed comparison inside a sized cast was lowered as an unsigned comparison"

  let unsignedArithmeticShift :=
    "module unsigned_asr_resize (output logic [63:0] y); " ++
    "assign y = (64)'($unsigned(8'h80 >>> 1)); endmodule\n"
  ensure (isError (parseAndLowerNative unsignedArithmeticShift) &&
      isError (parseAndLower unsignedArithmeticShift))
    "$unsigned wrapper hid a signed-sensitive arithmetic shift from validation"

  let arithmeticShiftThroughWire :=
    "module wire_asr_resize (input logic [7:0] x, output logic [7:0] y); " ++
    "logic [7:0] w; assign w = (8)'($unsigned(x)); " ++
    "assign y = w >>> 1; endmodule\n"
  ensure (isError (parseAndLowerNative arithmeticShiftThroughWire) &&
      isError (parseAndLower arithmeticShiftThroughWire))
    "a wire hid sized-cast dataflow into an arithmetic right shift"

  let explicitUnsignedExpression :=
    "module explicit_unsigned_resize (output logic [63:0] y); " ++
    "assign y = (64)'($unsigned(~0)); endmodule\n"
  let explicitUnsignedBench :=
    "module tb; wire [63:0] y; explicit_unsigned_resize dut (.*); " ++
    "initial begin #1; if (y !== 64'h00000000ffffffff) " ++
    "$fatal(1, \"$unsigned operand changed meaning\"); " ++
    "$display(\"PASS explicit unsigned\"); $finish; end endmodule\n"
  checkFailClosedOrSemantics
    "explicit_unsigned_resize" explicitUnsignedExpression explicitUnsignedBench
  let explicitUnsignedDesign ← requireOk (parseAndLowerNative explicitUnsignedExpression)
  let explicitUnsignedCpp ← requireOk
    (Sparkle.Backend.CppSim.toCppSimDesignChecked explicitUnsignedDesign)
  IO.FS.createDirAll tempDir
  let explicitUnsignedCppPath := s!"{tempDir}/explicit_unsigned_resize.cpp"
  let explicitUnsignedCppExe := s!"{tempDir}/explicit_unsigned_resize"
  IO.FS.writeFile explicitUnsignedCppPath
    (explicitUnsignedCpp ++
      "\nint main() { explicit_unsigned_resize d; d.eval(); " ++
      "return d.y == 0x00000000ffffffffULL ? 0 : 1; }\n")
  let _ ← runProcess "C++ compile explicit unsigned resize" "c++"
    #["-std=c++17", "-Wall", "-Wextra", "-Werror",
      explicitUnsignedCppPath, "-o", explicitUnsignedCppExe]
  let _ ← runProcess "CppSim explicit unsigned resize semantics"
    explicitUnsignedCppExe #[]

  let safeMaterializedReference :=
    "module safe_materialized_ref #(parameter W=8) " ++
    "(input logic [W-1:0] x, output logic [W-1:0] y); " ++
    "assign y = $unsigned((W)'(x)); endmodule\n"
  let _ ← requireOk (parseAndLowerNative safeMaterializedReference)
  let _ ← requireOk (parseAndLower safeMaterializedReference)

  for (label, operator) in
      [("add", "+"), ("sub", "-"), ("mul", "*"), ("pow", "**"),
       ("and", "&"), ("or", "|"), ("xor", "^"),
       ("eq", "=="), ("neq", "!=")] do
    let source :=
      s!"module wrapped_signed_{label} #(parameter W=64) " ++
      "(output logic [W-1:0] y); " ++
      s!"assign y = $unsigned((W)'(1 {operator} 2)); endmodule\n"
    ensure (isError (parseAndLowerNative source) &&
        isError (parseAndLower source))
      s!"$unsigned materialization hid signed {label} operands"

private def checkSpecialization (design : Design) (width : Nat) : IO Design := do
  let specialized ← requireOk (specializeDesign design [("W", width)])
  let top ← requireTop specialized
  ensure top.parameters.isEmpty s!"W={width}: specialization retained W"
  for dimension in top.dimensionExpressions do
    ensure dimension.isConcrete s!"W={width}: symbolic dimension survived"
  let widths := moduleResizeWidths top
  ensure (!widths.isEmpty) s!"W={width}: specialization erased all resize nodes"
  for target in widths do
    ensure target.isConcrete s!"W={width}: symbolic resize target survived"
  ensure (widths.any (fun target => target.toNat? == some width))
    s!"W={width}: concrete resize target is missing"
  let emitted ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked specialized)
  ensure (!(contains emitted "parameter W"))
    s!"W={width}: concrete Verilog still declares W"
  return specialized

private def runCppSim (design : Design) (width : Nat) : IO Unit := do
  let cpp ← requireOk
    (Sparkle.Backend.CppSim.toCppSimDesignChecked design)
  let inputValue := (2 ^ width) + 5
  let pairValue := (5 * (2 ^ width)) + 5
  let harness :=
    "\nint main() {\n" ++
    "  native_resize d;\n" ++
    s!"  d.x = {inputValue}ULL; d.eval();\n" ++
    s!"  if (d.trunc != 5ULL || d.zext != 5ULL || d.pair != {pairValue}ULL) return 1;\n" ++
    "  return 0;\n" ++
    "}\n"
  IO.FS.createDirAll tempDir
  let sourcePath := s!"{tempDir}/cpp_resize_{width}.cpp"
  let executable := s!"{tempDir}/cpp_resize_{width}"
  IO.FS.writeFile sourcePath (cpp ++ harness)
  let _ ← runProcess s!"C++ compile W={width}" "c++"
    #["-std=c++17", "-Wall", "-Wextra", "-Werror", sourcePath, "-o", executable]
  let _ ← runProcess s!"CppSim execute W={width}" executable #[]

/-- C++ promotes `uint8_t + uint8_t` to `int`.  A resize backend must first
    normalize the operand at its IR source width, otherwise the 8-bit sum
    255+1 incorrectly becomes the 16-bit value 256 instead of wrapping to 0. -/
private def checkCppSimPromotedArithmetic : IO Unit := do
  let module_ : Module :=
    { (Module.empty "resize_promoted_arithmetic") with
      outputs := [{ name := "y", ty := .bitVector 16 }]
      body :=
        [.assign "y"
          (.resize 16 (.op .add [.const 255 8, .const 1 8]))] }
  let verilog ← requireOk (Sparkle.Backend.Verilog.toVerilogChecked module_)
  ensure (contains verilog "$unsigned")
    "IR resize backend omitted its self-determined unsigned barrier"
  let verilogBench :=
    "module tb; wire [15:0] y; resize_promoted_arithmetic dut (.*); " ++
    "initial begin #1; if (y !== 16'h0000) " ++
    "$fatal(1, \"IR 8-bit add did not wrap before resize\"); " ++
    "$display(\"PASS IR arithmetic resize\"); $finish; end endmodule\n"
  runIcarus "ir_arithmetic_resize" verilog verilogBench
  let reparsed ← requireOk (parseAndLowerNative verilog)
  let reemitted ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked reparsed)
  runIcarus "ir_arithmetic_resize_roundtrip" reemitted verilogBench

  let cpp ← requireOk (Sparkle.Backend.CppSim.toCppSimChecked module_)
  let harness :=
    "\nint main() {\n" ++
    "  resize_promoted_arithmetic d; d.eval();\n" ++
    "  return d.y == 0 ? 0 : 1;\n" ++
    "}\n"
  IO.FS.createDirAll tempDir
  let sourcePath := s!"{tempDir}/cpp_resize_promoted.cpp"
  let executable := s!"{tempDir}/cpp_resize_promoted"
  IO.FS.writeFile sourcePath (cpp ++ harness)
  let _ ← runProcess "C++ compile promoted resize" "c++"
    #["-std=c++17", "-Wall", "-Wextra", "-Werror", sourcePath, "-o", executable]
  let _ ← runProcess "CppSim execute promoted resize" executable #[]

private def checkVerify (width : Nat) : IO Unit := do
  let design ← requireOk (parseAndLowerNative verifyCastSource)
  let specialized ← requireOk (specializeDesign design [("W", width)])
  let top ← requireTop specialized
  let leanSource ← requireOk (Tools.SVParser.Verify.moduleToLean top)
  ensure (contains leanSource s!"BitVec {width}")
    s!"Verify lost the W={width} target type"
  IO.FS.createDirAll tempDir
  let path := s!"{tempDir}/VerifyResize{width}.lean"
  IO.FS.writeFile path ("import Sparkle\n\n" ++ leanSource)
  let _ ← runProcess s!"Verify Lean compile W={width}" "lake"
    #["env", "lean", path]

private def checkConstantFolding : IO Unit := do
  ensure
    (Sparkle.IR.Optimize.foldConstants (.resize 16 (.const 0x1ff 8)) ==
      .const 0xff 16)
    "widening relabelled 0x1ff#8 instead of zero-extending 0xff"
  ensure
    (Sparkle.IR.Optimize.foldConstants (.resize 16 (.const (-1) 8)) ==
      .const 0xff 16)
    "widening -1#8 sign-extended instead of zero-extending 0xff"
  ensure
    (Sparkle.IR.Optimize.foldConstants (.resize 3 (.const 13 4)) ==
      .const 5 3)
    "narrowing a constant did not retain its least-significant bits"

private def checkElaborationSizedCastModulo : IO Unit := do
  let parameterSource :=
    "module truncated_parameter #(parameter P = (4)'(8'd16)) " ++
    "(output logic [3:0] y); assign y = P; endmodule\n"
  let parameterBench :=
    "module tb; wire [3:0] y; truncated_parameter dut (.*); " ++
    "initial begin #1; if (y !== 4'd0) " ++
    "$fatal(1, \"parameter sized cast was not truncated\"); " ++
    "$display(\"PASS parameter cast modulo\"); $finish; end endmodule\n"
  runIcarus "parameter_cast_modulo_original" parameterSource parameterBench
  let nativeParameterDesign ← requireOk (parseAndLowerNative parameterSource)
  let nativeParameterTop ← requireTop nativeParameterDesign
  ensure (nativeParameterTop.parameters.length == 1 &&
      nativeParameterTop.parameters.any fun parameter =>
        parameter.name == "P" && parameter.defaultValue == 0)
    "parameter P=(4)'(8'd16) did not lower with default value zero"
  let nativeParameterVerilog ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked nativeParameterDesign)
  runIcarus "parameter_cast_modulo_native"
    nativeParameterVerilog parameterBench
  let legacyParameterDesign ← requireOk (parseAndLower parameterSource)
  let legacyParameterVerilog ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked legacyParameterDesign)
  runIcarus "parameter_cast_modulo_legacy"
    legacyParameterVerilog parameterBench

  let repeatModuloSource :=
    "module repeat_modulo (output logic y); " ++
    "assign y = {(2)'(5){1'b1}}; endmodule\n"
  let repeatModuloBench :=
    "module tb; wire y; repeat_modulo dut (.*); " ++
    "initial begin #1; if (y !== 1'b1) " ++
    "$fatal(1, \"repeat count cast did not truncate to one\"); " ++
    "$display(\"PASS repeat modulo\"); $finish; end endmodule\n"
  runIcarus "repeat_cast_modulo_original" repeatModuloSource repeatModuloBench

  let unsignedRepeatSource :=
    "module repeat_unsigned (output logic [1:0] y); " ++
    "assign y = {$unsigned((2)'(2'd2)){1'b1}}; endmodule\n"
  let unsignedRepeatBench :=
    "module tb; wire [1:0] y; repeat_unsigned dut (.*); " ++
    "initial begin #1; if (y !== 2'b11) " ++
    "$fatal(1, \"$unsigned repeat count did not evaluate to two\"); " ++
    "$display(\"PASS unsigned repeat count\"); $finish; end endmodule\n"
  runIcarus "repeat_unsigned_original" unsignedRepeatSource unsignedRepeatBench

  let repeatedCastSource :=
    "module repeat_cast_value (input logic [4:0] x, output logic [5:0] y); " ++
    "assign y = {2{(3)'(x)}}; endmodule\n"
  let repeatedCastBench :=
    "module tb; logic [4:0] x; wire [5:0] y; repeat_cast_value dut (.*); " ++
    "initial begin x = 5'd5; #1; if (y !== 6'b101101) " ++
    "$fatal(1, \"repeat of sized cast changed field width\"); " ++
    "$display(\"PASS repeated sized cast %b\", y); $finish; end endmodule\n"
  runIcarus "repeat_cast_value_original" repeatedCastSource repeatedCastBench

  for (label, source, bench) in
      [("repeat_cast_modulo", repeatModuloSource, repeatModuloBench),
       ("repeat_unsigned", unsignedRepeatSource, unsignedRepeatBench),
       ("repeat_cast_value", repeatedCastSource, repeatedCastBench)] do
    let nativeDesign ← requireOk (parseAndLowerNative source)
    let nativeVerilog ← requireOk
      (Sparkle.Backend.Verilog.toVerilogDesignChecked nativeDesign)
    runIcarus s!"{label}_native" nativeVerilog bench
    let legacyDesign ← requireOk (parseAndLower source)
    let legacyVerilog ← requireOk
      (Sparkle.Backend.Verilog.toVerilogDesignChecked legacyDesign)
    runIcarus s!"{label}_legacy" legacyVerilog bench

  let invalidRepeats :=
    [("dynamic",
      "module repeat_dynamic (input logic [1:0] n, output logic [3:0] y); " ++
      "assign y = {n{1'b1}}; endmodule\n"),
     ("zero",
      "module repeat_zero (output logic y); " ++
      "assign y = {0{1'b1}}; endmodule\n"),
     ("negative",
      "module repeat_negative (output logic y); " ++
      "assign y = {-1{1'b1}}; endmodule\n")]
  for (label, source) in invalidRepeats do
    ensure (isError (parseAndLowerNative source) &&
        isError (parseAndLower source))
      s!"{label} repeat count did not fail closed"

private def parseAndEmitFails (source : String) : Bool :=
  match parseAndLowerNative source with
  | .error _ => true
  | .ok design => isError (Sparkle.Backend.Verilog.toVerilogDesignChecked design)

private def checkUnsupportedFailClosed : IO Unit := do
  let signed :=
    "module signed_resize #(parameter W=3) " ++
    "(input logic [W-1:0] x, output logic [W-1:0] y); " ++
    "assign y = (W)'($signed(x)); endmodule\n"
  ensure (parseAndEmitFails signed)
    "signed value was silently lowered as an unsigned resize"

  let undeclared :=
    "module undeclared_resize (input logic [3:0] x, output logic [3:0] y); " ++
    "assign y = (M)'(x); endmodule\n"
  ensure (parseAndEmitFails undeclared)
    "undeclared resize parameter M was accepted"

  let dynamicWidth :=
    "module dynamic_resize (input logic [3:0] x, output logic [3:0] y); " ++
    "assign y = (x)'(x); endmodule\n"
  ensure (parseAndEmitFails dynamicWidth)
    "runtime signal x was accepted as a resize dimension"

  let zeroWidth :=
    "module zero_resize (input logic [3:0] x, output logic y); " ++
    "assign y = (0)'(x); endmodule\n"
  ensure (parseAndEmitFails zeroWidth)
    "zero-width resize was accepted"

def main : IO UInt32 := do
  checkConstantFolding
  let (nativeDesign, emitted) ← parseEmitNative
  for width in [3, 17, 257] do
    runIcarus s!"native_resize_{width}" emitted (castBench width true)
  checkConcreteRoundTrip
  checkLiteralRoundTrip
  checkSymbolicConstantMaterialization
  checkNestedRoundTrip
  checkExpressionSourceWidth
  checkOptimizerPreservesExpressionWidths
  checkExpressionVerify
  checkSequentialResetLiteralCasts
  checkContextOperandsFailClosed
  checkConcreteSignedCasts
  let specialized3 ← checkSpecialization nativeDesign 3
  let specialized17 ← checkSpecialization nativeDesign 17
  let specialized257 ← checkSpecialization nativeDesign 257
  runCppSim specialized3 3
  runCppSim specialized17 17
  checkCppSimPromotedArithmetic
  checkElaborationSizedCastModulo
  ensure
    (isError (Sparkle.Backend.CppSim.toCppSimDesignChecked specialized257))
    "CppSim accepted a 257-bit resize despite its current 64-bit limit"
  for width in [3, 17, 257] do
    checkVerify width
  checkUnsupportedFailClosed
  IO.println "sized cast tests: PASS"
  return 0
