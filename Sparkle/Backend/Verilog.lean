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

/-- Emit a symbolic hardware dimension as a SystemVerilog constant expression. -/
partial def emitDimExpr : DimExpr → String
  | .literal value => s!"{value}"
  | .parameter name => sanitizeName name
  | .add lhs rhs => s!"({emitDimExpr lhs} + {emitDimExpr rhs})"
  | .sub lhs rhs => s!"({emitDimExpr lhs} - {emitDimExpr rhs})"
  | .mul lhs rhs => s!"({emitDimExpr lhs} * {emitDimExpr rhs})"
  | .div lhs rhs => s!"({emitDimExpr lhs} / {emitDimExpr rhs})"
  | .mod lhs rhs => s!"({emitDimExpr lhs} % {emitDimExpr rhs})"
  | .pow base exponent => s!"({emitDimExpr base} ** {emitDimExpr exponent})"
  | .clog2 value => s!"$clog2({emitDimExpr value})"
  | .min lhs rhs =>
      s!"(({emitDimExpr lhs} < {emitDimExpr rhs}) ? {emitDimExpr lhs} : {emitDimExpr rhs})"
  | .max lhs rhs =>
      s!"(({emitDimExpr lhs} > {emitDimExpr rhs}) ? {emitDimExpr lhs} : {emitDimExpr rhs})"

/-- Convert HWType to Verilog type declaration -/
def emitType (ty : HWType) : String :=
  match ty with
  | .bit => "logic"
  | .bitVector 1 => "logic"
  | .bitVector w => s!"logic [{w-1}:0]"
  | .bitVectorDim width => s!"logic [{emitDimExpr width}-1:0]"
  | .array size elemType =>
    s!"{emitType elemType} [{size-1}:0]"
  | .arrayDim size elemType =>
    s!"{emitType elemType} [{emitDimExpr size}-1:0]"

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
  | .mod => "%"
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
  | .sext => "$signed"
  | .neg => "-"
  | .popcount => "$countones"
  | .mux => "?"  -- Special case, handled in emitExpr

/-- Convert IR expression to Verilog expression -/
partial def emitExpr (e : Expr) : String :=
  match e with
  | .const value width =>
    if value < 0 then
      -- Negative values: convert to two's complement hex to avoid
      -- invalid Verilog literals like 32'd-2147483648
      let modulus : Int := (2 : Int) ^ width
      let unsigned := ((value % modulus) + modulus) % modulus
      s!"{width}'h{String.ofList (Nat.toDigits 16 unsigned.toNat)}"
    else
      s!"{width}'d{value}"

  | .constDim value width =>
    s!"{emitDimExpr width}'({value})"
  | .dimension value =>
    emitDimExpr value
  | .ref name =>
    sanitizeName name

  | .concat args =>
    s!"\{{String.intercalate ", " (args.map emitExpr)}}"

  | .slice e hi lo =>
    s!"{emitExpr e}[{hi}:{lo}]"

  | .sliceDim e hi lo =>
    s!"{emitExpr e}[{emitDimExpr hi}:{emitDimExpr lo}]"

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

  | .op .popcount args =>
    match args with
    | [arg] => s!"$countones({emitExpr arg})"
    | _ => "/* ERROR: popcount requires 1 argument */"

  | .op .sext args =>
    match args with
    | [arg] => s!"$signed({emitExpr arg})"
    | _ => "/* ERROR: sext requires 1 argument */"

  | .op operator args =>
    -- Binary operators
    match args with
    | [arg1, arg2] =>
      match operator with
      | .lt_s | .le_s | .gt_s | .ge_s =>
        s!"($signed({emitExpr arg1}) {emitOperator operator} $signed({emitExpr arg2}))"
      | .asr =>
        s!"($signed({emitExpr arg1}) >>> {emitExpr arg2})"
      | _ =>
        s!"({emitExpr arg1} {emitOperator operator} {emitExpr arg2})"
    | _ => s!"/* ERROR: operator {operator} with wrong arity */"

/-- Emit a single statement.
    The optional `wires` parameter provides wire declarations for register
    reset value width lookup. -/
partial def emitStmt (stmt : Stmt) (indent : String := "    ")
    (wires : List Port := []) : String :=
  match stmt with
  | .assign lhs rhs =>
    s!"{indent}assign {sanitizeName lhs} = {emitExpr rhs};"

  | .assignExpr lhs rhs =>
    s!"{indent}assign {emitExpr lhs} = {emitExpr rhs};"

  | .generateFor label index start stop body =>
    let indexName := sanitizeName index
    let bodyIndent := indent ++ "        "
    let bodyCode := String.intercalate "\n" (body.map (emitStmt · bodyIndent wires))
    s!"{indent}genvar {indexName};\n" ++
      s!"{indent}generate\n" ++
      s!"{indent}    for ({indexName} = {emitDimExpr start}; " ++
      s!"{indexName} < {emitDimExpr stop}; {indexName} = {indexName} + 1) begin : {sanitizeName label}\n" ++
      bodyCode ++ "\n" ++
      s!"{indent}    end\n" ++
      s!"{indent}endgenerate"

  | .register output clock reset input initValue =>
    -- Generate always_ff block for register
    let resetValue := emitExpr initValue
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
    let lastAddress := match addrWidth.toNat? with
      | some width => s!"{(2 ^ width) - 1}"
      | none => s!"((2 ** {emitDimExpr addrWidth}) - 1)"
    let resetIndex := sanitizeName s!"{name}_reset_index"
    let memDecl :=
      s!"{indent}{emitType (hwTypeFromDim dataWidth)} {sanitizeName name} [0:{lastAddress}];\n" ++
      s!"{indent}integer {resetIndex};"
    let resetMemory :=
      s!"{indent}        for ({resetIndex} = 0; {resetIndex} <= {lastAddress}; " ++
      s!"{resetIndex} = {resetIndex} + 1) begin\n" ++
      s!"{indent}            {sanitizeName name}[{resetIndex}] <= '0;\n" ++
      s!"{indent}        end"
    if comboRead then
      -- Combinational read: assign readData = mem[readAddr]
      let assignRead := s!"{indent}assign {sanitizeName readData} = {sanitizeName name}[{emitExpr readAddr}];"
      let alwaysBlock :=
        s!"{indent}always_ff @(posedge {sanitizeName clock} or posedge rst) begin\n" ++
        s!"{indent}    if (rst) begin\n" ++
        resetMemory ++ "\n" ++
        s!"{indent}    end else if ({emitExpr writeEnable}) begin\n" ++
        s!"{indent}        {sanitizeName name}[{emitExpr writeAddr}] <= {emitExpr writeData};\n" ++
        s!"{indent}    end\n" ++
        s!"{indent}end"
      memDecl ++ "\n" ++ assignRead ++ "\n" ++ alwaysBlock
    else
      -- Registered read: readData latched inside always_ff
      let alwaysBlock :=
        s!"{indent}always_ff @(posedge {sanitizeName clock} or posedge rst) begin\n" ++
        s!"{indent}    if (rst) begin\n" ++
        resetMemory ++ "\n" ++
        s!"{indent}        {sanitizeName readData} <= '0;\n" ++
        s!"{indent}    end else begin\n" ++
        s!"{indent}        if ({emitExpr writeEnable}) begin\n" ++
        s!"{indent}            {sanitizeName name}[{emitExpr writeAddr}] <= {emitExpr writeData};\n" ++
        s!"{indent}        end\n" ++
        s!"{indent}        {sanitizeName readData} <= {sanitizeName name}[{emitExpr readAddr}];\n" ++
        s!"{indent}    end\n" ++
        s!"{indent}end"
      memDecl ++ "\n" ++ alwaysBlock

  | .inst moduleName instName connections parameterBindings =>
    let parameterOverrides := if parameterBindings.isEmpty then "" else
      let bindings := parameterBindings.map fun (name, value) =>
        s!".{sanitizeName name}({emitDimExpr value})"
      " #(\n" ++ indent ++ "    " ++
        String.intercalate (",\n" ++ indent ++ "    ") bindings ++
        "\n" ++ indent ++ ")"
    let connStrs := connections.map fun (portName, expr) =>
      s!".{sanitizeName portName}({emitExpr expr})"
    let connList := String.intercalate ", " connStrs
    s!"{indent}{sanitizeName moduleName}{parameterOverrides} {sanitizeName instName} ({connList});"

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

/-- Emit a SystemVerilog module parameter list. -/
def emitParameterList (parameters : List Parameter) : String :=
  if parameters.isEmpty then
    ""
  else
    let declarations := parameters.map fun parameter =>
      s!"parameter integer {sanitizeName parameter.name} = {parameter.defaultValue}"
    " #(\n    " ++ String.intercalate ",\n    " declarations ++ "\n)"

/-- Emit the full module -/
def emitModule (m : Module) : String :=
  -- For primitive/blackbox modules, just emit a comment (actual module comes from vendor)
  if m.isPrimitive then
    s!"// Primitive module: {m.name}\n" ++
    s!"// This is a blackbox module provided by the technology library\n" ++
    s!"// Interface: inputs={m.inputs.length}, outputs={m.outputs.length}\n\n"
  else
    let header := s!"// Generated by Sparkle HDL\n" ++
                  s!"// Module: {m.name}\n\n" ++
                  s!"module {sanitizeName m.name}{emitParameterList m.parameters} " ++
                  s!"({emitPortList m.inputs m.outputs});\n"

    -- Filter out wires that are already declared as input/output ports
    let portNames := (m.inputs ++ m.outputs).map (·.name)
    let internalWires := m.wires.filter fun w => !portNames.contains w.name
    let wires := if internalWires.isEmpty then
      ""
    else
      "\n" ++ emitWireDecls internalWires ++ "\n"

    let body := if m.body.isEmpty then
      ""
    else
      let stmts := m.body.map (emitStmt · "    " m.wires)
      "\n" ++ String.intercalate "\n\n" stmts ++ "\n"

    let footer := "\nendmodule\n"

    header ++ wires ++ body ++ footer

/-- Main entry point: Convert a Module to SystemVerilog -/
def toVerilog (m : Module) : String :=
  emitModule m

/-- Convert a full Design to SystemVerilog -/
def toVerilogDesign (d : Design) : String :=
  let modules := d.modules.map emitModule
  String.intercalate "\n" modules

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
