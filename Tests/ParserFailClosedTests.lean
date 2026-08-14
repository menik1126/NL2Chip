/-
  Regression tests for parser/lowering boundaries where accepting a source
  with the wrong SystemVerilog sizing semantics is worse than rejecting it.
-/

import Sparkle.Backend.Verilog
import Sparkle.IR.Specialize
import Tools.SVParser

namespace Tests.ParserFailClosedTests

open Sparkle.IR.AST Sparkle.IR.Specialize
open Tools.SVParser.Lower

private def tempDir : String := "/tmp/sparkle_parser_fail_closed_tests"

private def ensure (condition : Bool) (message : String) : IO Unit :=
  unless condition do throw (IO.userError message)

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

private def zeroBench (moduleName : String) : String :=
  "module tb; wire [7:0] y; " ++ moduleName ++ " dut(.y(y)); " ++
  "initial begin #1; if (y !== 8'd0) " ++
  "$fatal(1, \"raw SystemVerilog expression was Nat-folded to a nonzero value\"); " ++
    "$display(\"PASS zero %h\", y); $finish; end endmodule\n"

private def runOracle (label source bench : String) : IO String := do
  IO.FS.createDirAll tempDir
  let sourcePath := s!"{tempDir}/{label}.sv"
  let executable := s!"{tempDir}/{label}.vvp"
  IO.FS.writeFile sourcePath (source ++ "\n" ++ bench)
  let _ ← runProcess s!"iverilog {label}" "iverilog"
    #["-g2012", "-s", "tb", "-o", executable, sourcePath]
  runProcess s!"vvp {label}" "vvp" #[executable]

private def runZeroOracle (label moduleName source : String) : IO Unit := do
  let output ← runOracle label source (zeroBench moduleName)
  ensure (contains output "PASS zero")
    s!"{label}: zero-semantics oracle did not complete"

private def checkLoweredZero
    (label moduleName : String) (design : Design) : IO Unit := do
  let emitted ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked design)
  runZeroOracle label moduleName emitted

/-- A raw source is acceptable only if the lowering path either rejects it or
    emits the same zero result as SystemVerilog's 32-bit unsized `1` rules. -/
private def checkRejectedOrZero
    (label moduleName source : String) : IO Unit := do
  runZeroOracle s!"{label}_original" moduleName source

  match parseAndLowerNative source with
  | .error _ => pure ()
  | .ok native =>
      let specialized ← requireOk (specializeDesign native [("W", 40)])
      checkLoweredZero s!"{label}_native" moduleName specialized

  match parseAndLower source with
  | .error _ => pure ()
  | .ok legacy => checkLoweredZero s!"{label}_legacy" moduleName legacy

private def checkRawSizingDoesNotBecomeNat : IO Unit := do
  let sizedCast :=
    "module raw_sized_cast #(parameter W=40) (output logic [7:0] y); " ++
    "assign y = (8)'((1 << W) >> W); endmodule\n"
  checkRejectedOrZero "raw_sized_cast" "raw_sized_cast" sizedCast

  let rawData :=
    "module raw_shift_data #(parameter W=40) (output logic [7:0] y); " ++
    "assign y = (1 << W) >> W; endmodule\n"
  checkRejectedOrZero "raw_shift_data" "raw_shift_data" rawData

  let rawGenerate :=
    "module raw_shift_generate #(parameter W=40) (output logic [7:0] y);\n" ++
    "generate if ((1 << W) != 0) begin : nonzero_branch\n" ++
    "  assign y = 8'd1;\n" ++
    "end else begin : zero_branch\n" ++
    "  assign y = 8'd0;\n" ++
    "end endgenerate\nendmodule\n"
  checkRejectedOrZero "raw_shift_generate" "raw_shift_generate" rawGenerate

  let wideShift :=
    "module raw_wide_shift #(parameter [31:0] W=40) " ++
    "(output logic [79:0] y); assign y=(1 << W) >> W; endmodule\n"
  let wideShiftBench :=
    "module tb; wire [79:0] y; raw_wide_shift dut(.y(y)); " ++
    "initial begin #1; if (y !== 80'd1) $fatal(1); " ++
    "$display(\"PASS wide shift\"); $finish; end endmodule\n"
  let output ← runOracle "raw_wide_shift_original" wideShift wideShiftBench
  ensure (contains output "PASS wide shift") "wide-shift SV oracle failed"
  ensure (isError (parseAndLowerNative wideShift) && isError (parseAndLower wideShift))
    "known-width assignment lost destination context for a parameterized right shift"

  let wideMask :=
    "module raw_wide_mask #(parameter [31:0] K=40) " ++
    "(output logic [79:0] y); assign y=(1 << K)-1; endmodule\n"
  let wideMaskBench :=
    "module tb; wire [79:0] y; raw_wide_mask dut(.y(y)); " ++
    "initial begin #1; if (y !== 80'h0000000000ffffffffff) $fatal(1); " ++
    "$display(\"PASS wide mask\"); $finish; end endmodule\n"
  let original ← runOracle "raw_wide_mask_original" wideMask wideMaskBench
  ensure (contains original "PASS wide mask") "wide-mask SV oracle failed"
  let native ← requireOk (parseAndLowerNative wideMask)
  let emitted ← requireOk (Sparkle.Backend.Verilog.toVerilogDesignChecked native)
  let roundTrip ← runOracle "raw_wide_mask_roundtrip" emitted wideMaskBench
  ensure (contains roundTrip "PASS wide mask")
    "parameter mask did not retain the destination's 80-bit context"

private def checkParameterizedResetFailsClosed : IO Unit := do
  let source :=
    "module parameter_reset #(parameter W=8, parameter INIT=1) (\n" ++
    "  input logic clk, input logic rst, input logic [W-1:0] d,\n" ++
    "  output logic [W-1:0] q);\n" ++
    "always_ff @(posedge clk or posedge rst) begin\n" ++
    "  if (rst) q <= INIT; else q <= d;\n" ++
    "end\nendmodule\n"
  ensure (isError (parseAndLowerNative source))
    "native lowering froze parameter-dependent reset INIT at its default value"

private def checkLegacyProceduralSpecializationBoundaries : IO Unit := do
  let narrowArithmetic :=
    "module narrow_parameter_step #(parameter STEP=1) (\n" ++
    "  input logic clk, output logic [6:0] q);\n" ++
    "always_ff @(posedge clk) q <= q - STEP;\n" ++
    "endmodule\n"
  let _ ← requireOk (parseAndLower narrowArithmetic)

  -- The same default substitution is not safe when the procedural assignment
  -- widens an unsized shift expression.  In SV the 80-bit destination context
  -- reaches `(1 << K)`; the legacy expression collector would otherwise run
  -- that intermediate at 32 bits and silently produce a different value.
  let widenedShift :=
    "module wide_parameter_step #(parameter [31:0] K=40) (\n" ++
    "  input logic clk, output logic [79:0] y);\n" ++
    "always_ff @(posedge clk) y <= (1 << K) >> K;\n" ++
    "endmodule\n"
  ensure (isError (parseAndLower widenedShift) &&
      isError (parseAndLowerNative widenedShift))
    "parameter-dependent procedural shift lost its 80-bit destination context"

/-- An untyped data-only localparam inherits SystemVerilog's fixed expression
    sizing.  Treating its raw shift/subtraction as an unbounded Nat changes the
    result once K exceeds the unsized literal's 32-bit width. -/
private def checkUntypedRawLocalparamFailsClosed : IO Unit := do
  let source :=
    "module untyped_raw_localparam #(parameter K=40) " ++
    "(output logic [79:0] y);\n" ++
    "localparam MASK = (1 << K) - 1;\n" ++
    "assign y = MASK; endmodule\n"
  ensure (isError (parseAndLowerNative source))
    "native lowering Nat-folded an untyped data-only raw-shift localparam"

private def exactMemorySource : String :=
  "module directional_memory #(\n" ++
  "  parameter AW=2, parameter DW=7, parameter DEPTH=3\n" ++
  ") (\n" ++
  "  input logic clk, input logic [AW-1:0] wr_addr,\n" ++
  "  input logic [DW-1:0] wr_data, input logic wr_en,\n" ++
  "  input logic [AW-1:0] rd_addr, output logic [DW-1:0] rd_data\n" ++
  ");\n" ++
  "logic [DW-1:0] storage [DEPTH-1:0];\n" ++
  "assign rd_data = storage[rd_addr];\n" ++
  "always_ff @(posedge clk) begin\n" ++
  "  if (wr_en) storage[wr_addr] <= wr_data;\n" ++
  "end\nendmodule\n"

private def checkUnpackedMemoryRanges : IO Unit := do
  let native ← requireOk (parseAndLowerNative exactMemorySource)
  let specialized ← requireOk
    (specializeDesign native [("AW", 5), ("DW", 13), ("DEPTH", 17)])
  let emitted ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked specialized)
  ensure (contains emitted "storage [0:16]")
    s!"descending [DEPTH-1:0] memory did not preserve exact depth 17:\n{emitted}"
  ensure (!contains emitted "storage [0:31]")
    "descending symbolic memory depth was rounded up to 2**AW"

  for (label, range) in [("ascending", "[1:DEPTH]"),
      ("descending", "[DEPTH:1]")] do
    let nonzeroBase := exactMemorySource.replace "[DEPTH-1:0]" range
    ensure (isError (parseAndLowerNative nonzeroBase) &&
        isError (parseAndLower nonzeroBase))
      s!"{label} nonzero-base unpacked memory range {range} was silently rebased"

  let underflowing := exactMemorySource.replace "[DEPTH-1:0]" "[0:DEPTH-2]"
  ensure (isError (parseAndLowerNative underflowing) &&
      isError (parseAndLower underflowing))
    "symbolic unpacked range [0:DEPTH-2] was reinterpreted as saturating Nat subtraction"

private def checkTrailingInvalidModuleRejected : IO Unit := do
  let source :=
    "module first_valid (output logic y); assign y = 1'b0; endmodule\n" ++
    "module second_invalid (output logic y); assign y = ; endmodule\n"
  ensure (isError (Tools.SVParser.Parser.parse source))
    "parser ignored an invalid second module after a valid first module"
  ensure (isError (parseAndLowerNative source) && isError (parseAndLower source))
    "parse-and-lower silently returned only the first of two modules"

private def checkUnknownLiteralsRejected : IO Unit := do
  for (label, literal) in [("x", "8'bxxxxxxxx"), ("z", "8'bzzzzzzzz")] do
    let source :=
      s!"module canonical_{label} #(parameter W=8) (output logic [W-1:0] y); " ++
      s!"assign y = $unsigned((W)'({literal})); endmodule\n"
    ensure (isError (parseAndLowerNative source) && isError (parseAndLower source))
      s!"canonical {label} literal was silently converted to numeric zero"

private def checkRawParameterDefaultFailsClosed : IO Unit := do
  let source :=
    "module raw_default #(parameter P=((1 << 40) >> 40)) " ++
    "(output logic [7:0] y); assign y=P; endmodule\n"
  runZeroOracle "raw_parameter_default_original" "raw_default" source
  ensure (isError (parseAndLowerNative source))
    "native lowering evaluated a packed raw parameter default as unbounded Nat"

private def checkUnsoundPositivityRejected : IO Unit := do
  let source :=
    "module raw_underflow #(parameter [31:0] P=0) (output logic [7:0] y); " ++
    "assign y=(8)'((((P+1)&2)-1)); endmodule\n"
  ensure (isError (parseAndLowerNative source) && isError (parseAndLower source))
    "bitwise-and was incorrectly treated as proof that a packed subtraction cannot underflow"

private def checkZeroWidthLocalparamRejected : IO Unit := do
  let source :=
    "module zero_width_localparam #(parameter [31:0] W=1) " ++
    "(output logic [1:0] y); " ++
    "localparam [W-1:0] LP=2; assign y=LP; endmodule\n"
  let native ← requireOk (parseAndLowerNative source)
  ensure (isError (specializeDesign native [("W", 0)]))
    "localparam [W-1:0] remained one bit when W was specialized to zero"

private def checkSizedClampLiteralNotCanonical : IO Unit := do
  let source :=
    "module sized_clamp #(parameter [31:0] W=8) " ++
    "(output logic [((((W > 0) && (W <= 20'd1048576)) ? W : 1)-1):0] y); " ++
    "assign y='0; endmodule\n"
  -- `20'd1048576` is zero after its explicit 20-bit truncation, so this
  -- external range is always one bit.  It only resembles Sparkle's canonical
  -- unsized cap and must not be recovered as the symbolic dimension W.
  ensure (isError (parseAndLowerNative source) && isError (parseAndLower source))
    "a sized/truncated cap literal was mistaken for Sparkle's canonical unsized dimension clamp"

def main : IO UInt32 := do
  let checks : List (String × IO Unit) :=
    [("raw sizing", checkRawSizingDoesNotBecomeNat),
     ("parameter reset", checkParameterizedResetFailsClosed),
     ("legacy procedural specialization", checkLegacyProceduralSpecializationBoundaries),
     ("untyped raw localparam", checkUntypedRawLocalparamFailsClosed),
     ("unpacked memory ranges", checkUnpackedMemoryRanges),
     ("trailing invalid module", checkTrailingInvalidModuleRejected),
     ("unknown literals", checkUnknownLiteralsRejected),
     ("raw parameter default", checkRawParameterDefaultFailsClosed),
     ("raw subtraction positivity", checkUnsoundPositivityRejected),
     ("zero-width localparam", checkZeroWidthLocalparamRejected),
     ("noncanonical sized clamp", checkSizedClampLiteralNotCanonical)]
  let mut failures : List String := []
  for (label, check) in checks do
    try check
    catch error => failures := failures ++ [s!"{label}: {error}"]
  if failures.isEmpty then
    IO.println "PASS: parser fail-closed regressions"
    return 0
  else
    IO.eprintln
      ("FAIL: parser fail-closed regressions:\n" ++
        String.intercalate "\n" (failures.map ("  - " ++ ·)))
    return 1

end Tests.ParserFailClosedTests

def main : IO UInt32 := Tests.ParserFailClosedTests.main
