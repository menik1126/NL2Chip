/-
  SystemVerilog Backend

  Generates synthesizable SystemVerilog code from the IR.
-/

import Sparkle.IR.AST
import Sparkle.IR.Type

namespace Sparkle.Backend.Verilog

open Sparkle.IR.AST
open Sparkle.IR.Type

/-- Maximum temporary packed width used to evaluate a retained mathematical
    Nat expression.  The final hardware width/depth is independent of this
    limit; it only prevents a hostile shift/power override from asking an SV
    frontend to construct a multi-billion-bit intermediate before a guard can
    report the invalid configuration. -/
def maxNatWorkWidth : Nat := DimExpr.maxNatWorkWidth

/-- Sanitize a name to be a valid Verilog identifier -/
def sanitizeName (name : String) : String :=
  name.replace "." "_"
    |>.replace "-" "_"
    |>.replace " " "_"
    |>.replace "'" "_prime"
    |>.replace "#" ""

/-- Raw rendering used only for the size of the work-width casts below.
    Hardware dimensions and parameter values use `emitDimExpr`, which gives
    every Nat subexpression an explicit, sufficient unsigned width. -/
private partial def emitRawDimExpr : DimExpr → String
  | .literal value => toString value
  | .param name => sanitizeName name
  | .add lhs rhs => s!"({emitRawDimExpr lhs} + {emitRawDimExpr rhs})"
  | .sub lhs rhs =>
      -- Lean Nat subtraction saturates at zero; unsigned SystemVerilog
      -- subtraction wraps, so retain the source semantics explicitly.
      let lhs' := emitRawDimExpr lhs
      let rhs' := emitRawDimExpr rhs
      s!"(({lhs'} >= {rhs'}) ? ({lhs'} - {rhs'}) : 0)"
  | .mul lhs rhs => s!"({emitRawDimExpr lhs} * {emitRawDimExpr rhs})"
  | .div lhs rhs =>
      -- `Nat.div lhs 0 = 0`; SystemVerilog division by zero instead yields X.
      let lhs' := emitRawDimExpr lhs
      let rhs' := emitRawDimExpr rhs
      s!"(({rhs'} == 0) ? 0 : ({lhs'} / {rhs'}))"
  | .mod lhs rhs =>
      -- `Nat.mod lhs 0 = lhs`; preserve that total Lean operation rather than
      -- relying on SystemVerilog's X-producing zero-divisor behavior.
      let lhs' := emitRawDimExpr lhs
      let rhs' := emitRawDimExpr rhs
      s!"(({rhs'} == 0) ? {lhs'} : ({lhs'} % {rhs'}))"
  | .pow lhs rhs => s!"({emitRawDimExpr lhs} ** {emitRawDimExpr rhs})"
  | .shl lhs rhs => s!"({emitRawDimExpr lhs} << {emitRawDimExpr rhs})"
  | .shr lhs rhs => s!"({emitRawDimExpr lhs} >> {emitRawDimExpr rhs})"
  | .bitAnd lhs rhs => s!"({emitRawDimExpr lhs} & {emitRawDimExpr rhs})"
  | .bitOr lhs rhs => s!"({emitRawDimExpr lhs} | {emitRawDimExpr rhs})"
  | .bitXor lhs rhs => s!"({emitRawDimExpr lhs} ^ {emitRawDimExpr rhs})"
  | .clog2 value => s!"$clog2({emitRawDimExpr value})"
  | .min lhs rhs =>
      let lhs' := emitRawDimExpr lhs
      let rhs' := emitRawDimExpr rhs
      s!"(({lhs'} < {rhs'}) ? {lhs'} : {rhs'})"
  | .max lhs rhs =>
      let lhs' := emitRawDimExpr lhs
      let rhs' := emitRawDimExpr rhs
      s!"(({lhs'} > {rhs'}) ? {lhs'} : {rhs'})"

/-- Evaluate the meta-level expression that determines a work width in an
    explicit 64-bit unsigned context.  Checked emission accepts only
    expressions whose own conservative value bound fits in 64 bits, so this
    layer cannot itself wrap while computing a cast size. -/
private partial def emitMetaNat64 (expression : DimExpr) : String :=
  let wrap (body : String) : String := s!"$unsigned((64)'({body}))"
  match expression with
  | .literal value => wrap (toString value)
  | .param name => wrap (sanitizeName name)
  | .add lhs rhs => wrap s!"({emitMetaNat64 lhs} + {emitMetaNat64 rhs})"
  | .sub lhs rhs =>
      let lhs' := emitMetaNat64 lhs
      let rhs' := emitMetaNat64 rhs
      wrap s!"(({lhs'} >= {rhs'}) ? ({lhs'} - {rhs'}) : 0)"
  | .mul lhs rhs => wrap s!"({emitMetaNat64 lhs} * {emitMetaNat64 rhs})"
  | .div lhs rhs =>
      let lhs' := emitMetaNat64 lhs
      let rhs' := emitMetaNat64 rhs
      wrap s!"(({rhs'} == 0) ? 0 : ({lhs'} / {rhs'}))"
  | .mod lhs rhs =>
      let lhs' := emitMetaNat64 lhs
      let rhs' := emitMetaNat64 rhs
      wrap s!"(({rhs'} == 0) ? {lhs'} : ({lhs'} % {rhs'}))"
  | .pow lhs rhs => wrap s!"({emitMetaNat64 lhs} ** {emitMetaNat64 rhs})"
  | .shl lhs rhs => wrap s!"({emitMetaNat64 lhs} << {emitMetaNat64 rhs})"
  | .shr lhs rhs => wrap s!"({emitMetaNat64 lhs} >> {emitMetaNat64 rhs})"
  | .bitAnd lhs rhs => wrap s!"({emitMetaNat64 lhs} & {emitMetaNat64 rhs})"
  | .bitOr lhs rhs => wrap s!"({emitMetaNat64 lhs} | {emitMetaNat64 rhs})"
  | .bitXor lhs rhs => wrap s!"({emitMetaNat64 lhs} ^ {emitMetaNat64 rhs})"
  | .clog2 value =>
      let value' := emitMetaNat64 value
      wrap s!"(({value'} <= 1) ? 0 : $clog2({value'}))"
  | .min lhs rhs =>
      let lhs' := emitMetaNat64 lhs
      let rhs' := emitMetaNat64 rhs
      wrap s!"(({lhs'} < {rhs'}) ? {lhs'} : {rhs'})"
  | .max lhs rhs =>
      let lhs' := emitMetaNat64 lhs
      let rhs' := emitMetaNat64 rhs
      wrap s!"(({lhs'} > {rhs'}) ? {lhs'} : {rhs'})"

/-- Render a mathematical Nat expression without allowing an outer packed
    context (or an unsized literal's legacy width) to truncate intermediates.
    The canonical wrapper is recovered by the SV parser only after it verifies
    the same conservative bound. -/
partial def emitNatValue (expression : DimExpr) : String :=
  let expression := expression.normalize
  let wrap (body : String) : String :=
    let bound := emitMetaNat64 expression.natValueBitWidthBound
    let safeBound := s!"(({bound} <= {maxNatWorkWidth}) ? {bound} : 1)"
    s!"$unsigned(({safeBound})'({body}))"
  match expression with
  | .literal value => wrap (toString value)
  | .param name => wrap (sanitizeName name)
  | .add lhs rhs => wrap s!"({emitNatValue lhs} + {emitNatValue rhs})"
  | .sub lhs rhs =>
      let lhs' := emitNatValue lhs
      let rhs' := emitNatValue rhs
      wrap s!"(({lhs'} >= {rhs'}) ? ({lhs'} - {rhs'}) : 0)"
  | .mul lhs rhs => wrap s!"({emitNatValue lhs} * {emitNatValue rhs})"
  | .div lhs rhs =>
      let lhs' := emitNatValue lhs
      let rhs' := emitNatValue rhs
      wrap s!"(({rhs'} == 0) ? 0 : ({lhs'} / {rhs'}))"
  | .mod lhs rhs =>
      let lhs' := emitNatValue lhs
      let rhs' := emitNatValue rhs
      wrap s!"(({rhs'} == 0) ? {lhs'} : ({lhs'} % {rhs'}))"
  | .pow lhs rhs => wrap s!"({emitNatValue lhs} ** {emitNatValue rhs})"
  | .shl lhs rhs => wrap s!"({emitNatValue lhs} << {emitNatValue rhs})"
  | .shr lhs rhs => wrap s!"({emitNatValue lhs} >> {emitNatValue rhs})"
  | .bitAnd lhs rhs => wrap s!"({emitNatValue lhs} & {emitNatValue rhs})"
  | .bitOr lhs rhs => wrap s!"({emitNatValue lhs} | {emitNatValue rhs})"
  | .bitXor lhs rhs => wrap s!"({emitNatValue lhs} ^ {emitNatValue rhs})"
  | .clog2 value =>
      let value' := emitNatValue value
      wrap s!"(({value'} <= 1) ? 0 : $clog2({value'}))"
  | .min lhs rhs =>
      let lhs' := emitNatValue lhs
      let rhs' := emitNatValue rhs
      wrap s!"(({lhs'} < {rhs'}) ? {lhs'} : {rhs'})"
  | .max lhs rhs =>
      let lhs' := emitNatValue lhs
      let rhs' := emitNatValue rhs
      wrap s!"(({lhs'} > {rhs'}) ? {lhs'} : {rhs'})"

/-- Emit a width, depth, index, or parameter override with Lean Nat semantics. -/
def emitDimExpr (expression : DimExpr) : String := emitNatValue expression.normalize

/-- Clamp a hardware dimension to one for declarations and sized casts.  Invalid
    or pathologically large parameter overrides must remain parseable so that
    the generated constant generate guard can report a controlled error instead
    of triggering front-end crashes while constructing a zero- or multi-billion-
    bit range. -/
def emitSafeDimension (dimension : DimExpr) : String :=
  match dimension.toNat? with
  | some value =>
      if value > 0 && value <= maxNatWorkWidth then toString value else "1"
  | none =>
      let rendered := emitDimExpr dimension
      s!"((({rendered}) > 0 && ({rendered}) <= {maxNatWorkWidth}) ? ({rendered}) : 1)"

/-- Emit the high endpoint of a nonempty packed/unpacked range. -/
def emitRangeHigh (dimension : DimExpr) : String :=
  match dimension.toNat? with
  | some value => toString (value - 1)
  | none => s!"({emitSafeDimension dimension} - 1)"

/-- Emit a memory range under an explicit concrete-depth opt-in.  Parameter-
    dependent depths deliberately keep the ordinary conservative limit and
    guard; only a fully concrete memory declaration may use the larger cap. -/
def emitMemoryRangeHigh (depth : DimExpr)
    (maxMemoryDepth : Nat := maxNatWorkWidth) : String :=
  match depth.toNat? with
  | some value =>
      if value > 0 && value <= maxMemoryDepth then toString (value - 1) else "0"
  | none => emitRangeHigh depth

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
      -- its size.  A sized cast inherits the literal's signedness, whereas IR
      -- constants are unsigned packed values.  Materialize the width first,
      -- then make that result unsigned so later widening/comparison cannot
      -- silently sign-extend an unsized decimal literal.
      s!"$unsigned({emitSafeDimension width}'({value}))"

  | .paramConst value width =>
    -- A parameter constant is a Lean Nat expression materialized as an
    -- unsigned packed value.  The sized context prevents unsized literals and
    -- intermediate shifts from being silently limited to 32 bits.
    s!"$unsigned(({emitSafeDimension width})'({emitNatValue value}))"

  | .ref name =>
    sanitizeName name

  | .resize width value =>
    -- IR packed values are unsigned.  `$unsigned` makes widening explicitly
    -- zero-extending while the sized cast truncates to the least-significant
    -- `width` bits when narrowing.  Keep the safe dimension wrapper so an
    -- invalid zero override reaches Sparkle's generated validation guard
    -- instead of crashing the downstream parser on a zero-sized cast.
    s!"({emitSafeDimension width})'($unsigned({emitExpr value}))"

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
    (wires : List Port := [])
    (maxMemoryDepth : Nat := maxNatWorkWidth) : String :=
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

  | .memory name _addrWidth dataWidth depth clock writeAddr writeData writeEnable readAddr readData comboRead =>
    -- Generate memory array and always_ff block
    let memDecl := s!"{indent}logic [{emitRangeHigh dataWidth}:0] {sanitizeName name} [0:{emitMemoryRangeHigh depth maxMemoryDepth}];"
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

/-- Emit a parameter-only native generate condition. -/
partial def emitNativeCondition : NativeCondition → String
  | .nonzero value => s!"({emitDimExpr value} != 0)"
  | .eq lhs rhs => s!"({emitDimExpr lhs} == {emitDimExpr rhs})"
  | .ne lhs rhs => s!"({emitDimExpr lhs} != {emitDimExpr rhs})"
  | .lt lhs rhs => s!"({emitDimExpr lhs} < {emitDimExpr rhs})"
  | .le lhs rhs => s!"({emitDimExpr lhs} <= {emitDimExpr rhs})"
  | .gt lhs rhs => s!"({emitDimExpr lhs} > {emitDimExpr rhs})"
  | .ge lhs rhs => s!"({emitDimExpr lhs} >= {emitDimExpr rhs})"
  | .and lhs rhs => s!"({emitNativeCondition lhs} && {emitNativeCondition rhs})"
  | .or lhs rhs => s!"({emitNativeCondition lhs} || {emitNativeCondition rhs})"
  | .not condition => s!"!({emitNativeCondition condition})"

/-- Emit the canonical procedural subset retained for SystemVerilog. -/
partial def emitProcStmt (statement : ProcStmt) (indent : String := "        ") : String :=
  match statement with
  | .blocking lhs rhs => s!"{indent}{emitExpr lhs} = {emitExpr rhs};"
  | .ifElse condition then_ else_ =>
      let thenBody := String.intercalate "\n"
        (then_.map (emitProcStmt · (indent ++ "    ")))
      let elseBody := String.intercalate "\n"
        (else_.map (emitProcStmt · (indent ++ "    ")))
      s!"{indent}if ({emitExpr condition}) begin\n{thenBody}\n{indent}end" ++
        (if else_.isEmpty then "" else
          s!" else begin\n{elseBody}\n{indent}end")
  | .forLoop var init bound step inclusive body =>
      let comparison := if inclusive then "<=" else "<"
      let loopBody := String.intercalate "\n"
        (body.map (emitProcStmt · (indent ++ "    ")))
      s!"{indent}for ({sanitizeName var} = {init}; {sanitizeName var} {comparison} " ++
        s!"{emitDimExpr bound}; {sanitizeName var} = {sanitizeName var} + {step}) begin\n" ++
        loopBody ++ s!"\n{indent}end"

/-- Emit one SystemVerilog-native module item.  A nested conditional generate
    omits a second `generate/endgenerate` pair because generate regions cannot
    be nested. -/
partial def emitNativeItem (item : NativeItem) (indent : String := "    ")
    (insideGenerate : Bool := false) : String :=
  match item with
  | .wireDecl name ty => s!"{indent}{emitType ty} {sanitizeName name};"
  | .integerDecl name => s!"{indent}integer {sanitizeName name};"
  | .contAssign lhs rhs => s!"{indent}assign {emitExpr lhs} = {emitExpr rhs};"
  | .process .comb body =>
      let statements := String.intercalate "\n"
        (body.map (emitProcStmt · (indent ++ "    ")))
      s!"{indent}always_comb begin\n{statements}\n{indent}end"
  | .generateIf condition thenItems elseItems =>
      let thenBody := String.intercalate "\n"
        (thenItems.map (emitNativeItem · (indent ++ "    ") true))
      let elseBody := String.intercalate "\n"
        (elseItems.map (emitNativeItem · (indent ++ "    ") true))
      let generatePrefix := if insideGenerate then "if" else "generate if"
      let suffix := if insideGenerate then "" else " endgenerate"
      s!"{indent}{generatePrefix} ({emitNativeCondition condition}) begin\n{thenBody}\n{indent}end" ++
        (if elseItems.isEmpty then suffix else
          s!" else begin\n{elseBody}\n{indent}end{suffix}")
  | .inst moduleName instName connections parameterOverrides =>
      let connectionList := String.intercalate ", " <| connections.map fun (name, value) =>
        s!".{sanitizeName name}({emitExpr value})"
      let parameterList := if parameterOverrides.isEmpty then "" else
        " #(" ++ String.intercalate ", " (parameterOverrides.map fun (name, value) =>
          s!".{sanitizeName name}({emitDimExpr value})") ++ ")"
      s!"{indent}{sanitizeName moduleName}{parameterList} {sanitizeName instName} ({connectionList});"

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
      let message := (s!"Sparkle invalid hardware dimension in {role}: {dimension} must be between 1 and {maxNatWorkWidth}")
        |>.replace "\\" "\\\\" |>.replace "\"" "\\\""
      s!"{indent}generate if (!((({rendered}) > 0) && (({rendered}) <= {maxNatWorkWidth}))) begin : sparkle_invalid_dimension_{index}\n" ++
      s!"{indent}    initial $fatal(1, \"{message}\");\n" ++
      s!"{indent}end endgenerate"
  let workExpressions := m.dimensionExpressions.foldl (fun result expression =>
    let expression := expression.normalize
    if expression.parameters.isEmpty || result.contains expression then result
    else result ++ [expression]) []
  let workWidthGuards := workExpressions.zipIdx.map fun (expression, index) =>
    let bound := emitMetaNat64 expression.natValueBitWidthBound
    let message := (s!"Sparkle Nat expression work width exceeds {maxNatWorkWidth} bits: {expression}")
      |>.replace "\\" "\\\\" |>.replace "\"" "\\\""
    s!"{indent}generate if (!(({bound}) <= {maxNatWorkWidth})) begin : sparkle_invalid_nat_work_width_{index}\n" ++
    s!"{indent}    initial $fatal(1, \"{message}\");\n" ++
    s!"{indent}end endgenerate"
  String.intercalate "\n" (parameterGuards ++ dimensionGuards ++ workWidthGuards)

/-- Emit the full module -/
def emitModule (m : Module)
    (maxMemoryDepth : Nat := maxNatWorkWidth) : String :=
  -- For primitive/blackbox modules, just emit a comment (actual module comes from vendor)
  if m.isPrimitive then
    s!"// Primitive module: {m.name}\n" ++
    s!"// This is a blackbox module provided by the technology library\n" ++
    s!"// Interface: inputs={m.inputs.length}, outputs={m.outputs.length}\n\n"
  else
    let parameterList := if m.parameters.isEmpty then "" else
      let entries := m.parameters.map fun parameter =>
        -- Sparkle's native Nat parameter contract is an explicit unsigned
        -- 32-bit configuration value.  This makes the parameter leaf bound in
        -- `natValueBitWidthBound` independent of tool-specific inference.
        s!"parameter [31:0] {sanitizeName parameter.name} = {parameter.defaultValue}"
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
      let stmts := m.body.map (emitStmt · "    " m.wires maxMemoryDepth)
      "\n" ++ String.intercalate "\n\n" stmts ++ "\n"

    let nativeBody := if m.nativeItems.isEmpty then "" else
      "\n" ++ String.intercalate "\n\n" (m.nativeItems.map emitNativeItem) ++ "\n"

    let footer := "\nendmodule\n"

    header ++ wires ++ (if guards.isEmpty then "" else "\n" ++ guards ++ "\n") ++
      body ++ nativeBody ++ footer

/-- Checked entry point for callers that need diagnostics instead of emitting
    malformed or ambiguous SystemVerilog. -/
def toVerilogChecked (m : Module)
    (maxMemoryDepth : Nat := maxNatWorkWidth) : Except String String := do
  if maxMemoryDepth == 0 then
    throw "Sparkle SystemVerilog maximum memory depth must be positive"
  m.validateDimensions
  m.validateSanitizedNames sanitizeName
  let lookupDefault := fun name =>
    m.parameters.find? (fun parameter => parameter.name == name)
      |>.map (·.defaultValue)
  let memoryDepthRoles : Std.HashSet String := Std.HashSet.ofList <|
    m.body.filterMap fun statement => match statement with
      | .memory name _ _ _ _ _ _ _ _ _ _ =>
          some s!"module '{m.name}' memory '{name}' depth"
      | _ => none
  for (role, dimension) in m.positiveDimensions do
    match dimension.eval? lookupDefault with
    | some value =>
        -- A caller may explicitly accept a larger *concrete* memory.  Packed
        -- widths, array sizes, address/data widths, and symbolic memory depths
        -- all retain the global work/dimension limit.
        let limit := if memoryDepthRoles.contains role && dimension.toNat?.isSome
          then maxMemoryDepth else maxNatWorkWidth
        if value > limit then
          throw s!"{role} evaluates to {value}, exceeding Sparkle's safe SystemVerilog dimension limit {limit}"
    | none => pure () -- `validateDimensions` already reports this case.
  for parameter in m.parameters do
    if parameter.defaultValue > 0xffffffff then
      throw s!"module '{m.name}' parameter '{parameter.name}' default {parameter.defaultValue} exceeds Sparkle's 32-bit native parameter contract"
  for rawExpression in m.dimensionExpressions do
    let expression := rawExpression.normalize
    match expression.natValueBitWidthBound.natValueBitWidthBound.toNat? with
    | some metaWidth =>
        if metaWidth > 64 then
          throw s!"module '{m.name}' Nat expression '{expression}' requires more than 64 bits to compute its symbolic work width; specialize or simplify the nested shift/power expression"
    | none =>
        throw s!"module '{m.name}' Nat expression '{expression}' has a parameter-dependent work-width calculation; specialize or simplify the nested shift/power expression"
  return emitModule m maxMemoryDepth

/-- Main entry point: Convert a Module to SystemVerilog -/
def toVerilog (m : Module) : String :=
  match toVerilogChecked m with
  | .ok verilog => verilog
  | .error message => s!"/* ERROR: {message} */\n"

/-- Convert a full Design to SystemVerilog -/
def toVerilogDesignChecked (d : Design)
    (maxMemoryDepth : Nat := maxNatWorkWidth) : Except String String := do
  if maxMemoryDepth == 0 then
    throw "Sparkle SystemVerilog maximum memory depth must be positive"
  for module_ in d.modules do
    let emittedName := sanitizeName module_.name
    for other in d.modules do
      if module_.name != other.name && emittedName == sanitizeName other.name then
        throw s!"module names '{module_.name}' and '{other.name}' both sanitize to '{emittedName}'"
  let modules ← d.modules.mapM fun module_ =>
    toVerilogChecked module_ maxMemoryDepth
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
