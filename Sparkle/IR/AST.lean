/-
  Abstract Syntax Tree for Hardware Netlist

  Defines the IR (Intermediate Representation) that hardware designs compile to.
  This is similar to a simplified Verilog AST.
-/

import Sparkle.IR.Type

namespace Sparkle.IR.AST

open Sparkle.IR.Type

/-- A SystemVerilog-elaboration parameter retained by the hardware IR. -/
structure Parameter where
  name : String
  /-- A nonnegative default used when a downstream tool supplies no override.
      Positivity is checked only at uses that denote hardware widths/lengths. -/
  defaultValue : Nat := 1
  deriving Repr, BEq, Inhabited

/-- Port declaration (input/output of a module) -/
structure Port where
  name : String
  ty   : HWType
  deriving Repr, BEq, Inhabited


/--
  Hardware operators

  These represent primitive operations that map directly to hardware gates.
-/
inductive Operator where
  | and  : Operator  -- Bitwise AND
  | or   : Operator  -- Bitwise OR
  | xor  : Operator  -- Bitwise XOR
  | not  : Operator  -- Bitwise NOT
  | add  : Operator  -- Addition
  | sub  : Operator  -- Subtraction
  | mul  : Operator  -- Multiplication
  | eq   : Operator  -- Equality comparison
  | lt_u : Operator  -- Less than comparison (unsigned)
  | lt_s : Operator  -- Less than comparison (signed)
  | le_u : Operator  -- Less than or equal (unsigned)
  | le_s : Operator  -- Less than or equal (signed)
  | gt_u : Operator  -- Greater than (unsigned)
  | gt_s : Operator  -- Greater than (signed)
  | ge_u : Operator  -- Greater than or equal (unsigned)
  | ge_s : Operator  -- Greater than or equal (signed)
  | mux  : Operator  -- Multiplexer (ternary: condition ? then : else)
  | shl  : Operator  -- Shift left
  | shr  : Operator  -- Shift right (logical)
  | asr  : Operator  -- Arithmetic shift right (signed)
  | neg  : Operator  -- Arithmetic negation
  deriving Repr, BEq, DecidableEq

namespace Operator

/-- Convert operator to string representation -/
def toString : Operator → String
  | and  => "and"
  | or   => "or"
  | xor  => "xor"
  | not  => "not"
  | add  => "add"
  | sub  => "sub"
  | mul  => "mul"
  | eq   => "eq"
  | lt_u => "lt_u"
  | lt_s => "lt_s"
  | le_u => "le_u"
  | le_s => "le_s"
  | gt_u => "gt_u"
  | gt_s => "gt_s"
  | ge_u => "ge_u"
  | ge_s => "ge_s"
  | mux  => "mux"
  | shl  => "shl"
  | shr  => "shr"
  | asr  => "asr"
  | neg  => "neg"

instance : ToString Operator where
  toString := Operator.toString

end Operator

/--
  Expression in the netlist IR

  - Const: Literal constant value
  - Ref: Reference to a wire or port by name
  - Op: Application of an operator to arguments
-/
inductive Expr where
  | const (value : Int) (width : DimExpr) : Expr
  | ref (name : String) : Expr
  | op (operator : Operator) (args : List Expr) : Expr
  | concat (args : List Expr) : Expr
  | slice (expr : Expr) (hi lo : DimExpr) : Expr
  | index (array : Expr) (idx : Expr) : Expr
  deriving Repr, BEq, Inhabited

namespace Expr

/-- Create a constant expression from a BitVec -/
def ofBitVec {n : Nat} (bv : BitVec n) : Expr :=
  .const bv.toInt n

/-- Create a wire reference -/
def wire (name : String) : Expr := .ref name

/-- Helper constructors for common operations -/
def and (a b : Expr) : Expr := .op .and [a, b]
def or (a b : Expr) : Expr := .op .or [a, b]
def xor (a b : Expr) : Expr := .op .xor [a, b]
def not (a : Expr) : Expr := .op .not [a]
def add (a b : Expr) : Expr := .op .add [a, b]
def sub (a b : Expr) : Expr := .op .sub [a, b]
def mul (a b : Expr) : Expr := .op .mul [a, b]
def eq (a b : Expr) : Expr := .op .eq [a, b]
def lt_u (a b : Expr) : Expr := .op .lt_u [a, b]
def lt_s (a b : Expr) : Expr := .op .lt_s [a, b]
def mux (cond then_ else_ : Expr) : Expr := .op .mux [cond, then_, else_]

/-- Replace module parameters in every expression dimension.  Slice bounds are
    substituted as constant expressions but are not treated as positive
    hardware dimensions: zero is a valid bit offset. -/
partial def substituteDimensions (lookup : String → Option DimExpr) : Expr → Expr
  | .const value width => .const value (width.substitute lookup)
  | .ref name => .ref name
  | .op operator args => .op operator (args.map (substituteDimensions lookup))
  | .concat args => .concat (args.map (substituteDimensions lookup))
  | .slice expr hi lo =>
      .slice (expr.substituteDimensions lookup) (hi.substitute lookup) (lo.substitute lookup)
  | .index array idx =>
      .index (array.substituteDimensions lookup) (idx.substituteDimensions lookup)

/-- All dimension expressions occurring in an expression, including legal-zero
    slice bounds.  This is used to reject references to undeclared parameters. -/
partial def dimensionExpressions : Expr → List DimExpr
  | .const _ width => [width]
  | .ref _ => []
  | .op _ args | .concat args => args.flatMap dimensionExpressions
  | .slice expr hi lo => expr.dimensionExpressions ++ [hi, lo]
  | .index array idx => array.dimensionExpressions ++ idx.dimensionExpressions

/-- Dimensions that denote an actual packed value and therefore must be
    positive.  Slice indexes are intentionally excluded. -/
partial def positiveDimensions (role : String) : Expr → List (String × DimExpr)
  | .const _ width => [(s!"{role} constant width", width)]
  | .ref _ => []
  | .op _ args | .concat args => args.flatMap (positiveDimensions role)
  | .slice expr _ _ => expr.positiveDimensions role
  | .index array idx =>
      array.positiveDimensions role ++ idx.positiveDimensions role

/-- Convert expression to string (for debugging) -/
partial def toString : Expr → String
  | const v w => s!"{v}#{w}"
  | ref name => name
  | op operator args =>
      let argStr := String.intercalate ", " (args.map toString)
      s!"{operator}({argStr})"
  | concat args => s!"\{{String.intercalate ", " (args.map toString)}}"
  | slice e hi lo => s!"{toString e}[{hi}:{lo}]"
  | index arr idx => s!"{toString arr}[{toString idx}]"

instance : ToString Expr where
  toString := Expr.toString

end Expr

/--
  Statement in the netlist IR

  - Assign: Continuous assignment (combinational logic)
  - Register: Sequential logic (D flip-flop)
  - Memory: Synchronous RAM/BRAM primitive
  - Inst: Instantiation of another module
-/
inductive Stmt where
  | assign (lhs : String) (rhs : Expr) : Stmt
  | register
      (output : String)      -- Output wire name
      (clock : String)       -- Clock signal name
      (reset : String)       -- Reset signal name
      (input : Expr)         -- Input expression
      (initValue : Int)      -- Reset/initial value
      : Stmt
  | memory
      (name : String)         -- Memory instance name
      (addrWidth : DimExpr)   -- Address width (size = 2^addrWidth)
      (dataWidth : DimExpr)   -- Data width
      (clock : String)        -- Clock signal
      (writeAddr : Expr)      -- Write address port
      (writeData : Expr)      -- Write data port
      (writeEnable : Expr)    -- Write enable port
      (readAddr : Expr)       -- Read address port
      (readData : String)     -- Read data output wire
      (comboRead : Bool := false) -- Combinational (same-cycle) read
      : Stmt
  | inst
      (moduleName : String)   -- Name of module to instantiate
      (instName : String)     -- Instance name
      (connections : List (String × Expr))  -- Port connections
      (parameterOverrides : List (String × DimExpr) := [])
      : Stmt
  deriving Repr, BEq

namespace Stmt

/-- Replace module parameters throughout a statement. -/
def substituteDimensions (lookup : String → Option DimExpr) : Stmt → Stmt
  | .assign lhs rhs => .assign lhs (rhs.substituteDimensions lookup)
  | .register output clock reset input initValue =>
      .register output clock reset (input.substituteDimensions lookup) initValue
  | .memory name addrWidth dataWidth clock writeAddr writeData writeEnable
      readAddr readData comboRead =>
    .memory name (addrWidth.substitute lookup) (dataWidth.substitute lookup) clock
      (writeAddr.substituteDimensions lookup) (writeData.substituteDimensions lookup)
      (writeEnable.substituteDimensions lookup) (readAddr.substituteDimensions lookup)
      readData comboRead
  | .inst moduleName instName connections parameterOverrides =>
    .inst moduleName instName
      (connections.map fun (portName, expr) =>
        (portName, expr.substituteDimensions lookup))
      (parameterOverrides.map fun (name, value) =>
        (name, value.substitute lookup))

/-- All dimension expressions occurring in a statement. -/
def dimensionExpressions : Stmt → List DimExpr
  | .assign _ rhs => rhs.dimensionExpressions
  | .register _ _ _ input _ => input.dimensionExpressions
  | .memory _ addrWidth dataWidth _ writeAddr writeData writeEnable readAddr _ _ =>
      [addrWidth, dataWidth] ++
        [writeAddr, writeData, writeEnable, readAddr].flatMap Expr.dimensionExpressions
  | .inst _ _ connections parameterOverrides =>
      connections.flatMap (fun (_, expr) => expr.dimensionExpressions) ++
        parameterOverrides.map (·.2)

/-- Dimensions in a statement that must elaborate to positive values. -/
def positiveDimensions (role : String) : Stmt → List (String × DimExpr)
  | .assign lhs rhs => rhs.positiveDimensions s!"{role} assignment '{lhs}'"
  | .register output _ _ input _ =>
      input.positiveDimensions s!"{role} register '{output}'"
  | .memory name addrWidth dataWidth _ writeAddr writeData writeEnable readAddr _ _ =>
      [(s!"{role} memory '{name}' address width", addrWidth),
       (s!"{role} memory '{name}' data width", dataWidth)] ++
        [writeAddr, writeData, writeEnable, readAddr].flatMap
          (Expr.positiveDimensions s!"{role} memory '{name}'")
  | .inst _ instName connections _ =>
      connections.flatMap fun (_, expr) =>
        expr.positiveDimensions s!"{role} instance '{instName}'"

/-- Convert statement to string (for debugging) -/
def toString : Stmt → String
  | assign lhs rhs => s!"{lhs} := {rhs}"
  | register output clock reset input initValue =>
      s!"reg {output} @(posedge {clock}, {reset}) <= {input} (init: {initValue})"
  | memory name addrWidth dataWidth clock writeAddr writeData writeEnable readAddr readData comboRead =>
      let readKind := if comboRead then "combo_read" else "read"
      s!"memory {name}[2^{addrWidth}][{dataWidth}] @(posedge {clock}) " ++
      s!"write({writeAddr}, {writeData}, {writeEnable}) {readKind}({readAddr}) => {readData}"
  | inst modName instName conns parameterOverrides =>
      let paramStr := if parameterOverrides.isEmpty then "" else
        " #(" ++ String.intercalate ", "
          (parameterOverrides.map fun (name, value) => s!".{name}({value})") ++ ")"
      let connStr := String.intercalate ", " (conns.map fun (p, e) => s!".{p}({e})")
      s!"{modName}{paramStr} {instName}({connStr})"

instance : ToString Stmt where
  toString := Stmt.toString

end Stmt

/--
  Module: A hardware module with inputs, outputs, internal wires, and logic

  This represents a synthesizable hardware component.

  If isPrimitive is true, this is a blackbox module (e.g., vendor SRAM, clock gate)
  that should be instantiated but not defined. The body is ignored for primitives.
-/
structure Module where
  name        : String
  parameters  : List Parameter := []
  inputs      : List Port
  outputs     : List Port
  wires       : List Port    -- Internal wires (ignored for primitives)
  body        : List Stmt    -- Logic (ignored for primitives)
  assertions  : List (String × Expr) := []  -- Formal assertions (name, condition)
  isPrimitive : Bool := false  -- True for vendor-provided blackbox modules
  deriving Repr, BEq

namespace Module

/-- Create an empty module -/
def empty (name : String) : Module :=
  { name := name
  , parameters := []
  , inputs := []
  , outputs := []
  , wires := []
  , body := []
  , isPrimitive := false
  }

/-- Create a primitive (blackbox) module with specified interface -/
def primitive (name : String) (inputs : List Port) (outputs : List Port) : Module :=
  { name := name
  , parameters := []
  , inputs := inputs
  , outputs := outputs
  , wires := []
  , body := []
  , isPrimitive := true
  }

/-- Add an input port -/
def addInput (m : Module) (p : Port) : Module :=
  { m with inputs := m.inputs ++ [p] }

/-- Add an elaboration parameter to the module in source-binder order. -/
def addParameter (m : Module) (p : Parameter) : Module :=
  { m with parameters := m.parameters ++ [p] }

/-- Add an output port -/
def addOutput (m : Module) (p : Port) : Module :=
  { m with outputs := m.outputs ++ [p] }

/-- Add an internal wire -/
def addWire (m : Module) (p : Port) : Module :=
  { m with wires := m.wires ++ [p] }

/-- Add a statement to the body -/
def addStmt (m : Module) (s : Stmt) : Module :=
  { m with body := m.body ++ [s] }

/-- Replace dimensions throughout a module body and interface.  Parameter
    declarations themselves are left intact so callers can choose whether they
    are specializing a child module or rewriting one in place. -/
def substituteDimensions (m : Module) (lookup : String → Option DimExpr) : Module :=
  { m with
    inputs := m.inputs.map fun p =>
      { p with ty := p.ty.substituteDimensions lookup }
    outputs := m.outputs.map fun p =>
      { p with ty := p.ty.substituteDimensions lookup }
    wires := m.wires.map fun p =>
      { p with ty := p.ty.substituteDimensions lookup }
    body := m.body.map (Stmt.substituteDimensions lookup)
    assertions := m.assertions.map fun (name, expr) =>
      (name, expr.substituteDimensions lookup) }

/-- Every expression that may refer to a module parameter. -/
def dimensionExpressions (m : Module) : List DimExpr :=
  let portExprs := (m.inputs ++ m.outputs ++ m.wires).flatMap fun port =>
    (port.ty.dimensions s!"port '{port.name}'").map (·.2)
  portExprs ++ m.body.flatMap Stmt.dimensionExpressions ++
    m.assertions.flatMap (fun (_, expr) => expr.dimensionExpressions)

/-- Every hardware width/length that must be positive. -/
def positiveDimensions (m : Module) : List (String × DimExpr) :=
  let portDimensions := (m.inputs ++ m.outputs ++ m.wires).flatMap fun port =>
    port.ty.dimensions s!"module '{m.name}' port/wire '{port.name}'"
  portDimensions ++ m.body.flatMap (Stmt.positiveDimensions s!"module '{m.name}'") ++
    m.assertions.flatMap fun (name, expr) =>
      expr.positiveDimensions s!"module '{m.name}' assertion '{name}'"

/-- Validate the dimension contract of a module under its declared default
    parameter environment.  This catches both literal zero widths and derived
    zero widths such as `W - 8` with a default `W = 4`. -/
def validateDimensions (m : Module) : Except String Unit := do
  for parameter in m.parameters do
    if m.parameters.countP (fun other => other.name == parameter.name) > 1 then
      throw s!"module '{m.name}' declares duplicate parameter '{parameter.name}'"
  let declared := m.parameters.map (·.name)
  for expr in m.dimensionExpressions do
    for name in expr.parameters do
      unless declared.contains name do
        throw s!"module '{m.name}' dimension '{expr}' references undeclared parameter '{name}'"
  let lookupDefault := fun name =>
    m.parameters.find? (fun parameter => parameter.name == name)
      |>.map (·.defaultValue)
  for (role, expr) in m.positiveDimensions do
    match expr.eval? lookupDefault with
    | some 0 => throw s!"{role} evaluates to zero under the module's default parameters"
    | some _ => pure ()
    | none =>
      throw s!"{role} '{expr}' cannot be evaluated under the module's default parameters"

/-- Validate names after applying the backend's identifier sanitization.  SV
    parameters and ports share declaration scopes closely enough that a
    sanitized collision is ambiguous and must not be emitted. -/
def validateSanitizedNames (m : Module) (sanitize : String → String) : Except String Unit := do
  let declarations :=
    (m.parameters.map fun parameter => ("parameter", parameter.name)) ++
    ((m.inputs ++ m.outputs ++ m.wires).map fun port => ("port/wire", port.name))
  let distinctDeclarations := declarations.foldl (fun result declaration =>
    if result.any (fun existing => existing == declaration) then result
    else result ++ [declaration]) []
  for (kind, name) in distinctDeclarations do
    let sanitized := sanitize name
    if sanitized.isEmpty then
      throw s!"module '{m.name}' {kind} '{name}' sanitizes to an empty SystemVerilog identifier"
    if distinctDeclarations.countP (fun (_, otherName) => sanitize otherName == sanitized) > 1 then
      throw s!"module '{m.name}' has colliding SystemVerilog identifier '{sanitized}' after sanitizing {kind} '{name}'"

/-- Convert module to string (for debugging) -/
def toString (m : Module) : String :=
  let parameterStr := String.intercalate ", "
    (m.parameters.map fun p => s!"{p.name}={p.defaultValue}")
  let inputStr := String.intercalate ", " (m.inputs.map fun p => s!"{p.name}: {p.ty}")
  let outputStr := String.intercalate ", " (m.outputs.map fun p => s!"{p.name}: {p.ty}")
  let wireStr := String.intercalate ", " (m.wires.map fun p => s!"{p.name}: {p.ty}")
  let bodyStr := String.intercalate "\n  " (m.body.map Stmt.toString)
  s!"module {m.name}\n" ++
  s!"  parameters: {parameterStr}\n" ++
  s!"  inputs:  {inputStr}\n" ++
  s!"  outputs: {outputStr}\n" ++
  s!"  wires:   {wireStr}\n" ++
  s!"  body:\n  {bodyStr}"

instance : ToString Module where
  toString := Module.toString

end Module

/--
  Design: A collection of modules that make up a hardware project.
-/
structure Design where
  topModule : String
  modules   : List Module
  deriving Repr, BEq

namespace Design

/-- Create an empty design -/
def empty (topName : String) : Design :=
  { topModule := topName, modules := [] }

/-- Add a module to the design -/
def addModule (d : Design) (m : Module) : Design :=
  { d with modules := d.modules ++ [m] }

/-- Find a module by name -/
def findModule (d : Design) (name : String) : Option Module :=
  d.modules.find? (·.name == name)

end Design

end Sparkle.IR.AST
