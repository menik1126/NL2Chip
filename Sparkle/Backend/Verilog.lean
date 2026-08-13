/-
  SystemVerilog Backend

  Generates synthesizable SystemVerilog code from the IR.
-/

import Sparkle.IR.AST
import Sparkle.IR.Type

namespace Sparkle.Backend.Verilog

open Sparkle.IR.AST
open Sparkle.IR.Type

/-- Sanitize a name to be a valid Verilog identifier -/
def sanitizeName (name : String) : String :=
  name.replace "." "_"
    |>.replace "-" "_"
    |>.replace " " "_"
    |>.replace "'" "_prime"
    |>.replace "#" ""

/-- Emit a constant SystemVerilog expression used for widths and dimensions. -/
partial def emitDimExpr : DimExpr → String
  | .literal value => toString value
  | .param name => sanitizeName name
  | .add lhs rhs => s!"({emitDimExpr lhs} + {emitDimExpr rhs})"
  | .sub lhs rhs =>
      -- Lean Nat subtraction saturates at zero; unsigned SystemVerilog
      -- subtraction wraps, so retain the source semantics explicitly.
      let lhs' := emitDimExpr lhs
      let rhs' := emitDimExpr rhs
      s!"(({lhs'} >= {rhs'}) ? ({lhs'} - {rhs'}) : 0)"
  | .mul lhs rhs => s!"({emitDimExpr lhs} * {emitDimExpr rhs})"
  | .div lhs rhs =>
      -- `Nat.div lhs 0 = 0`; SystemVerilog division by zero instead yields X.
      let lhs' := emitDimExpr lhs
      let rhs' := emitDimExpr rhs
      s!"(({rhs'} == 0) ? 0 : ({lhs'} / {rhs'}))"
  | .mod lhs rhs =>
      -- `Nat.mod lhs 0 = lhs`; preserve that total Lean operation rather than
      -- relying on SystemVerilog's X-producing zero-divisor behavior.
      let lhs' := emitDimExpr lhs
      let rhs' := emitDimExpr rhs
      s!"(({rhs'} == 0) ? {lhs'} : ({lhs'} % {rhs'}))"
  | .pow lhs rhs => s!"({emitDimExpr lhs} ** {emitDimExpr rhs})"
  | .min lhs rhs =>
      let lhs' := emitDimExpr lhs
      let rhs' := emitDimExpr rhs
      s!"(({lhs'} < {rhs'}) ? {lhs'} : {rhs'})"
  | .max lhs rhs =>
      let lhs' := emitDimExpr lhs
      let rhs' := emitDimExpr rhs
      s!"(({lhs'} > {rhs'}) ? {lhs'} : {rhs'})"

/-- Clamp a hardware dimension to one for declarations and sized casts.  Invalid
    zero-valued parameter overrides must remain parseable so that the generated
    constant generate guard can report a controlled error instead of triggering
    front-end crashes while constructing a `[-1:0]` range. -/
def emitSafeDimension (dimension : DimExpr) : String :=
  match dimension.toNat? with
  | some value => toString (Nat.max value 1)
  | none =>
      let rendered := emitDimExpr dimension
      s!"(({rendered}) > 0 ? ({rendered}) : 1)"

/-- Emit the high endpoint of a nonempty packed/unpacked range. -/
def emitRangeHigh (dimension : DimExpr) : String :=
  match dimension.toNat? with
  | some value => toString (value - 1)
  | none => s!"({emitSafeDimension dimension} - 1)"

/-- Convert HWType to Verilog type declaration -/
def emitType (ty : HWType) : String :=
  match ty with
  | .bit => "logic"
  | .bitVector (.literal 1) => "logic"
  | .bitVector w => s!"logic [{emitRangeHigh w}:0]"
  | .array size elemType =>
    s!"{emitType elemType} [{emitRangeHigh size}:0]"

/-- Convert Operator to Verilog operator symbol -/
def emitOperator (op : Operator) : String :=
  match op with
  | .and => "&"
  | .or  => "|"
  | .xor => "^"
  | .not => "~"
  | .add => "+"
  | .sub => "-"
  | .mul => "*"
  | .eq  => "=="
  | .lt_u => "<"
  | .lt_s => "<" -- Handled in emitExpr with $signed()
  | .le_u => "<="
  | .le_s => "<=" -- Handled in emitExpr with $signed()
  | .gt_u => ">"
  | .gt_s => ">" -- Handled in emitExpr with $signed()
  | .ge_u => ">="
  | .ge_s => ">=" -- Handled in emitExpr with $signed()
  | .shl => "<<"
  | .shr => ">>"
  | .asr => ">>>"
  | .neg => "-"
  | .mux => "?"  -- Special case, handled in emitExpr

/-- Convert IR expression to Verilog expression -/
partial def emitExpr (e : Expr) : String :=
  match e with
  | .const value width =>
    match width.toNat? with
    | some concreteWidth =>
      if value < 0 then
        -- Negative values: convert to two's complement hex to avoid
        -- invalid Verilog literals like 32'd-2147483648
        let modulus : Int := (2 : Int) ^ concreteWidth
        let unsigned := ((value % modulus) + modulus) % modulus
        s!"{concreteWidth}'h{String.ofList (Nat.toDigits 16 unsigned.toNat)}"
      else
        s!"{concreteWidth}'d{value}"
    | none =>
      -- IEEE 1800 sized casting accepts a constant parameter expression as
      -- its size.  The cast gives literals the same elaborated width as the
      -- corresponding Lean BitVec without forcing a concrete default here.
      s!"{emitSafeDimension width}'({value})"

  | .ref name =>
    sanitizeName name

  | .concat args =>
    s!"\{{String.intercalate ", " (args.map emitExpr)}}"

  | .slice e hi lo =>
    -- Lean's `BitVec.extractLsb'` is total: bits above the source width are
    -- zero.  A raw SystemVerilog part-select instead produces X for an
    -- out-of-range index.  Logical right shift supplies the low-aligned
    -- value, and an explicit result-width cast both truncates and zero-extends
    -- it to the exact Lean slice width.
    let sliceWidth := hi - lo + 1
    s!"{emitSafeDimension sliceWidth}'($unsigned({emitExpr e}) >> {emitDimExpr lo})"

  | .index arr idx =>
    s!"{emitExpr arr}[{emitExpr idx}]"

  | .op .mux args =>
    -- Mux is special: cond ? then_val : else_val
    match args with
    | [cond, thenVal, elseVal] =>
      s!"({emitExpr cond} ? {emitExpr thenVal} : {emitExpr elseVal})"
    | _ => "/* ERROR: mux requires 3 arguments */"

  | .op .not args =>
    -- Unary NOT
    match args with
    | [arg] => s!"~{emitExpr arg}"
    | _ => "/* ERROR: not requires 1 argument */"

  | .op .neg args =>
    -- Unary negation
    match args with
    | [arg] => s!"-{emitExpr arg}"
    | _ => "/* ERROR: neg requires 1 argument */"

  | .op operator args =>
    -- Binary operators
    match args with
    | [arg1, arg2] =>
      match operator with
      | .lt_s | .le_s | .gt_s | .ge_s | .asr =>
        s!"($signed({emitExpr arg1}) {emitOperator operator} $signed({emitExpr arg2}))"
      | _ =>
        s!"({emitExpr arg1} {emitOperator operator} {emitExpr arg2})"
    | _ => s!"/* ERROR: operator {operator} with wrong arity */"

/-- Emit a single statement.
    The optional `wires` parameter provides wire declarations for register
    reset value width lookup. -/
def emitStmt (stmt : Stmt) (indent : String := "    ")
    (wires : List Port := []) : String :=
  match stmt with
  | .assign lhs rhs =>
    s!"{indent}assign {sanitizeName lhs} = {emitExpr rhs};"

  | .register output clock reset input initValue =>
    -- Generate always_ff block for register
    -- Look up output wire width for correct reset literal width
    let resetValue := match wires.find? (fun p => p.name == output) with
      | some p => emitExpr (.const initValue p.ty.width)
      | none => s!"/* ERROR: missing type for register {sanitizeName output} */ 'x"
    -- If clock name ends with "__neg", emit negedge trigger (no reset for negedge regs)
    if clock.endsWith "__neg" then
      let baseClock := clock.dropRight 5
      s!"{indent}always_ff @(negedge {sanitizeName baseClock}) begin\n" ++
      s!"{indent}    {sanitizeName output} <= {emitExpr input};\n" ++
      s!"{indent}end"
    -- If clock name ends with "__norst", emit posedge trigger without reset
    else if clock.endsWith "__norst" then
      let baseClock := clock.dropRight 7
      s!"{indent}always_ff @(posedge {sanitizeName baseClock}) begin\n" ++
      s!"{indent}    {sanitizeName output} <= {emitExpr input};\n" ++
      s!"{indent}end"
    else
      s!"{indent}always_ff @(posedge {sanitizeName clock} or posedge {sanitizeName reset}) begin\n" ++
      s!"{indent}    if ({sanitizeName reset})\n" ++
      s!"{indent}        {sanitizeName output} <= {resetValue};\n" ++
      s!"{indent}    else\n" ++
      s!"{indent}        {sanitizeName output} <= {emitExpr input};\n" ++
      s!"{indent}end"

  | .memory name addrWidth dataWidth clock writeAddr writeData writeEnable readAddr readData comboRead =>
    -- Generate memory array and always_ff block
    let memSize := DimExpr.mkPow 2 addrWidth
    let memDecl := s!"{indent}logic [{emitRangeHigh dataWidth}:0] {sanitizeName name} [0:{emitRangeHigh memSize}];"
    if comboRead then
      -- Combinational read: assign readData = mem[readAddr]
      let assignRead := s!"{indent}assign {sanitizeName readData} = {sanitizeName name}[{emitExpr readAddr}];"
      let alwaysBlock :=
        s!"{indent}always_ff @(posedge {sanitizeName clock}) begin\n" ++
        s!"{indent}    if ({emitExpr writeEnable}) begin\n" ++
        s!"{indent}        {sanitizeName name}[{emitExpr writeAddr}] <= {emitExpr writeData};\n" ++
        s!"{indent}    end\n" ++
        s!"{indent}end"
      memDecl ++ "\n" ++ assignRead ++ "\n" ++ alwaysBlock
    else
      -- Registered read: readData latched inside always_ff
      let alwaysBlock :=
        s!"{indent}always_ff @(posedge {sanitizeName clock}) begin\n" ++
        s!"{indent}    if ({emitExpr writeEnable}) begin\n" ++
        s!"{indent}        {sanitizeName name}[{emitExpr writeAddr}] <= {emitExpr writeData};\n" ++
        s!"{indent}    end\n" ++
        s!"{indent}    {sanitizeName readData} <= {sanitizeName name}[{emitExpr readAddr}];\n" ++
        s!"{indent}end"
      memDecl ++ "\n" ++ alwaysBlock

  | .inst moduleName instName connections parameterOverrides =>
    let connStrs := connections.map fun (portName, expr) =>
      s!".{sanitizeName portName}({emitExpr expr})"
    let connList := String.intercalate ", " connStrs
    let parameterList := if parameterOverrides.isEmpty then "" else
      let entries := parameterOverrides.map fun (parameterName, value) =>
        s!".{sanitizeName parameterName}({emitDimExpr value})"
      " #(" ++ String.intercalate ", " entries ++ ")"
    s!"{indent}{sanitizeName moduleName}{parameterList} {sanitizeName instName} ({connList});"

/-- Emit port declarations for module header -/
def emitPortList (inputs : List Port) (outputs : List Port) : String :=
  let inputDecls := inputs.map fun p =>
    s!"input {emitType p.ty} {sanitizeName p.name}"
  let outputDecls := outputs.map fun p =>
    s!"output {emitType p.ty} {sanitizeName p.name}"

  let allPorts := inputDecls ++ outputDecls
  if allPorts.isEmpty then
    ""
  else
    "\n    " ++ String.intercalate ",\n    " allPorts ++ "\n"

/-- Emit wire declarations -/
def emitWireDecls (wires : List Port) (indent : String := "    ") : String :=
  if wires.isEmpty then
    ""
  else
    let wireDecls := wires.map fun p =>
      s!"{indent}{emitType p.ty} {sanitizeName p.name};"
    String.intercalate "\n" wireDecls ++ "\n"

/-- Deduplicate dimensions structurally while retaining first-use order. -/
def uniqueDimensions (dimensions : List (String × DimExpr)) : List (String × DimExpr) :=
  dimensions.foldl (fun result entry =>
    if result.any (fun existing => existing.2 == entry.2) then result
    else result ++ [entry]) []

/-- Emit constant generate-time failures for invalid parameter overrides.  The
    declaration ranges are independently clamped, so even tools that elaborate
    ranges before generate conditions can parse the module safely. -/
def emitDimensionGuards (m : Module) (indent : String := "    ") : String :=
  let parameterGuards := m.parameters.zipIdx.map fun (parameter, index) =>
    let name := sanitizeName parameter.name
    s!"{indent}generate if (!({name} >= 0)) begin : sparkle_invalid_nat_parameter_{index}\n" ++
    s!"{indent}    initial $fatal(1, \"Sparkle Nat parameter {name} must be nonnegative\");\n" ++
    s!"{indent}end endgenerate"
  let dimensions := uniqueDimensions <| m.positiveDimensions.filter fun (_, dim) =>
    !dim.parameters.isEmpty
  let dimensionGuards := dimensions.zipIdx.map fun ((role, dimension), index) =>
      let rendered := emitDimExpr dimension
      let message := (s!"Sparkle invalid hardware dimension in {role}: {dimension} must be positive")
        |>.replace "\\" "\\\\" |>.replace "\"" "\\\""
      s!"{indent}generate if (!(({rendered}) > 0)) begin : sparkle_invalid_dimension_{index}\n" ++
      s!"{indent}    initial $fatal(1, \"{message}\");\n" ++
      s!"{indent}end endgenerate"
  String.intercalate "\n" (parameterGuards ++ dimensionGuards)

/-- Emit the full module -/
def emitModule (m : Module) : String :=
  -- For primitive/blackbox modules, just emit a comment (actual module comes from vendor)
  if m.isPrimitive then
    s!"// Primitive module: {m.name}\n" ++
    s!"// This is a blackbox module provided by the technology library\n" ++
    s!"// Interface: inputs={m.inputs.length}, outputs={m.outputs.length}\n\n"
  else
    let parameterList := if m.parameters.isEmpty then "" else
      let entries := m.parameters.map fun parameter =>
        -- Untyped parameters retain arbitrary-precision nonnegative integer
        -- defaults instead of silently truncating at 32 bits.
        s!"parameter {sanitizeName parameter.name} = {parameter.defaultValue}"
      " #(\n    " ++ String.intercalate ",\n    " entries ++ "\n)"
    let header := s!"// Generated by Sparkle HDL\n" ++
                  s!"// Module: {m.name}\n\n" ++
                  s!"module {sanitizeName m.name}{parameterList} ({emitPortList m.inputs m.outputs});\n"

    -- Filter out wires that are already declared as input/output ports
    let portNames := (m.inputs ++ m.outputs).map (·.name)
    let internalWires := m.wires.filter fun w => !portNames.contains w.name
    let wires := if internalWires.isEmpty then
      ""
    else
      "\n" ++ emitWireDecls internalWires ++ "\n"

    let guards := emitDimensionGuards m
    let body := if m.body.isEmpty then "" else
      let stmts := m.body.map (emitStmt · "    " m.wires)
      "\n" ++ String.intercalate "\n\n" stmts ++ "\n"

    let footer := "\nendmodule\n"

    header ++ wires ++ (if guards.isEmpty then "" else "\n" ++ guards ++ "\n") ++ body ++ footer

/-- Checked entry point for callers that need diagnostics instead of emitting
    malformed or ambiguous SystemVerilog. -/
def toVerilogChecked (m : Module) : Except String String := do
  m.validateDimensions
  m.validateSanitizedNames sanitizeName
  return emitModule m

/-- Main entry point: Convert a Module to SystemVerilog -/
def toVerilog (m : Module) : String :=
  match toVerilogChecked m with
  | .ok verilog => verilog
  | .error message => s!"/* ERROR: {message} */\n"

/-- Convert a full Design to SystemVerilog -/
def toVerilogDesignChecked (d : Design) : Except String String := do
  for module_ in d.modules do
    let emittedName := sanitizeName module_.name
    for other in d.modules do
      if module_.name != other.name && emittedName == sanitizeName other.name then
        throw s!"module names '{module_.name}' and '{other.name}' both sanitize to '{emittedName}'"
  let modules ← d.modules.mapM toVerilogChecked
  return String.intercalate "\n" modules

def toVerilogDesign (d : Design) : String :=
  match toVerilogDesignChecked d with
  | .ok modules => modules
  | .error message => s!"/* ERROR: {message} */\n"

/-- Write module to a file -/
def writeVerilogFile (m : Module) (filename : String) : IO Unit := do
  let verilog := toVerilog m
  IO.FS.writeFile filename verilog
  IO.println s!"Generated {filename}"

/-- Write a full design to a file -/
def writeVerilogDesignFile (d : Design) (filename : String) : IO Unit := do
  let verilog := toVerilogDesign d
  IO.FS.writeFile filename verilog
  IO.println s!"Generated {filename}"

end Sparkle.Backend.Verilog
