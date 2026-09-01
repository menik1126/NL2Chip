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
  | .udiv => "/"
  | .sdiv => "/"
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
      | .udiv =>
        s!"(({emitExpr arg2} == '0) ? ({emitExpr arg1} ^ {emitExpr arg1}) : " ++
          s!"({emitExpr arg1} / {emitExpr arg2}))"
      | .sdiv =>
        s!"(($signed({emitExpr arg2}) == 0) ? " ++
          s!"($signed({emitExpr arg1}) - $signed({emitExpr arg1})) : " ++
          s!"($signed({emitExpr arg1}) / $signed({emitExpr arg2})))"
      | .asr =>
        s!"($signed({emitExpr arg1}) >>> {emitExpr arg2})"
      | _ =>
        s!"({emitExpr arg1} {emitOperator operator} {emitExpr arg2})"
    | _ => s!"/* ERROR: operator {operator} with wrong arity */"

/-- Render the active edge expression for a physical clock domain. -/
def emitDomainEdge (domain : ClockDomain) : String :=
  let edge := match domain.activeEdge with
    | .rising => "posedge"
    | .falling => "negedge"
  s!"{edge} {sanitizeName domain.clock}"

/-- Emit a single statement using the module's explicit clock-domain table. -/
partial def emitStmt (stmt : Stmt) (domains : List ClockDomain)
    (indent : String := "    ") (wires : List Port := []) : String :=
  match stmt with
  | .assign lhs rhs =>
    s!"{indent}assign {sanitizeName lhs} = {emitExpr rhs};"

  | .assignExpr lhs rhs =>
    s!"{indent}assign {emitExpr lhs} = {emitExpr rhs};"

  | .generateFor label index start stop body =>
    let indexName := sanitizeName index
    let bodyIndent := indent ++ "        "
    let bodyCode := String.intercalate "\n"
      (body.map (emitStmt · domains bodyIndent wires))
    s!"{indent}genvar {indexName};\n" ++
      s!"{indent}generate\n" ++
      s!"{indent}    for ({indexName} = {emitDimExpr start}; " ++
      s!"{indexName} < {emitDimExpr stop}; {indexName} = {indexName} + 1) begin : {sanitizeName label}\n" ++
      bodyCode ++ "\n" ++
      s!"{indent}    end\n" ++
      s!"{indent}endgenerate"

  | .signedDot output lhs rhs laneCount lhsWidth rhsWidth resultWidth =>
    let outputName := sanitizeName output
    let indexName := sanitizeName (output ++ "_dot_index")
    let accumName := sanitizeName (output ++ "_dot_accum")
    let resultWidthText := emitDimExpr resultWidth
    let lhsSlice :=
      s!"{emitExpr lhs}[({indexName} * {emitDimExpr lhsWidth}) +: {emitDimExpr lhsWidth}]"
    let rhsSlice :=
      s!"{emitExpr rhs}[({indexName} * {emitDimExpr rhsWidth}) +: {emitDimExpr rhsWidth}]"
    let lhsSigned := s!"$signed({resultWidthText}'($signed({lhsSlice})))"
    let rhsSigned := s!"$signed({resultWidthText}'($signed({rhsSlice})))"
    s!"{indent}always_comb begin : {outputName}_signed_dot\n" ++
      s!"{indent}    integer {indexName};\n" ++
      s!"{indent}    logic signed [{resultWidthText}-1:0] {accumName};\n" ++
      s!"{indent}    {accumName} = '0;\n" ++
      s!"{indent}    for ({indexName} = 0; {indexName} < {emitDimExpr laneCount}; " ++
      s!"{indexName} = {indexName} + 1) begin\n" ++
      s!"{indent}        {accumName} = $signed({accumName}) + " ++
      s!"({lhsSigned} * {rhsSigned});\n" ++
      s!"{indent}    end\n" ++
      s!"{indent}    {outputName} = {accumName};\n" ++
      s!"{indent}end"

  | .cdc output _sourceDomain _destDomain input kind =>
    s!"{indent}/* CDC: {kind} */ assign {sanitizeName output} = {emitExpr input};"

  | .register output domainId registerReset input initValue =>
    match domains.find? (fun domain => domain.id == domainId) with
    | none => s!"{indent}/* ERROR: unknown clock domain '{domainId}' */"
    | some domain =>
      let edge := emitDomainEdge domain
      let emitWithoutReset :=
        s!"{indent}always_ff @({edge}) begin\n" ++
        s!"{indent}    {sanitizeName output} <= {emitExpr input};\n" ++
        s!"{indent}end"
      match registerReset, domain.reset with
      | .none, _ | .domain, none => emitWithoutReset
      | .domain, some reset =>
          let event := match domain.resetKind with
            | .synchronous => edge
            | .asynchronous => s!"{edge} or posedge {sanitizeName reset}"
          s!"{indent}always_ff @({event}) begin\n" ++
          s!"{indent}    if ({sanitizeName reset})\n" ++
          s!"{indent}        {sanitizeName output} <= {emitExpr initValue};\n" ++
          s!"{indent}    else\n" ++
          s!"{indent}        {sanitizeName output} <= {emitExpr input};\n" ++
          s!"{indent}end"

  | .memory name addrWidth dataWidth domainId writeAddr writeData writeEnable readAddr readData comboRead =>
    -- Generate memory array and always_ff block
    let lastAddress := match addrWidth.toNat? with
      | some width => s!"{(2 ^ width) - 1}"
      | none => s!"((2 ** {emitDimExpr addrWidth}) - 1)"
    let resetIndex := sanitizeName s!"{name}_reset_index"
    let memDecl :=
      s!"{indent}{emitType (hwTypeFromDim dataWidth)} {sanitizeName name} [0:{lastAddress}];\n" ++
      s!"{indent}integer {resetIndex};"
    match domains.find? (fun domain => domain.id == domainId) with
      | none => memDecl ++ "\n" ++ s!"{indent}/* ERROR: unknown clock domain '{domainId}' */"
      | some domain =>
        let edge := emitDomainEdge domain
        let writeAndRead :=
          s!"{indent}        if ({emitExpr writeEnable}) begin\n" ++
          s!"{indent}            {sanitizeName name}[{emitExpr writeAddr}] <= {emitExpr writeData};\n" ++
          s!"{indent}        end" ++
          (if comboRead then "" else
            "\n" ++ s!"{indent}        {sanitizeName readData} <= {sanitizeName name}[{emitExpr readAddr}];")
        let resetMemory :=
          s!"{indent}        for ({resetIndex} = 0; {resetIndex} <= {lastAddress}; " ++
          s!"{resetIndex} = {resetIndex} + 1) begin\n" ++
          s!"{indent}            {sanitizeName name}[{resetIndex}] <= '0;\n" ++
          s!"{indent}        end" ++
          (if comboRead then "" else
            "\n" ++ s!"{indent}        {sanitizeName readData} <= '0;")
        let alwaysBlock := match domain.reset with
          | none =>
              s!"{indent}always_ff @({edge}) begin\n" ++
              writeAndRead ++ "\n" ++
              s!"{indent}end"
          | some reset =>
              let event := match domain.resetKind with
                | .synchronous => edge
                | .asynchronous => s!"{edge} or posedge {sanitizeName reset}"
              s!"{indent}always_ff @({event}) begin\n" ++
              s!"{indent}    if ({sanitizeName reset}) begin\n" ++
              resetMemory ++ "\n" ++
              s!"{indent}    end else begin\n" ++
              writeAndRead ++ "\n" ++
              s!"{indent}    end\n" ++
              s!"{indent}end"
        let assignRead := if comboRead then
          "\n" ++ s!"{indent}assign {sanitizeName readData} = {sanitizeName name}[{emitExpr readAddr}];"
          else ""
        memDecl ++ assignRead ++ "\n" ++ alwaysBlock

  | .asyncMemory name addrWidth dataWidth writeDomain writeAddr writeData writeEnable
      _readDomain readAddr readData =>
    let lastAddress := match addrWidth.toNat? with
      | some width => s!"{(2 ^ width) - 1}"
      | none => s!"((2 ** {emitDimExpr addrWidth}) - 1)"
    let memDecl :=
      s!"{indent}{emitType (hwTypeFromDim dataWidth)} {sanitizeName name} [0:{lastAddress}];"
    let asyncRead :=
      s!"{indent}assign {sanitizeName readData} = {sanitizeName name}[{emitExpr readAddr}];"
    match domains.find? (fun domain => domain.id == writeDomain) with
    | none =>
        memDecl ++ "\n" ++ asyncRead ++ "\n" ++
        s!"{indent}/* ERROR: unknown async-memory write domain '{writeDomain}' */"
    | some domain =>
        let edge := emitDomainEdge domain
        let writeBlock :=
          s!"{indent}always_ff @({edge}) begin\n" ++
          s!"{indent}    if ({emitExpr writeEnable}) begin\n" ++
          s!"{indent}        {sanitizeName name}[{emitExpr writeAddr}] <= {emitExpr writeData};\n" ++
          s!"{indent}    end\n" ++
          s!"{indent}end"
        memDecl ++ "\n" ++ asyncRead ++ "\n" ++ writeBlock

  | .inst moduleName instName connections parameterBindings _domainMap =>
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
      let stmts := m.body.map (emitStmt · m.clockDomains "    " m.wires)
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
