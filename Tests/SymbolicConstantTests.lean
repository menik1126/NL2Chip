/-
  Regression tests for natural-number expressions materialized as packed
  parameter-dependent constants.
-/

import Sparkle
import Sparkle.Backend.CppSim
import Sparkle.Backend.Verilog
import Sparkle.Compiler.Elab
import Sparkle.IR.Optimize
import Sparkle.IR.Specialize
import Tools.SVParser
import Tools.SVParser.Verify

namespace Tests.SymbolicConstantTests

open Sparkle.Core.Domain Sparkle.Core.Signal
open Sparkle.IR.AST Sparkle.IR.Type Sparkle.IR.Specialize
open Tools.SVParser.Lower

set_option maxRecDepth 4096
set_option maxHeartbeats 800000

/-- Exercise the Lean elaborator before runtime tests exercise the SV parser. -/
def leanParameterizedMask {dom : DomainConfig} {W K : Nat}
    : Signal dom (BitVec W) :=
  Signal.pure (BitVec.ofNat W ((1 <<< K) - 1))

#synthesizeVerilog leanParameterizedMask parameters [W := 8, K := 3]

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

private def requireTop (design : Design) : IO Module :=
  match design.findModule design.topModule with
  | some module_ => pure module_
  | none => throw (IO.userError s!"missing top module '{design.topModule}'")

private def assignRhs? (module_ : Module) (name : String) : Option Expr :=
  module_.body.findSome? fun statement => match statement with
    | .assign lhs rhs => if lhs == name then some rhs else none
    | _ => none

private partial def containsRef (target : String) : Expr → Bool
  | .ref name => name == target
  | .op _ args | .concat args => args.any (containsRef target)
  | .resize _ value | .slice value _ _ => containsRef target value
  | .index array index => containsRef target array || containsRef target index
  | .const _ _ | .paramConst _ _ => false

private partial def containsMul : Expr → Bool
  | .op .mul _ => true
  | .op _ args | .concat args => args.any containsMul
  | .resize _ value | .slice value _ _ => containsMul value
  | .index array index => containsMul array || containsMul index
  | .const _ _ | .paramConst _ _ | .ref _ => false

/-- A parser-independent IR fixture for wide natural-number computations. -/
private def directIRExpressions : List (String × DimExpr) :=
  let width : DimExpr := .param "W"
  let wideOne := DimExpr.mkShl 1 width
  [ ("shift_right", DimExpr.mkShr wideOne 1)
  , ("divide_two", DimExpr.mkDiv wideOne 2)
  , ("remainder", DimExpr.mkMod (DimExpr.mkAdd wideOne 3) 7)
  , ("logarithm", DimExpr.mkClog2 wideOne)
  ]

private def directIRDesign : Design :=
  let width : DimExpr := .param "W"
  let resultWidth := DimExpr.mkAdd width 1
  let outputs := directIRExpressions.map fun (name, _) =>
    ({ name, ty := .bitVector resultWidth } : Port)
  let body := directIRExpressions.map fun (name, value) =>
    Stmt.assign name (.paramConst value resultWidth)
  let module_ : Module :=
    { (Module.empty "direct_symbolic_constants") with
      «parameters» := [{ name := "W", defaultValue := 40 }]
      outputs
      body }
  { topModule := module_.name, modules := [module_] }

private def directExpected (name : String) (width : Nat) : Option Nat :=
  match name with
  | "shift_right" | "divide_two" => some (1 <<< (width - 1))
  | "remainder" => some (((1 <<< width) + 3) % 7)
  | "logarithm" => some width
  | _ => none

private def checkDirectIRSemantics (width : Nat) : IO Unit := do
  let lookup := fun name => if name == "W" then some width else none
  for (name, valueExpr) in directIRExpressions do
    let value ← match valueExpr.eval? lookup with
      | some value => pure value
      | none => throw (IO.userError s!"{name}: direct IR value did not evaluate")
    let expected ← match directExpected name width with
      | some value => pure value
      | none => throw (IO.userError s!"{name}: test fixture has no expected value")
    ensure (value == expected)
      s!"{name}: W={width} evaluated to {value}, expected {expected}"
    let boundExpr := valueExpr.natValueBitWidthBound
    let bound ← match boundExpr.eval? lookup with
      | some bound => pure bound
      | none => throw (IO.userError s!"{name}: working-width bound remained unresolved")
    ensure (bound > 0) s!"{name}: working-width bound was not strictly positive"
    ensure (value < 2 ^ bound)
      s!"{name}: value {value} does not fit conservative {bound}-bit bound"

  let specialized ← requireOk (specializeDesign directIRDesign [("W", width)])
  let top ← requireTop specialized
  for (name, _) in directIRExpressions do
    let expected := (directExpected name width).getD 0
    match assignRhs? top name with
    | some (.const value targetWidth) =>
        ensure (value == Int.ofNat expected && targetWidth.toNat? == some (width + 1))
          s!"{name}: specialized direct IR constant has the wrong value or width"
    | _ => throw (IO.userError s!"{name}: direct IR parameter constant did not specialize")

  ensure ((DimExpr.literal 0).natValueBitWidthBound.toNat? == some 1)
    "zero literal did not receive a one-bit working width"
  ensure ((DimExpr.literal 1).natValueBitWidthBound.toNat? == some 1)
    "one literal did not receive a one-bit working width"
  ensure ((DimExpr.literal 8).natValueBitWidthBound.toNat? == some 4)
    "literal working width is not its positive binary width"
  ensure ((DimExpr.param "W").natValueBitWidthBound.toNat? == some 32)
    "retained parameter working width is not 32 bits"
  let powerBound := (DimExpr.mkPow 2 (.param "W")).natValueBitWidthBound
  ensure (powerBound.eval? lookup == some (Nat.max 1 (2 * width)))
    "power working-width bound did not use max 1 (baseWidth * exponent)"

private def nativeSource : String :=
  "module symbolic_constants #(parameter W = 8, parameter K = 3) (\n" ++
  "  output logic [W-1:0] shift_mask,\n" ++
  "  output logic [W-1:0] pow_mask,\n" ++
  "  output logic [W-1:0] mixed_mask,\n" ++
  "  output logic [W-1:0] raw_width,\n" ++
  "  output logic [W-1:0] log_width,\n" ++
  "  output logic [W-1:0] local_mask,\n" ++
  "  output logic [W-1:0] local_k\n" ++
  ");\n" ++
  "localparam [W-1:0] LOCAL_MASK = $unsigned((W)'((1 << K) - 1));\n" ++
  "localparam LOCAL_K = K + 1;\n" ++
  "assign shift_mask = $unsigned((W)'((1 << K) - 1));\n" ++
  "assign pow_mask = $unsigned((W)'((2 ** W) - 1));\n" ++
  "assign mixed_mask = $unsigned((W)'((((1 << (K + 1)) - 1) & ((2 ** W) - 1))));\n" ++
  "assign raw_width = W;\n" ++
  "assign log_width = $clog2(W);\n" ++
  "assign local_mask = LOCAL_MASK;\n" ++
  "assign local_k = LOCAL_K;\n" ++
  "endmodule\n"

private def checkNativeShape (design : Design) : IO Unit := do
  let top ← requireTop design
  let width : DimExpr := .param "W"
  let shiftValue := DimExpr.mkSub (DimExpr.mkShl 1 (.param "K")) 1
  let powValue := DimExpr.mkSub (DimExpr.mkPow 2 width) 1
  match assignRhs? top "shift_mask" with
  | some (.paramConst value targetWidth) =>
      ensure (value == shiftValue && targetWidth == width)
        "shift mask was not retained as a W-bit parameter constant"
  | _ => throw (IO.userError "shift mask lost its parameter-constant node")
  match assignRhs? top "pow_mask" with
  | some (.paramConst value targetWidth) =>
      ensure (value == powValue && targetWidth == width)
        "power mask did not retain exponentiation"
  | _ => throw (IO.userError "power mask lost its parameter-constant node")
  ensure (!(top.body.any fun statement => match statement with
      | .assign _ rhs => containsMul rhs
      | _ => false)) "SystemVerilog ** was silently lowered as multiplication"
  match assignRhs? top "raw_width" with
  | some (.paramConst (.param "W") targetWidth) =>
      ensure (targetWidth == width) "raw parameter reference used the wrong packed width"
  | _ => throw (IO.userError "raw parameter reference survived as a data-wire reference")
  match assignRhs? top "log_width" with
  | some (.paramConst (.clog2 (.param "W")) targetWidth) =>
      ensure (targetWidth == width) "$clog2 parameter constant used the wrong width"
  | _ => throw (IO.userError "$clog2(W) was not retained symbolically")
  match assignRhs? top "LOCAL_MASK" with
  | some (.paramConst value targetWidth) =>
      ensure (value == shiftValue && targetWidth == width)
        "packed localparam was frozen at its default value"
  | _ => throw (IO.userError "packed localparam was not retained symbolically")
  match assignRhs? top "LOCAL_K" with
  | some (.paramConst value (.literal 32)) =>
      ensure (value == DimExpr.mkAdd (.param "K") 1)
        "untyped localparam was frozen at its default value"
  | _ => throw (IO.userError "untyped localparam was not retained as a 32-bit parameter constant")

private def requireConcreteConst (module_ : Module) (name : String)
    (expectedValue expectedWidth : Nat) : IO Unit :=
  match assignRhs? module_ name with
  | some (.const value width) =>
      ensure (value == Int.ofNat expectedValue && width.toNat? == some expectedWidth)
        s!"{name}: specialized constant differs from expected {expectedValue}#{expectedWidth}"
  | _ => throw (IO.userError s!"{name}: specialization did not fold parameter constant")

private def checkSpecialization (design : Design) (width key : Nat) : IO Design := do
  let specialized ← requireOk (specializeDesign design [("W", width), ("K", key)])
  let top ← requireTop specialized
  ensure top.parameters.isEmpty s!"W={width}: specialization retained parameters"
  for dimension in top.dimensionExpressions do
    ensure dimension.isConcrete s!"W={width}: specialization retained {dimension}"
  let modulus := 2 ^ width
  let shiftMask := ((1 <<< key) - 1) % modulus
  let powMask := ((2 ^ width) - 1) % modulus
  let mixedMask := (((1 <<< (key + 1)) - 1) &&& ((2 ^ width) - 1)) % modulus
  requireConcreteConst top "shift_mask" shiftMask width
  requireConcreteConst top "pow_mask" powMask width
  requireConcreteConst top "mixed_mask" mixedMask width
  requireConcreteConst top "raw_width" (width % modulus) width
  requireConcreteConst top "log_width" (DimExpr.clog2Nat width % modulus) width
  ensure (!(top.body.any fun statement => match statement with
      | .assign _ rhs => containsRef "W" rhs || containsRef "K" rhs
      | _ => false)) s!"W={width}: parameter survived as a data-wire reference"
  let optimized := Sparkle.IR.Optimize.optimizeDesign specialized
  let _ ← requireOk (Sparkle.Backend.Verilog.toVerilogDesignChecked optimized)
  return optimized

private def runProcess (label command : String) (args : Array String) : IO String := do
  let result ← IO.Process.output { cmd := command, args }
  unless result.exitCode == 0 do
    throw (IO.userError
      s!"{label} failed (exit {result.exitCode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
  return result.stdout

private def runDirectIRVerilog (width : Nat) : IO Unit := do
  let verilog ← requireOk
    (Sparkle.Backend.Verilog.toVerilogDesignChecked directIRDesign)
  -- The canonical work-width wrappers must survive a native parse and a
  -- second specialization, not merely simulate correctly once.
  let reparsed ← requireOk (parseAndLowerNative verilog)
  let reparsedSpecialized ← requireOk (specializeDesign reparsed [("W", width)])
  let reparsedTop ← requireTop reparsedSpecialized
  for (name, _) in directIRExpressions do
    let expected := (directExpected name width).getD 0
    requireConcreteConst reparsedTop name expected (width + 1)

  let expectedHighBit :=
    "{1'b0, 1'b1, {" ++ toString (width - 1) ++ "{1'b0}}}"
  let remainder := ((1 <<< width) + 3) % 7
  let bench :=
    "module tb; localparam W=" ++ toString width ++ ";\n" ++
    "wire [W:0] shift_right, divide_two, remainder, logarithm;\n" ++
    "direct_symbolic_constants #(.W(W)) dut(.*); initial begin #1;\n" ++
    s!"if (shift_right !== {expectedHighBit}) $fatal(1, \"shift-right W={width}\");\n" ++
    s!"if (divide_two !== {expectedHighBit}) $fatal(1, \"divide W={width}\");\n" ++
    s!"if (remainder !== {width + 1}'d{remainder}) $fatal(1, \"mod W={width}\");\n" ++
    s!"if (logarithm !== {width + 1}'d{width}) $fatal(1, \"clog2 W={width}\");\n" ++
    "$display(\"PASS direct Nat\"); $finish; end endmodule\n"
  let dir := "/tmp/sparkle_symbolic_constant_tests"
  IO.FS.createDirAll dir
  let sourcePath := s!"{dir}/direct_{width}.sv"
  let designPath := s!"{dir}/direct_{width}_design.sv"
  let executable := s!"{dir}/direct_{width}.vvp"
  IO.FS.writeFile designPath verilog
  IO.FS.writeFile sourcePath (verilog ++ "\n" ++ bench)
  let _ ← runProcess s!"iverilog direct W={width}" "iverilog"
    #["-g2012", "-s", "tb", "-o", executable, sourcePath]
  let _ ← runProcess s!"vvp direct W={width}" "vvp" #[executable]
  if width == 65 then
    let _ ← runProcess "yosys direct W=65" "yosys"
      #["-q", "-p",
        "read_verilog -sv " ++ designPath ++
        "; hierarchy -top direct_symbolic_constants; chparam -set W 65 direct_symbolic_constants; proc; opt"]

private def runWideVerilog (verilog : String) (width key : Nat) : IO Unit := do
  let highZeros := width - key
  let replicate (count : Nat) (bit : String) : String :=
    "{" ++ toString count ++ "{" ++ bit ++ "}}"
  let expectedShift := "{" ++ replicate highZeros "1'b0" ++ ", " ++
    replicate key "1'b1" ++ "}"
  let expectedMixed :=
    if key + 1 <= width then
      "{" ++ replicate (width - (key + 1)) "1'b0" ++ ", " ++
        replicate (key + 1) "1'b1" ++ "}"
    else replicate width "1'b1"
  let expectedAllOnes := replicate width "1'b1"
  let bench :=
    "module tb;\n" ++
    s!"localparam W = {width}; localparam K = {key};\n" ++
    "wire [W-1:0] shift_mask, pow_mask, mixed_mask, raw_width, log_width, local_mask, local_k;\n" ++
    "symbolic_constants #(.W(W), .K(K)) dut (.*);\n" ++
    "initial begin #1;\n" ++
    s!"if (shift_mask !== {expectedShift}) $fatal(1, \"shift mask W={width}\");\n" ++
    s!"if (pow_mask !== {expectedAllOnes}) $fatal(1, \"pow mask W={width}\");\n" ++
    s!"if (mixed_mask !== {expectedMixed}) $fatal(1, \"mixed mask W={width}\");\n" ++
    "if (local_mask !== shift_mask) $fatal(1, \"local mask froze defaults\");\n" ++
    s!"if (raw_width !== {width}) $fatal(1, \"raw W value\");\n" ++
    s!"if (log_width !== {DimExpr.clog2Nat width}) $fatal(1, \"clog2 W\");\n" ++
    s!"if (local_k !== {key + 1}) $fatal(1, \"local K froze defaults\");\n" ++
    "$display(\"PASS\"); $finish; end endmodule\n"
  let dir := "/tmp/sparkle_symbolic_constant_tests"
  IO.FS.createDirAll dir
  let sourcePath := s!"{dir}/wide_{width}.sv"
  let executable := s!"{dir}/wide_{width}.vvp"
  IO.FS.writeFile sourcePath (verilog ++ "\n" ++ bench)
  let _ ← runProcess s!"iverilog W={width}" "iverilog"
    #["-g2012", "-s", "tb", "-o", executable, sourcePath]
  let _ ← runProcess s!"vvp W={width}" "vvp" #[executable]

private def checkParserConstantSyntax : IO Unit := do
  let source :=
    "module parser_constant_syntax #(parameter K = 2) (\n" ++
    "  output logic [63:0] sized_literal, precedence, associativity);\n" ++
    "assign sized_literal = $unsigned((64)'(4'd31 + K));\n" ++
    "assign precedence = $unsigned((64)'(2 * 3 ** K));\n" ++
    "assign associativity = $unsigned((64)'(2 ** 3 ** K));\n" ++
    "endmodule\n"
  let design ← requireOk (parseAndLowerNative source)
  let specialized ← requireOk (specializeDesign design [("K", 2)])
  let top ← requireTop specialized
  -- 4'd31 is truncated to 15 before addition.  Exponentiation binds more
  -- tightly than multiplication, while chained powers follow SV's
  -- left-to-right behavior: (2**3)**2 = 64.
  requireConcreteConst top "sized_literal" 17 64
  requireConcreteConst top "precedence" 18 64
  requireConcreteConst top "associativity" 64 64

  let unknownSystemFunction :=
    "module unknown_system #(parameter K=2) (output logic [31:0] y); " ++
    "assign y = $bits(K); endmodule\n"
  ensure (isError (parseAndLowerNative unknownSystemFunction))
    "unknown $bits system function was silently erased to its argument"

/-- An untyped localparam inherits the packed width of this sized-cast RHS.
    It must not become a fixed 32-bit hardware wire during native lowering. -/
private def checkUntypedWideLocalparam : IO Unit := do
  let source :=
    "module untyped_wide_localparam #(parameter W=40) " ++
    "(output logic [W-1:0] y);\n" ++
    "localparam MASK = $unsigned((W)'((1 << W) - 1));\n" ++
    "assign y = MASK; endmodule\n"
  let design ← requireOk (parseAndLowerNative source)
  let verilog ← requireOk (Sparkle.Backend.Verilog.toVerilogDesignChecked design)
  let bench :=
    "module tb; wire [39:0] y40; wire [64:0] y65;\n" ++
    "untyped_wide_localparam #(.W(40)) d40(.y(y40));\n" ++
    "untyped_wide_localparam #(.W(65)) d65(.y(y65));\n" ++
    "initial begin #1;\n" ++
    "if (y40 !== 40'hffffffffff) $fatal(1, \"W=40 localparam truncated\");\n" ++
    "if (y65 !== {65{1'b1}}) $fatal(1, \"W=65 localparam truncated\");\n" ++
    "$display(\"PASS localparam\"); $finish; end endmodule\n"
  let dir := "/tmp/sparkle_symbolic_constant_tests"
  IO.FS.createDirAll dir
  let sourcePath := s!"{dir}/untyped_localparam.sv"
  let executable := s!"{dir}/untyped_localparam.vvp"
  IO.FS.writeFile sourcePath (verilog ++ "\n" ++ bench)
  let _ ← runProcess "iverilog untyped localparam" "iverilog"
    #["-g2012", "-s", "tb", "-o", executable, sourcePath]
  let _ ← runProcess "vvp untyped localparam" "vvp" #[executable]

private def checkCppSimWideIntermediate (design : Design) : IO Unit := do
  let specialized ← checkSpecialization design 8 80
  let cpp ← requireOk
    (Sparkle.Backend.CppSim.toCppSimDesignChecked specialized)
  ensure (contains cpp "0xffULL" || contains cpp "0xFFULL")
    "CppSim did not reduce an 80-bit intermediate modulo the 8-bit result width"
  let dir := "/tmp/sparkle_symbolic_constant_tests"
  IO.FS.createDirAll dir
  let path := s!"{dir}/cpp_w8_k80.cpp"
  IO.FS.writeFile path cpp
  let _ ← runProcess "g++ CppSim W=8 K=80" "g++"
    #["-std=c++17", "-x", "c++", "-fsyntax-only", path]

private def checkVerificationPreservesIntermediateWidths : IO Unit := do
  let module_ : Module :=
    { (Module.empty "verify_intermediate_width") with
      inputs := [{ name := "clk", ty := .bit }]
      outputs := [{ name := "q", ty := .bitVector 8 }]
      wires := [{ name := "q", ty := .bitVector 8 }]
      body := [.register "q" "clk" "rst"
        (.op .shr [.const 256 32, .const 8 32]) 0] }
  let source ← requireOk (Tools.SVParser.Verify.moduleToLeanChecked module_)
  ensure (contains source "256#32" && contains source "8#32")
    "verification source rewrote explicit 32-bit constants to the 8-bit register width"
  ensure (contains source "BitVec.setWidth 8")
    "verification source did not apply the register width at the final assignment boundary"

private def checkCppSimTotalizedShifts : IO Unit := do
  let module_ : Module :=
    { (Module.empty "totalized_shifts") with
      inputs := [{ name := "x", ty := .bitVector 16 }]
      outputs :=
        [{ name := "left32", ty := .bitVector 16 },
         { name := "right40", ty := .bitVector 16 },
         { name := "asr32", ty := .bitVector 16 },
         { name := "slice80", ty := .bit }]
      body :=
        [.assign "left32" (.op .shl [.ref "x", .const 32 6]),
         .assign "right40" (.op .shr [.ref "x", .const 40 6]),
         .assign "asr32" (.op .asr [.ref "x", .const 32 6]),
         .assign "slice80" (.slice (.ref "x") 80 80)] }
  let design : Design := { topModule := module_.name, modules := [module_] }
  let cpp ← requireOk (Sparkle.Backend.CppSim.toCppSimDesignChecked design)
  let main :=
    "\nint main() { totalized_shifts s; s.x = 0x8001; s.eval(); " ++
    "return (s.left32 == 0 && s.right40 == 0 && s.asr32 == 0xffff && " ++
    "s.slice80 == 0) ? 0 : 1; }\n"
  let dir := "/tmp/sparkle_symbolic_constant_tests"
  IO.FS.createDirAll dir
  let sourcePath := s!"{dir}/totalized_shifts.cpp"
  let executable := s!"{dir}/totalized_shifts"
  IO.FS.writeFile sourcePath (cpp ++ main)
  let _ ← runProcess "g++ totalized shifts" "g++"
    #["-std=c++17", "-O2", "-x", "c++", sourcePath, "-o", executable]
  let _ ← runProcess "CppSim totalized shifts" executable #[]

private def checkCppSimRejectsWideExpressionChild : IO Unit := do
  let module_ : Module :=
    { (Module.empty "wide_shift_rhs") with
      outputs := [{ name := "y", ty := .bitVector 8 }]
      body :=
        [.assign "y" (.op .shl [.const 1 8, .const 80 81])] }
  let design : Design := { topModule := module_.name, modules := [module_] }
  for (api, result) in
      [("module", Sparkle.Backend.CppSim.toCppSimChecked module_),
       ("design", Sparkle.Backend.CppSim.toCppSimDesignChecked design),
       ("JIT", Sparkle.Backend.CppSim.toCppSimJITChecked design)] do
    match result with
    | .ok _ =>
        throw (IO.userError
          s!"CppSim {api} checked API accepted an 81-bit shift RHS under an 8-bit output")
    | .error message =>
        ensure (contains message "81-bit expression")
          s!"CppSim {api} checked API returned the wrong wide-expression error: {message}"

private def checkCppSimPassiveWideAggregate : IO Unit := do
  -- Match the optimized H.264 tuple-packing shape: a 32-bit high word plus a
  -- scalar 64-bit resize containing two more 32-bit words, followed by a
  -- same-width packed-container copy to the public output.
  let packed64 := Expr.resize 64 (.concat
    [.const 1432778632 32, .const 2578103244 32])
  let module_ : Module :=
    { (Module.empty "passive_wide_aggregate") with
      outputs := [{ name := "out", ty := .bitVector 96 }]
      wires := [{ name := "packed", ty := .bitVector 96 }]
      body :=
        [.assign "packed" (.concat [.const 287454020 32, packed64]),
         .assign "out" (.ref "packed")] }
  let design : Design := { topModule := module_.name, modules := [module_] }
  let cpp ← requireOk (Sparkle.Backend.CppSim.toCppSimJITChecked design)
  let main :=
    "\nint main() { void* s = jit_create(); jit_eval(s); " ++
    "bool ok = jit_num_outputs() == 3 && " ++
    "jit_get_output(s, 0) == 0x99aabbccULL && " ++
    "jit_get_output(s, 1) == 0x55667788ULL && " ++
    "jit_get_output(s, 2) == 0x11223344ULL; " ++
    "jit_destroy(s); return ok ? 0 : 1; }\n"
  let dir := "/tmp/sparkle_symbolic_constant_tests"
  IO.FS.createDirAll dir
  let sourcePath := s!"{dir}/passive_wide_aggregate.cpp"
  let executable := s!"{dir}/passive_wide_aggregate"
  IO.FS.writeFile sourcePath (cpp ++ main)
  let _ ← runProcess "g++ passive wide aggregate" "g++"
    #["-std=c++17", "-O2", "-x", "c++", sourcePath, "-o", executable]
  let _ ← runProcess "CppSim passive wide aggregate" executable #[]

  let wideArithmetic : Module :=
    { (Module.empty "wide_arithmetic") with
      outputs := [{ name := "out", ty := .bitVector 96 }]
      body := [.assign "out" (.op .add [.const 1 96, .const 2 96])] }
  ensure (isError (Sparkle.Backend.CppSim.toCppSimChecked wideArithmetic))
    "CppSim accepted wide arithmetic as a passive aggregate"

  let nonWordConcat : Module :=
    { (Module.empty "non_word_concat") with
      outputs := [{ name := "out", ty := .bitVector 88 }]
      body := [.assign "out" (.concat [.const 1 24, .const 2 64])] }
  ensure (isError (Sparkle.Backend.CppSim.toCppSimChecked nonWordConcat))
    "CppSim accepted a non-word-aligned wide concat"

  let wideInput : Module :=
    { (Module.empty "wide_input") with
      inputs := [{ name := "x", ty := .bitVector 96 }] }
  ensure (isError (Sparkle.Backend.CppSim.toCppSimChecked wideInput))
    "CppSim accepted a wide input through its uint64_t ABI"

  let wideRegister : Module :=
    { (Module.empty "wide_register") with
      outputs := [{ name := "q", ty := .bitVector 96 }]
      body := [.register "q" "clk" "rst" (.const 0 32) 0] }
  ensure (isError (Sparkle.Backend.CppSim.toCppSimChecked wideRegister))
    "CppSim accepted a wide register as a passive aggregate"

  let wideSlice : Module :=
    { (Module.empty "wide_slice") with
      outputs := [{ name := "y", ty := .bitVector 32 }]
      wires := [{ name := "packed", ty := .bitVector 96 }]
      body :=
        [.assign "packed" (.concat [.const 1 32, .const 2 64]),
         .assign "y" (.slice (.ref "packed") 31 0)] }
  ensure (isError (Sparkle.Backend.CppSim.toCppSimChecked wideSlice))
    "CppSim accepted a slice from a wide packed container"

  let arrayLeaf : Module :=
    { (Module.empty "wide_concat_array_leaf") with
      outputs := [{ name := "out", ty := .bitVector 96 }]
      wires := [{ name := "bytes", ty := .array 4 (.bitVector 8) }]
      body :=
        [.assign "out" (.concat [.const 1 32, .ref "bytes", .const 2 32])] }
  ensure (isError (Sparkle.Backend.CppSim.toCppSimChecked arrayLeaf))
    "CppSim accepted an unpacked array aggregate as a 32-bit concat leaf"

  let nestedArrayIndex : Module :=
    { (Module.empty "wide_concat_nested_array_index") with
      outputs := [{ name := "out", ty := .bitVector 96 }]
      wires := [{ name := "matrix", ty := .array 2 (.array 4 (.bitVector 8)) }]
      body :=
        [.assign "out" (.concat
          [.const 1 32, .index (.ref "matrix") (.const 0 1), .const 2 32])] }
  ensure (isError (Sparkle.Backend.CppSim.toCppSimChecked nestedArrayIndex))
    "CppSim accepted an index whose result is still an unpacked array"

  let arrayTarget : Module :=
    { (Module.empty "wide_array_target") with
      outputs := [{ name := "out", ty := .array 3 (.bitVector 32) }]
      body := [.assign "out" (.concat [.const 1 32, .const 2 32, .const 3 32])] }
  ensure (isError (Sparkle.Backend.CppSim.toCppSimChecked arrayTarget))
    "CppSim accepted an unpacked array as a passive packed assignment target"

private def checkFailClosed : IO Unit := do
  let dataDependentCast :=
    "module bad #(parameter W=8) (input logic [W-1:0] x, output logic [W-1:0] y); " ++
    "assign y = $unsigned((W)'(x + 1)); endmodule\n"
  ensure (isError (parseAndLowerNative dataDependentCast))
    "data-dependent context-sized arithmetic was accepted as a parameter constant"
  let dynamicPower :=
    "module bad_power #(parameter W=8) (input logic [W-1:0] x, output logic [W-1:0] y); " ++
    "assign y = 2 ** x; endmodule\n"
  ensure (isError (parseAndLowerNative dynamicPower))
    "dynamic exponentiation was silently lowered"

def main : IO Unit := do
  checkDirectIRSemantics 40
  checkDirectIRSemantics 65
  runDirectIRVerilog 40
  runDirectIRVerilog 65
  checkParserConstantSyntax

  let design ← requireOk (parseAndLowerNative nativeSource)
  checkNativeShape design

  let emitted ← requireOk (Sparkle.Backend.Verilog.toVerilogDesignChecked design)
  ensure (contains emitted " ** ") "Verilog emission changed exponentiation to multiplication"
  ensure (contains emitted "$clog2") "Verilog emission lost $clog2"
  let reparsed ← requireOk (parseAndLowerNative emitted)
  checkNativeShape reparsed

  ensure (isError (Sparkle.Backend.CppSim.toCppSimDesignChecked design))
    "CppSim accepted an unspecialized parameter constant"
  let unspecializedTop ← requireTop design
  ensure (isError (Tools.SVParser.Verify.moduleToLeanChecked unspecializedTop))
    "verification model accepted an unspecialized parameter constant"

  for (width, key) in [(3, 2), (17, 9), (64, 40), (65, 40), (257, 80)] do
    let specialized ← checkSpecialization design width key
    if width <= 64 then
      let _ ← requireOk (Sparkle.Backend.CppSim.toCppSimDesignChecked specialized)
      let top ← requireTop specialized
      let _ ← requireOk (Tools.SVParser.Verify.moduleToLeanChecked top)

  checkCppSimWideIntermediate design
  checkVerificationPreservesIntermediateWidths
  checkCppSimTotalizedShifts
  checkCppSimRejectsWideExpressionChild
  checkCppSimPassiveWideAggregate

  runWideVerilog emitted 65 40
  runWideVerilog emitted 257 80
  checkUntypedWideLocalparam
  checkFailClosed
  IO.println "symbolic constant tests passed"

end Tests.SymbolicConstantTests

/-- Lake executable entry point. -/
def main : IO Unit := Tests.SymbolicConstantTests.main
